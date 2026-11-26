# ruff: noqa: PLR2004
from datetime import timedelta
from hashlib import sha256
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from sabil_book.exceptions import InvalidTransitionError
from sabil_book.offers.models import Order
from sabil_book.offers.order_services import accept_result
from sabil_book.offers.order_services import open_dispute
from sabil_book.offers.order_services import process_review_timer
from sabil_book.offers.order_services import request_correction
from sabil_book.offers.order_services import resolve_dispute
from sabil_book.offers.order_services import upload_result
from sabil_book.offers.tasks import process_order_reviews
from sabil_book.offers.tasks import scan_attachment
from sabil_book.offers.tests.factories import OrderFactory
from sabil_book.payments.models import Payment
from sabil_book.payments.models import Payout
from sabil_book.payments.services import confirm_payment
from sabil_book.payments.services import confirm_payout
from sabil_book.payments.services import confirm_refund
from sabil_book.payments.tasks import issue_order_payout
from sabil_book.payments.tests.factories import PaymentFactory
from sabil_book.users.tests.factories import UserFactory

S = Order.OrderStatus
# Independent contract, so a mistakenly added transition fails the matrix.
ALLOWED = {
    (S.AWAITING_PAYMENT, S.FUNDED),
    (S.FUNDED, S.DELIVERED),
    (S.FUNDED, S.DISPUTED),
    (S.DELIVERED, S.UNDER_REVIEW),
    (S.UNDER_REVIEW, S.COMPLETED),
    (S.UNDER_REVIEW, S.CORRECTION_REQUESTED),
    (S.UNDER_REVIEW, S.DISPUTED),
    (S.CORRECTION_REQUESTED, S.DELIVERED),
    (S.CORRECTION_REQUESTED, S.DISPUTED),
    (S.DISPUTED, S.COMPLETED),
    (S.DISPUTED, S.REFUNDED),
    (S.COMPLETED, S.PAID_OUT),
}


@pytest.mark.parametrize("source", S.values)
@pytest.mark.parametrize("target", S.values)
def test_complete_transition_matrix(source, target):
    order = Order(status=source)
    if (source, target) in ALLOWED:
        order.transition_to(target)
        assert order.status == target
    else:
        with pytest.raises(InvalidTransitionError):
            order.transition_to(target)
        assert order.status == source


@pytest.fixture
def order(db):
    return OrderFactory(status=S.FUNDED, funded_at=timezone.now())


@pytest.fixture
def client():
    return APIClient()


def result_file():
    return SimpleUploadedFile(
        "result.txt",
        b"Delivered result",
        content_type="text/plain",
    )


def deliver(order):
    result = upload_result(order.pk, order.offer.provider.user, result_file())
    with patch("sabil_book.offers.antivirus.scan_stream", return_value="clean"):
        scan_attachment(result.attachments.latest("pk").pk)
    result.refresh_from_db()
    return result


def test_upload_stores_file_hash_and_deadline(order, settings):
    settings.ORDER_REVIEW_PERIOD_SECONDS = 2
    before = timezone.now()
    delivered = deliver(order)
    attachment = delivered.attachments.get()
    assert delivered.status == S.DELIVERED
    assert attachment.file.read() == b"Delivered result"
    assert attachment.file_hash == sha256(b"Delivered result").hexdigest()
    assert attachment.storage_key == attachment.file.name
    assert delivered.review_deadline == delivered.delivered_at + timedelta(seconds=2)
    assert delivered.delivered_at >= before
    process_review_timer(order.pk)
    order.refresh_from_db()
    assert order.status == S.UNDER_REVIEW


@pytest.mark.usefixtures("payments_enabled")
def test_accelerated_timer_completes_once_and_enqueues_after_commit(
    order,
    settings,
    django_capture_on_commit_callbacks,
):
    settings.ORDER_REVIEW_PERIOD_SECONDS = 2
    delivered = deliver(order)
    deadline = delivered.review_deadline
    with patch(
        "django.utils.timezone.now",
        return_value=deadline - timedelta(microseconds=1),
    ):
        assert process_order_reviews.apply().get() == 0
    with patch("sabil_book.payments.tasks.issue_order_payout.delay") as payout:
        with (
            django_capture_on_commit_callbacks(execute=True),
            patch("django.utils.timezone.now", return_value=deadline),
        ):
            assert process_order_reviews.apply().get() == 1
            payout.assert_not_called()
        payout.assert_called_once_with(order.pk)
        assert process_order_reviews.apply().get() == 0
    order.refresh_from_db()
    assert order.status == S.COMPLETED
    assert order.completed_at == deadline
    assert order.review_deadline is None


def test_correction_cancels_timer_and_redelivery_resets_period(order, settings):
    settings.ORDER_REVIEW_PERIOD_SECONDS = 2
    delivered = deliver(order)
    old_deadline = delivered.review_deadline
    customer = order.offer.request.customer
    request_correction(order.pk, customer, "Fix the result")
    with patch(
        "django.utils.timezone.now",
        return_value=old_deadline + timedelta(seconds=1),
    ):
        assert process_review_timer(order.pk) is False
        redelivered = deliver(order)
        assert redelivered.review_deadline > old_deadline
        assert process_review_timer(order.pk) is False
    assert redelivered.attachments.count() == 2
    assert redelivered.correction_reason == ""
    accept_result(order.pk, customer)
    order.refresh_from_db()
    assert order.status == S.COMPLETED


@pytest.mark.usefixtures("payments_enabled")
@pytest.mark.parametrize("provider_wins", [True, False])
def test_dispute_resolution_integrates_payout_and_refund(
    order,
    provider_wins,
    django_capture_on_commit_callbacks,
):
    payment = PaymentFactory(order=order, status=Payment.PaymentStatus.SUCCEEDED)
    deliver(order)
    open_dispute(order.pk, order.offer.request.customer, "Wrong result")
    assert process_review_timer(order.pk) is False
    assert order.dispute.reason == "Wrong result"
    with (
        patch("sabil_book.payments.tasks.issue_order_payout.delay") as payout,
        patch("sabil_book.payments.tasks.refund_order.delay") as refund,
    ):
        with django_capture_on_commit_callbacks(execute=True):
            resolved = resolve_dispute(
                order.pk,
                in_provider_favour=provider_wins,
                resolution="Staff decision",
            )
            payout.assert_not_called()
            refund.assert_not_called()
        if provider_wins:
            assert resolved.status == S.COMPLETED
            payout.assert_called_once_with(order.pk)
            refund.assert_not_called()
        else:
            assert resolved.status == S.DISPUTED
            refund.assert_called_once_with(order.pk)
            confirm_refund(payment.pk)
            assert confirm_refund(payment.pk).status == S.REFUNDED
    with pytest.raises(InvalidTransitionError):
        resolve_dispute(order.pk, in_provider_favour=True, resolution="Again")


@pytest.mark.parametrize("action", ["accept", "request-correction", "dispute"])
@pytest.mark.parametrize("actor", ["customer", "provider", "stranger", "anonymous"])
def test_customer_actions_enforce_access(order, client, action, actor):
    deliver(order)
    users = {
        "customer": order.offer.request.customer,
        "provider": order.offer.provider.user,
        "stranger": UserFactory(),
        "anonymous": None,
    }
    client.force_authenticate(users[actor])
    response = client.post(
        f"/api/orders/{order.pk}/{action}/",
        {"reason": "Please fix"},
    )
    expected = {"customer": 200, "provider": 403, "stranger": 404, "anonymous": 401}
    assert response.status_code == expected[actor]
    if actor != "customer":
        order.refresh_from_db()
        assert order.status == S.DELIVERED


@pytest.mark.parametrize("source", S.values)
@pytest.mark.parametrize(
    "action",
    ["accept", "request-correction", "dispute", "attachments"],
)
def test_action_state_matrix(order, client, source, action):
    order.status = source
    order.review_deadline = timezone.now() + timedelta(hours=1)
    order.save()
    provider_action = action == "attachments"
    client.force_authenticate(
        order.offer.provider.user if provider_action else order.offer.request.customer,
    )
    payload = (
        {"file": result_file()} if provider_action else {"reason": "Change needed"}
    )
    response = client.post(
        f"/api/orders/{order.pk}/{action}/",
        payload,
        format="multipart",
    )
    allowed = (
        {S.FUNDED, S.CORRECTION_REQUESTED}
        if provider_action
        else {S.DELIVERED, S.UNDER_REVIEW}
    )
    assert response.status_code == (200 if source in allowed else 409)
    if source not in allowed:
        order.refresh_from_db()
        assert order.status == source
        assert order.attachments.count() == 0


def test_upload_access_validation_and_hidden_orders(order, client, settings):
    for user, expected in [
        (order.offer.request.customer, 403),
        (UserFactory(), 404),
        (None, 401),
    ]:
        client.force_authenticate(user)
        assert (
            client.post(
                f"/api/orders/{order.pk}/attachments/",
                {"file": result_file()},
            ).status_code
            == expected
        )
    client.force_authenticate(order.offer.provider.user)
    assert client.post(f"/api/orders/{order.pk}/attachments/", {}).status_code == 400
    settings.ORDER_RESULT_MAX_BYTES = 1
    assert (
        client.post(
            f"/api/orders/{order.pk}/attachments/",
            {"file": result_file()},
        ).status_code
        == 400
    )
    stranger = UserFactory()
    client.force_authenticate(stranger)
    assert client.get(f"/api/orders/{order.pk}/").status_code == 404
    assert client.get("/api/orders/").data == []


@pytest.mark.parametrize("action", ["accept", "request-correction", "dispute"])
def test_customer_cannot_override_expired_review(order, client, action):
    delivered = deliver(order)
    client.force_authenticate(order.offer.request.customer)
    with patch("django.utils.timezone.now", return_value=delivered.review_deadline):
        response = client.post(
            f"/api/orders/{order.pk}/{action}/",
            {"reason": "Too late"},
        )
    assert response.status_code == 409
    order.refresh_from_db()
    assert order.status == S.DELIVERED


def test_unfunded_result_is_rejected_without_attachment(order):
    order.funded_at = None
    order.save()
    with pytest.raises(InvalidTransitionError):
        deliver(order)
    assert not order.attachments.exists()


@pytest.mark.usefixtures("payments_enabled")
def test_payment_api_selects_country_and_callback_funds_order(order, client, settings):
    order.status = S.AWAITING_PAYMENT
    order.funded_at = None
    order.save()
    customer = order.offer.request.customer
    customer.country = "KZ"
    customer.save()
    settings.PAYMENT_PROVIDERS_BY_COUNTRY = {
        "KZ": {"provider": "test", "currency": "KZT"},
    }
    client.force_authenticate(customer)
    with patch("sabil_book.payments.services.payment_backend", return_value=Mock()):
        response = client.post(f"/api/orders/{order.pk}/pay/")
        assert response.status_code == 202
        payment = Payment.objects.get(order=order)
        assert payment.currency == "KZT"
        assert payment.provider == "test"
        assert payment.amount == order.offer.price
        assert client.post(f"/api/orders/{order.pk}/pay/").data["id"] == payment.pk
    order.refresh_from_db()
    assert order.status == S.AWAITING_PAYMENT
    assert confirm_payment(payment.pk).status == S.FUNDED
    assert confirm_payment(payment.pk).funded_at is not None
    assert client.post(f"/api/orders/{order.pk}/pay/").status_code == 409


@pytest.mark.usefixtures("payments_enabled")
def test_unconfigured_payment_does_not_claim_success(order, client):
    order.status = S.AWAITING_PAYMENT
    order.save()
    client.force_authenticate(order.offer.request.customer)
    assert client.post(f"/api/orders/{order.pk}/pay/").status_code == 503
    assert not order.payments.exists()


def test_payout_waits_for_callback_and_is_idempotent(order):
    order.offer.provider.kyc_status = "approved"
    order.offer.provider.save()
    PaymentFactory(order=order, status=Payment.PaymentStatus.SUCCEEDED)
    deliver(order)
    accept_result(order.pk, order.offer.request.customer)
    backend = Mock()
    with patch("sabil_book.payments.tasks.payment_backend", return_value=backend):
        issue_order_payout.apply(args=[order.pk]).get()
        issue_order_payout.apply(args=[order.pk]).get()
    payout = Payout.objects.get(order=order)
    assert payout.status == Payout.PayoutStatus.PENDING
    assert backend.issue_payout.call_count == 2
    assert backend.issue_payout.call_args.args[0].pk == payout.pk
    assert confirm_payout(payout.pk).status == S.PAID_OUT
    assert confirm_payout(payout.pk).status == S.PAID_OUT


def test_payout_kyc_hold_and_missing_adapter_keep_money_pending(order):
    PaymentFactory(order=order, status=Payment.PaymentStatus.SUCCEEDED)
    order.status = S.COMPLETED
    order.save()
    order.offer.provider.kyc_status = "pending"
    order.offer.provider.save()
    issue_order_payout(order.pk)
    payout = order.payouts.get()
    assert payout.status == Payout.PayoutStatus.ON_HOLD
    with pytest.raises(InvalidTransitionError):
        confirm_payout(payout.pk)
    order.offer.provider.kyc_status = "approved"
    order.offer.provider.save()
    issue_order_payout(order.pk)
    payout.refresh_from_db()
    assert payout.status == Payout.PayoutStatus.PENDING
    order.refresh_from_db()
    assert order.status == S.COMPLETED


@pytest.mark.parametrize("source", S.values)
def test_financial_callbacks_reject_wrong_order_state(order, source):
    order.status = source
    order.save()
    payment = PaymentFactory(order=order, status=Payment.PaymentStatus.PENDING)
    if source != S.AWAITING_PAYMENT:
        with pytest.raises(InvalidTransitionError):
            confirm_payment(payment.pk)
        payment.refresh_from_db()
        assert payment.status == Payment.PaymentStatus.PENDING
    payout = Payout.objects.create(
        order=order,
        provider="test",
        amount=order.offer.price,
    )
    if source != S.COMPLETED:
        with pytest.raises(InvalidTransitionError):
            confirm_payout(payout.pk)
        payout.refresh_from_db()
        assert payout.status == Payout.PayoutStatus.PENDING
    payment.status = Payment.PaymentStatus.SUCCEEDED
    payment.save()
    if source != S.DISPUTED:
        with pytest.raises(InvalidTransitionError):
            confirm_refund(payment.pk)
        payment.refresh_from_db()
        assert payment.status == Payment.PaymentStatus.SUCCEEDED


def test_pending_dispute_cannot_be_refunded_without_resolution(order):
    payment = PaymentFactory(order=order, status=Payment.PaymentStatus.SUCCEEDED)
    deliver(order)
    open_dispute(order.pk, order.offer.request.customer, "Wrong result")
    with pytest.raises(InvalidTransitionError):
        confirm_refund(payment.pk)
    order.refresh_from_db()
    assert order.status == S.DISPUTED


@pytest.mark.usefixtures("payments_enabled")
def test_financial_tasks_call_adapter_without_faking_success(order):
    from sabil_book.payments.tasks import initiate_payment  # noqa: PLC0415
    from sabil_book.payments.tasks import refund_order  # noqa: PLC0415

    payment = PaymentFactory(order=order, status=Payment.PaymentStatus.PENDING)
    backend = Mock()
    with patch("sabil_book.payments.tasks.payment_backend", return_value=backend):
        initiate_payment.apply(args=[payment.pk]).get()
        backend.initiate_payment.assert_called_once()
        payment.refresh_from_db()
        assert payment.status == Payment.PaymentStatus.PENDING
        payment.status = Payment.PaymentStatus.SUCCEEDED
        payment.save()
        deliver(order)
        open_dispute(order.pk, order.offer.request.customer, "Wrong result")
        resolve_dispute(
            order.pk,
            in_provider_favour=False,
            resolution="Refund approved",
        )
        refund_order.apply(args=[order.pk]).get()
        backend.refund_payment.assert_called_once()
        payment.refresh_from_db()
        assert payment.status == Payment.PaymentStatus.SUCCEEDED
        order.refresh_from_db()
        assert order.status == S.DISPUTED


def test_failed_file_storage_does_not_advance_order(order):
    with (
        patch(
            "django.core.files.storage.FileSystemStorage.save",
            side_effect=OSError("Storage offline"),
        ),
        pytest.raises(OSError, match="Storage offline"),
    ):
        deliver(order)
    order.refresh_from_db()
    assert order.status == S.FUNDED
    assert not order.attachments.exists()


@pytest.mark.usefixtures("payments_enabled")
def test_accept_offer_creates_unpaid_order(db):
    from sabil_book.offers.services import accept_offer  # noqa: PLC0415
    from sabil_book.offers.tests.factories import OfferFactory  # noqa: PLC0415

    offer = OfferFactory(request__status="published")
    accept_offer(offer.pk, offer.request.customer)
    assert offer.order.status == S.AWAITING_PAYMENT
    assert offer.order.funded_at is None

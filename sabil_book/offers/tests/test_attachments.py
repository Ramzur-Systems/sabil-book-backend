# ruff: noqa: PLR2004, S105
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from unittest.mock import Mock
from unittest.mock import patch
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest
from celery.exceptions import Retry
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from sabil_book.exceptions import InvalidTransitionError
from sabil_book.notifications.models import NotificationDelivery
from sabil_book.offers.antivirus import ScanError
from sabil_book.offers.antivirus import scan_stream
from sabil_book.offers.models import Attachment
from sabil_book.offers.models import Order
from sabil_book.offers.order_services import process_review_timer
from sabil_book.offers.order_services import upload_result
from sabil_book.offers.storage import PrivateAttachmentStorage
from sabil_book.offers.tasks import purge_expired_attachments
from sabil_book.offers.tasks import recover_attachment_scans
from sabil_book.offers.tasks import scan_attachment
from sabil_book.offers.tasks import sweep_orphan_attachments
from sabil_book.offers.tests.factories import OrderFactory
from sabil_book.users.tests.factories import UserFactory

S = Order.OrderStatus


@pytest.fixture
def order(db):
    return OrderFactory(status=S.FUNDED, funded_at=timezone.now())


def upload(order):
    upload_result(
        order.pk,
        order.offer.provider.user,
        SimpleUploadedFile("result.txt", b"result"),
    )
    return order.attachments.latest("pk")


def test_upload_queues_only_after_commit_and_never_exposes_url(
    order,
    django_capture_on_commit_callbacks,
):
    with patch("sabil_book.offers.tasks.scan_attachment.delay") as enqueue:
        with django_capture_on_commit_callbacks(execute=True):
            attachment = upload(order)
            enqueue.assert_not_called()
        enqueue.assert_called_once_with(attachment.pk)
    order.refresh_from_db()
    assert attachment.av_scan_status == "pending"
    assert order.status == S.FUNDED
    assert order.review_deadline is None
    assert process_review_timer(order.pk) is False
    client = APIClient()
    client.force_authenticate(order.offer.request.customer)
    result = client.get(f"/api/orders/{order.pk}/").data["attachments"][0]
    assert "file" not in result
    assert "storage_key" not in result
    assert client.get(f"/api/attachments/{attachment.pk}/download/").status_code == 403
    with pytest.raises(InvalidTransitionError):
        upload(order)


def test_clean_scan_publishes_once_and_starts_review_at_scan(order):
    attachment = upload(order)
    with patch("sabil_book.offers.antivirus.scan_stream", return_value="clean") as scan:
        scan_attachment(attachment.pk)
        order.refresh_from_db()
        deadline = order.review_deadline
        scan_attachment(attachment.pk)
        scan.assert_called_once()
    order.refresh_from_db()
    attachment.refresh_from_db()
    assert attachment.av_scan_status == "clean"
    assert attachment.av_scanned_at is not None
    assert order.status == S.DELIVERED
    assert order.review_deadline == deadline
    assert order.delivered_at >= attachment.created_at
    assert NotificationDelivery.objects.filter(event="result_delivered").count() == 1


@pytest.mark.parametrize("result", ["infected", "error"])
def test_rejected_scan_notifies_provider_and_allows_replacement(order, result):
    attachment = upload(order)
    with patch(
        "sabil_book.offers.antivirus.scan_stream",
        return_value="infected",
        side_effect=ScanError("damaged") if result == "error" else None,
    ):
        scan_attachment(attachment.pk)
        scan_attachment(attachment.pk)
    attachment.refresh_from_db()
    order.refresh_from_db()
    assert attachment.av_scan_status == result
    assert order.status == S.FUNDED
    assert order.review_deadline is None
    notice = NotificationDelivery.objects.get(event="attachment_rejected")
    assert notice.recipient_id == order.offer.provider.user_id
    assert notice.context["attachment_id"] == attachment.pk
    assert not NotificationDelivery.objects.filter(event="result_delivered").exists()
    assert upload(order).av_scan_status == "pending"


def test_scanner_outage_retries_then_stays_pending_until_max_age(order):
    attachment = upload(order)
    with patch(
        "sabil_book.offers.antivirus.scan_stream",
        side_effect=OSError("offline"),
    ):
        with (
            patch.object(scan_attachment, "retry", side_effect=Retry),
            pytest.raises(Retry),
        ):
            scan_attachment(attachment.pk)
        attachment.refresh_from_db()
        assert attachment.av_scan_status == "pending"
        # Exhausted retries during an outage leave the row for recovery.
        scan_attachment.apply(args=[attachment.pk], retries=3).get()
        attachment.refresh_from_db()
        assert attachment.av_scan_status == "pending"
        assert not NotificationDelivery.objects.filter(
            event="attachment_rejected",
        ).exists()
        Attachment.objects.filter(pk=attachment.pk).update(
            created_at=timezone.now() - timedelta(days=2),
        )
        scan_attachment.apply(args=[attachment.pk], retries=3).get()
    attachment.refresh_from_db()
    assert attachment.av_scan_status == "error"
    assert order.status == S.FUNDED


def _age(attachment, **delta):
    Attachment.objects.filter(pk=attachment.pk).update(
        created_at=timezone.now() - timedelta(**delta),
    )


def test_recovery_skips_fresh_and_recently_queued_scans(order):
    attachment = upload(order)
    with patch("sabil_book.offers.tasks.scan_attachment.delay") as enqueue:
        recover_attachment_scans()
        enqueue.assert_not_called()  # Fresh upload: its own task is in flight.
        _age(attachment, minutes=11)
        recover_attachment_scans()
        enqueue.assert_called_once_with(attachment.pk)
        enqueue.reset_mock()
        recover_attachment_scans()
        enqueue.assert_not_called()  # Just re-queued; no duplicate chain.
        Attachment.objects.filter(pk=attachment.pk).update(
            av_scan_queued_at=timezone.now() - timedelta(minutes=11),
        )
        recover_attachment_scans()
        enqueue.assert_called_once_with(attachment.pk)
        enqueue.reset_mock()
        attachment.av_scan_status = "error"
        attachment.save()
        recover_attachment_scans()
        enqueue.assert_not_called()


def test_recovery_fails_closed_after_max_age(order):
    attachment = upload(order)
    _age(attachment, days=2)
    with patch("sabil_book.offers.tasks.scan_attachment.delay") as enqueue:
        recover_attachment_scans()
        enqueue.assert_not_called()
    attachment.refresh_from_db()
    assert attachment.av_scan_status == "error"


def test_rejected_scan_deletes_stored_object(
    order,
    django_capture_on_commit_callbacks,
):
    attachment = upload(order)
    storage, key = attachment.file.storage, attachment.file.name
    assert storage.exists(key)
    with (
        patch("sabil_book.offers.antivirus.scan_stream", return_value="infected"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        scan_attachment(attachment.pk)
    attachment.refresh_from_db()
    assert not storage.exists(key)
    assert not attachment.file
    assert attachment.storage_key == key


def test_sweeper_deletes_only_old_unreferenced_objects(order, settings):
    attachment = upload(order)
    storage = attachment.file.storage
    orphan = storage.save(f"results/{order.pk}/orphan.txt", BytesIO(b"x"))
    settings.ATTACHMENT_ORPHAN_GRACE_SECONDS = 3600
    assert sweep_orphan_attachments() == 0  # Too new: upload may be in flight.
    settings.ATTACHMENT_ORPHAN_GRACE_SECONDS = -1
    assert sweep_orphan_attachments() == 1
    assert not storage.exists(orphan)
    assert storage.exists(attachment.file.name)


def test_legacy_attachment_without_file_is_not_scanned(order):
    attachment = Attachment.objects.create(order=order, file_hash="abc")
    scan_attachment(attachment.pk)
    attachment.refresh_from_db()
    assert attachment.av_scan_status == "error"


def test_download_filename_keeps_extension(order):
    attachment = upload(order)
    Attachment.objects.filter(pk=attachment.pk).update(av_scan_status="clean")
    Order.objects.filter(pk=order.pk).update(status=S.DELIVERED)
    client = APIClient()
    client.force_authenticate(order.offer.request.customer)
    with patch.object(
        attachment.file.storage,
        "url",
        return_value="https://s3.test/signed",
    ) as url:
        response = client.get(f"/api/attachments/{attachment.pk}/download/")
    assert response.status_code == 200
    disposition = url.call_args.kwargs["parameters"]["ResponseContentDisposition"]
    assert disposition == 'attachment; filename="result.txt"'


@pytest.mark.parametrize("av_status", Attachment.ScanStatus.values)
@pytest.mark.parametrize("order_status", S.values)
def test_download_state_matrix(order, av_status, order_status):
    attachment = upload(order)
    Attachment.objects.filter(pk=attachment.pk).update(av_scan_status=av_status)
    Order.objects.filter(pk=order.pk).update(status=order_status)
    client = APIClient()
    client.force_authenticate(order.offer.request.customer)
    with patch.object(
        attachment.file.storage,
        "url",
        return_value="https://s3.test/signed",
    ) as sign:
        response = client.get(f"/api/attachments/{attachment.pk}/download/")
    allowed = av_status == "clean" and order_status not in {
        S.FUNDED,
        S.AWAITING_PAYMENT,
    }
    assert response.status_code == (200 if allowed else 403)
    if allowed:
        assert response.data == {"url": "https://s3.test/signed", "expires_in": 300}
        assert response["Cache-Control"] == "private, no-store"
        assert sign.call_args.kwargs["expire"] == 300
    else:
        sign.assert_not_called()


@pytest.mark.parametrize("actor", ["provider", "stranger", "anonymous"])
def test_download_denies_other_users_without_signing(order, actor):
    attachment = upload(order)
    client = APIClient()
    users = {
        "provider": order.offer.provider.user,
        "stranger": UserFactory(),
        "anonymous": None,
    }
    client.force_authenticate(users[actor])
    with patch.object(attachment.file.storage, "url") as sign:
        response = client.get(f"/api/attachments/{attachment.pk}/download/")
        assert response.status_code == (401 if actor == "anonymous" else 404)
        sign.assert_not_called()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("payload.exe", b"MZpayload"),
        ("fake.pdf", b"not a PDF"),
        ("fake.png", b"not a PNG"),
        ("fake.jpg", b"not a JPEG"),
        ("binary.txt", b"binary\0data"),
        ("empty.txt", b""),
    ],
)
def test_upload_rejects_disallowed_content(order, name, content):
    client = APIClient()
    client.force_authenticate(order.offer.provider.user)
    response = client.post(
        f"/api/orders/{order.pk}/attachments/",
        {"file": SimpleUploadedFile(name, content)},
    )
    assert response.status_code == 400
    assert not order.attachments.exists()


@pytest.mark.parametrize(
    ("reply", "result"),
    [
        (b"stream: OK\0", "clean"),
        (b"stream: Eicar-Signature FOUND\0", "infected"),
        (b"stream: ERROR\0", None),
        (b"", None),
        (b"other: OK\0", None),
        (b"stream: OK", None),
    ],
)
def test_clamav_protocol_requires_explicit_success(reply, result):
    connection = Mock()
    connection.recv.side_effect = [reply, b""]
    with patch("socket.create_connection") as connect:
        connect.return_value.__enter__.return_value = connection
        if result:
            assert (
                scan_stream(BytesIO(b"result"), sha256(b"result").hexdigest()) == result
            )
        else:
            with pytest.raises(ScanError):
                scan_stream(BytesIO(b"result"), sha256(b"result").hexdigest())
    assert connection.sendall.call_args_list[0].args == (b"zINSTREAM\0",)
    assert connection.sendall.call_args_list[1].args == (b"\0\0\0\x06result",)
    assert connection.sendall.call_args_list[2].args == (b"\0\0\0\0",)


def test_hash_mismatch_blocks_even_when_antivirus_reports_clean():
    with patch("socket.create_connection") as connect:
        connect.return_value.__enter__.return_value.recv.return_value = b"stream: OK\0"
        with pytest.raises(ScanError, match="SHA-256"):
            scan_stream(BytesIO(b"corrupted"), sha256(b"original").hexdigest())


def test_private_s3_storage_generates_v4_expiring_urls(settings):
    settings.ATTACHMENT_S3_ACCESS_KEY = "test-access"
    settings.ATTACHMENT_S3_SECRET_KEY = "test-secret"
    settings.ATTACHMENT_S3_ENDPOINT_URL = "https://s3.example.test"
    storage = PrivateAttachmentStorage()
    assert (
        storage.get_object_parameters("result.txt")["CacheControl"]
        == "private, no-store"
    )
    # No ACL header: the key must not need permission to change ACLs.
    assert storage.default_acl is None
    url = storage.url("results/result.txt", expire=123)
    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-Expires"] == ["123"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Signature"]
    assert storage.custom_domain is None


def test_legacy_delivered_scan_preserves_deadline(order):
    attachment = upload(order)
    now = timezone.now()
    Order.objects.filter(pk=order.pk).update(
        status=S.DELIVERED,
        delivered_at=now,
        review_deadline=now,
    )
    assert process_review_timer(order.pk) is False
    with patch("sabil_book.offers.antivirus.scan_stream", return_value="clean"):
        scan_attachment(attachment.pk)
    order.refresh_from_db()
    attachment.refresh_from_db()
    assert order.delivered_at == now
    assert order.review_deadline == now
    assert attachment.av_scan_status == "clean"


# Explicit opt-in against a real S3-compatible service (e.g. local SeaweedFS).
# Uses ATTACHMENT_TEST_S3_BUCKET when the key cannot create buckets; otherwise a
# temporary bucket is created and removed.
def test_real_s3_private_upload_and_expiring_download(settings):  # noqa: PLR0915
    import os  # noqa: PLC0415
    import time  # noqa: PLC0415
    from urllib.error import HTTPError  # noqa: PLC0415
    from urllib.request import urlopen  # noqa: PLC0415
    from uuid import uuid4  # noqa: PLC0415

    from django.core.files.base import ContentFile  # noqa: PLC0415

    from sabil_book.offers.tasks import _stored_keys  # noqa: PLC0415

    endpoint = os.environ.get("ATTACHMENT_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("Set ATTACHMENT_TEST_S3_ENDPOINT to run the real S3 test.")
    settings.ATTACHMENT_S3_ENDPOINT_URL = endpoint
    settings.ATTACHMENT_S3_PUBLIC_ENDPOINT_URL = None
    settings.ATTACHMENT_S3_ACCESS_KEY = os.environ["ATTACHMENT_TEST_S3_ACCESS_KEY"]
    settings.ATTACHMENT_S3_SECRET_KEY = os.environ["ATTACHMENT_TEST_S3_SECRET_KEY"]
    existing_bucket = os.environ.get("ATTACHMENT_TEST_S3_BUCKET")
    settings.ATTACHMENT_S3_BUCKET = existing_bucket or f"attachment-test-{uuid4().hex}"
    storage = PrivateAttachmentStorage()
    client = storage.connection.meta.client
    if not existing_bucket:
        client.create_bucket(Bucket=storage.bucket_name)
    prefix = f"results/test-{uuid4().hex}"
    key = None
    try:
        key = storage.save(f"{prefix}/result.pdf", ContentFile(b"%PDF-1.4 test"))
        head = client.head_object(Bucket=storage.bucket_name, Key=key)
        assert head["CacheControl"] == "private, no-store"
        assert list(_stored_keys(storage, prefix)) == [key]
        signed = storage.url(
            key,
            expire=2,
            parameters={
                "ResponseContentDisposition": 'attachment; filename="result.pdf"',
            },
        )
        unsigned = signed.split("?", 1)[0]
        with pytest.raises(HTTPError) as denied:
            urlopen(unsigned, timeout=5)  # noqa: S310
        assert denied.value.code == 403
        with pytest.raises(HTTPError) as tampered:
            urlopen(signed.replace("result.pdf", "result.exe"), timeout=5)  # noqa: S310
        assert tampered.value.code == 403
        with urlopen(signed, timeout=5) as response:  # noqa: S310
            assert response.read() == b"%PDF-1.4 test"
            assert response.headers["Content-Disposition"] == (
                'attachment; filename="result.pdf"'
            )
        time.sleep(3)
        with pytest.raises(HTTPError) as expired:
            urlopen(signed, timeout=5)  # noqa: S310
        assert expired.value.code == 403
        storage.delete(key)
        assert not storage.exists(key)
        key = None
    finally:
        if key:
            storage.delete(key)
        if not existing_bucket:
            client.delete_bucket(Bucket=storage.bucket_name)


def test_download_urls_are_signed_for_the_public_endpoint(settings):
    settings.ATTACHMENT_S3_ENDPOINT_URL = "http://seaweedfs:8333"
    settings.ATTACHMENT_S3_PUBLIC_ENDPOINT_URL = "https://files.example.com"
    settings.ATTACHMENT_S3_ACCESS_KEY = "test-access"
    settings.ATTACHMENT_S3_SECRET_KEY = "test-secret"
    url = PrivateAttachmentStorage().url("results/1/a.pdf", expire=60)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == "https://files.example.com"
    assert parts.path == "/sabil-book-results/results/1/a.pdf"
    assert "X-Amz-Signature" in parse_qs(parts.query)


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("broken.pdf", b"%PDF-1.7\ntruncated"),
        ("broken.png", b"\x89PNG\r\n\x1a\ntruncated"),
        ("broken.txt", b"a" * 2048 + b"\xff"),
    ],
)
def test_damaged_documents_are_not_published_even_when_av_returns_clean(
    order,
    filename,
    content,
):
    upload_result(
        order.pk,
        order.offer.provider.user,
        SimpleUploadedFile(filename, content),
    )
    attachment = order.attachments.get()
    with patch("sabil_book.offers.antivirus.scan_stream", return_value="clean"):
        scan_attachment(attachment.pk)
    attachment.refresh_from_db()
    order.refresh_from_db()
    assert attachment.file_hash == sha256(content).hexdigest()
    assert attachment.av_scan_status == "error"
    assert order.status == S.FUNDED
    assert NotificationDelivery.objects.filter(event="attachment_rejected").exists()


@pytest.mark.parametrize("extension", ["pdf", "png", "jpg", "txt"])
def test_valid_document_formats_pass_validation(order, extension):
    from PIL import Image  # noqa: PLC0415
    from pypdf import PdfWriter  # noqa: PLC0415

    buffer = BytesIO()
    if extension == "pdf":
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(buffer)
    elif extension in {"png", "jpg"}:
        Image.new("RGB", (2, 2)).save(
            buffer,
            format="PNG" if extension == "png" else "JPEG",
        )
    else:
        buffer.write(("a" * 1023 + "Привет").encode())
    upload_result(
        order.pk,
        order.offer.provider.user,
        SimpleUploadedFile(f"result.{extension}", buffer.getvalue()),
    )
    attachment = order.attachments.get()
    with patch("sabil_book.offers.antivirus.scan_stream", return_value="clean"):
        scan_attachment(attachment.pk)
    attachment.refresh_from_db()
    assert attachment.av_scan_status == "clean"


def _finished(order, status, days_ago):
    when = timezone.now() - timedelta(days=days_ago)
    field = "refunded_at" if status == S.REFUNDED else "completed_at"
    Order.objects.filter(pk=order.pk).update(status=status, **{field: when})


@pytest.mark.parametrize("status", [S.COMPLETED, S.PAID_OUT, S.REFUNDED])
def test_attachments_are_purged_after_retention(
    order,
    status,
    settings,
    django_capture_on_commit_callbacks,
):
    settings.ATTACHMENT_RETENTION_DAYS = 30
    attachment = upload(order)
    storage, key = attachment.file.storage, attachment.file.name
    _finished(order, status, days_ago=29)
    assert purge_expired_attachments() == 0  # Still downloadable.
    _finished(order, status, days_ago=31)
    with django_capture_on_commit_callbacks(execute=True):
        assert purge_expired_attachments() == 1
    assert not Attachment.objects.filter(pk=attachment.pk).exists()
    assert not storage.exists(key)
    order.refresh_from_db()
    assert order.status == status  # Only the attachment is removed.


@pytest.mark.parametrize(
    "status",
    [S.FUNDED, S.DELIVERED, S.UNDER_REVIEW, S.CORRECTION_REQUESTED, S.DISPUTED],
)
def test_open_orders_keep_attachments_regardless_of_age(order, status):
    attachment = upload(order)
    Order.objects.filter(pk=order.pk).update(
        status=status,
        completed_at=timezone.now() - timedelta(days=365),
    )
    assert purge_expired_attachments() == 0
    assert Attachment.objects.filter(pk=attachment.pk).exists()


def test_purge_leaves_object_for_sweeper_when_storage_delete_fails(
    order,
    settings,
    django_capture_on_commit_callbacks,
):
    attachment = upload(order)
    _finished(order, S.PAID_OUT, days_ago=31)
    with (
        patch.object(attachment.file.storage, "delete", side_effect=OSError),
        django_capture_on_commit_callbacks(execute=True),
    ):
        assert purge_expired_attachments() == 1  # robust on_commit: no crash.
    assert not Attachment.objects.filter(pk=attachment.pk).exists()
    settings.ATTACHMENT_ORPHAN_GRACE_SECONDS = -1
    assert sweep_orphan_attachments() == 1


def test_upload_survives_broker_outage_after_commit(
    order,
    django_capture_on_commit_callbacks,
):
    # A failing robust callback must be logged, not raised (the row is
    # committed and recovery re-queues the scan later).
    with (
        patch(
            "sabil_book.offers.tasks.scan_attachment.delay",
            side_effect=ConnectionError("broker down"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        attachment = upload(order)
    assert attachment.av_scan_status == "pending"

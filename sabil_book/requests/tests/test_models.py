import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction

from sabil_book.requests.models import Request
from sabil_book.requests.tests.factories import RequestFactory
from sabil_book.users.tests.factories import ProviderProfileFactory


class TestRequest:
    def test_category_is_required(self, user):
        request = Request(
            customer=user,
            title="Research request",
            description="Research description",
        )
        with pytest.raises(ValidationError) as exc_info:
            request.full_clean()
        assert "category" in exc_info.value.error_dict

    def test_budget_is_optional(self, user):
        request = Request.objects.create(
            customer=user,
            category=Request.RequestCategory.RESEARCH_BRIEF,
            title="Research request",
            description="Research description",
        )
        assert request.budget is None

    def test_str_includes_customer_and_category(self, db):
        request = RequestFactory.create(
            category=Request.RequestCategory.RESEARCH_BRIEF,
        )
        assert str(request) == f"{request.customer} - Research brief"

    def test_deleted_when_customer_is_deleted(self, user):
        request = RequestFactory.create(customer=user)
        user.delete()
        assert not Request.objects.filter(pk=request.pk).exists()

    def test_direct_request_requires_selected_provider(self, db):
        with pytest.raises(IntegrityError), transaction.atomic():
            RequestFactory.create(
                visibility=Request.RequestVisibility.DIRECT,
                selected_provider=None,
                direct_status=Request.DirectRequestStatus.PENDING,
            )

    def test_public_request_cannot_have_direct_fields(self, db):
        with pytest.raises(IntegrityError), transaction.atomic():
            RequestFactory.create(
                visibility=Request.RequestVisibility.PUBLIC,
                selected_provider=ProviderProfileFactory(),
                direct_status=Request.DirectRequestStatus.PENDING,
            )


@pytest.mark.django_db
def test_request_factory_creates_valid_instance():
    request = RequestFactory.create()
    assert request.pk is not None
    assert request.customer_id is not None

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sabil_book.users.models import ProviderProfile
from sabil_book.users.tests.factories import ProviderProfileFactory
from sabil_book.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from sabil_book.users.models import User


def test_user_str_returns_email(user: User):
    assert str(user) == user.email


def test_user_uuid_is_unique_per_instance(db):
    first_user = UserFactory.create()
    second_user = UserFactory.create()
    assert first_user.uuid != second_user.uuid


class TestProviderProfile:
    def test_defaults_to_not_submitted_kyc_status(self, user: User):
        profile = ProviderProfile.objects.create(user=user)
        assert profile.kyc_status == ProviderProfile.KYCStatus.NOT_SUBMITTED

    def test_str_includes_user_and_kyc_status(self, db):
        profile = ProviderProfileFactory.create(
            kyc_status=ProviderProfile.KYCStatus.APPROVED,
        )
        assert str(profile) == f"{profile.user} (Approved)"

    def test_deleted_when_user_is_deleted(self, user: User):
        profile = ProviderProfileFactory.create(user=user)
        user.delete()
        assert not ProviderProfile.objects.filter(pk=profile.pk).exists()


@pytest.mark.django_db
def test_provider_profile_factory_creates_valid_instance():
    profile = ProviderProfileFactory.create()
    assert profile.pk is not None
    assert profile.user_id is not None

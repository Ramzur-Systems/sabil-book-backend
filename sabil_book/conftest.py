from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sabil_book.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from sabil_book.users.models import User


@pytest.fixture(autouse=True)
def _media_storage(settings, tmpdir) -> None:
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture
def payments_enabled(settings) -> None:
    """Opt a test into the (currently hidden) payment flow."""
    settings.PAYMENTS_ENABLED = True


@pytest.fixture
def user(db) -> User:
    return UserFactory.create()

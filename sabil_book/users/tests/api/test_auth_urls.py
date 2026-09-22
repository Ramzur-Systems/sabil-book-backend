from __future__ import annotations

from django.urls import resolve
from django.urls import reverse


def test_register_url():
    assert reverse("auth:register") == "/api/auth/register/"
    assert resolve("/api/auth/register/").view_name == "auth:register"


def test_login_url():
    assert reverse("auth:login") == "/api/auth/login/"
    assert resolve("/api/auth/login/").view_name == "auth:login"


def test_refresh_url():
    assert reverse("auth:refresh") == "/api/auth/refresh/"
    assert resolve("/api/auth/refresh/").view_name == "auth:refresh"


def test_logout_url():
    assert reverse("auth:logout") == "/api/auth/logout/"
    assert resolve("/api/auth/logout/").view_name == "auth:logout"


def test_me_url():
    assert reverse("auth:me") == "/api/auth/me/"
    assert resolve("/api/auth/me/").view_name == "auth:me"

"""
With these settings, tests run faster.
"""

import os
from pathlib import Path
from tempfile import gettempdir

# ``base`` initializes PostgreSQL settings during import.  Supply placeholders
# so this test-only configuration can replace that connection below.
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from .base import *  # noqa: F403
from .base import TEMPLATES
from .base import env

TEST_DATABASE_NAME = Path(gettempdir()) / "sabil-book-test.sqlite3"

# Tests must not depend on the Docker Compose PostgreSQL service or its
# environment variables. A separate SQLite database also prevents a local test
# run from touching a developer's application database. It is file-backed so
# database work performed by Channels' worker threads sees the same test data.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": TEST_DATABASE_NAME,
        "ATOMIC_REQUESTS": True,
        "TEST": {"NAME": TEST_DATABASE_NAME},
    },
}

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="L79PltBQfboqIpDqzpNT9tnYpuey7kU9HQTsrwgYiiYMSZpywfufFn6TTnr6U0A1",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#test-runner
TEST_RUNNER = "django.test.runner.DiscoverRunner"

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# DEBUGGING FOR TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES[0]["OPTIONS"]["debug"] = True  # type: ignore[index]

# MEDIA
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "http://media.testserver/"

# Channels
# ------------------------------------------------------------------------------
# In-process channel layer: WebSocket tests don't need a real Redis, and this
# keeps them fast and isolated from whatever else is running against REDIS_URL.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}
# KYC/AML provider webhook
# ------------------------------------------------------------------------------
KYC_WEBHOOK_SECRET = "test-kyc-webhook-secret"  # noqa: S105
# Your stuff...
# ------------------------------------------------------------------------------

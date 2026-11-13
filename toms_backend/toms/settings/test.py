"""Test settings: isolated in-memory SQLite, no access to any real database.

The legacy tables are created inside the test database by the ``legacy_tables``
fixture (see ``apps/core/testing.py``), because those models are
``managed = False`` and therefore never emitted by ``migrate``.
"""

import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-only-key-with-sufficient-length-for-hs256-signing"  # noqa: S105

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        # The legacy tables are created *after* migrate() by the fixture, so
        # Django's initial-state snapshot would try to read them too early.
        # Nothing here relies on serialized_rollback, so serialisation is off.
        "TEST": {"SERIALIZE": False},
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Upload tests must never write into the real asset directories.
_TEST_ASSETS = Path(tempfile.mkdtemp(prefix="toms-test-assets-"))
LEGACY_UPLOAD_DIR = _TEST_ASSETS / "uploads"
LEGACY_SIGNATURE_DIR = _TEST_ASSETS / "signatures"

# Throttling would make assertions on repeated login attempts flaky.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {"login": "1000/min"},
}

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "null": {"class": "logging.NullHandler"},
        # Server errors print their traceback during the test run; lower-level
        # noise stays suppressed.
        "console": {"class": "logging.StreamHandler", "level": "ERROR"},
    },
    "root": {"handlers": ["console", "null"], "level": "ERROR"},
}

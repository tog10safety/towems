"""Base settings shared by every TOWEMS environment.

Security posture, relative to the legacy Flask ``config.py``:

* No credential or secret is ever hard-coded. ``DATABASE_URL`` and
  ``DJANGO_SECRET_KEY`` must come from the environment (``.env`` is loaded in
  development only, and is git-ignored).
* No insecure fallback secret is used when ``DEBUG`` is off: the settings
  module raises ``ImproperlyConfigured`` instead of booting with a known key.
* ``USE_TZ`` is **False** on purpose - see docs/DATABASE_COMPATIBILITY.md §5.
  The legacy tables store a mix of UTC-naive and Asia/Manila-naive timestamps;
  enabling timezone support would silently shift historical values.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # .../toms_backend
PROJECT_ROOT = BASE_DIR.parent                            # .../mrm-system

# --------------------------------------------------------------------------
# Environment helpers
# --------------------------------------------------------------------------


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return default if value in (None, "") else value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_database_url(url: str) -> dict:
    """Translate a postgres URL into a Django DATABASES entry.

    The password is placed in the settings dict only; it is never logged,
    printed, or returned by any endpoint.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured(
            "DATABASE_URL must be a PostgreSQL URL "
            "(postgres://user:password@host:port/dbname)."
        )
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parts.path.lstrip("/"),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "localhost",
        "PORT": str(parts.port or 5432),
        "CONN_MAX_AGE": int(env("DJANGO_CONN_MAX_AGE", "60")),
        "OPTIONS": {
            "connect_timeout": int(env("DJANGO_DB_CONNECT_TIMEOUT", "10")),
        },
    }


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env_bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
    "apps.legacy",
    "apps.ops",
    "apps.command",
    "apps.messaging",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Report-only by default until the rollout has collected clean reports.
    "apps.core.middleware.ContentSecurityPolicyMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Stamps each signed-in account's last activity (dashboard presence rail).
    # It needs the session user, so it follows AuthenticationMiddleware.
    "apps.accounts.presence.PresenceMiddleware",
    # An expired MFA grace window leaves a setup-only session. Only sessions
    # flagged by LoginView are restricted; token clients are untouched.
    "apps.accounts.mfa_middleware.MFAEnforcementMiddleware",
    "apps.core.middleware.RequestAuditMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "toms.urls"
WSGI_APPLICATION = "toms.wsgi.application"
ASGI_APPLICATION = "toms.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

# --------------------------------------------------------------------------
# Custom user model: the EXISTING `users` table, unmanaged by Django
# --------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = ["apps.accounts.backends.WerkzeugPasswordBackend"]

# Verifies the legacy Werkzeug scrypt hashes. When enabled it would re-hash
# passwords into Django's PBKDF2 on successful login - that WRITES to the
# legacy `users` table, so it is off until explicitly approved.
TOMS_REHASH_ON_LOGIN = env_bool("TOMS_REHASH_ON_LOGIN", False)

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

DATABASE_URL = env("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {"default": parse_database_url(DATABASE_URL)}
else:
    # Each environment module must supply DATABASES (test uses SQLite; dev,
    # staging and production refuse to start without DATABASE_URL).
    DATABASES = {}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# --------------------------------------------------------------------------
# Internationalisation / time
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
# Deliberately False: preserves naive legacy timestamps exactly. Flipping this
# would make Django read every legacy row as UTC and display it +8 hours, which
# the data-preservation rules forbid. See docs/DATABASE_COMPATIBILITY.md §5 and
# apps/core/timeutils.py, which owns the naive==Philippine-time convention.
USE_TZ = False

# Every timestamp must be Philippine time even when the host's own zone is UTC
# (the DigitalOcean image defaults to UTC). Normalising the process zone here
# keeps Django's ``auto_now_add`` columns, ``timezone.now()`` and MODEL code
# writing the same wall clock as ``apps.core.timeutils.now()``. ``tzset`` is
# POSIX-only; on Windows the zone is applied through the environment variable
# that the OS library reads.
if os.name == "nt":
    # The Windows C runtime does not understand IANA zone names: setting
    # "Asia/Manila" there falls back to UTC. "PST-8" is the POSIX form for
    # UTC+08:00, so a host left on UTC still writes Philippine wall-clock time.
    os.environ["TZ"] = "PST-8"
else:
    os.environ["TZ"] = TIME_ZONE
if hasattr(time, "tzset"):
    time.tzset()

# --------------------------------------------------------------------------
# Sessions / CSRF / CORS
# --------------------------------------------------------------------------

SESSION_COOKIE_NAME = "toms_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = int(env("TOMS_SESSION_AGE", "3600"))
#: How long a phone's read-only alert token outlives its session. It is not a
#: session: it only reads the notification pulse and the message count, and is
#: revoked on sign-out, password change and expiry.
DEVICE_TOKEN_AGE_DAYS = int(env("TOMS_DEVICE_TOKEN_AGE_DAYS", "30"))

CSRF_COOKIE_HTTPONLY = False  # the SPA reads it to set the X-CSRFToken header
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

CORS_ALLOWED_ORIGINS = env_list(
    "DJANGO_CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)
CORS_ALLOW_CREDENTIALS = True

# --------------------------------------------------------------------------
# Content Security Policy (report-only rollout)
# --------------------------------------------------------------------------
# The browser posts violations to this path; the endpoint logs them under the
# "towems.csp" logger and always returns 204. Keep report-only enabled until
# the rollout reports are clean, then set TOMS_CSP_REPORT_ONLY=false.
CSP_REPORT_ONLY = env_bool("TOMS_CSP_REPORT_ONLY", True)
CSP_REPORT_URI = env("TOMS_CSP_REPORT_URI", "/api/csp-report/")
CSP_EXTRA_IMG_SRC = env_list("TOMS_CSP_EXTRA_IMG_SRC")
CSP_EXTRA_CONNECT_SRC = env_list("TOMS_CSP_EXTRA_CONNECT_SRC")

# --------------------------------------------------------------------------
# Django REST Framework
# --------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": int(env("TOMS_PAGE_SIZE", "25")),
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.errors.toms_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "login": env("TOMS_LOGIN_THROTTLE", "10/min"),
    },
}

# --------------------------------------------------------------------------
# Map place search (mission planning / AOR pickers)
# --------------------------------------------------------------------------
# External lookup for businesses, buildings and facilities ("SM City Butuan").
# With a Google Places key the server proxies Google; without one it uses
# OpenStreetMap Nominatim, which needs no account. Set TOMS_GEOCODER=off to
# disable external calls entirely (the picker keeps its offline PSGC search).
GEOCODER_PROVIDER = env("TOMS_GEOCODER", "auto")
GOOGLE_MAPS_API_KEY = env("TOMS_GOOGLE_MAPS_API_KEY", "")
GEOCODER_TIMEOUT = float(env("TOMS_GEOCODER_TIMEOUT", "8"))
GEOCODER_CACHE_SECONDS = int(env("TOMS_GEOCODER_CACHE_SECONDS", "600"))
GEOCODER_USER_AGENT = env(
    "TOMS_GEOCODER_USER_AGENT",
    "TOWEMS-TacticalOps/1.0 (mission planning map picker)",
)

# Time added after the planned enroute time before the Air Operations map marks
# a simulated aircraft as arrived. This is a display/planning value only; it
# never writes an actual ATD/ATA or changes a mission's execution status.
AIR_SIMULATION_ARRIVAL_BUFFER_MINUTES = int(
    env("AIR_SIMULATION_ARRIVAL_BUFFER_MINUTES", "10")
)

# An actual ATD/ATA that differs from the plan by more than this many minutes is
# surfaced as a non-blocking warning on the Daily Operations board. This catches
# date and time-entry mistakes without preventing a legitimate early or late
# departure. Set to 0 to turn the warning off.
AIR_ACTUAL_TIME_WARNING_MINUTES = int(
    env("AIR_ACTUAL_TIME_WARNING_MINUTES", "120")
)

SPECTACULAR_SETTINGS = {
    "TITLE": "TOWEMS API",
    "DESCRIPTION": (
        "Tactical Operations Wings Enterprise Management System API. Replaces the legacy MRMS "
        "Flask application while preserving its PostgreSQL schema and records."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": __import__("datetime").timedelta(
        minutes=int(env("TOMS_JWT_ACCESS_MINUTES", "60"))
    ),
    "REFRESH_TOKEN_LIFETIME": __import__("datetime").timedelta(
        days=int(env("TOMS_JWT_REFRESH_DAYS", "7"))
    ),
}

# --------------------------------------------------------------------------
# Legacy asset locations (read-only for Django; files stay where they are)
# --------------------------------------------------------------------------

LEGACY_UPLOAD_DIR = PROJECT_ROOT / "uploads"
LEGACY_SIGNATURE_DIR = PROJECT_ROOT / "signatures"

#: Deployment policy: every active account enrols a TOTP authenticator
#: (Google Authenticator) on its first sign-in after the TOWEMS cutover, not
#: only Administrators and Commanders.
MFA_REQUIRED_FOR_ALL_ACCOUNTS = True

#: The first-sign-in profile refresh asks for the actual Gmail account the
#: person will use, so the address must end with this suffix. Set to "" only if
#: a deployment must also accept Google Workspace addresses on unit domains.
PROFILE_EMAIL_SUFFIX = "@gmail.com"
LEGACY_PDF_DIR = PROJECT_ROOT / "approved_pdfs"
LEGACY_LOGO_DIR = PROJECT_ROOT / "static" / "unit_logos"
# Static images reused by the MRM PDF (page footer, default logo, risk matrix).
LEGACY_STATIC_DIR = PROJECT_ROOT / "static"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": env("TOMS_LOG_LEVEL", "INFO")},
}

# Values shared with the legacy enum definitions (kept in one place so the
# serializers, validators and permission classes cannot drift).
AUTHORITY_ADMIN = "Administrator"
AUTHORITY_EVALUATOR = "Evaluator"
AUTHORITY_CREATOR = "Creator"
AUTHORITY_UNIT_SAFETY = "Unit Safety Officer"

AUTHORITY_CHOICES = [
    AUTHORITY_ADMIN,
    AUTHORITY_EVALUATOR,
    AUTHORITY_CREATOR,
    AUTHORITY_UNIT_SAFETY,
]

RISK_SEVERITIES = ["A", "B", "C", "D", "E"]
RISK_LIKELIHOODS = [1, 2, 3, 4, 5]

# --------------------------------------------------------------------------
# Private messaging
# --------------------------------------------------------------------------
# Retention is a deployment decision: ``purge_messages`` deletes messages past
# this window (from the scheduler), and the API reports the value to the UI.
MESSAGING_RETENTION_DAYS = int(env("TOMS_MESSAGING_RETENTION_DAYS", "365"))
MESSAGING_MAX_MESSAGE_LENGTH = int(env("TOMS_MESSAGING_MAX_LENGTH", "4000"))
MESSAGING_PAGE_SIZE = int(env("TOMS_MESSAGING_PAGE_SIZE", "40"))
#: How long a "typing…" mark stays meaningful; abandoned composers age out.
MESSAGING_TYPING_SECONDS = int(env("TOMS_MESSAGING_TYPING_SECONDS", "6"))
#: Mirrors the presence window used by the top-bar "online now" rail.
MESSAGING_ONLINE_MINUTES = int(env("TOMS_MESSAGING_ONLINE_MINUTES", "5"))

# --- Closed-tab browser push -------------------------------------------------
# Generate one key pair per deployment with `manage.py vapid_keys` and keep it
# in the environment. VAPID_PRIVATE_KEY is base64-wrapped PEM so it fits on a
# single env line; VAPID_PUBLIC_KEY is the base64url point the browser needs.
# With no keys the API reports push as disabled and messaging works as before.
VAPID_PUBLIC_KEY = env("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = env("VAPID_SUBJECT", "mailto:no-reply@mrm-system.online")
#: How long a push service may retain an undelivered alert (seconds).
MESSAGING_PUSH_TTL = int(env("TOMS_MESSAGING_PUSH_TTL", "86400"))
#: Browser push endpoints the server will POST to. Browsers hand these out
#: themselves, so the list keeps a forged subscription from turning the sender
#: into a request to an internal host (blind SSRF). Extend only for a new
#: browser vendor's push service.
MESSAGING_PUSH_ALLOWED_HOSTS = tuple(
    host.strip().lower()
    for host in (
        env(
            "TOMS_MESSAGING_PUSH_HOSTS",
            "fcm.googleapis.com,android.googleapis.com,push.services.mozilla.com,"
            "notify.windows.com,push.apple.com",
        )
        or ""
    ).split(",")
    if host.strip()
)

# --- Phone push (Firebase Cloud Messaging) ----------------------------------
# FCM is free for unlimited messages and is the one thing the watcher cannot do
# for itself: Google keeps a connection to the phone and wakes the app, so
# delivery does not depend on a foreground service that Android or an OEM
# battery saver may end. Create a Firebase project, add the Android app
# ``online.mrmsystem.towems``, keep the downloaded google-services.json in
# toms_android/android/app/, and point FCM_CREDENTIALS_FILE at the
# service-account JSON kept outside the repository. With either unset the
# server does not push; the watcher still polls, exactly as before.
FCM_PROJECT_ID = env("FCM_PROJECT_ID", "")
FCM_CREDENTIALS_FILE = env("FCM_CREDENTIALS_FILE", "")

# Environment modules (dev/staging/prod) validate their own requirements.

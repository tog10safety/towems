"""Development settings.

Local-only conveniences. Never import this module in a deployed environment.
"""

import os

from dotenv import load_dotenv

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, SECRET_KEY, env_bool, env_list, parse_database_url

# Load .env from the backend directory if present (development only).
load_dotenv(BASE_DIR / ".env")

DEBUG = env_bool("DJANGO_DEBUG", True)

if not os.environ.get("DATABASE_URL"):
    # Fall back to a local development database name without any credential in
    # source: PGUSER/PGPASSWORD (if needed) are read from the environment.
    DATABASES = {  # noqa: F405
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("TOMS_DEV_DB_NAME", "toms_staging"),
            "USER": os.environ.get("PGUSER", "postgres"),
            "PASSWORD": os.environ.get("PGPASSWORD", ""),
            "HOST": os.environ.get("PGHOST", "localhost"),
            "PORT": os.environ.get("PGPORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }

if not SECRET_KEY:
    # Development-only ephemeral key. Chosen so a missing .env cannot be
    # mistaken for a production-safe configuration.
    SECRET_KEY = "dev-only-insecure-key-not-for-deployment"  # noqa: S105

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

# Vite dev server
CORS_ALLOWED_ORIGINS = env_list(
    "DJANGO_CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {"login": "100/min"},
}

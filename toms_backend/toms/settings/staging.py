"""Staging settings - production-grade security against a staging database."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import DATABASES, SECRET_KEY, env, env_bool, env_list

DEBUG = False

if not DATABASES:
    raise ImproperlyConfigured(
        "DATABASE_URL must point at the staging database copy. "
        "Never point staging at the production database."
    )
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set for staging.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list the staging hostnames.")

# HTTPS is terminated by the reverse proxy in front of gunicorn.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "3600"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
CORS_ALLOWED_ORIGINS = env_list("DJANGO_CORS_ALLOWED_ORIGINS")

# Re-hashing writes to the legacy `users` table: requires explicit approval.
TOMS_REHASH_ON_LOGIN = env_bool("TOMS_REHASH_ON_LOGIN", False)

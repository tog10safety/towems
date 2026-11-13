"""Run Django management commands against the STAGING database copy.

The staging database URL is derived from the legacy config (or DATABASE_URL)
by replacing the database name with `toms_staging`. Credentials are placed in
the process environment only - never printed, never written to disk.

    python tools/staging_manage.py migrate --plan
    python tools/staging_manage.py migrate
    python tools/staging_manage.py verify_legacy_mapping

This deliberately refuses to run when the resolved database is the production /
source database named in DATABASE_URL, and refuses when the target database
does not exist (run tools/staging_db.py --create first).
"""

from __future__ import annotations

import os
import sys
from urllib.parse import quote, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "toms_backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

STAGING_NAME = "toms_staging"


def build_staging_url() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        from config import Config

        raw = Config.SQLALCHEMY_DATABASE_URI
    raw = raw.replace("postgres://", "postgresql://", 1)
    parts = urlsplit(raw)

    source_db = parts.path.lstrip("/")
    if source_db == STAGING_NAME:
        return raw

    user = quote(parts.username or "", safe="")
    password = quote(parts.password or "", safe="")
    credentials = f"{user}:{password}@" if password else f"{user}@"
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    return f"postgresql://{credentials}{host}{port}/{STAGING_NAME}"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    os.environ["DATABASE_URL"] = build_staging_url()
    os.environ["DJANGO_SETTINGS_MODULE"] = "toms.settings.staging"
    os.environ.setdefault("DJANGO_SECRET_KEY", "staging-local-only-not-a-production-key")
    os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
    os.environ.setdefault("DJANGO_SECURE_SSL_REDIRECT", "false")
    os.environ.setdefault("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:5173")
    os.environ.setdefault("DJANGO_CORS_ALLOWED_ORIGINS", "http://localhost:5173")

    import django

    django.setup()
    from django.db import connection

    if connection.settings_dict["NAME"] != STAGING_NAME:
        print(f"Refusing: resolved database is {connection.settings_dict['NAME']!r}, not staging.")
        return 2

    from django.core.management import execute_from_command_line

    execute_from_command_line(["manage.py", *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

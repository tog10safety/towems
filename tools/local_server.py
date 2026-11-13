"""Run the Django backend locally against the STAGING database copy.

Uses development settings (so cookies work over plain http on localhost) but
points at `toms_staging`, never at the source/production database. Credentials
are placed in the environment only and never printed.

    python tools/local_server.py 0.0.0.0:8000
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "toms_backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

STAGING_NAME = "toms_staging"


def staging_url() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        from config import Config

        raw = Config.SQLALCHEMY_DATABASE_URI
    raw = raw.replace("postgres://", "postgresql://", 1)
    parts = urlsplit(raw)
    if parts.path.lstrip("/") == STAGING_NAME:
        return raw
    user = parts.username or ""
    password = parts.password or ""
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    return f"postgresql://{user}:{password}@{host}{port}/{STAGING_NAME}"


def main() -> int:
    os.environ["DATABASE_URL"] = staging_url()
    os.environ["DJANGO_SETTINGS_MODULE"] = "toms.settings.dev"
    os.environ.setdefault("DJANGO_SECRET_KEY", "dev-local-only-not-a-production-key-0001")
    os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

    import django

    django.setup()
    from django.db import connection

    if connection.settings_dict["NAME"] != STAGING_NAME:
        print("Refusing to serve: database is not the staging copy.")
        return 2

    from django.core.management import execute_from_command_line

    addrport = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:8000"
    execute_from_command_line(["manage.py", "runserver", addrport, "--noreload"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

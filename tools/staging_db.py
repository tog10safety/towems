"""Create (never overwrite) a staging clone of the existing database.

Reads credentials from the application's own config without printing them.
The clone is created from `mrm_system` so that staging exercises the real
schema and real data volume.

    python tools/staging_db.py --status
    python tools/staging_db.py --create

Safety rules:
  * refuses to touch the database named in DATABASE_URL;
  * refuses to overwrite an existing `toms_staging`;
  * copies schema AND data (pg_dump | pg_restore) so foreign keys resolve.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

STAGING_NAME = "toms_staging"


def source_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        from config import Config  # legacy Flask config - read, never printed

        url = Config.SQLALCHEMY_DATABASE_URI
    return url.replace("postgres://", "postgresql://", 1)


def split(url: str):
    parts = urlsplit(url)
    return {
        "user": parts.username or "",
        "password": parts.password or "",
        "host": parts.hostname or "localhost",
        "port": str(parts.port or 5432),
        "db": parts.path.lstrip("/"),
    }


def psycopg2_connect(parts, dbname):
    import psycopg2

    return psycopg2.connect(
        dbname=dbname,
        user=parts["user"],
        password=parts["password"],
        host=parts["host"],
        port=parts["port"],
        connect_timeout=10,
    )


def describe(parts) -> str:
    return f"{parts['host']}:{parts['port']}/{parts['db']}"


def status(parts) -> int:
    conn = psycopg2_connect(parts, "postgres")
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database ORDER BY datname")
            names = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    print(f"Source database: {describe(parts)}")
    print(f"Databases: {', '.join(names)}")
    print(f"Staging clone present: {'yes' if STAGING_NAME in names else 'no'}")
    return 0


def create(parts) -> int:
    if parts["db"] == STAGING_NAME:
        print("Refusing: the configured DATABASE_URL already points at staging.")
        return 2

    conn = psycopg2_connect(parts, "postgres")
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (STAGING_NAME,))
            if cur.fetchone():
                print(
                    f"Refusing to overwrite existing database {STAGING_NAME!r}. "
                    "Drop it manually if you intend to rebuild the staging copy."
                )
                return 3

            # Preferred path: template copy (atomic, exact).
            try:
                cur.execute(f'CREATE DATABASE "{STAGING_NAME}" TEMPLATE "{parts["db"]}"')
                print(f"Created {STAGING_NAME} as an exact clone of {parts['db']}.")
                return 0
            except Exception as exc:
                print(f"Template clone unavailable ({type(exc).__name__}); falling back to dump/restore.")

            cur.execute(f'CREATE DATABASE "{STAGING_NAME}"')
    finally:
        conn.close()

    if not shutil.which("pg_dump") or not shutil.which("psql"):
        print("pg_dump/psql not on PATH; cannot complete the clone.")
        return 4

    env = dict(os.environ, PGPASSWORD=parts["password"])
    dump_cmd = [
        "pg_dump",
        "--no-owner",
        "--no-privileges",
        "-h", parts["host"],
        "-p", parts["port"],
        "-U", parts["user"],
        "-d", parts["db"],
    ]
    restore_cmd = [
        "psql",
        "--quiet",
        "-h", parts["host"],
        "-p", parts["port"],
        "-U", parts["user"],
        "-d", STAGING_NAME,
    ]

    dump = subprocess.run(dump_cmd, env=env, capture_output=True)
    if dump.returncode != 0:
        print("pg_dump failed:", dump.stderr.decode(errors="replace")[:500])
        return 5

    restore = subprocess.run(restore_cmd, input=dump.stdout, env=env, capture_output=True)
    if restore.returncode != 0:
        print("psql restore failed:", restore.stderr.decode(errors="replace")[:500])
        return 6

    print(f"Created {STAGING_NAME} from a logical dump of {parts['db']}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true")
    group.add_argument("--create", action="store_true")
    args = parser.parse_args()

    parts = split(source_url())
    return status(parts) if args.status else create(parts)


if __name__ == "__main__":
    raise SystemExit(main())

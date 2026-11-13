"""Read-only introspection of the live PostgreSQL schema.

Connects using the application's own configuration (config.Config) or the
DATABASE_URL environment variable. Never prints the connection string or any
credential. Issues only SELECT / catalog reads; it never writes to the database.

Usage:
    python tools/introspect_schema.py                # writes docs/DATABASE_SCHEMA_INVENTORY.md
    python tools/introspect_schema.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    sys.exit("psycopg2 is required: pip install psycopg2-binary")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def get_dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        from config import Config  # imported lazily so secrets are never echoed

        url = Config.SQLALCHEMY_DATABASE_URI
    return url.replace("postgres://", "postgresql://", 1)


def describe_target(dsn: str) -> str:
    """Return host/dbname only - safe to display."""
    from urllib.parse import urlsplit

    parts = urlsplit(dsn)
    return f"{parts.hostname or '?'}:{parts.port or 5432}/{parts.path.lstrip('/') or '?'}"


def fetch(conn) -> dict:
    out: dict = {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT table_name, table_schema
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
            """
        )
        tables = cur.fetchall()

        out["tables"] = []
        for t in tables:
            name, schema = t["table_name"], t["table_schema"]

            cur.execute(
                """
                SELECT c.column_name, c.data_type, c.udt_name, c.is_nullable,
                       c.column_default, c.character_maximum_length,
                       c.numeric_precision, c.numeric_scale
                FROM information_schema.columns c
                WHERE c.table_schema = %s AND c.table_name = %s
                ORDER BY c.ordinal_position
                """,
                (schema, name),
            )
            columns = cur.fetchall()

            cur.execute(
                """
                SELECT a.attname AS column_name
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = %s::regclass AND i.indisprimary
                """,
                (f'"{schema}"."{name}"',),
            )
            pk = [r["column_name"] for r in cur.fetchall()]

            cur.execute(
                """
                SELECT kcu.column_name,
                       ccu.table_schema AS foreign_schema,
                       ccu.table_name   AS foreign_table,
                       ccu.column_name  AS foreign_column,
                       rc.delete_rule
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                     ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                LEFT JOIN information_schema.referential_constraints rc
                     ON rc.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = %s AND tc.table_name = %s
                ORDER BY kcu.column_name
                """,
                (schema, name),
            )
            fks = cur.fetchall()

            cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s", (schema, name))
            indexes = cur.fetchall()

            cur.execute(f'SELECT count(*) AS n FROM "{schema}"."{name}"')
            row_count = cur.fetchone()["n"]

            out["tables"].append(
                {
                    "schema": schema,
                    "name": name,
                    "row_count": row_count,
                    "pk": pk,
                    "columns": [dict(c) for c in columns],
                    "foreign_keys": [dict(f) for f in fks],
                    "indexes": [dict(i) for i in indexes],
                }
            )

        cur.execute(
            """
            SELECT t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) AS labels
            FROM pg_type t
            JOIN pg_enum e ON e.enumtypid = t.oid
            GROUP BY t.typname ORDER BY t.typname
            """
        )
        out["enums"] = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT extname FROM pg_extension ORDER BY extname")
        out["extensions"] = [r["extname"] for r in cur.fetchall()]

        cur.execute("SHOW server_version")
        out["server_version"] = cur.fetchone()["server_version"]

        cur.execute("SELECT current_database() AS db, current_user AS usr")
        ident = cur.fetchone()
        # database/role names are not secret; the password is never selected
        out["database"] = ident["db"]
        out["role"] = ident["usr"]

    return out


def render_markdown(data: dict, target: str) -> str:
    lines: list[str] = []
    lines.append("# Database Schema Inventory (generated)\n")
    lines.append(
        "This file is generated by `tools/introspect_schema.py` using **read-only** catalog "
        "queries. It reflects the live schema of the database the application is configured "
        "to use.\n"
    )
    lines.append(f"- Target (host/db): `{target}`")
    lines.append(f"- Server version: `{data['server_version']}`")
    lines.append(f"- Extensions installed: {', '.join(f'`{e}`' for e in data['extensions']) or 'none'}")
    postgis = "postgis" in data["extensions"]
    lines.append(f"- PostGIS available: **{'yes' if postgis else 'no'}**")
    lines.append(f"- Tables: **{len(data['tables'])}**\n")

    lines.append("## Extracted PostgreSQL ENUM types\n")
    if data["enums"]:
        lines.append("| Enum type | Labels |")
        lines.append("| --- | --- |")
        for e in data["enums"]:
            lines.append(f"| `{e['typname']}` | `{e['labels']}` |")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Tables\n")
    lines.append("| Table | Rows | PK | Columns | FKs | Indexes |")
    lines.append("| --- | ---: | --- | ---: | ---: | ---: |")
    for t in data["tables"]:
        lines.append(
            f"| `{t['name']}` | {t['row_count']} | `{', '.join(t['pk']) or '-'}` | "
            f"{len(t['columns'])} | {len(t['foreign_keys'])} | {len(t['indexes'])} |"
        )
    lines.append("")

    for t in data["tables"]:
        lines.append(f"### `{t['name']}` ({t['row_count']} rows)\n")
        lines.append("| # | Column | Type | Null | Default |")
        lines.append("| ---: | --- | --- | --- | --- |")
        for i, c in enumerate(t["columns"], 1):
            typ = c["data_type"]
            if c["udt_name"] and c["udt_name"] not in ("varchar", "int4", "int8", "text", "bool", "timestamp", "float8", "numeric"):
                typ = c["udt_name"]
            size = c["character_maximum_length"]
            if size:
                typ = f"{typ}({size})"
            default = (c["column_default"] or "").replace("|", "\\|")
            if len(default) > 60:
                default = default[:57] + "..."
            lines.append(
                f"| {i} | `{c['column_name']}` | `{typ}` | "
                f"{'YES' if c['is_nullable'] == 'YES' else 'no'} | `{default}` |"
            )
        if t["foreign_keys"]:
            lines.append("\n**Foreign keys**\n")
            lines.append("| Column | References | On delete |")
            lines.append("| --- | --- | --- |")
            for fk in t["foreign_keys"]:
                lines.append(
                    f"| `{fk['column_name']}` | `{fk['foreign_table']}.{fk['foreign_column']}` | `{fk['delete_rule']}` |"
                )
        if t["indexes"]:
            lines.append("\n**Indexes**\n")
            for idx in t["indexes"]:
                lines.append(f"- `{idx['indexname']}`: `{idx['indexdef']}`")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="also write raw JSON to this path")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "DATABASE_SCHEMA_INVENTORY.md"))
    args = ap.parse_args()

    dsn = get_dsn()
    target = describe_target(dsn)
    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        conn.set_session(readonly=True, autocommit=True)
        data = fetch(conn)
    finally:
        conn.close()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(data, target))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)

    print(f"Target: {target}")
    print(f"Tables: {len(data['tables'])}  Enums: {len(data['enums'])}  PostGIS: {'postgis' in data['extensions']}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

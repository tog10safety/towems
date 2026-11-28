"""Database engine, sessions, settings, audit and sync queue helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import (
    AuditLog,
    Base,
    DatabaseMeta,
    Setting,
    SyncQueue,
    now,
)

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def init_db(path: Path | str | None = None) -> Engine:
    global _engine, _SessionLocal
    target = Path(path) if path else config.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        f"sqlite:///{target.as_posix()}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(_engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - driver callback
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    Base.metadata.create_all(_engine)
    _ensure_lightweight_columns(_engine)
    _SessionLocal = sessionmaker(
        bind=_engine, expire_on_commit=False, autoflush=False, future=True
    )
    with session_scope() as session:
        _ensure(session, "schema_version", "1")
        _ensure(session, "session_timeout_minutes", str(config.DEFAULT_SESSION_MINUTES))
        _ensure(session, "auto_sync", "1")
        _ensure(session, "sync_weekday", str(config.DEFAULT_SYNC_WEEKDAY))
        _ensure(session, "sync_hour", str(config.DEFAULT_SYNC_HOUR))
        _ensure(session, "sync_minute", str(config.DEFAULT_SYNC_MINUTE))
        _ensure(session, "api_base", config.DEFAULT_API_BASE)
        _ensure(session, "org_name", "TOWEMS Armory")
    return _engine


def _ensure_lightweight_columns(target: Engine) -> None:
    """Add columns introduced after the first release without losing data.

    The desktop database is a local cache plus its own accountability ledger,
    so a full Alembic stack would be disproportionate. These additive changes
    are safe for SQLite and are applied only when the column is absent.
    """
    additions = {
        "firearm": [
            ("sync_state", "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
            ("synced_at", "DATETIME"),
        ],
        "ammunition": [
            ("sync_state", "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
            ("synced_at", "DATETIME"),
        ],
    }
    with target.begin() as connection:
        for table, columns in additions.items():
            existing = {
                row[1]
                for row in connection.exec_driver_sql(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            for column, ddl in columns:
                if column not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                    )


def engine() -> Engine:
    if _engine is None:
        init_db()
    return _engine  # type: ignore[return-value]


def SessionLocal() -> Session:
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()  # type: ignore[misc]


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure(session: Session, key: str, default: str) -> None:
    if session.get(Setting, key) is None:
        session.add(Setting(key=key, value=default))


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row is not None else default


def set_setting(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)


def setting_bool(session: Session, key: str, default: bool = False) -> bool:
    return get_setting(session, key, "1" if default else "0").strip() in {
        "1",
        "true",
        "yes",
        "on",
    }


def audit(
    session: Session,
    *,
    username: str,
    action: str,
    entity_type: str,
    entity_id: str | int = "",
    reference: str = "",
    before: dict | None = None,
    after: dict | None = None,
    note: str = "",
) -> AuditLog:
    row = AuditLog(
        username=username or "system",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id or ""),
        reference=reference or "",
        before_json=json.dumps(before or {}, default=str, ensure_ascii=True),
        after_json=json.dumps(after or {}, default=str, ensure_ascii=True),
        note=note or "",
    )
    session.add(row)
    return row


def enqueue(
    session: Session,
    *,
    entity_type: str,
    entity_uuid: str,
    action: str,
    payload: dict,
) -> SyncQueue:
    """Queue one operation, replacing an earlier pending copy of the same one."""
    existing = session.scalars(
        select(SyncQueue).where(
            SyncQueue.entity_type == entity_type,
            SyncQueue.entity_uuid == entity_uuid,
            SyncQueue.action == action,
            SyncQueue.state == "pending",
        )
    ).first()
    if existing is not None:
        existing.payload_json = json.dumps(payload, default=str, ensure_ascii=True)
        existing.updated_at = now()
        session.flush()
        return existing
    row = SyncQueue(
        entity_type=entity_type,
        entity_uuid=entity_uuid,
        action=action,
        payload_json=json.dumps(payload, default=str, ensure_ascii=True),
    )
    session.add(row)
    session.flush()
    return row


def pending_sync_count(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(SyncQueue).where(SyncQueue.state == "pending")
    ) or 0


def failed_sync_count(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(SyncQueue).where(SyncQueue.state == "failed")
    ) or 0


def next_sync_due(session: Session, from_time: datetime | None = None) -> datetime:
    """Return the next configured weekly sync point."""
    current = from_time or datetime.now()
    try:
        weekday = int(get_setting(session, "sync_weekday", str(config.DEFAULT_SYNC_WEEKDAY)))
        hour = int(get_setting(session, "sync_hour", str(config.DEFAULT_SYNC_HOUR)))
        minute = int(get_setting(session, "sync_minute", str(config.DEFAULT_SYNC_MINUTE)))
    except ValueError:
        weekday, hour, minute = (
            config.DEFAULT_SYNC_WEEKDAY,
            config.DEFAULT_SYNC_HOUR,
            config.DEFAULT_SYNC_MINUTE,
        )
    days = (weekday - current.weekday()) % 7
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if days == 0 and candidate <= current:
        days = 7
    return candidate + timedelta(days=days)


def backup_database(destination: Path | str) -> Path:
    """Consistent SQLite backup using the online backup API."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_path = Path(engine().url.database)
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    return destination


def restore_database(source: Path | str) -> None:
    """Replace the live database. The caller should restart the application."""
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(source)
    target = Path(engine().url.database)
    dispose_engine()
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    init_db(target)


def dispose_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def database_info() -> dict:
    path = Path(engine().url.database)
    return {
        "path": str(path),
        "size": path.stat().st_size if path.exists() else 0,
        "schema": 1,
    }

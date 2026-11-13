#!/usr/bin/env python3
"""Encrypted PostgreSQL backups with checksums, retention and restore drills.

This is an operational tool, not part of the Django request path. It uses the
PostgreSQL client tools already present on the host and never writes a
plaintext dump outside a short-lived working directory.

Typical use::

    export DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/mrm_system
    export TOMS_BACKUP_GPG_RECIPIENT=backup@example.com
    python tools/postgres_backup.py backup
    python tools/postgres_backup.py restore-test

The backup is a custom-format ``pg_dump`` encrypted with GPG. The manifest
records the plaintext and encrypted SHA-256 digests, the database identity,
the exported snapshot and row counts for key tables. The restore drill
decrypts the latest backup, restores it into a separate scratch database and
compares those counts before dropping the scratch database.

Secrets are read from ``DATABASE_URL`` and, for symmetric encryption, from the
file named by ``TOMS_BACKUP_GPG_PASSPHRASE_FILE``. Neither value is printed or
passed on a command line. A public-key recipient is preferred because it keeps
the private key off the backup host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_DIR = ROOT / "_backups" / "postgres"
DEFAULT_KEY_TABLES = (
    "users",
    "units",
    "mrm_forms",
    "hazard_registry",
    "mission_hazards",
    "django_migrations",
)
BACKUP_SUFFIX = ".dump.gpg"
SCRATCH_PREFIX = "toms_restore_"


class BackupError(RuntimeError):
    """A configuration or execution problem that should be shown to the operator."""


@dataclass(frozen=True)
class DBConfig:
    host: str
    port: int
    db: str
    user: str
    password: str
    sslmode: str | None = None

    @classmethod
    def from_url(cls, url: str) -> "DBConfig":
        parsed = urlsplit(url)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise BackupError("DATABASE_URL must use postgres:// or postgresql://")
        if not parsed.path or parsed.path == "/":
            raise BackupError("DATABASE_URL does not name a database")

        try:
            port = parsed.port or 5432
        except ValueError as exc:
            raise BackupError("DATABASE_URL has an invalid port") from exc

        query = parse_qs(parsed.query)
        sslmode = query.get("sslmode", [None])[0]
        return cls(
            host=parsed.hostname or "localhost",
            port=port,
            db=unquote(parsed.path.lstrip("/")),
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            sslmode=sslmode,
        )

    def with_db(self, db: str) -> "DBConfig":
        return DBConfig(
            host=self.host,
            port=self.port,
            db=db,
            user=self.user,
            password=self.password,
            sslmode=self.sslmode,
        )

    def describe(self) -> str:
        return f"{self.host}:{self.port}/{self.db}"

    def subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.password:
            env["PGPASSWORD"] = self.password
        if self.sslmode:
            env["PGSSLMODE"] = self.sslmode
        return env

    def connect(self, db: str | None = None, readonly: bool = False):
        try:
            import psycopg2
        except ImportError as exc:
            raise BackupError(
                "psycopg2 is not installed; run this with the application virtualenv"
            ) from exc

        kwargs = {
            "dbname": db or self.db,
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "connect_timeout": 10,
        }
        if self.sslmode:
            kwargs["sslmode"] = self.sslmode
        if readonly:
            kwargs["options"] = "-c default_transaction_read_only=on"
        return psycopg2.connect(**kwargs)


@dataclass(frozen=True)
class EncryptionConfig:
    mode: str
    recipient: str | None
    passphrase_file: Path | None


def database_config() -> DBConfig:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise BackupError(
            "DATABASE_URL is not set. Set it explicitly so a backup cannot "
            "accidentally target an unintended database."
        )
    return DBConfig.from_url(url.replace("postgres://", "postgresql://", 1))


def load_local_environment() -> None:
    """Load the backend's local .env without overriding exported variables."""
    env_path = ROOT / "toms_backend" / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)


def backup_directory(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("TOMS_BACKUP_DIR") or str(DEFAULT_BACKUP_DIR)
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise BackupError(f"{name} must be an integer") from exc
    if value < 0:
        raise BackupError(f"{name} cannot be negative")
    return value


def encryption_config() -> EncryptionConfig:
    recipient = os.environ.get("TOMS_BACKUP_GPG_RECIPIENT", "").strip() or None
    passphrase_raw = os.environ.get("TOMS_BACKUP_GPG_PASSPHRASE_FILE", "").strip()
    passphrase_file = Path(passphrase_raw).expanduser() if passphrase_raw else None

    if passphrase_file is not None:
        if not passphrase_file.is_file():
            raise BackupError(
                "TOMS_BACKUP_GPG_PASSPHRASE_FILE does not point to a readable file"
            )
        if passphrase_file.stat().st_size == 0:
            raise BackupError("TOMS_BACKUP_GPG_PASSPHRASE_FILE is empty")

    if not recipient and passphrase_file is None:
        raise BackupError(
            "No backup encryption configured. Set TOMS_BACKUP_GPG_RECIPIENT "
            "(preferred) or TOMS_BACKUP_GPG_PASSPHRASE_FILE."
        )

    mode = "gpg-public" if recipient else "gpg-symmetric"
    return EncryptionConfig(mode, recipient, passphrase_file)


def require_gpg() -> None:
    if shutil.which("gpg") is None:
        raise BackupError("gpg is required but was not found on PATH")


def run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(
            cmd,
            env=env or os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise BackupError(f"Could not run {cmd[0]}: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if len(detail) > 1500:
            detail = detail[:1500] + "..."
        raise BackupError(
            f"{Path(cmd[0]).name} failed with exit code {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    return completed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_checksum(path: Path) -> str:
    digest = sha256_file(path)
    checksum_path = path.with_name(path.name + ".sha256")
    checksum_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    _restrict_permissions(path)
    _restrict_permissions(checksum_path)
    return digest


def _restrict_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows ACLs are managed by the host; chmod is best-effort here.
        pass


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    _restrict_permissions(path)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"Cannot read backup manifest {path}: {exc}") from exc


def sidecar_paths(backup: Path) -> tuple[Path, Path]:
    return backup.with_suffix(".json"), backup.with_name(backup.name + ".sha256")


def parse_backup_time(path: Path) -> datetime | None:
    match = re.search(r"-(\d{8}T\d{6}Z)\.dump\.gpg$", path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def list_backups(directory: Path) -> list[Path]:
    backups = [path for path in directory.glob(f"*{BACKUP_SUFFIX}") if parse_backup_time(path)]
    return sorted(backups, key=lambda path: parse_backup_time(path), reverse=True)  # type: ignore[arg-type]


def safe_database_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip(".-")
    return cleaned or "database"


def row_counts(connection, tables: tuple[str, ...]) -> dict[str, int | None]:
    from psycopg2 import sql

    counts: dict[str, int | None] = {}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            if cursor.fetchone()[0] is None:
                counts[table] = None
                continue
            cursor.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            )
            counts[table] = int(cursor.fetchone()[0])
    return counts


def key_tables() -> tuple[str, ...]:
    raw = os.environ.get("TOMS_BACKUP_KEY_TABLES", "").strip()
    if not raw:
        return DEFAULT_KEY_TABLES
    tables = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not tables:
        raise BackupError("TOMS_BACKUP_KEY_TABLES contains no table names")
    return tables


def gpg_encrypt(plain: Path, encrypted: Path, config: EncryptionConfig) -> None:
    require_gpg()
    gpg_env = os.environ.copy()
    gpg_env.pop("PGPASSWORD", None)
    base = ["gpg", "--batch", "--yes", "--no-tty", "--output", str(encrypted)]

    if config.mode == "gpg-public":
        cmd = base + [
            "--encrypt",
            "--recipient",
            config.recipient or "",
            "--trust-model",
            "always",
            str(plain),
        ]
    else:
        cmd = base + [
            "--pinentry-mode",
            "loopback",
            "--passphrase-file",
            str(config.passphrase_file),
            "--symmetric",
            "--cipher-algo",
            "AES256",
            "--s2k-digest-algo",
            "SHA512",
            str(plain),
        ]

    run(cmd, env=gpg_env)
    if not encrypted.is_file() or encrypted.stat().st_size == 0:
        raise BackupError("GPG reported success but produced no encrypted output")
    _restrict_permissions(encrypted)


def gpg_decrypt(encrypted: Path, plain: Path, config: EncryptionConfig) -> None:
    require_gpg()
    gpg_env = os.environ.copy()
    gpg_env.pop("PGPASSWORD", None)
    cmd = ["gpg", "--batch", "--yes", "--no-tty", "--output", str(plain)]
    if config.passphrase_file is not None:
        cmd += [
            "--pinentry-mode",
            "loopback",
            "--passphrase-file",
            str(config.passphrase_file),
        ]
    cmd += ["--decrypt", str(encrypted)]
    run(cmd, env=gpg_env)
    if not plain.is_file() or plain.stat().st_size == 0:
        raise BackupError("GPG reported success but produced no plaintext output")
    _restrict_permissions(plain)


def pg_dump_version() -> str:
    completed = run(["pg_dump", "--version"])
    return completed.stdout.strip()


def create_backup(args: argparse.Namespace) -> int:
    config = database_config()
    encryption = encryption_config()
    directory = backup_directory(args.backup_dir)
    tables = key_tables()
    retention_daily = env_int("TOMS_BACKUP_RETENTION_DAILY", 30)
    retention_monthly = env_int("TOMS_BACKUP_RETENTION_MONTHLY", 12)

    now = datetime.now(timezone.utc)
    stem = f"toms-{safe_database_name(config.db)}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    encrypted = directory / f"{stem}{BACKUP_SUFFIX}"
    counter = 1
    while encrypted.exists():
        encrypted = directory / f"{stem}-{counter}{BACKUP_SUFFIX}"
        counter += 1
    manifest_path, checksum_path = sidecar_paths(encrypted)

    work = Path(tempfile.mkdtemp(prefix=".work-", dir=directory))
    _restrict_permissions(work)
    plain = work / encrypted.name[:-4]
    connection = None
    manifest: dict = {}
    local_ready = False

    print(f"Database: {config.describe()}")
    print(f"Backup:   {encrypted}")
    print(f"Encryption: {encryption.mode}")

    try:
        connection = config.connect()
        connection.autocommit = False
        snapshot = None
        use_snapshot = os.environ.get("TOMS_BACKUP_SNAPSHOT", "1").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

        print("[1/6] Exporting a repeatable-read snapshot...")
        with connection.cursor() as cursor:
            cursor.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            if use_snapshot:
                cursor.execute("SELECT pg_export_snapshot()")
                snapshot = cursor.fetchone()[0]

        print("[2/6] Dumping the database...")
        dump_cmd = [
            "pg_dump",
            "--format=custom",
            "--compress=9",
            "--no-owner",
            "--no-privileges",
            "--lock-wait-timeout=30s",
            "-h",
            config.host,
            "-p",
            str(config.port),
            "-U",
            config.user,
            "-d",
            config.db,
            "--file",
            str(plain),
        ]
        if snapshot:
            dump_cmd.insert(1, f"--snapshot={snapshot}")
        run(dump_cmd, env=config.subprocess_env())
        _restrict_permissions(plain)

        print("[3/6] Capturing key-table counts from the same snapshot...")
        counts = row_counts(connection, tables)
        connection.commit()
        connection.close()
        connection = None

        print("[4/6] Verifying the dump archive...")
        run(["pg_restore", "--list", str(plain)])
        plaintext_digest = sha256_file(plain)

        print("[5/6] Encrypting and writing checksums...")
        gpg_encrypt(plain, encrypted, encryption)
        encrypted_digest = write_checksum(encrypted)

        manifest = {
            "format": 1,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "database": {
                "name": config.db,
                "host": config.host,
                "port": config.port,
            },
            "backup": {
                "file": encrypted.name,
                "bytes": encrypted.stat().st_size,
                "sha256": encrypted_digest,
                "encryption": encryption.mode,
            },
            "dump": {
                "format": "custom",
                "pg_dump": pg_dump_version(),
                "plaintext_sha256": plaintext_digest,
                "snapshot": bool(snapshot),
            },
            "row_counts": counts,
            "retention": {
                "daily": retention_daily,
                "monthly": retention_monthly,
            },
            "offsite": [],
        }
        write_json_atomic(manifest_path, manifest)
        local_ready = True

        print("[6/6] Copying off-host if configured...")
        try:
            offsite = copy_offsite(encrypted, checksum_path, manifest_path, args)
        except BackupError as exc:
            manifest["offsite_error"] = str(exc)
            write_json_atomic(manifest_path, manifest)
            raise
        if offsite:
            manifest["offsite"] = offsite
            write_json_atomic(manifest_path, manifest)
        else:
            print("No off-host destination configured; local backup remains.")

        if not args.no_prune:
            prune_backups(
                directory,
                retention_daily,
                retention_monthly,
                dry_run=False,
            )

        print("Backup complete.")
        print(f"  encrypted: {encrypted.name}")
        print(f"  sha256:    {encrypted_digest}")
        print(f"  rows:      {format_counts(counts)}")
        return 0
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
        shutil.rmtree(work, ignore_errors=True)
        if not local_ready:
            for path in (encrypted, checksum_path, manifest_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def copy_offsite(
    encrypted: Path,
    checksum: Path,
    manifest: Path,
    args: argparse.Namespace,
) -> list[dict]:
    if args.no_upload:
        print("Off-host copy skipped by --no-upload.")
        return []

    results: list[dict] = []
    destination_raw = os.environ.get("TOMS_BACKUP_OFFSITE_DIR", "").strip()
    if destination_raw:
        destination = Path(destination_raw).expanduser().resolve()
        if destination == encrypted.parent:
            raise BackupError("TOMS_BACKUP_OFFSITE_DIR must not be the local backup directory")
        destination.mkdir(parents=True, exist_ok=True)
        for source in (encrypted, checksum, manifest):
            target = destination / source.name
            temporary = target.with_name(target.name + ".part")
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
            _restrict_permissions(target)
        if sha256_file(destination / encrypted.name) != sha256_file(encrypted):
            raise BackupError("Off-host checksum verification failed")
        print(f"Copied to off-host directory: {destination}")
        results.append({"type": "directory", "path": str(destination)})

    command_raw = os.environ.get("TOMS_BACKUP_UPLOAD_COMMAND", "").strip()
    if command_raw:
        mapping = {
            "{file}": str(encrypted),
            "{checksum}": str(checksum),
            "{manifest}": str(manifest),
        }
        tokens = shlex.split(command_raw)
        if not tokens:
            raise BackupError("TOMS_BACKUP_UPLOAD_COMMAND is empty")
        for placeholder, value in mapping.items():
            tokens = [token.replace(placeholder, value) for token in tokens]
        upload_env = os.environ.copy()
        upload_env.pop("PGPASSWORD", None)
        upload_env.pop("PGSSLMODE", None)
        run(tokens, env=upload_env)
        print("Off-host upload command completed.")
        results.append({"type": "command", "status": "ok"})

    return results


def prune_backups(
    directory: Path,
    daily: int,
    monthly: int,
    dry_run: bool,
) -> list[Path]:
    backups = list_backups(directory)
    if not backups:
        return []

    now = datetime.now(timezone.utc)
    daily_dates = {(now - timedelta(days=offset)).date() for offset in range(daily)}
    monthly_keys = previous_months(now, monthly)

    keep: set[Path] = {backups[0]}
    seen_dates: set = set()
    seen_months: set = set()
    for backup in backups:
        moment = parse_backup_time(backup)
        if moment is None:
            continue
        day = moment.date()
        month = (moment.year, moment.month)
        if day in daily_dates and day not in seen_dates:
            keep.add(backup)
            seen_dates.add(day)
        if month in monthly_keys and month not in seen_months:
            keep.add(backup)
            seen_months.add(month)

    removed: list[Path] = []
    for backup in backups:
        if backup in keep:
            continue
        removed.append(backup)
        if dry_run:
            print(f"Would remove: {backup.name}")
            continue
        manifest, checksum = sidecar_paths(backup)
        for path in (backup, checksum, manifest):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        print(f"Removed by retention: {backup.name}")
    return removed


def previous_months(now: datetime, count: int) -> set[tuple[int, int]]:
    months: set[tuple[int, int]] = set()
    year, month = now.year, now.month
    for _ in range(count):
        months.add((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return months


def restore_admin_config(source: DBConfig) -> DBConfig:
    raw = os.environ.get("TOMS_BACKUP_RESTORE_ADMIN_URL", "").strip()
    if not raw:
        return source
    return DBConfig.from_url(raw.replace("postgres://", "postgresql://", 1))


def restore_test(args: argparse.Namespace) -> int:
    directory = backup_directory(args.backup_dir)
    if args.file:
        encrypted = Path(args.file).expanduser().resolve()
        if not encrypted.is_file():
            raise BackupError(f"Backup file not found: {encrypted}")
    else:
        backups = list_backups(directory)
        if not backups:
            raise BackupError(f"No backups found in {directory}")
        encrypted = backups[0]

    manifest_path, checksum_path = sidecar_paths(encrypted)
    manifest = read_json(manifest_path)
    expected_digest = manifest.get("backup", {}).get("sha256")
    if not expected_digest:
        raise BackupError("Backup manifest does not contain an encrypted checksum")
    actual_digest = sha256_file(encrypted)
    if actual_digest != expected_digest:
        raise BackupError("Encrypted backup checksum does not match its manifest")
    if checksum_path.is_file():
        recorded = checksum_path.read_text(encoding="utf-8").split()[0]
        if recorded != actual_digest:
            raise BackupError("Checksum sidecar does not match the encrypted backup")

    source = database_config()
    admin = restore_admin_config(source)
    encryption = encryption_config()
    tables = key_tables()

    work = Path(tempfile.mkdtemp(prefix=".restore-", dir=directory))
    _restrict_permissions(work)
    plain = work / (encrypted.name[:-4] if encrypted.name.endswith(".gpg") else encrypted.name + ".dump")
    scratch = f"{SCRATCH_PREFIX}{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    if scratch == source.db or scratch == admin.db:
        raise BackupError("Refusing to use the source database as the restore scratch database")

    print(f"Backup:  {encrypted}")
    print(f"Scratch: {scratch}")
    created = False
    try:
        print("[1/5] Decrypting and verifying the archive...")
        gpg_decrypt(encrypted, plain, encryption)
        expected_plain = manifest.get("dump", {}).get("plaintext_sha256")
        if expected_plain and sha256_file(plain) != expected_plain:
            raise BackupError("Decrypted dump checksum does not match its manifest")
        run(["pg_restore", "--list", str(plain)])

        print("[2/5] Creating a separate scratch database...")
        admin_env = admin.with_db("postgres").subprocess_env()
        run(
            [
                "createdb",
                "--maintenance-db=postgres",
                "-h",
                admin.host,
                "-p",
                str(admin.port),
                "-U",
                admin.user,
                scratch,
            ],
            env=admin_env,
        )
        created = True

        print("[3/5] Restoring into the scratch database...")
        run(
            [
                "pg_restore",
                "--no-owner",
                "--no-privileges",
                "--single-transaction",
                "--exit-on-error",
                "--dbname",
                scratch,
                "-h",
                admin.host,
                "-p",
                str(admin.port),
                "-U",
                admin.user,
                str(plain),
            ],
            env=admin_env,
        )

        print("[4/5] Comparing key-table counts...")
        connection = admin.connect(db=scratch)
        try:
            actual_counts = row_counts(connection, tables)
        finally:
            connection.close()
        expected_counts = manifest.get("row_counts") or {}
        mismatches = {
            table: (expected_counts.get(table), actual_counts.get(table))
            for table in expected_counts
            if actual_counts.get(table) != expected_counts.get(table)
        }
        if mismatches:
            detail = ", ".join(
                f"{table}: expected {expected!r}, got {actual!r}"
                for table, (expected, actual) in mismatches.items()
            )
            raise BackupError(f"Restore row-count verification failed: {detail}")
        print(f"  counts match: {format_counts(actual_counts)}")

        print("[5/5] Restore drill passed.")
        if args.keep:
            print(f"Scratch database kept for inspection: {scratch}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if created and not args.keep:
            try:
                run(
                    [
                        "dropdb",
                        "--if-exists",
                        "--maintenance-db=postgres",
                        "-h",
                        admin.host,
                        "-p",
                        str(admin.port),
                        "-U",
                        admin.user,
                        scratch,
                    ],
                    env=admin_env,
                )
                print(f"Dropped scratch database: {scratch}")
            except BackupError as exc:
                print(f"Warning: could not drop scratch database {scratch}: {exc}", file=sys.stderr)


def list_command(args: argparse.Namespace) -> int:
    directory = backup_directory(args.backup_dir)
    backups = list_backups(directory)
    if not backups:
        print(f"No backups in {directory}")
        return 0
    print(f"Backups in {directory}: {len(backups)}")
    for backup in backups:
        manifest_path, _ = sidecar_paths(backup)
        try:
            manifest = read_json(manifest_path)
        except BackupError:
            manifest = {}
        counts = manifest.get("row_counts")
        print(
            f"  {backup.name}  {format_bytes(backup.stat().st_size)}"
            + (f"  rows: {format_counts(counts)}" if counts else "")
        )
    return 0


def prune_command(args: argparse.Namespace) -> int:
    directory = backup_directory(args.backup_dir)
    daily = env_int("TOMS_BACKUP_RETENTION_DAILY", 30)
    monthly = env_int("TOMS_BACKUP_RETENTION_MONTHLY", 12)
    removed = prune_backups(directory, daily, monthly, dry_run=args.dry_run)
    print(f"Retention: {len(removed)} backup(s) selected for removal.")
    return 0


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def format_counts(counts: dict[str, int | None] | None) -> str:
    if not counts:
        return "none recorded"
    return ", ".join(
        f"{table}={value if value is not None else 'missing'}"
        for table, value in counts.items()
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Encrypted PostgreSQL backups, retention and restore drills."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Create, encrypt and optionally upload a backup")
    backup.add_argument("--backup-dir", help="Override TOMS_BACKUP_DIR")
    backup.add_argument("--no-upload", action="store_true", help="Skip off-host copying")
    backup.add_argument("--no-prune", action="store_true", help="Do not apply retention now")
    backup.set_defaults(func=create_backup)

    restore = subparsers.add_parser(
        "restore-test",
        help="Restore the latest backup into a scratch database and verify it",
    )
    restore.add_argument("--backup-dir", help="Override TOMS_BACKUP_DIR")
    restore.add_argument("--file", help="Specific encrypted backup instead of the latest")
    restore.add_argument(
        "--keep",
        action="store_true",
        help="Keep the scratch database for manual inspection",
    )
    restore.set_defaults(func=restore_test)

    listing = subparsers.add_parser("list", help="List local backups and their manifests")
    listing.add_argument("--backup-dir", help="Override TOMS_BACKUP_DIR")
    listing.set_defaults(func=list_command)

    prune = subparsers.add_parser("prune", help="Apply daily/monthly retention")
    prune.add_argument("--backup-dir", help="Override TOMS_BACKUP_DIR")
    prune.add_argument("--dry-run", action="store_true", help="Show what would be removed")
    prune.set_defaults(func=prune_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_local_environment()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BackupError as exc:
        print(f"Backup error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

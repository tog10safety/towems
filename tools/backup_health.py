#!/usr/bin/env python3
"""Alert when the latest encrypted backup or restore drill is stale.

This is an operational check, not part of the Django request path. It reads the
manifests written by ``postgres_backup.py`` and the ``restore-drill.json``
marker written by a successful ``restore-test`` run. It exits non-zero when a
check fails so systemd, cron mail or a monitoring agent can raise an alert.

Set ``TOMS_BACKUP_ALERT_WEBHOOK`` to an HTTP(S) endpoint to receive a JSON
alert when a check fails. The payload is deliberately limited to timestamps,
file names, ages and failure messages; it never contains database credentials
or backup contents.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from .postgres_backup import (
        RESTORE_STATUS_FILENAME,
        BackupError,
        list_backups,
        load_local_environment,
        parse_backup_time,
        read_json,
        resolve_backup_directory,
        sha256_file,
        sidecar_paths,
    )
except ImportError:
    from postgres_backup import (  # type: ignore[no-redef]
        RESTORE_STATUS_FILENAME,
        BackupError,
        list_backups,
        load_local_environment,
        parse_backup_time,
        read_json,
        resolve_backup_directory,
        sha256_file,
        sidecar_paths,
    )

DEFAULT_MAX_BACKUP_AGE_HOURS = 36
DEFAULT_MAX_RESTORE_AGE_DAYS = 35
DEFAULT_ALERT_TIMEOUT_SECONDS = 10


class HealthError(RuntimeError):
    """A configuration problem that prevents the health check from running."""


def env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise HealthError(f"{name} must be an integer") from exc
    if value <= 0:
        raise HealthError(f"{name} must be greater than zero")
    return value


def parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HealthError(f"{label} does not contain a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HealthError(f"{label} has an invalid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_age(delta_seconds: float) -> str:
    seconds = max(0, int(delta_seconds))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def check_age(
    label: str,
    timestamp: datetime,
    now: datetime,
    max_age_seconds: float,
    limit_text: str,
) -> list[str]:
    age_seconds = (now - timestamp).total_seconds()
    if age_seconds < -600:
        return [f"{label} timestamp is more than 10 minutes in the future"]
    if age_seconds > max_age_seconds:
        return [
            f"{label} is stale: {format_age(age_seconds)} old (limit {limit_text})"
        ]
    return []


def latest_backup_check(directory: Path, now: datetime) -> tuple[dict, list[str]]:
    details: dict[str, object] = {"directory": str(directory)}
    failures: list[str] = []

    if not directory.is_dir():
        return details, [f"backup directory does not exist: {directory}"]

    backups = list_backups(directory)
    if not backups:
        return details, [f"no encrypted backups found in {directory}"]

    latest = backups[0]
    details["name"] = latest.name
    details["size_bytes"] = latest.stat().st_size

    manifest_path, checksum_path = sidecar_paths(latest)
    if not manifest_path.is_file():
        failures.append(f"latest backup manifest is missing: {manifest_path.name}")
        return details, failures

    try:
        manifest = read_json(manifest_path)
    except BackupError as exc:
        return details, [str(exc)]
    if not isinstance(manifest, dict):
        return details, [f"latest backup manifest is not a JSON object: {manifest_path.name}"]

    created_value = manifest.get("created_at")
    if created_value:
        try:
            created_at = parse_timestamp(created_value, "latest backup manifest")
        except HealthError as exc:
            failures.append(str(exc))
            created_at = None
    else:
        created_at = parse_backup_time(latest)

    if created_at is None:
        failures.append("latest backup has no usable creation timestamp")
    else:
        details["created_at"] = created_at.isoformat().replace("+00:00", "Z")
        details["age_seconds"] = max(0, int((now - created_at).total_seconds()))
        try:
            max_age_hours = env_positive_int(
                "TOMS_BACKUP_MAX_AGE_HOURS", DEFAULT_MAX_BACKUP_AGE_HOURS
            )
        except HealthError as exc:
            failures.append(str(exc))
            max_age_hours = DEFAULT_MAX_BACKUP_AGE_HOURS
        failures.extend(
            check_age(
                "latest encrypted backup",
                created_at,
                now,
                max_age_hours * 3600,
                f"{max_age_hours}h",
            )
        )

    backup_section = manifest.get("backup")
    expected_digest = (
        backup_section.get("sha256")
        if isinstance(backup_section, dict)
        else None
    )
    if not expected_digest:
        failures.append("latest backup manifest has no encrypted SHA-256 digest")
    else:
        actual_digest = sha256_file(latest)
        details["sha256"] = actual_digest
        if actual_digest != expected_digest:
            failures.append("latest encrypted backup checksum does not match its manifest")
        if checksum_path.is_file():
            recorded = checksum_path.read_text(encoding="utf-8").split()[0]
            if recorded != actual_digest:
                failures.append("latest backup checksum sidecar does not match the file")

    return details, failures


def offsite_check(directory: Path, latest_details: dict) -> list[str]:
    raw = os.environ.get("TOMS_BACKUP_OFFSITE_DIR", "").strip()
    if not raw:
        return []

    name = latest_details.get("name")
    if not isinstance(name, str):
        return []

    offsite = Path(raw).expanduser().resolve()
    if not offsite.is_dir():
        return [f"off-site backup directory does not exist: {offsite}"]

    local = directory / name
    failures: list[str] = []
    for path in (offsite / name, offsite / f"{local.stem}.json", offsite / f"{name}.sha256"):
        if not path.is_file():
            failures.append(f"off-site backup is missing: {path.name}")

    remote = offsite / name
    if remote.is_file():
        local_digest = latest_details.get("sha256")
        if isinstance(local_digest, str):
            if sha256_file(remote) != local_digest:
                failures.append("off-site encrypted backup checksum does not match local backup")

    offsite_manifest = offsite / local.with_suffix(".json").name
    if offsite_manifest.is_file():
        try:
            offsite_metadata = read_json(offsite_manifest)
        except BackupError as exc:
            failures.append(str(exc))
        else:
            if not isinstance(offsite_metadata, dict):
                failures.append("off-site backup manifest is not a JSON object")
            else:
                offsite_section = offsite_metadata.get("backup")
                offsite_digest = (
                    offsite_section.get("sha256")
                    if isinstance(offsite_section, dict)
                    else None
                )
                if offsite_digest != latest_details.get("sha256"):
                    failures.append("off-site backup manifest checksum does not match local backup")
                if offsite_metadata.get("created_at") != latest_details.get("created_at"):
                    failures.append("off-site backup manifest timestamp does not match local backup")

    offsite_checksum = offsite / f"{name}.sha256"
    if offsite_checksum.is_file():
        try:
            recorded = offsite_checksum.read_text(encoding="utf-8").split()[0]
        except (OSError, IndexError):
            failures.append("off-site checksum sidecar is empty or unreadable")
        else:
            if recorded != latest_details.get("sha256"):
                failures.append("off-site checksum sidecar does not match local backup")
    return failures


def restore_check(directory: Path, now: datetime) -> tuple[dict, list[str]]:
    status_path = directory / RESTORE_STATUS_FILENAME
    details: dict[str, object] = {"status_file": str(status_path)}
    if not status_path.is_file():
        return details, [
            f"no successful restore drill recorded in {status_path.name}"
        ]

    try:
        status = read_json(status_path)
    except BackupError as exc:
        return details, [str(exc)]
    if not isinstance(status, dict):
        return details, [f"restore drill marker is not a JSON object: {status_path.name}"]
    if not isinstance(status.get("backup"), str) or not status["backup"].strip():
        return details, ["restore drill marker does not record a backup"]

    completed_value = status.get("completed_at")
    try:
        completed_at = parse_timestamp(completed_value, "restore drill marker")
    except HealthError as exc:
        return details, [str(exc)]

    details["completed_at"] = completed_at.isoformat().replace("+00:00", "Z")
    details["backup"] = status.get("backup")
    details["age_seconds"] = max(0, int((now - completed_at).total_seconds()))

    try:
        max_age_days = env_positive_int(
            "TOMS_RESTORE_MAX_AGE_DAYS", DEFAULT_MAX_RESTORE_AGE_DAYS
        )
    except HealthError as exc:
        return details, [str(exc)]

    return details, check_age(
        "restore drill",
        completed_at,
        now,
        max_age_days * 86400,
        f"{max_age_days}d",
    )


def post_alert(
    webhook: str,
    failures: list[str],
    backup: dict,
    restore: dict,
    now: datetime,
) -> None:
    try:
        timeout = env_positive_int(
            "TOMS_BACKUP_ALERT_TIMEOUT", DEFAULT_ALERT_TIMEOUT_SECONDS
        )
    except HealthError as exc:
        print(f"Alert webhook skipped: {exc}", file=sys.stderr)
        return

    if not webhook.startswith(("https://", "http://")):
        print("Alert webhook skipped: URL must use http:// or https://", file=sys.stderr)
        return

    summary = "TOWEMS backup health ALERT: " + "; ".join(failures)
    payload = {
        "service": "towems-backup",
        "status": "alert",
        "text": summary,
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "failures": failures,
        "latest_backup": backup,
        "latest_restore": restore,
    }
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "towems-backup-health/1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1)
    except urllib.error.HTTPError as exc:
        print(f"Alert webhook failed with HTTP {exc.code}.", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - alert delivery must not mask the health result
        print(f"Alert webhook failed: {type(exc).__name__}.", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Alert when the latest TOWEMS encrypted backup or restore drill is stale."
    )
    parser.add_argument("--backup-dir", help="Override TOMS_BACKUP_DIR")
    parser.add_argument(
        "--no-webhook",
        action="store_true",
        help="Do not send TOMS_BACKUP_ALERT_WEBHOOK even when configured",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_local_environment()
    args = build_parser().parse_args(argv)
    now = datetime.now(timezone.utc)

    try:
        directory = resolve_backup_directory(args.backup_dir)
        backup_details, backup_failures = latest_backup_check(directory, now)
        failures = list(backup_failures)
        failures.extend(offsite_check(directory, backup_details))
        restore_details, restore_failures = restore_check(directory, now)
        failures.extend(restore_failures)
    except (HealthError, BackupError, OSError) as exc:
        print(f"TOWEMS backup health: CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    lines = [
        "TOWEMS backup health: " + ("ALERT" if failures else "OK"),
        f"  backup directory: {directory}",
    ]
    if backup_details.get("name"):
        lines.append(
            "  latest backup: "
            f"{backup_details['name']} "
            f"({format_age(float(backup_details.get('age_seconds', 0)))} old)"
        )
    if restore_details.get("completed_at"):
        lines.append(
            "  last restore drill: "
            f"{restore_details['completed_at']} "
            f"({format_age(float(restore_details.get('age_seconds', 0)))} old)"
        )
    lines.extend(f"  ALERT: {failure}" for failure in failures)
    print("\n".join(lines), file=sys.stderr if failures else sys.stdout)

    if failures:
        webhook = os.environ.get("TOMS_BACKUP_ALERT_WEBHOOK", "").strip()
        if webhook and not args.no_webhook:
            post_alert(webhook, failures, backup_details, restore_details, now)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

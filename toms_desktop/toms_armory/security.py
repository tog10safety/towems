"""Local authentication and role-based permissions."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Iterable

ROLE_ADMIN = "administrator"
ROLE_ARMORER = "armorer"
ROLE_SUPERVISOR = "supervisor"
ROLE_VIEWER = "viewer"

ROLE_LABELS = {
    ROLE_ADMIN: "System Administrator",
    ROLE_ARMORER: "Armorer / Logistics NCOIC",
    ROLE_SUPERVISOR: "Armory Supervisor (read-only)",
    ROLE_VIEWER: "Viewer",
}

PERMISSIONS = {
    ROLE_ADMIN: {
        "view",
        "manage_inventory",
        "manage_personnel",
        "assign",
        "issue",
        "return",
        "reports",
        "sync",
        "manage_users",
        "settings",
        "audit",
    },
    ROLE_ARMORER: {
        "view",
        "manage_inventory",
        "manage_personnel",
        "assign",
        "issue",
        "return",
        "reports",
        "sync",
    },
    ROLE_SUPERVISOR: {"view", "reports"},
    ROLE_VIEWER: {"view"},
}

PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 with a per-password salt."""
    if not password or len(password) < 10:
        raise ValueError("Use a password of at least 10 characters.")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = str(encoded).split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def role_permissions(role: str) -> set[str]:
    return set(PERMISSIONS.get(role, set()))


def has_permission(role: str, permission: str) -> bool:
    return permission in role_permissions(role)


def require_permission(role: str, permission: str) -> None:
    if not has_permission(role, permission):
        raise PermissionError("Your local role does not permit this action.")


def is_strong_password(password: str) -> tuple[bool, str]:
    if len(password or "") < 10:
        return False, "Use at least 10 characters."
    if not any(ch.isdigit() for ch in password):
        return False, "Include at least one number."
    if not any(ch.isalpha() for ch in password):
        return False, "Include at least one letter."
    return True, ""


def new_device_label() -> str:
    return os.environ.get("COMPUTERNAME") or "Armory Desktop"

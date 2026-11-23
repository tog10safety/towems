"""TOTP multi-factor authentication for privileged accounts.

Policy
======
Administrators and Commanders must enrol a TOTP authenticator. The first
privileged sign-in opens a **14-day grace window**: the account can still use
TOWEMS, but every page carries an enrolment prompt. After the window closes the
only thing the password session may do is enrol; a successful TOTP/recovery
check upgrades that session to a normal one.

The MFA rows are additive. The legacy ``users`` table is unmanaged and is never
altered. Recovery codes are one-time values stored as Django password hashes;
the plaintext is returned exactly once, when enrolment completes.
"""

from __future__ import annotations

import base64
import io
import math
import re
import secrets
import time
from datetime import datetime, timedelta

import pyotp
import qrcode
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.db import transaction
from django.utils import timezone
from qrcode.image.svg import SvgPathImage

from apps.accounts.models import MFARecoveryCode, UserMFA
from apps.core import roles
from apps.core.audit import log_action

#: Accounts holding either role must enrol. ``commander`` is an appointment
#: role, so this is deliberately based on effective roles rather than the legacy
#: ``users.authority`` column.
MFA_REQUIRED_ROLES = frozenset({roles.ROLE_ADMINISTRATOR, roles.ROLE_COMMANDER})

#: Length of the self-service enrolment window. An administrator reset restarts
#: it for the affected account only.
ENROLLMENT_GRACE_DAYS = 14

#: A password sign-in that needs a second factor is held in this signed token,
#: not in a Django session, until the factor is verified.
CHALLENGE_SALT = "tows.mfa.challenge"
CHALLENGE_MAX_AGE = 300

RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_TOTP_ALPHABET = re.compile(r"\D")


class MFAError(ValueError):
    """A safe, user-facing MFA failure."""


def _now() -> datetime:
    return timezone.now()


def get_state(user):
    """Return the MFA state for ``user`` or ``None`` without creating a row."""
    if user is None or not getattr(user, "pk", None):
        return None
    return UserMFA.objects.filter(pk=user.pk).first()


def role_requires_mfa(user) -> bool:
    """Whether deployment policy asks this account for a second factor.

    Since the TOWEMS cutover every active account enrols Google Authenticator,
    not only Administrators and Commanders (``MFA_REQUIRED_FOR_ALL_ACCOUNTS``).
    """
    if user is None or not getattr(user, "is_active", False):
        return False
    if not getattr(user, "pk", None):
        return False
    if getattr(settings, "MFA_REQUIRED_FOR_ALL_ACCOUNTS", False):
        return True
    return bool(roles.effective_roles(user) & MFA_REQUIRED_ROLES)


def is_enabled(state) -> bool:
    return bool(state is not None and state.enabled_at)


def requires_mfa(user) -> bool:
    """True for a privileged account or one that has already enrolled.

    Once enrolled, MFA keeps applying even if the appointment is later removed;
    silently dropping a second factor because a role changed would weaken an
    account that had already been protected.
    """
    state = get_state(user)
    if is_enabled(state):
        return True
    return role_requires_mfa(user)


def ensure_state(user):
    """Return the account's state, creating it and starting the grace clock."""
    now = _now()
    state, created = UserMFA.objects.get_or_create(
        user_id=user.pk,
        defaults={"grace_started_at": now},
    )
    if not created and state.grace_started_at is None and state.enabled_at is None:
        state.grace_started_at = now
        state.save(update_fields=["grace_started_at", "updated_at"])
    return state


def grace_deadline(state):
    if state is None or state.grace_started_at is None:
        return None
    return state.grace_started_at + timedelta(days=ENROLLMENT_GRACE_DAYS)


def grace_days_remaining(state) -> int | None:
    deadline = grace_deadline(state)
    if deadline is None:
        return None
    remaining = (deadline - _now()).total_seconds()
    return max(0, int(math.ceil(remaining / 86400)))


def grace_active(state, now=None) -> bool:
    deadline = grace_deadline(state)
    if deadline is None:
        return False
    return (now or _now()) <= deadline


def recovery_codes_remaining(state) -> int:
    if state is None:
        return 0
    return state.recovery_codes.filter(used_at__isnull=True).count()


def status_payload(user, request=None) -> dict:
    """MFA fields merged into the account payload and the admin register."""
    state = get_state(user)
    enabled = is_enabled(state)
    required = enabled or role_requires_mfa(user)
    deadline = grace_deadline(state)
    session = getattr(request, "session", None)
    return {
        "mfa_required": required,
        "mfa_enabled": enabled,
        "mfa_account": (state.account or "") if state is not None else "",
        "mfa_enrollment_required": required and not enabled,
        "mfa_setup_required": bool(session and session.get("mfa_setup_required")),
        "mfa_verified": bool(session and session.get("mfa_verified")),
        "mfa_grace_deadline": deadline.isoformat() if deadline else None,
        "mfa_grace_days_remaining": grace_days_remaining(state),
        "mfa_recovery_codes_remaining": recovery_codes_remaining(state),
        "mfa_enrolled_at": state.enabled_at.isoformat() if enabled else None,
    }


def enrolment_account(user) -> str:
    """The Gmail address the authenticator entry is created under.

    Google Authenticator must show the person's own Google account - that is
    the entry they recognise when approving a code - so the profile address is
    required and is used verbatim. No address, no enrolment.
    """
    from apps.accounts import setup_gate
    from apps.accounts.models import UserProfile

    profile = UserProfile.objects.filter(user_id=user.pk).first()
    email = (getattr(profile, "email", "") or "").strip().lower()
    if not setup_gate.email_ok(email):
        raise MFAError(
            "Record your Google (Gmail) account on your profile before setting up "
            "Google Authenticator."
        )
    return email


def begin_setup(user):
    """Create/keep a pending secret and return the provisioning material."""
    # Refused before any state is written when the account has no Gmail yet.
    account = enrolment_account(user)
    state = ensure_state(user)
    if is_enabled(state):
        raise MFAError("Multi-factor authentication is already enabled for this account.")
    changed: list[str] = []
    if not state.secret:
        state.secret = pyotp.random_base32()
        changed.append("secret")
    if state.account != account:
        state.account = account
        changed.append("account")
    if changed:
        state.save(update_fields=[*changed, "updated_at"])

    # ``name`` is the account shown inside the authenticator app.
    uri = pyotp.TOTP(state.secret).provisioning_uri(
        name=account,
        issuer_name="TOWEMS",
    )
    qr = qrcode.make(uri, image_factory=SvgPathImage)
    buffer = io.BytesIO()
    qr.save(buffer)
    data_uri = "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode(
        "ascii"
    )
    return {
        "secret": state.secret,
        "otpauth_url": uri,
        "qr_data_uri": data_uri,
        "issuer": "TOWEMS",
        "account": account,
    }


def _normalise_totp(code: str) -> str:
    return _TOTP_ALPHABET.sub("", code or "")


def verify_totp(state, code: str) -> bool:
    """Verify a six-digit code and reject a replay of the same time step."""
    code = _normalise_totp(code)
    if state is None or not state.secret or len(code) != 6:
        return False

    totp = pyotp.TOTP(state.secret)
    current = int(time.time()) // 30
    matched = None
    # Check the three accepted time steps explicitly so the stored counter is
    # the step that actually matched. Using the current step for every match
    # would reject a valid code from the next window right after enrolment.
    for step in (current - 1, current, current + 1):
        if totp.verify(
            code,
            # pyotp treats a naive datetime as process-local, so build the
            # moment the same way. UTC here would shift the counter on hosts
            # whose zone is not UTC (TOWEMS sets Asia/Manila).
            for_time=datetime.fromtimestamp(step * 30),
            valid_window=0,
        ):
            matched = step
            break
    if matched is None:
        return False
    if state.last_used_counter is not None and matched <= state.last_used_counter:
        return False

    state.last_used_counter = matched
    state.last_used_at = _now()
    state.save(update_fields=["last_used_counter", "last_used_at", "updated_at"])
    return True


def _new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def generate_recovery_codes(state, count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Replace any previous codes with a fresh set, returning the plaintext once."""
    codes = [_new_recovery_code() for _ in range(count)]
    state.recovery_codes.all().delete()
    MFARecoveryCode.objects.bulk_create(
        [
            MFARecoveryCode(mfa=state, code_hash=make_password(code.replace("-", "")))
            for code in codes
        ]
    )
    return codes


def complete_setup(user, code: str) -> list[str]:
    """Verify the first TOTP code and enable MFA, returning recovery codes once."""
    state = get_state(user)
    if state is None or is_enabled(state):
        raise MFAError("Multi-factor authentication is already enabled for this account.")
    if not verify_totp(state, code):
        raise MFAError("That authenticator code is not valid. Try the current code.")

    with transaction.atomic():
        state.enabled_at = _now()
        state.grace_started_at = None
        state.save(update_fields=["enabled_at", "grace_started_at", "updated_at"])
        recovery_codes = generate_recovery_codes(state)

    log_action(
        actor=user,
        action="enable_mfa",
        entity_type="user",
        entity_id=user.pk,
        after={"enabled": True, "recovery_codes": len(recovery_codes)},
        note="TOTP multi-factor authentication enrolled",
    )
    return recovery_codes


def verify_login_code(state, code: str, *, recovery: bool = False) -> bool:
    if state is None:
        return False
    if recovery:
        return consume_recovery_code(state, code)
    return verify_totp(state, code)


def consume_recovery_code(state, code: str) -> bool:
    """Consume one unused recovery code. Case, spaces and dashes are ignored."""
    normalised = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    if len(normalised) < 8:
        return False
    for row in state.recovery_codes.filter(used_at__isnull=True):
        if check_password(normalised, row.code_hash):
            row.used_at = _now()
            row.save(update_fields=["used_at"])
            return True
    return False


def issue_challenge(user) -> str:
    """A short-lived signed challenge, replacing a pre-MFA session."""
    return signing.dumps(
        {"uid": user.pk, "nonce": secrets.token_urlsafe(12)},
        salt=CHALLENGE_SALT,
    )


def read_challenge(token: str):
    """Return the active user behind a challenge token, or ``None``."""
    from apps.accounts.models import User

    if not token:
        return None
    try:
        payload = signing.loads(
            token,
            salt=CHALLENGE_SALT,
            max_age=CHALLENGE_MAX_AGE,
        )
    except (signing.BadSignature, signing.SignatureExpired):
        return None

    try:
        user_id = int(payload.get("uid"))
    except (AttributeError, TypeError, ValueError):
        return None
    return User.objects.filter(pk=user_id, is_active=True).first()


def reset_for_user(user, *, actor, reason: str, request=None):
    """Clear enrolment and restart the grace window; administrator-only path.

    The caller must enforce the Administrator permission. This function writes
    the TOWEMS audit record (including the reason) and the legacy activity row.
    """
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise MFAError("Record why the MFA enrolment is being reset.")

    now = _now()
    state = ensure_state(user)
    before = {
        "enabled": is_enabled(state),
        "recovery_codes": recovery_codes_remaining(state),
        "grace_started_at": (
            state.grace_started_at.isoformat() if state.grace_started_at else None
        ),
    }

    with transaction.atomic():
        state.recovery_codes.all().delete()
        state.secret = ""
        state.account = ""
        state.enabled_at = None
        state.last_used_counter = None
        state.last_used_at = None
        state.grace_started_at = now
        state.save(
            update_fields=[
                "secret",
                "account",
                "enabled_at",
                "last_used_counter",
                "last_used_at",
                "grace_started_at",
                "updated_at",
            ]
        )
        log_action(
            actor=actor,
            action="reset_mfa",
            entity_type="user",
            entity_id=user.pk,
            before=before,
            after={"enabled": False, "grace_started_at": now.isoformat()},
            note=reason,
            request=request,
        )
        from apps.core.legacy_audit import record_activity

        record_activity(
            user=actor,
            activity_type="user_modified",
            module="users",
            description=f"Reset MFA enrolment for @{user.username}: {reason}",
            request=request,
            resource_id=user.pk,
            resource_type="user",
        )
    return state

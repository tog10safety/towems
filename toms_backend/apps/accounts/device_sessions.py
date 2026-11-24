"""One device at a time, for the roles that asked for it.

A Creator account may be signed in on exactly one device. A second sign-in is
refused with a body that names the device already holding the session, plus a
short-lived signed challenge; the person can then choose to end that session
and continue here. Every other role keeps multi-device access, which the
Operations Center and Command posts need to do their work.

Django's ``django_session`` table remains the source of truth for whether a
session is alive. This module only adds the account and device to each session
key, so a stale row can never keep somebody out: rows whose session has expired
are skipped and deleted.
"""

from __future__ import annotations

import secrets

from django.contrib.sessions.models import Session
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.core import roles

#: Roles limited to one device, by their legacy base role (``users.authority``).
SINGLE_DEVICE_ROLES = frozenset({roles.ROLE_CREATOR})

#: Signed token that authorises ending the other device's session.
CONFLICT_SALT = "towems.device-conflict"
#: Long enough for the person to read the dialog, short enough to be useless later.
CONFLICT_MAX_AGE = 300

#: Longest user agent kept; the label is what the dialog shows anyway.
USER_AGENT_LIMIT = 255


class DeviceConflict(APIException):
    """409: this account is already signed in on another device."""

    status_code = 409
    default_code = "device_in_use"
    default_detail = "This account is already signed in on another device."


def requires_single_device(user) -> bool:
    """True for the roles that may hold one session at a time."""
    return roles.base_role(user) in SINGLE_DEVICE_ROLES


def device_label(user_agent: str) -> str:
    """A short, human label for a user agent, e.g. "Windows computer · Chrome".

    Deliberately hand-rolled: the point is a sentence a duty officer can read
    ("someone is trying to sign in while your Windows computer is online"), not
    a device fingerprint.
    """
    text = (user_agent or "").lower()
    if not text:
        return "Unknown device"

    if "iphone" in text or "ipod" in text:
        platform = "iPhone"
    elif "ipad" in text:
        platform = "iPad"
    elif "android" in text:
        platform = "Android device"
    elif "windows" in text:
        platform = "Windows computer"
    elif "macintosh" in text or "mac os x" in text:
        platform = "Mac"
    elif "linux" in text:
        platform = "Linux computer"
    else:
        platform = "Unrecognised device"

    for token, name in (
        ("edg/", "Edge"),
        ("chrome/", "Chrome"),
        ("firefox/", "Firefox"),
        ("safari/", "Safari"),
    ):
        if token in text:
            return f"{platform} · {name}"
    return platform


def _client_ip(request) -> str | None:
    """The caller's address, as nginx reports it.

    ``X-Forwarded-For`` is only trusted because the deployment puts nginx in
    front of gunicorn on the same host; the left-most entry is the browser.
    """
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded or request.META.get("REMOTE_ADDR") or None


def _clean_ip(request) -> str | None:
    """``ip_address`` is a PostgreSQL inet column; drop anything that is not one."""
    value = _client_ip(request)
    if not value:
        return None
    try:
        from django.core.validators import validate_ipv46_address

        validate_ipv46_address(value)
    except ValidationError:
        return None
    return value


def _live_rows(user, *, exclude_key: str | None = None):
    """This account's device rows whose session still exists and has not expired."""
    from apps.accounts.models import DeviceSession

    queryset = DeviceSession.objects.filter(user=user)
    if exclude_key:
        queryset = queryset.exclude(session_key=exclude_key)
    rows = list(queryset)
    if not rows:
        return []

    keys = [row.session_key for row in rows]
    sessions = {
        session.session_key: session
        for session in Session.objects.filter(session_key__in=keys)
    }
    now = timezone.now()
    live, stale = [], []
    for row in rows:
        session = sessions.get(row.session_key)
        if session is None or session.expire_date <= now:
            stale.append(row.session_key)
            continue
        live.append(row)

    if stale:
        DeviceSession.objects.filter(session_key__in=stale).delete()
    live.sort(key=lambda row: row.last_seen_at or row.created_at, reverse=True)
    return live


def _untracked_sessions(user, *, exclude_key: str | None = None) -> list:
    """This account's live sessions that no ``DeviceSession`` row knows about.

    Sessions opened before this feature existed - or by anything that did not go
    through :func:`remember` - still hold the account, so the single-device rule
    has to count them. Without this a Creator already signed in elsewhere could
    simply sign in again and hold two sessions.
    """
    from apps.accounts.models import DeviceSession

    tracked = set(
        DeviceSession.objects.filter(user=user).values_list("session_key", flat=True)
    )
    found = []
    now = timezone.now()
    for session in Session.objects.filter(expire_date__gt=now):
        if exclude_key and session.session_key == exclude_key:
            continue
        if session.session_key in tracked:
            continue
        try:
            data = session.get_decoded()
        except Exception:  # pragma: no cover - a corrupt row is simply skipped
            continue
        if str(data.get("_auth_user_id")) == str(user.pk):
            found.append(session)
    return found


def _live_devices(user, *, exclude_key: str | None = None) -> list[dict]:
    """Every other device holding this account, tracked or not."""
    devices = [_describe(row) for row in _live_rows(user, exclude_key=exclude_key)]
    for session in _untracked_sessions(user, exclude_key=exclude_key):
        devices.append(
            {
                "session": session.session_key[:8],
                "label": "Another browser session",
                "ip_address": "",
                "since": "",
                "last_seen": "",
                "untracked": True,
            }
        )
    return devices


def _describe(row) -> dict:
    return {
        "session": row.session_key[:8],
        "label": row.device_label or "Unrecognised device",
        "ip_address": row.ip_address or "",
        "since": row.created_at.isoformat() if row.created_at else "",
        "last_seen": row.last_seen_at.isoformat() if row.last_seen_at else "",
    }


def other_devices(user) -> list[dict]:
    """Devices other than the caller's that currently hold a session."""
    return _live_devices(user)


def remember(user, request, session_key: str) -> None:
    """Record this session against the account and its device."""
    from apps.accounts.models import DeviceSession

    if not session_key:
        return
    DeviceSession.objects.update_or_create(
        session_key=session_key,
        defaults={
            "user": user,
            "ip_address": _clean_ip(request),
            "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:USER_AGENT_LIMIT],
            "device_label": device_label(request.META.get("HTTP_USER_AGENT") or ""),
        },
    )


def forget(session_key: str | None) -> None:
    """Drop the row for a session that has just ended."""
    from apps.accounts.models import DeviceSession

    if not session_key:
        return
    DeviceSession.objects.filter(session_key=session_key).delete()


def end_session(session_key: str) -> None:
    """End one session everywhere: Django's table and the device row."""
    from apps.accounts.models import DeviceSession

    Session.objects.filter(session_key=session_key).delete()
    DeviceSession.objects.filter(session_key=session_key).delete()


def end_others(user, *, keep_key: str | None = None) -> int:
    """End every live session of ``user`` except ``keep_key``; returns the count."""
    rows = _live_rows(user, exclude_key=keep_key)
    for row in rows:
        end_session(row.session_key)
    ended = len(rows)
    # Sessions opened before they were tracked have no row to delete.
    for session in _untracked_sessions(user, exclude_key=keep_key):
        Session.objects.filter(session_key=session.session_key).delete()
        ended += 1
    return ended


def issue_conflict(user, *, verified: bool, enrollment_required: bool = False) -> str:
    """A short-lived token authorising "sign out the other device and continue".

    It is issued only after the password (and, when the account has one, the
    authenticator code) has already been verified, and it carries that state so
    the confirmation cannot be replayed to skip either factor.
    """
    return signing.dumps(
        {
            "uid": user.pk,
            "verified": bool(verified),
            "enrollment_required": bool(enrollment_required),
            "nonce": secrets.token_urlsafe(12),
        },
        salt=CONFLICT_SALT,
    )


def read_conflict(token: str):
    """Return ``(user, verified, enrollment_required)`` for a token, or ``None``."""
    from apps.accounts.models import User

    if not token:
        return None
    try:
        payload = signing.loads(token, salt=CONFLICT_SALT, max_age=CONFLICT_MAX_AGE)
    except (signing.BadSignature, signing.SignatureExpired):
        return None

    try:
        user_id = int(payload.get("uid"))
    except (AttributeError, TypeError, ValueError):
        return None
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return None
    return user, bool(payload.get("verified", True)), bool(
        payload.get("enrollment_required", False)
    )


def register_login(
    user,
    request,
    session_key: str,
    *,
    verified: bool,
    enrollment_required: bool = False,
) -> dict | None:
    """Record the new session, or report the device that already holds one.

    Returns the conflict body (challenge + the device to name) when the account
    is limited to one device and another device is live, otherwise ``None``.
    """
    if not requires_single_device(user):
        remember(user, request, session_key)
        return None

    devices = _live_devices(user, exclude_key=session_key)
    if not devices:
        remember(user, request, session_key)
        return None

    first = devices[0]
    return {
        "detail": (
            f"This account is signed in on {first['label']}"
            f"{' from ' + first['ip_address'] if first['ip_address'] else ''}. "
            "Only one device may be signed in at a time."
        ),
        "code": "device_in_use",
        "device": first,
        "devices": devices,
        "challenge": issue_conflict(
            user, verified=verified, enrollment_required=enrollment_required
        ),
        "expires_in": CONFLICT_MAX_AGE,
    }

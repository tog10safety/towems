"""Long-lived, read-only credentials for the phone's alert watcher.

A phone must be told about an abort order at 03:00, long after the web session
it signed in with has expired. Extending the session would defeat the
deployment's one-hour limit - an unattended browser would stay signed in
forever - and signing the watcher in again would mean keeping the password and
the authenticator seed on the device.

So the shell exchanges a live, fully verified session for one narrow token:

* it authenticates ``GET`` on the two endpoints the watcher reads (the
  notification pulse and the message summary) plus a single write - the driver's
  own GPS breadcrumbs, while that driver's run is open - and nothing else;
* only its SHA-256 digest is stored, so a database reader cannot replay it;
* it is bound to the install that asked for it and to the session that minted
  it, and is revoked when that session ends (sign-out, device switch), when the
  password is changed or reset, and when the account is deactivated;
* it expires after ``TOKEN_AGE_DAYS`` anyway, and the app renews it silently
  the next time it is opened with a live session.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets

from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import (
    BaseAuthentication,
    SessionAuthentication,
    get_authorization_header,
)
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.accounts.models import DeviceToken

#: Prefix that makes a leaked string recognisable in a log or a paste.
TOKEN_PREFIX = "towems_dev_"

#: A token outlives the one-hour session but not forever.
TOKEN_AGE_DAYS = 30

#: Client-generated install identifier: no spaces, no path characters.
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

#: ``last_used_at`` is bookkeeping, not a security check: stamping it on every
#: 20-second poll would write to the database 4,000 times a day per phone.
LAST_USED_STEP = dt.timedelta(minutes=10)


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _age_days() -> int:
    return int(getattr(settings, "DEVICE_TOKEN_AGE_DAYS", TOKEN_AGE_DAYS))


def issue(user, *, device_id: str, label: str = "", user_agent: str = "", session_key: str = ""):
    """Mint (or rotate) this install's token and return ``(raw, row)``.

    One token per install: asking again from the same phone replaces the old
    value, so a token captured from an earlier log cannot be replayed.
    """
    if not _DEVICE_ID.match(device_id or ""):
        raise ValueError("A device identifier is required.")

    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = timezone.now()
    row, _created = DeviceToken.objects.update_or_create(
        user=user,
        device_id=device_id,
        defaults={
            "session_key": session_key or "",
            "token_hash": _digest(raw),
            "label": (label or "")[:80],
            "user_agent": (user_agent or "")[:255],
            "last_used_at": None,
            "expires_at": now + dt.timedelta(days=_age_days()),
            "revoked_at": None,
        },
    )
    return raw, row


def authenticate(raw: str):
    """Return the live token row for ``raw``, or ``None``.

    Expired, revoked and deactivated-account tokens never authenticate.
    """
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    row = (
        DeviceToken.objects.select_related("user")
        .filter(
            token_hash=_digest(raw),
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if row is None or not row.user.is_active:
        return None

    now = timezone.now()
    if row.last_used_at is None or now - row.last_used_at >= LAST_USED_STEP:
        DeviceToken.objects.filter(pk=row.pk).update(last_used_at=now)
        row.last_used_at = now
    return row


def revoke_for_session(session_key: str) -> int:
    """End the tokens minted by one session; returns how many were revoked."""
    if not session_key:
        return 0
    return DeviceToken.objects.filter(
        session_key=session_key, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())


def revoke_all(user) -> int:
    """End every token of one account (password change or reset)."""
    return DeviceToken.objects.filter(
        user=user, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())


class DeviceTokenAuthentication(BaseAuthentication):
    """Authenticate ``Authorization: Device <token>`` on the alert endpoints.

    Only registered where a watcher may read, so a token can never reach the
    rest of the API even if it is copied off the phone.
    """

    keyword = "Device"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword.lower().encode():
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Malformed device token.")
        try:
            raw = parts[1].decode("ascii")
        except UnicodeDecodeError:
            raise AuthenticationFailed("Malformed device token.")

        row = authenticate(raw)
        if row is None:
            raise AuthenticationFailed(
                "This phone's alert token is no longer valid. "
                "Open TOWEMS and sign in again."
            )
        return row.user, row

    def authenticate_header(self, request):
        return self.keyword


class DeviceTokenTracking(BasePermission):
    """The one write a token may make: the position of its own driver's vehicle.

    Attached only to the phone's tracking endpoint, where ``tracking.can_ping``
    still decides whether this account drives that run. A token copied off a
    phone can therefore not move somebody else's vehicle, and can report nothing
    at all once the run is closed.
    """

    message = "A phone alert token may only report its own driver's position."

    def has_permission(self, request, view):
        if isinstance(request.auth, DeviceToken):
            return request.method in SAFE_METHODS or request.method == "POST"
        return True


class DeviceTokenReadOnly(BasePermission):
    """A device token may read; it can never write, even on its own endpoints."""

    message = "A phone alert token may only read alerts."

    def has_permission(self, request, view):
        if isinstance(request.auth, DeviceToken):
            return request.method in SAFE_METHODS
        return True


#: The alert and tracking endpoints accept a session, a device token or a JWT.
ALERT_AUTHENTICATION = [
    DeviceTokenAuthentication,
    SessionAuthentication,
    JWTAuthentication,
]

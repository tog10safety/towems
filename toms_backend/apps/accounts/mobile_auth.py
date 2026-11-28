"""Android biometric token chain.

The password and authenticator are only ever typed into the existing web/MFA
login, inside the Android WebView.  That verified Django session is exchanged
once for a short-lived access token and an opaque, rotating refresh token.  The
refresh token is encrypted by the native shell with a key that never leaves the
Android Keystore; this module stores only its SHA-256 digest.

Security properties:

* access tokens live 15 minutes and name both the install and the session;
* refresh tokens rotate on every use and are bound to the install;
* a chain is capped at 30 days from its first authentication;
* closing the app does not end the chain: the Keystore-protected token stays
  valid until sign-out, device revocation or that 30-day limit;
* revoking the device or the account revokes every token in the chain on the
  next request, without waiting for an access token to expire.

This is deliberately separate from :mod:`apps.accounts.device_tokens`, which is
a narrow, read-only watcher credential for the alert service.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import secrets

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts import security
from apps.accounts.models import MobileDevice, MobileSession
from apps.core.audit import log_action

logger = logging.getLogger(__name__)

REFRESH_PREFIX = "towems_mob_"
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

ACCESS_LIFETIME = dt.timedelta(minutes=15)
# The token is deliberately long-lived: an Android app that is closed (or has
# its process killed by the OS) must still be able to reopen with biometrics.
# The absolute 30-day limit and explicit sign-out/revocation remain the caps.
REFRESH_LIFETIME = dt.timedelta(days=30)
SESSION_MAX_LIFETIME = dt.timedelta(days=30)
INACTIVITY_LIFETIME = dt.timedelta(days=30)

#: A database write per request would be wasteful; activity is bookkeeping,
#: while the actual timeout is still checked against the last stored stamp.
ACTIVITY_WRITE_STEP = dt.timedelta(seconds=60)

_last_activity_write: dict[int, dt.datetime] = {}


class MobileAuthError(Exception):
    """A refresh token or device can no longer be used."""


class DeviceRevoked(MobileAuthError):
    pass


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_refresh() -> str:
    return REFRESH_PREFIX + secrets.token_urlsafe(48)


def _issue_access(session: MobileSession) -> tuple[str, int]:
    token = AccessToken.for_user(session.user)
    # Force the required 15 minutes even if a deployment's generic API-token
    # setting is longer. The mobile chain has its own fixed policy.
    token.set_exp(lifetime=ACCESS_LIFETIME)
    token["typ"] = "mobile"
    token["msid"] = session.pk
    token["dev"] = session.device.device_id
    token["sst"] = int(session.session_started_at.timestamp())
    return str(token), int(ACCESS_LIFETIME.total_seconds())


def _touch_activity(user, now=None) -> None:
    """Stamp the account clock, throttled to one write per minute."""
    now = now or timezone.now()
    key = int(user.pk)
    previous = _last_activity_write.get(key)
    if previous is not None and now - previous < ACTIVITY_WRITE_STEP:
        return
    _last_activity_write[key] = now
    try:
        security.record_authenticated_activity(user, when=now)
    except Exception:
        _last_activity_write.pop(key, None)
        logger.warning("Could not stamp mobile activity for user %s", key, exc_info=True)


def _audit(user, action: str, *, device=None, request=None, note: str = "", after=None):
    log_action(
        actor=user,
        action=action,
        entity_type="mobile_device" if device is not None else "auth",
        entity_id=device.pk if device is not None else user.pk,
        after=after or {},
        note=note,
        request=request,
    )


def _revoke_session(session: MobileSession, *, when=None) -> None:
    if session.revoked_at is not None:
        return
    MobileSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(
        revoked_at=when or timezone.now()
    )


@transaction.atomic
def enroll(
    user,
    *,
    device_id: str,
    label: str = "",
    platform: str = "android",
    app_version: str = "",
    session_key: str = "",
    request=None,
) -> dict:
    """Exchange a live, MFA-verified web session for a mobile token chain.

    A device the administrator has revoked cannot enrol itself back in.  An
    authorised device re-enrolling replaces its old refresh chain, which is the
    correct behaviour after a reinstall that kept the install id.
    """
    if not DEVICE_ID_RE.match(device_id or ""):
        raise MobileAuthError("A valid device identifier is required.")

    now = timezone.now()
    device, created = MobileDevice.objects.select_for_update().get_or_create(
        user=user,
        device_id=device_id,
        defaults={
            "label": (label or "Android device")[:80],
            "platform": (platform or "android")[:32],
            "app_version": (app_version or "")[:32],
            "is_authorized": True,
            "authorized_at": now,
            "last_seen_at": now,
            "session_key": (session_key or "")[:64],
        },
    )
    if not created and (not device.is_authorized or device.revoked_at is not None):
        raise DeviceRevoked(
            "This device has been revoked. An administrator must authorise it again."
        )

    device.label = (label or device.label or "Android device")[:80]
    device.platform = (platform or device.platform or "android")[:32]
    device.app_version = (app_version or device.app_version or "")[:32]
    device.last_seen_at = now
    if session_key:
        device.session_key = session_key[:64]
    if device.authorized_at is None:
        device.authorized_at = now
    device.save(
        update_fields=[
            "label",
            "platform",
            "app_version",
            "last_seen_at",
            "session_key",
            "authorized_at",
            "updated_at",
        ]
    )

    # One live refresh chain per install: a repeated enrolment is a fresh login.
    MobileSession.objects.filter(
        user=user, device=device, revoked_at__isnull=True
    ).update(revoked_at=now, rotated_at=now)

    raw_refresh = _new_refresh()
    session = MobileSession.objects.create(
        user=user,
        device=device,
        refresh_hash=_digest(raw_refresh),
        session_started_at=now,
        last_activity_at=now,
        refresh_expires_at=now + REFRESH_LIFETIME,
    )
    access, expires_in = _issue_access(session)
    security.record_authenticated_activity(user, when=now)
    _audit(
        user,
        "mobile_device_registered",
        device=device,
        request=request,
        note="Android device enrolled for biometric refresh",
        after={
            "device_id": device.device_id,
            "label": device.label,
            "platform": device.platform,
        },
    )
    return {
        "access_token": access,
        "refresh_token": raw_refresh,
        "expires_in": expires_in,
        "refresh_expires_in": int(REFRESH_LIFETIME.total_seconds()),
        "device": {
            "id": device.pk,
            "device_id": device.device_id,
            "label": device.label,
        },
    }


@transaction.atomic
def refresh(raw_refresh: str, *, device_id: str, request=None) -> dict:
    """Rotate a refresh token and issue a new 15-minute access token."""
    if not raw_refresh or not raw_refresh.startswith(REFRESH_PREFIX):
        raise MobileAuthError("This session has expired. Sign in again.")
    if not DEVICE_ID_RE.match(device_id or ""):
        raise MobileAuthError("A valid device identifier is required.")

    session = (
        MobileSession.objects.select_for_update()
        .select_related("user", "device")
        .filter(refresh_hash=_digest(raw_refresh))
        .first()
    )
    if session is None:
        raise MobileAuthError("This session has expired. Sign in again.")

    now = timezone.now()
    if session.revoked_at is not None:
        raise MobileAuthError("This session has been revoked. Sign in again.")
    if session.device.device_id != device_id:
        # A refresh token copied to another install must not work.
        _revoke_session(session, when=now)
        raise DeviceRevoked("This token belongs to another device.")
    if not session.device.is_authorized or session.device.revoked_at is not None:
        _revoke_session(session, when=now)
        raise DeviceRevoked(
            "This device has been revoked. An administrator must authorise it again."
        )
    if not session.user.is_active:
        _revoke_session(session, when=now)
        raise MobileAuthError("This account is inactive.")
    if now - session.session_started_at > SESSION_MAX_LIFETIME:
        _revoke_session(session, when=now)
        raise MobileAuthError(
            "The 30-day session limit has been reached. Sign in again."
        )
    if now - session.last_activity_at > INACTIVITY_LIFETIME:
        _revoke_session(session, when=now)
        raise MobileAuthError("The session has expired. Sign in again.")
    if session.refresh_expires_at <= now:
        _revoke_session(session, when=now)
        raise MobileAuthError("The refresh token has expired. Sign in again.")

    raw_refresh = _new_refresh()
    session.refresh_hash = _digest(raw_refresh)
    session.refresh_expires_at = now + REFRESH_LIFETIME
    session.last_activity_at = now
    session.rotated_at = now
    session.save(
        update_fields=[
            "refresh_hash",
            "refresh_expires_at",
            "last_activity_at",
            "rotated_at",
        ]
    )

    # A refresh is requested only from inside the app: a biometric unlock or a
    # 15-minute access token expiring while the app is in use. It is therefore
    # real use of the device, and keeps the chain alive across app restarts.
    MobileDevice.objects.filter(pk=session.device_id).update(last_seen_at=now)
    _touch_activity(session.user, now=now)
    access, expires_in = _issue_access(session)
    _audit(
        session.user,
        "mobile_token_refresh",
        device=session.device,
        request=request,
        note="Access token renewed",
        after={"session": session.pk},
    )
    return {
        "access_token": access,
        "refresh_token": raw_refresh,
        "expires_in": expires_in,
        "refresh_expires_in": int(REFRESH_LIFETIME.total_seconds()),
    }


def check_access(token, *, request=None) -> MobileSession:
    """Validate a mobile access token's server-side session and limits."""
    session_id = token.get("msid")
    if token.get("typ") != "mobile" or not session_id:
        raise AuthenticationFailed("Not a mobile access token.")
    session = (
        MobileSession.objects.select_related("user", "device")
        .filter(pk=session_id)
        .first()
    )
    if session is None or session.revoked_at is not None:
        raise AuthenticationFailed("This mobile session has been revoked.")
    if not session.user.is_active:
        raise AuthenticationFailed("This account is inactive.")
    if not session.device.is_authorized or session.device.revoked_at is not None:
        raise AuthenticationFailed("This Android device has been revoked.")
    if session.device.device_id != token.get("dev"):
        raise AuthenticationFailed("This token belongs to another device.")

    now = timezone.now()
    if now - session.session_started_at > SESSION_MAX_LIFETIME:
        _revoke_session(session, when=now)
        raise AuthenticationFailed(
            "The 30-day session limit has been reached. Sign in again."
        )
    if now - session.last_activity_at > INACTIVITY_LIFETIME:
        _revoke_session(session, when=now)
        raise AuthenticationFailed("The session has expired. Sign in again.")

    interactive = getattr(request, "towems_interactive", True) if request else True
    if interactive:
        if now - session.last_activity_at >= ACTIVITY_WRITE_STEP:
            MobileSession.objects.filter(pk=session.pk).update(last_activity_at=now)
            MobileDevice.objects.filter(pk=session.device_id).update(last_seen_at=now)
        _touch_activity(session.user, now=now)
        # Keep the existing "online now" rail working for token clients.
        try:
            from apps.accounts.presence import stamp_presence

            stamp_presence(session.user)
        except Exception:  # presence must never fail a request
            logger.debug("Presence stamp failed for mobile session", exc_info=True)
    return session


class MobileJWTAuthentication(JWTAuthentication):
    """JWT auth that also enforces the mobile session's server-side state."""

    def authenticate(self, request):
        # Keep the request for the activity marker. DRF instantiates one
        # authentication class per request, so this is not shared state.
        self._towems_request = request
        return super().authenticate(request)

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if validated_token.get("typ") == "mobile":
            check_access(
                validated_token,
                request=getattr(self, "_towems_request", None),
            )
        return user


def revoke_all(
    user,
    *,
    reason: str,
    actor=None,
    request=None,
    revoke_devices: bool = True,
) -> int:
    """Revoke every mobile refresh chain for ``user``.

    Access tokens stop working immediately because authentication re-checks the
    session row.  When ``revoke_devices`` is true the installs also become
    unauthorised, so an administrator must approve them before biometric login
    can be used again.
    """
    now = timezone.now()
    sessions = MobileSession.objects.filter(user=user, revoked_at__isnull=True)
    count = sessions.update(revoked_at=now)
    if revoke_devices:
        MobileDevice.objects.filter(user=user, is_authorized=True).update(
            is_authorized=False,
            revoked_at=now,
            revoked_by=actor if getattr(actor, "pk", None) else None,
        )
    if count or revoke_devices:
        log_action(
            actor=actor,
            action="revoke_mobile_sessions",
            entity_type="auth",
            entity_id=user.pk,
            after={"sessions": count, "devices_revoked": bool(revoke_devices)},
            note=reason,
            request=request,
        )
    return count


def revoke_session(session_id, *, reason: str, request=None) -> bool:
    session = MobileSession.objects.filter(pk=session_id).first()
    if session is None or session.revoked_at is not None:
        return False
    _revoke_session(session)
    log_action(
        actor=session.user,
        action="revoke_mobile_session",
        entity_type="mobile_device",
        entity_id=session.device_id,
        note=reason,
        request=request,
    )
    return True


def revoke_device(device: MobileDevice, *, actor, reason: str, request=None) -> int:
    now = timezone.now()
    # The phone's live WebView session gets the same immediate treatment as its
    # access and refresh tokens; otherwise a revoked install could keep browsing
    # on the cookie it enrolled with.
    if device.session_key:
        from apps.accounts import device_sessions

        device_sessions.end_session(device.session_key)
    device.is_authorized = False
    device.revoked_at = now
    device.revoked_by = actor
    device.save(update_fields=["is_authorized", "revoked_at", "revoked_by", "updated_at"])
    count = MobileSession.objects.filter(device=device, revoked_at__isnull=True).update(
        revoked_at=now
    )
    log_action(
        actor=actor,
        action="revoke_mobile_device",
        entity_type="mobile_device",
        entity_id=device.pk,
        after={"device_id": device.device_id, "sessions": count},
        note=reason,
        request=request,
    )
    return count


def authorize_device(device: MobileDevice, *, actor, request=None) -> MobileDevice:
    device.is_authorized = True
    device.authorized_at = timezone.now()
    device.authorized_by = actor
    device.revoked_at = None
    device.revoked_by = None
    device.save(
        update_fields=[
            "is_authorized",
            "authorized_at",
            "authorized_by",
            "revoked_at",
            "revoked_by",
            "updated_at",
        ]
    )
    log_action(
        actor=actor,
        action="authorize_mobile_device",
        entity_type="mobile_device",
        entity_id=device.pk,
        after={"device_id": device.device_id},
        note="Android device authorised for biometric refresh",
        request=request,
    )
    return device

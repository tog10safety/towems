"""Authentication lifetime rules and automatic account deactivation.

The web session and the Android token chain make the same two promises:

* thirty minutes without an authenticated request ends the session;
* thirty days after the first authentication a fresh password + MFA sign-in is
  required.

Django's session table remains the web source of truth.  ``MobileSession`` in
:mod:`apps.accounts.mobile_auth` is the Android source of truth.  This module
keeps the independent activity clock used by the 30-day account deactivation
policy, and performs the one deactivation path used by both the administrator
action and the scheduled command.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import logout
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.audit import log_action


def _minutes(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def inactivity_limit() -> timedelta:
    return timedelta(minutes=_minutes("SESSION_INACTIVITY_MINUTES", 30))


def maximum_session() -> timedelta:
    return timedelta(days=_minutes("SESSION_MAX_DAYS", 30))


def account_inactivity_limit() -> timedelta:
    return timedelta(days=_minutes("ACCOUNT_INACTIVITY_DAYS", 30))


def record_authenticated_activity(user, *, when=None) -> None:
    """Record a successful authenticated request for the deactivation clock."""
    if user is None or not getattr(user, "pk", None):
        return
    from apps.accounts.models import UserProfile

    now = when or timezone.now()
    UserProfile.objects.update_or_create(
        user_id=user.pk,
        defaults={"security_last_active_at": now},
    )


def mark_session_started(session, *, when=None) -> None:
    """Stamp a freshly authenticated web session with both time limits."""
    now = when or timezone.now()
    session["auth_session_started"] = now.isoformat()
    session["auth_last_activity"] = now.isoformat()


def request_is_interactive(request) -> bool:
    """Distinguish a person's action from a background poll.

    The SPA polls notifications and presence every few seconds. Those requests
    are authenticated but must not hold an idle session open forever. A user
    event sets the activity header for two minutes; unsafe requests (a form
    submission, sign-out, acknowledgement) are always interactive.
    """
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return True
    if request.headers.get("X-TOWEMS-Activity") == "1":
        return True
    # Non-API navigation (for example the admin site) is a person opening a
    # page, not a poll.
    return not request.path.startswith("/api/")


def _stamp(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    # TOWEMS runs with USE_TZ=False (legacy MySQL compatibility). A value that
    # still carries an offset must be brought back to the project's naive
    # convention before it is compared with timezone.now().
    if settings.USE_TZ and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    elif not settings.USE_TZ and timezone.is_aware(parsed):
        parsed = timezone.make_naive(parsed, timezone.get_current_timezone())
    return parsed


class SessionSecurityMiddleware:
    """Enforce the web inactivity and absolute session limits.

    Runs after ``AuthenticationMiddleware``.  Requests authenticated by a DRF
    token do not have a session user at this point; their clock is enforced by
    :class:`apps.accounts.mobile_auth.MobileJWTAuthentication`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        interactive = request_is_interactive(request)
        request.towems_interactive = interactive
        if user is not None and getattr(user, "is_authenticated", False):
            session = getattr(request, "session", None)
            if session is not None:
                now = timezone.now()
                started = _stamp(session.get("auth_session_started"))
                last = _stamp(session.get("auth_last_activity"))
                if started is None or last is None:
                    # Sessions that predate this feature begin their clock now.
                    started = started or now
                    last = last or now
                    session["auth_session_started"] = started.isoformat()
                    session["auth_last_activity"] = last.isoformat()

                if (
                    now - last > inactivity_limit()
                    or now - started > maximum_session()
                ):
                    logout(request)
                elif interactive:
                    # SESSION_SAVE_EVERY_REQUEST writes this sliding value.
                    session["auth_last_activity"] = now.isoformat()
        return self.get_response(request)


def _end_web_sessions(user) -> None:
    from apps.accounts import device_sessions

    try:
        device_sessions.end_others(user)
    except Exception:
        # Session cleanup must not leave an account active after deactivation.
        import logging

        logging.getLogger(__name__).exception(
            "Could not end web sessions for deactivated user %s", user.pk
        )


@transaction.atomic
def deactivate_account(
    user,
    *,
    actor=None,
    reason: str,
    automatic: bool = False,
    request=None,
) -> bool:
    """Deactivate one account and revoke every credential it holds.

    The legacy ``is_active`` flag is the authoritative account switch.  The
    same transaction clears Django sessions, browser alert tokens and the
    Android refresh chain; active Android access tokens fail their next
    authentication because their ``MobileSession`` row is revoked.
    """
    if user is None or not user.is_active:
        return False

    from apps.accounts import device_tokens, mobile_auth

    user.is_active = False
    user.save(update_fields=["is_active"])
    _end_web_sessions(user)
    device_tokens.revoke_all(user)
    mobile_auth.revoke_all(
        user,
        reason=reason,
        actor=actor,
        request=request,
        revoke_devices=True,
    )
    log_action(
        actor=actor,
        action="auto_deactivate_user" if automatic else "deactivate_user",
        entity_type="user",
        entity_id=user.id,
        before={"is_active": True},
        after={"is_active": False},
        note=reason,
        request=request,
    )
    return True


def deactivate_inactive_accounts(*, actor=None, request=None) -> list[int]:
    """Deactivate active accounts whose last authenticated activity is stale.

    Accounts that have never had a profile, activity stamp or creation date are
    left alone: absence of a clock is not evidence of inactivity.  The system
    owner and the last active Administrator are always kept, matching the
    guards on the manual deactivation endpoint.
    """
    from apps.accounts.models import User
    from apps.accounts.ownership import is_system_owner
    from apps.core.permissions import ADMIN

    cutoff = timezone.now() - account_inactivity_limit()
    active_admins = User.objects.filter(authority=ADMIN, is_active=True).count()
    deactivated: list[int] = []

    rows = User.objects.filter(is_active=True).select_related("profile")
    for user in rows:
        if is_system_owner(user):
            continue
        if user.authority == ADMIN and active_admins <= 1:
            continue
        profile = getattr(user, "profile", None)
        # ``last_seen`` is deliberately not a fallback: background polling
        # updates it, while this policy is about authenticated human activity.
        # Migration 0018 seeds the security clock from it once at deployment.
        last = (
            getattr(profile, "security_last_active_at", None)
            or user.date_created
        )
        if last is None or last > cutoff:
            continue
        if deactivate_account(
            user,
            actor=actor,
            reason="Automatically deactivated after 30 days without authenticated activity.",
            automatic=True,
            request=request,
        ):
            deactivated.append(user.id)
            if user.authority == ADMIN:
                active_admins -= 1
    return deactivated

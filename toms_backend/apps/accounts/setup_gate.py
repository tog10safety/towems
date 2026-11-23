"""First sign-in account refresh and mandatory authenticator enrolment.

Deployment policy for the TOWEMS cutover
========================================
Every active account must, on its first sign-in after the cutover:

1. replace the password it was issued,
2. upload a profile picture,
3. upload the signature used on MRM certificates, and
4. record the Google (Gmail) account it will sign in with.

Only then does the account reach the rest of the API, and the very next step is
Google Authenticator (TOTP) enrolment, which is enforced until a code has been
confirmed.

The rollout is a **data** step, never DDL: ``manage.py require_profile_refresh``
flips the additive ``accounts_user_profile.onboarding_required`` flag for every
active account. The legacy ``users`` table is unmanaged and never altered here.

The gate itself is derived from data, so it cannot be bypassed by a hand-crafted
request: the middleware refuses every other ``/api/`` path while a session is
flagged, and the flag is recomputed from the account on every ``/auth/me/`` and
after every setup step.
"""

from __future__ import annotations

import os

from django.conf import settings

GAP_PASSWORD = "password"
GAP_PHOTO = "picture"
GAP_SIGNATURE = "signature"
GAP_EMAIL = "email"

GAP_LABELS = {
    GAP_PASSWORD: "Set a new password",
    GAP_PHOTO: "Upload a profile picture",
    GAP_SIGNATURE: "Upload your signature",
    GAP_EMAIL: "Record your Google (Gmail) account",
}

PROFILE_SETUP_FLAG = "profile_setup_required"
MFA_SETUP_FLAG = "mfa_setup_required"


def required_email_suffix() -> str:
    """Domain the refreshed account address must carry (``@gmail.com``).

    The deployment asks for a real Gmail account, so the default requires one.
    Set ``PROFILE_EMAIL_SUFFIX = ""`` only to accept any valid address.
    """
    return (getattr(settings, "PROFILE_EMAIL_SUFFIX", "@gmail.com") or "").lower()


def email_ok(email: str) -> bool:
    """A usable Google account address (local part + required domain)."""
    value = (email or "").strip().lower()
    if not value or "@" not in value:
        return False
    local, _sep, domain = value.partition("@")
    if not local or not domain or "." not in domain:
        return False
    suffix = required_email_suffix()
    return value.endswith(suffix) if suffix else True


def _safe_filename(filename: str) -> str:
    if not filename:
        return ""
    if os.path.basename(filename) != filename or filename.startswith("."):
        return ""
    return filename


def _profile(user):
    """Read the profile row from the database, never from a cached relation.

    The gate decides access, so it must see what was just saved rather than a
    ``user.profile`` instance another part of the request may still hold.
    """
    if user is None or not getattr(user, "pk", None):
        return None
    # A row already loaded for this request (``select_related`` or a save just
    # made) is authoritative; otherwise read it fresh so the gate never decides
    # on a stale, unused cache entry.
    state = getattr(user, "_state", None)
    if state is not None:
        cached = state.fields_cache.get("profile")
        if cached is not None:
            return cached
    try:
        from apps.accounts.models import UserProfile

        return UserProfile.objects.filter(user_id=user.pk).first()
    except Exception:  # noqa: BLE001 - unsaved user or no profile table
        return None


def onboarding_outstanding(user) -> bool:
    """True while the account owes the first-sign-in profile refresh."""
    if user is None or not getattr(user, "pk", None):
        return False
    profile = _profile(user)
    return bool(
        profile is not None
        and profile.onboarding_required
        and not profile.onboarding_completed_at
    )


def profile_gaps(user) -> list[str]:
    """The refresh steps still outstanding, as stable machine-readable keys.

    Empty when the account owes the refresh nothing (or was never asked to).
    """
    if not onboarding_outstanding(user):
        return []
    profile = _profile(user)
    gaps: list[str] = []
    if user.needs_password_change():
        gaps.append(GAP_PASSWORD)
    if not (getattr(profile, "avatar_filename", "") or ""):
        gaps.append(GAP_PHOTO)
    if not _safe_filename(getattr(user, "signature_filename", "") or ""):
        gaps.append(GAP_SIGNATURE)
    if not email_ok(getattr(profile, "email", "") or ""):
        gaps.append(GAP_EMAIL)
    return gaps


def gap_labels(gaps) -> list[str]:
    return [GAP_LABELS.get(gap, gap) for gap in gaps]


def mfa_outstanding(user) -> bool:
    """True when this account still owes its authenticator.

    Enrolment is enforced for every account taking part in the deployment
    refresh: hosted here when ``require_profile_refresh`` flags it, and for
    accounts registered afterwards (they start with the profile refresh too).
    An account that has finished the refresh must always carry a second factor,
    whatever its role.
    """
    from apps.accounts import mfa

    if user is None or not getattr(user, "pk", None):
        return False
    if not getattr(user, "is_active", False):
        return False
    if not mfa.requires_mfa(user):
        return False

    profile = _profile(user)
    in_rollout = bool(
        profile is not None
        and (profile.onboarding_required or profile.onboarding_completed_at)
    )
    if not in_rollout:
        return False
    return not mfa.is_enabled(mfa.get_state(user))


def sync_session(user, session) -> None:
    """Recompute the session's setup gate from the account's current data.

    Called on sign-in and after every setup step, so the middleware only has to
    read flags. The profile refresh always comes first: authenticator enrolment
    begins the moment the profile is complete.
    """
    if session is None:
        return
    session.pop(PROFILE_SETUP_FLAG, None)
    session.pop("profile_setup_gaps", None)
    session.pop(MFA_SETUP_FLAG, None)

    gaps = profile_gaps(user)
    if gaps:
        session[PROFILE_SETUP_FLAG] = True
        session["profile_setup_gaps"] = gaps
        return
    if mfa_outstanding(user):
        session[MFA_SETUP_FLAG] = True


def status_payload(user, request=None) -> dict:
    """Setup fields merged into the signed-in account payload."""
    session = getattr(request, "session", None)
    gaps = profile_gaps(user)
    return {
        "onboarding_required": onboarding_outstanding(user),
        "profile_setup_required": bool(session and session.get(PROFILE_SETUP_FLAG)),
        "profile_setup_gaps": gaps,
        "profile_setup_gap_labels": gap_labels(gaps),
        "profile_email_suffix": required_email_suffix(),
        "mfa_setup_required": bool(session and session.get(MFA_SETUP_FLAG)),
    }

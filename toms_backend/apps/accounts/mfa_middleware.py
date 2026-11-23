"""Keep a first-sign-in session inside its setup flow.

Two gates are enforced, in this order, and only for sessions that
:func:`apps.accounts.setup_gate.sync_session` has flagged:

``profile_setup_required``
    The account still owes the deployment profile refresh (new password,
    picture, signature, Gmail address). Only the profile, password, onboarding,
    authenticator-status and sign-out routes stay reachable.

``mfa_setup_required``
    The profile is complete but no authenticator has been enrolled yet. Only
    the authenticator setup routes, the session probe and sign-out stay
    reachable.

DRF token clients and ``force_authenticate`` test sessions never pass through
Django's session authentication, so they are unaffected.
"""

from __future__ import annotations

from django.http import JsonResponse

#: Reachable while the profile refresh is outstanding. ``auth/mfa/`` is included
#: so the setup page can report state, but finishing MFA never clears this gate:
#: ``sync_session`` recomputes both flags from the account itself.
_PROFILE_ALLOWED_PREFIXES = (
    "/api/auth/profile/",
    "/api/auth/password/",
    "/api/auth/onboarding/",
    "/api/auth/mfa/",
    "/api/auth/me/",
    "/api/auth/logout/",
    "/api/auth/csrf/",
    "/api/health/",
)

_ALLOWED_PREFIXES = (
    "/api/auth/mfa/",
    # Owning your own profile, its uploads, your password and a pending profile
    # refresh never require a second factor, and must stay fixable if a
    # picture or signature is missing later.
    "/api/auth/profile/",
    "/api/auth/password/",
    "/api/auth/onboarding/",
    "/api/auth/me/",
    "/api/auth/logout/",
    "/api/auth/csrf/",
    "/api/health/",
)


class MFAEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        session = getattr(request, "session", None)
        if (
            user is None
            or not getattr(user, "is_authenticated", False)
            or session is None
            or not request.path.startswith("/api/")
        ):
            return self.get_response(request)

        if session.get("profile_setup_required") and not request.path.startswith(
            _PROFILE_ALLOWED_PREFIXES
        ):
            return JsonResponse(
                {
                    "detail": (
                        "Finish your account setup - new password, profile picture, "
                        "signature and Google (Gmail) account - before using the "
                        "rest of TOWEMS."
                    ),
                    "code": "profile_setup_required",
                    "gaps": session.get("profile_setup_gaps", []),
                    "errors": {},
                },
                status=403,
            )

        if session.get("mfa_setup_required") and not request.path.startswith(
            _ALLOWED_PREFIXES
        ):
            return JsonResponse(
                {
                    "detail": (
                        "Set up Google Authenticator before using the rest of TOWEMS."
                    ),
                    "code": "mfa_setup_required",
                    "errors": {},
                },
                status=403,
            )
        return self.get_response(request)

"""Explicit CSRF enforcement for endpoints reachable while anonymous.

DRF's ``APIView`` is exempt from Django's CSRF middleware, and
``SessionAuthentication`` only enforces CSRF once a session user exists. That
leaves login (and password change for an expired session) unprotected unless
we check explicitly - which is what this helper does, using the same
``CSRFCheck`` machinery DRF itself uses.
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework import exceptions
from rest_framework.authentication import CSRFCheck


def enforce_csrf(request) -> None:
    """Raise ``PermissionDenied`` when the request lacks a valid CSRF token."""
    check = CSRFCheck(lambda _request: HttpResponse())
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise exceptions.PermissionDenied(f"CSRF verification failed: {reason}")

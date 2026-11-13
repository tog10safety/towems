"""Consistent error envelope for every TOWEMS API response."""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def _first_message(detail) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        for value in detail.values():
            message = _first_message(value)
            if message:
                return message
        return ""
    if isinstance(detail, (list, tuple)):
        for item in detail:
            message = _first_message(item)
            if message:
                return message
        return ""
    return str(detail)


def _audit_denial(request, response) -> None:
    """Record refused requests (spec §12A) without capturing any secret.

    Only the method, path, status and the *attempting* account are stored. Query
    strings, headers, bodies, passwords and tokens are deliberately excluded.
    """
    try:
        from apps.core.audit import log_action

        user = getattr(request, "user", None)
        actor = user if getattr(user, "is_authenticated", False) else None
        log_action(
            actor=actor,
            action="access_denied" if response.status_code == 403 else "authentication_failed",
            entity_type="security",
            entity_id="",
            note=f"{request.method} {request.path} -> {response.status_code}",
            request=request,
        )
    except Exception:  # pragma: no cover - auditing must never break a response
        logger.exception("Could not record a refused request")


def toms_exception_handler(exc, context):
    """Return ``{"detail": ..., "errors": {...}}`` for every error.

    DRF's default handler is still used first so status codes and headers
    (e.g. ``WWW-Authenticate``) are preserved; only the body is normalised.
    """
    response = exception_handler(exc, context)
    request = context.get("request") if context else None
    if response is not None and request is not None and response.status_code in (401, 403):
        _audit_denial(request, response)

    if response is None:
        if isinstance(exc, Http404):
            return Response({"detail": "Not found.", "errors": {}}, status=404)
        if isinstance(exc, PermissionDenied):
            return Response({"detail": "Permission denied.", "errors": {}}, status=403)
        logger.exception("Unhandled API exception")
        return Response(
            {"detail": "An unexpected server error occurred.", "errors": {}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    detail = response.data
    message = _first_message(detail) or "Request failed."
    errors = detail if isinstance(detail, dict) else {"non_field_errors": detail}
    response.data = {"detail": message, "errors": errors}
    return response

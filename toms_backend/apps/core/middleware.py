"""Attach a correlation id to each request for audit traceability."""

from __future__ import annotations

import uuid


class RequestAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        request.correlation_id = incoming[:64] or uuid.uuid4().hex
        response = self.get_response(request)
        response["X-Request-ID"] = request.correlation_id
        return response

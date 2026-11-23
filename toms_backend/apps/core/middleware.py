"""Request correlation and security response headers."""

from __future__ import annotations

import uuid

from django.conf import settings

from apps.core.csp import build_policy


class ContentSecurityPolicyMiddleware:
    """Attach the configured CSP to every response.

    Report-only is the default for the rollout. The same policy becomes
    enforcing only when ``CSP_REPORT_ONLY`` is explicitly disabled.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        header = (
            "Content-Security-Policy-Report-Only"
            if getattr(settings, "CSP_REPORT_ONLY", True)
            else "Content-Security-Policy"
        )
        response[header] = build_policy()
        return response


class RequestAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        request.correlation_id = incoming[:64] or uuid.uuid4().hex
        response = self.get_response(request)
        response["X-Request-ID"] = request.correlation_id
        return response

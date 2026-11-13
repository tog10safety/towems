"""Post-mission feedback endpoints.

Tagged personnel see what is waiting on them; the Unit Safety Officer audits the
queue. The rules live in :mod:`apps.ops.post_mission_feedback`; these views only
shape requests and responses.
"""

from __future__ import annotations

from django.utils.dateparse import parse_date
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.ops import post_mission_feedback as service


def _require_audit(request):
    if not service.can_audit(request.user):
        raise PermissionDenied(
            "Only the Unit Safety Officer or Administrator may audit post-mission feedback."
        )


def _fail(exc):
    payload = {"detail": str(exc)}
    if getattr(exc, "errors", None):
        payload["errors"] = exc.errors
    raise ValidationError(payload) from exc


class FeedbackRequestsView(APIView):
    """What this person owes, and what they have already filed."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        body = service.requests_for(request.user)
        body["can_audit"] = service.can_audit(request.user)
        body["requested_count"] = len(body["requested"])
        return Response(body)


class FeedbackSubmitView(APIView):
    """File feedback for one assessment."""

    permission_classes = [IsAuthenticated]

    def post(self, request, mrm_id):
        try:
            row = service.submit(request.user, mrm_id, request.data)
        except service.FeedbackError as exc:
            _fail(exc)
        return Response(
            service.feedback_payload(row, viewer=request.user), status=201
        )


class FeedbackAuditView(APIView):
    """The officer's queue, scoped to the units they audit."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_audit(request)
        params = request.query_params
        try:
            queryset = service.audit_queryset(
                request.user,
                unit=params.get("unit"),
                status=(params.get("status") or "").strip(),
                date_from=parse_date(params.get("date_from") or "") or None,
                date_to=parse_date(params.get("date_to") or "") or None,
                q=(params.get("q") or "").strip(),
            )
        except service.FeedbackError as exc:
            _fail(exc)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request)
        response = paginator.get_paginated_response(
            [service.feedback_payload(row, viewer=request.user) for row in page]
        )
        response.data["summary"] = service.summary(request.user)
        response.data["statuses"] = [
            {"value": value, "label": label}
            for value, label in service.STATUS_LABELS.items()
        ]
        return response


class FeedbackReviewView(APIView):
    """Record the officer's reading of one feedback record."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        _require_audit(request)
        try:
            row = service.review(request.user, pk, request.data)
        except service.FeedbackError as exc:
            _fail(exc)
        return Response(service.feedback_payload(row, viewer=request.user))


class FeedbackAdoptView(APIView):
    """Turn a recommended hazard into a registry row."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        _require_audit(request)
        try:
            body = service.adopt_into_registry(request.user, pk, request.data)
        except service.FeedbackError as exc:
            _fail(exc)
        return Response(body, status=201)

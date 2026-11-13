"""Hazard library endpoints (the legacy ``hazard_registry`` table).

Kept out of ``views.py`` because the module is already the whole operations API;
these four resources are self-contained and every rule lives in
:mod:`apps.ops.hazards`. The view layer only parses request parameters, applies
pagination and translates a service error into an HTTP status.
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import timeutils
from apps.core.pagination import StandardPagination
from apps.ops import hazards, umrr_pdf


def _require_view(request):
    if not hazards.can_view(request.user):
        raise PermissionDenied("You do not have access to the hazard library.")


def _require_manage(request):
    if not hazards.can_manage(request.user):
        raise PermissionDenied(
            "Only the Unit Safety Officer or Administrator may change the hazard library."
        )


def _fail(exc):
    """Translate a service error into the API's error shape."""
    if isinstance(exc, hazards.HazardImportError):
        raise ValidationError({"detail": str(exc), "errors": exc.errors}) from exc
    raise ValidationError({"detail": str(exc)}) from exc


class HazardListView(APIView):
    """GET the filtered registry, POST a new hazard."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_view(request)
        params = request.query_params
        try:
            queryset = hazards.registry_queryset(
                request.user,
                unit=params.get("unit"),
                activity_code=(params.get("activity_code") or "").strip(),
                q=(params.get("q") or "").strip(),
            )
        except hazards.HazardError as exc:
            _fail(exc)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request)
        response = paginator.get_paginated_response(
            [hazards.hazard_payload(row) for row in page]
        )
        # The page header and its filter menus come from the same scope as the
        # rows, so a count can never describe something the caller cannot open.
        response.data["summary"] = hazards.summary(request.user)
        response.data["activity_codes"] = hazards.activity_codes(
            request.user, unit=params.get("unit")
        )
        response.data["can_manage"] = hazards.can_manage(request.user)
        return response

    def post(self, request):
        _require_manage(request)
        try:
            row = hazards.create_hazard(request.user, request.data)
        except (hazards.HazardError, hazards.HazardImportError) as exc:
            _fail(exc)
        return Response(hazards.hazard_payload(row), status=201)


class HazardDetailView(APIView):
    """Read, edit or remove one hazard."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        _require_view(request)
        queryset = hazards.registry_queryset(request.user, include_usage=True)
        row = queryset.filter(pk=pk).first()
        if row is None:
            raise PermissionDenied("That hazard does not exist in your scope.")
        return Response(hazards.hazard_payload(row))

    def patch(self, request, pk):
        return self._write(request, pk, partial=True)

    def put(self, request, pk):
        return self._write(request, pk, partial=False)

    def delete(self, request, pk):
        # Erasing a hazard an assessment uses is the Super Administrator's
        # override; everyone else keeps the library rule (unused rows only).
        from apps.accounts.ownership import can_delete_records

        force = can_delete_records(request.user)
        if not force:
            _require_manage(request)
        try:
            return Response(hazards.delete_hazard(request.user, pk, force=force))
        except (hazards.HazardError, hazards.HazardImportError) as exc:
            _fail(exc)

    def _write(self, request, pk, *, partial):
        _require_manage(request)
        try:
            row = hazards.update_hazard(request.user, pk, request.data, partial=partial)
        except (hazards.HazardError, hazards.HazardImportError) as exc:
            _fail(exc)
        return Response(hazards.hazard_payload(row))


class HazardTemplateView(APIView):
    """The downloadable .xlsx import template.

    Its Unit column is a dropdown of the units this caller may import into, so
    the workbook can only name a unit TOWEMS already records.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_view(request)
        response = HttpResponse(
            hazards.build_template(request.user),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = (
            'attachment; filename="TOWEMS-hazard-registry-template.xlsx"'
        )
        return response


class HazardExportView(APIView):
    """The caller's current selection as a workbook they can re-import.

    Export belongs to the two roles that may import a workbook - the Unit
    Safety Officer and the Administrator - because it exists to be edited and
    sent back. Command and operations posts read the same rows on screen.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_manage(request)
        params = request.query_params
        try:
            rows = hazards.registry_queryset(
                request.user,
                unit=params.get("unit"),
                activity_code=(params.get("activity_code") or "").strip(),
                q=(params.get("q") or "").strip(),
                include_usage=False,
            )
        except hazards.HazardError as exc:
            _fail(exc)

        response = HttpResponse(
            hazards.export_workbook(rows, request.user),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = (
            'attachment; filename="TOWEMS-hazard-registry.xlsx"'
        )
        return response


class HazardUMRRView(APIView):
    """The Unit Mission Risk Registry as a PDF.

    ``?activities=A1,B2`` prints those activity codes in full, one form per
    code. ``?ids=1,2`` keeps its narrower meaning of the named hazards, and
    without either the whole library of the chosen unit is printed. Forms are
    grouped by activity code, so a registry never mixes codes in one table.
    Only the assessed (before) risk is printed, matching the unit's UMRR form.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_view(request)
        params = request.query_params
        try:
            queryset = hazards.registry_queryset(
                request.user,
                unit=params.get("unit"),
                activity_code=(params.get("activity_code") or "").strip(),
                q=(params.get("q") or "").strip(),
                include_usage=False,
            )
        except hazards.HazardError as exc:
            _fail(exc)

        raw_ids = (params.get("ids") or "").strip()
        selected_ids: list[int] = []
        if raw_ids:
            try:
                selected_ids = [
                    int(part) for part in raw_ids.split(",") if part.strip()
                ]
            except ValueError:
                raise ValidationError(
                    {"ids": "Hazard ids must be whole numbers."}
                ) from None
            queryset = queryset.filter(pk__in=selected_ids)

        raw_activities = (params.get("activities") or "").strip()
        selected_activities = [
            part.strip() for part in raw_activities.split(",") if part.strip()
        ]
        if selected_activities:
            queryset = queryset.filter(activity_code__in=selected_activities)

        rows = list(queryset.select_related("unit_id"))
        # A missing id is either out of scope or absent; answer both the same
        # way so the endpoint cannot be used to probe another unit's library.
        if selected_ids and len(rows) != len(set(selected_ids)):
            raise PermissionDenied(
                "Some selected hazards are outside your authorised scope."
            )
        if selected_activities:
            found = {row.activity_code for row in rows}
            if set(selected_activities) - found:
                raise PermissionDenied(
                    "Some selected activity codes are outside your authorised scope."
                )
        if not rows:
            raise ValidationError({"detail": "No hazards match that selection."})

        from apps.legacy.models import Unit

        unit = None
        if params.get("unit"):
            unit = Unit.objects.filter(pk=params.get("unit")).first()
        if unit is None:
            # One unit's rows carry that unit's letterhead. A selection that
            # spans units belongs to no single unit, so it keeps the PAF header.
            unit_ids = {row.unit_id_id for row in rows}
            unit = rows[0].unit_id if len(unit_ids) == 1 else None

        unit_label = getattr(unit, "acronym", "") or getattr(unit, "name", "") or "Unit"
        reviewed_by = f"Safety Office, {unit_label}" if unit else "Safety Office"
        content = umrr_pdf.generate_umrr_pdf(
            rows,
            unit=unit,
            reviewed_by=reviewed_by,
            generated_at=timeutils.now(),
        )
        response = HttpResponse(content, content_type="application/pdf")
        if unit is None:
            unit_label = "All Units"
        safe_label = "".join(
            char for char in unit_label if char.isalnum() or char in "-_ "
        ).strip().replace(" ", "_") or "Unit"
        response["Content-Disposition"] = (
            f'inline; filename="UMRR_{safe_label}.pdf"'
        )
        response["Cache-Control"] = "no-store"
        return response


class HazardImportView(APIView):
    """Bulk import from the template; all rows or none."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        _require_manage(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "Choose the .xlsx workbook to import."})
        try:
            result = hazards.import_workbook(request.user, upload)
        except (hazards.HazardError, hazards.HazardImportError) as exc:
            _fail(exc)
        return Response(
            {
                "created": result["created"],
                "rows": result["rows"],
                "detail": f"Imported {result['created']} hazard(s).",
            },
            status=201,
        )

"""Inventory endpoints that do not come from a router viewset.

Three of these are the Logistics Officer's spreadsheet workflow (template,
export, import) and the fourth is the statistics block above the register. They
are kept together because they all read the *same* scoped queryset the register
shows: a figure or a workbook must never describe something the caller cannot
open on screen.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import timeutils
from apps.core.scoping import unit_ids_for_filter
from apps.ops import inventory_excel
from apps.ops.models import InventoryCategory, InventoryItem, InventoryTransaction

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: An item is "low" at or below this many available - the same threshold the
#: dashboard uses, so the two pages never disagree.
LOW_STOCK_THRESHOLD = 5


def scoped_items(user):
    """Every inventory row this caller may see, before request filters."""
    queryset = InventoryItem.objects.select_related("category", "unit", "custodian")
    unit_ids = unit_ids_for_filter(user)
    if unit_ids is not None:
        queryset = queryset.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
    if not inventory_excel.can_view_sensitive(user):
        queryset = queryset.filter(is_sensitive=False, category__is_sensitive=False)
    return queryset


def filtered_items(request):
    """``scoped_items`` narrowed by the register's own filters."""
    queryset = scoped_items(request.user)
    params = request.query_params
    kind = (params.get("kind") or "").strip()
    if kind:
        queryset = queryset.filter(category__kind=kind)
    category = (params.get("category") or "").strip()
    if category.isdigit():
        queryset = queryset.filter(category_id=int(category))
    condition = (params.get("serviceability") or "").strip()
    if condition:
        queryset = queryset.filter(serviceability=condition)
    unit = (params.get("unit") or "").strip()
    if unit.isdigit():
        queryset = queryset.filter(unit_id=int(unit))
    search = (params.get("search") or "").strip()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(asset_tag__icontains=search)
            | Q(serial_number__icontains=search)
            | Q(description__icontains=search)
        )
    return queryset


def _require_manage(user) -> None:
    if not inventory_excel.can_manage(user):
        raise PermissionDenied(
            "Only the Logistics Officer or an Administrator may import or export inventory."
        )


def _statistics(user) -> dict:
    """Aggregates for the register's header tiles and breakdown tables."""
    items = scoped_items(user)
    totals = items.aggregate(
        count=Count("id"),
        quantity_total=Sum("quantity_total"),
        quantity_available=Sum("quantity_available"),
    )
    by_class_rows = {
        row["category__kind"]: row
        for row in items.values("category__kind").annotate(
            items=Count("id"),
            quantity_available=Sum("quantity_available"),
            quantity_total=Sum("quantity_total"),
        )
    }
    # Every class is listed, including the empty ones, so the breakdown does not
    # silently drop a class the unit has not stocked yet.
    by_class = [
        {
            "kind": key,
            "label": label,
            "items": by_class_rows.get(key, {}).get("items", 0),
            "quantity_available": by_class_rows.get(key, {}).get("quantity_available") or 0,
            "quantity_total": by_class_rows.get(key, {}).get("quantity_total") or 0,
        }
        for key, label in InventoryCategory.KIND_CHOICES
    ]

    condition_labels = dict(InventoryItem.SERVICEABILITY_CHOICES)
    by_condition = [
        {
            "condition": row["serviceability"],
            "label": condition_labels.get(row["serviceability"], row["serviceability"]),
            "items": row["items"],
        }
        for row in items.values("serviceability")
        .annotate(items=Count("id"))
        .order_by("serviceability")
    ]

    by_unit = []
    for row in (
        items.exclude(unit__isnull=True)
        .values("unit__name", "unit__acronym")
        .annotate(items=Count("id"))
        .order_by("-items", "unit__name")[:8]
    ):
        by_unit.append(
            {
                "unit": row["unit__name"],
                "acronym": row["unit__acronym"] or "",
                "items": row["items"],
            }
        )

    since = timeutils.now() - timedelta(days=30)
    movements = InventoryTransaction.objects.filter(
        item_id__in=items.values("id"), created_at__gte=since
    ).count()

    return {
        "items": totals["count"] or 0,
        "quantity_total": totals["quantity_total"] or 0,
        "quantity_available": totals["quantity_available"] or 0,
        "controlled": items.filter(Q(is_sensitive=True) | Q(category__is_sensitive=True)).count(),
        "low_stock": items.filter(quantity_available__lte=LOW_STOCK_THRESHOLD).count(),
        "out_of_stock": items.filter(quantity_available=0).count(),
        "unserviceable": items.filter(serviceability="unserviceable").count(),
        "under_maintenance": items.filter(serviceability="maintenance").count(),
        "movements_30d": movements,
        "low_stock_threshold": LOW_STOCK_THRESHOLD,
        "by_class": by_class,
        "by_condition": by_condition,
        "by_unit": by_unit,
        "can_manage": inventory_excel.can_manage(user),
        "can_view_sensitive": inventory_excel.can_view_sensitive(user),
    }


class InventoryStatsView(APIView):
    """Aggregates for the register's statistics block."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_statistics(request.user))


class InventoryTemplateView(APIView):
    """The downloadable import template, with every picker in place."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_manage(request.user)
        response = HttpResponse(
            inventory_excel.build_template(request.user), content_type=XLSX_MIME
        )
        response["Content-Disposition"] = (
            'attachment; filename="TOWEMS-inventory-template.xlsx"'
        )
        return response


class InventoryExportView(APIView):
    """The current selection as a workbook that can be edited and re-imported."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_manage(request.user)
        rows = filtered_items(request).order_by("category__kind", "name")
        response = HttpResponse(
            inventory_excel.export_workbook(rows, request.user), content_type=XLSX_MIME
        )
        response["Content-Disposition"] = 'attachment; filename="TOWEMS-inventory-register.xlsx"'
        return response


class InventoryImportView(APIView):
    """Bulk import from the template; all rows or none."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        _require_manage(request.user)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "Choose the .xlsx workbook to import."})
        try:
            result = inventory_excel.import_workbook(request.user, upload)
        except inventory_excel.InventoryImportError as exc:
            raise ValidationError({"detail": str(exc), "errors": exc.errors}) from exc
        return Response(
            {
                "created": result["created"],
                "rows": result["rows"],
                "detail": (
                    f"Imported {result['created']} item(s). Stock on hand was opened "
                    "as a receipt in the ledger."
                ),
                "stats": _statistics(request.user),
            },
            status=201,
        )

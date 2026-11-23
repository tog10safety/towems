"""Inventory endpoints that do not come from a router viewset.

Three of these are the Logistics Officer's spreadsheet workflow (template,
export, import) and the fourth is the statistics block above the register. They
are kept together because they all read the *same* scoped queryset the register
shows: a figure or a workbook must never describe something the caller cannot
open on screen.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.command.models import UnitMapMarker
from apps.core import timeutils
from apps.core.geo import point_in_geometry
from apps.core.scoping import unit_ids_for_filter
from apps.ops import inventory_excel
from apps.ops.models import (
    Airfield,
    AreaOfResponsibility,
    InventoryCategory,
    InventoryItem,
    InventoryTransaction,
    OperationalMapFeature,
)

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


def kind_values(params) -> list[str]:
    """The asset classes a request names; a section tab may name two of them.

    The Armory tab is firearms and ammunition, so ``kind`` is a comma list as
    well as a single value.
    """
    return [value.strip() for value in (params.get("kind") or "").split(",") if value.strip()]


def filtered_items(request):
    """``scoped_items`` narrowed by the register's own filters."""
    queryset = scoped_items(request.user)
    params = request.query_params
    # The export reads this same filter, so a tab exports what it lists.
    kinds = kind_values(params)
    if kinds:
        queryset = queryset.filter(category__kind__in=kinds)
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


#: A bulk store records where it sits as "Name (CODE)" - the string the airport
#: picker writes. The code in brackets is what pins the tank on the map.
LOCATION_CODE = re.compile(r"\(([A-Za-z0-9]{3,10})\)\s*$")

#: The two ways Jet A1 is held. Both are always reported, including when one
#: of them is empty, so a tank that has run out is visible rather than absent.
FUEL_FORMS = (
    ("drum", "In drums"),
    ("bulk", "In bulk"),
)

#: The aviation fuel the operational map pins: Jet A1 and AVGas. Automotive
#: fuel, lubricants, greases, hydraulic fluids and solvents are POL stock too,
#: but they are not what "fuel locations" means on a flight picture.
POL_AVIATION_FUEL = "POL-01"


def _fuel_stock_position(item, airfields, unit_markers):
    """Where one store sits.

    Its own moved-to point wins, then the airfield its location names, then the
    unit's map marker. Returns (latitude, longitude, source, label, airfield);
    the coordinates are None when the store has no position at all.
    """
    location = item.storage_location or ""
    code_match = LOCATION_CODE.search(location)
    airfield = airfields.get(code_match.group(1).upper()) if code_match else None
    marker = unit_markers.get(item.unit_id) if item.unit_id else None
    if item.location_latitude is not None and item.location_longitude is not None:
        return item.location_latitude, item.location_longitude, "stored", location, airfield
    if airfield is not None:
        return airfield.latitude, airfield.longitude, "airfield", airfield.name, airfield
    if marker is not None and marker.latitude is not None and marker.longitude is not None:
        return (
            marker.latitude,
            marker.longitude,
            "unit",
            marker.place_name or (item.unit.acronym if item.unit_id else "Unit location"),
            airfield,
        )
    return None, None, "", "", airfield


def _fuel_summary(items, since) -> dict:
    """POL monitoring for the register, in the units stock is actually held in.

    Drums are counted as drums and converted with that item's own capacity, so a
    drum type stocked at 200 L and another at 20 L both report litres without
    being mixed up. Bulk stock is already in litres. Every row filed under a
    POL category counts - Jet A1, AVGas, diesel, oils and greases alike - so a
    lubricant never drops out of the panel just because it has no grade.
    """
    fuel_items = (
        items.filter(category__kind="fuel")
        .select_related("unit", "category")
        .order_by("category__code", "fuel_form", "name")
    )
    # Airfields are shared reference data and there are few of them, so one
    # query resolves every tank's pin instead of a lookup per row.
    airfields = {row.code.upper(): row for row in Airfield.objects.all()}
    # A store that is not on an airfield (a unit's POL dump) still has a place:
    # the unit's own map marker. Without it the pin has nowhere to sit.
    unit_markers = {
        row.unit_id: row
        for row in UnitMapMarker.objects.filter(
            unit_id__in={item.unit_id for item in fuel_items if item.unit_id}
        )
    }
    buckets = {
        key: {
            "form": key,
            "label": label,
            "items": 0,
            "units_available": 0,
            "units_total": 0,
            "litres_available": 0,
            "litres_total": 0,
            "movements_30d": 0,
        }
        for key, label in FUEL_FORMS
    }
    movements = {
        row["item_id"]: row["total"]
        for row in InventoryTransaction.objects.filter(
            item__in=fuel_items, created_at__gte=since
        )
        .values("item_id")
        .annotate(total=Count("id"))
    }

    rows: list[dict] = []
    litres_available = 0
    litres_total = 0
    movement_count = 0
    for item in fuel_items:
        item_movements = movements.get(item.id, 0)
        movement_count += item_movements
        litres_available += item.litres(item.quantity_available)
        litres_total += item.litres(item.quantity_total)
        # Stock that has been moved carries the destination's own point, so it
        # is pinned there rather than at the airfield its old code named or at
        # the marker of the unit that still owns it.
        (
            latitude,
            longitude,
            position_source,
            position_label,
            airfield,
        ) = _fuel_stock_position(item, airfields, unit_markers)
        bucket = buckets.get(item.fuel_form)
        if bucket is not None:
            bucket["items"] += 1
            bucket["units_available"] += int(item.quantity_available or 0)
            bucket["units_total"] += int(item.quantity_total or 0)
            bucket["litres_available"] += item.litres(item.quantity_available)
            bucket["litres_total"] += item.litres(item.quantity_total)
            bucket["movements_30d"] += item_movements
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "category_code": item.category.code,
                "category_name": item.category.name,
                "grade": item.fuel_grade,
                "grade_label": item.get_fuel_grade_display() if item.fuel_grade else "",
                "form": item.fuel_form,
                "form_label": item.get_fuel_form_display(),
                # The batch dates travel with the row so the map can show that
                # two drums of the same grade do not expire on the same day.
                "date_issued": item.date_issued.isoformat() if item.date_issued else None,
                "testing_date": item.testing_date.isoformat() if item.testing_date else None,
                "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                "da_number": item.da_number or "",
                "unit_of_measure": item.unit_of_measure,
                "drum_capacity_litres": item.drum_capacity_litres,
                "quantity_available": item.quantity_available,
                "quantity_total": item.quantity_total,
                "litres_available": item.litres(item.quantity_available),
                "litres_total": item.litres(item.quantity_total),
                "storage_location": item.storage_location,
                # Set only when the store's recorded location names an airfield,
                # which is what the map pins. A drum park has no pin.
                "airport_code": airfield.code if airfield else "",
                "airport_name": airfield.name if airfield else "",
                "latitude": latitude,
                "longitude": longitude,
                # "airfield", "unit" (the unit's map marker) or "" when the
                # store has no position at all.
                "position_source": position_source,
                "position_label": position_label,
                "unit_acronym": item.unit.acronym if item.unit_id else "",
                "serviceability": item.serviceability,
                "movements_30d": item_movements,
            }
        )

    return {
        "grade": "pol",
        "label": "POL",
        "items": len(rows),
        "litres_available": litres_available,
        "litres_total": litres_total,
        "movements_30d": movement_count,
        "by_form": [buckets[key] for key, _ in FUEL_FORMS],
        "rows": rows,
    }


def fuel_positions(scope_ids):
    """POL stock pins for the operational picture.

    The scope is the caller's unit ids, so a store never appears outside the
    map's authorised picture. Position resolution is the register's own rule.
    A batch that holds nothing is not a place on the map. Returns
    ``(rows, count_without_position)``.
    """
    items = (
        InventoryItem.objects.filter(
            category__code=POL_AVIATION_FUEL, unit_id__in=scope_ids
        )
        .select_related("unit", "category")
        .order_by("category__code", "name")
    )
    airfields = {row.code.upper(): row for row in Airfield.objects.all()}
    unit_markers = {
        row.unit_id: row
        for row in UnitMapMarker.objects.filter(
            unit_id__in={item.unit_id for item in items if item.unit_id}
        )
    }

    rows = []
    without_position = 0
    for item in items:
        if (item.quantity_total or 0) <= 0 and (item.quantity_available or 0) <= 0:
            continue
        latitude, longitude, source, label, airfield = _fuel_stock_position(
            item, airfields, unit_markers
        )
        if latitude is None or longitude is None:
            without_position += 1
            continue
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "category_code": item.category.code,
                "category_name": item.category.name,
                "grade": item.fuel_grade,
                "grade_label": item.get_fuel_grade_display() if item.fuel_grade else "",
                "form": item.fuel_form,
                "form_label": item.get_fuel_form_display(),
                "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                "quantity_available": item.quantity_available,
                "quantity_total": item.quantity_total,
                "litres_available": item.litres(item.quantity_available),
                "storage_location": item.storage_location,
                "airport_code": airfield.code if airfield else "",
                "latitude": latitude,
                "longitude": longitude,
                "position_source": source,
                "position_label": label,
                "unit_id": item.unit_id,
                "unit_acronym": item.unit.acronym if item.unit_id else "",
            }
        )
    return rows, without_position


def _statistics(user, kinds=None) -> dict:
    """Aggregates for the register's header tiles and breakdown tables.

    ``kinds`` narrows the figures to one section of the register, so each tab
    monitors its own shelf: the Armory never counts materiel and POL never
    counts firearms. Without it the whole scoped store is reported.
    """
    kinds = list(kinds or [])
    items = scoped_items(user)
    if kinds:
        items = items.filter(category__kind__in=kinds)
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
    # A scoped tab lists only its own classes; the whole store lists every
    # class, including the empty ones, so the breakdown does not silently drop
    # a class the unit has not stocked yet.
    class_rows = (
        [(key, label) for key, label in InventoryCategory.KIND_CHOICES if key in kinds]
        if kinds
        else list(InventoryCategory.KIND_CHOICES)
    )
    by_class = [
        {
            "kind": key,
            "label": label,
            "items": by_class_rows.get(key, {}).get("items", 0),
            "quantity_available": by_class_rows.get(key, {}).get("quantity_available") or 0,
            "quantity_total": by_class_rows.get(key, {}).get("quantity_total") or 0,
        }
        for key, label in class_rows
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
        "fuel": _fuel_summary(items, since),
        "low_stock_threshold": LOW_STOCK_THRESHOLD,
        "by_class": by_class,
        "by_condition": by_condition,
        "by_unit": by_unit,
        "can_manage": inventory_excel.can_manage(user),
        "can_view_sensitive": inventory_excel.can_view_sensitive(user),
    }


def _unit_airfields(user) -> tuple[list[Airfield], bool]:
    """Active airfields inside the caller's own AOR, and whether AOR-limited.

    Bulk fuel is held on an airfield, and only the airfields inside the unit's
    own area of responsibility are offered. While a unit has no AOR recorded the
    full register comes back with ``aor_scoped`` false, so stocking bulk fuel is
    still possible instead of the form blocking on missing geography.
    """
    airfields = list(Airfield.objects.filter(is_active=True).order_by("name"))
    unit = getattr(user, "unit", None)
    if unit is None:
        return airfields, False
    polygons = [
        geometry
        for geometry in AreaOfResponsibility.objects.filter(
            unit=unit, is_active=True
        ).values_list("geometry", flat=True)
        if geometry
    ]
    if not polygons:
        return airfields, False
    inside = [
        row
        for row in airfields
        if any(
            point_in_geometry(row.longitude, row.latitude, geometry)
            for geometry in polygons
        )
    ]
    return inside, True


class FuelLocationView(APIView):
    """The airports a bulk Jet A1 store may be sited on: the unit's own AOR."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        airfields, scoped = _unit_airfields(request.user)
        search = (request.query_params.get("search") or "").strip().lower()
        if search:
            airfields = [
                row
                for row in airfields
                if search
                in " ".join(
                    [row.name, row.code, row.municipality, row.province]
                ).lower()
            ]
        unit = getattr(request.user, "unit", None)
        results = [
            {
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "kind": row.kind,
                "kind_label": row.get_kind_display(),
                "municipality": row.municipality,
                "province": row.province,
                "latitude": row.latitude,
                "longitude": row.longitude,
                # What the register records as the store's location.
                "display": f"{row.name} ({row.code})",
            }
            for row in airfields
        ]
        return Response(
            {
                "aor_scoped": scoped,
                "unit": (unit.acronym or unit.name) if unit is not None else "",
                "count": len(results),
                "results": results,
            }
        )


#: The kinds of place the operational map knows, in the order the transfer
#: picker groups them. Nothing else is offered: fuel is sent somewhere the map
#: can draw and the Wing answers for.
TRANSFER_TYPES = ("Friendly troops", "Airport", "Air strip", "Unit")


def _mother_wing(unit):
    """The Wing the caller's unit belongs to, or the unit when it is a Wing.

    A transfer is an act inside the Wing's area, so the Wing - not whatever
    group or squadron the officer is posted to - sets the boundary.
    """
    node, seen = unit, set()
    while node is not None and node.id not in seen:
        if node.category == "Wing":
            return node
        seen.add(node.id)
        node = node.parent
    return unit


def _wing_coverage(user) -> tuple[list[dict], object, bool]:
    """The polygons the mother Wing covers, the Wing, and whether scoped.

    The Wing's own AORs are joined by those of its subordinate groups and
    squadrons: an airfield inside a group's area is inside the Wing's coverage.
    A Wing with no AOR recorded anywhere returns no polygons, so the caller
    falls back to the whole register rather than blocking on missing geography.
    """
    from apps.command.services import descendant_ids

    unit = getattr(user, "unit", None)
    if unit is None:
        return [], None, False
    mother = _mother_wing(unit)
    polygons = [
        geometry
        for geometry in AreaOfResponsibility.objects.filter(
            unit_id__in=descendant_ids(mother.id), is_active=True
        ).values_list("geometry", flat=True)
        if geometry
    ]
    return polygons, mother, bool(polygons)


def _point_geometry(geometry) -> tuple[float, float] | None:
    """The (latitude, longitude) of a plotted Point, or ``None``."""
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return None
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2:
        return None
    try:
        return float(coordinates[1]), float(coordinates[0])
    except (TypeError, ValueError):
        return None


def _transfer_destinations(user) -> tuple[list[dict], object, bool]:
    """The places drums may be sent, the mother Wing, and whether scoped.

    Only the places the operational map knows are offered - friendly troops,
    airports, air strips and units - and only those the Wing covers. The
    transfer endpoint resolves its destination through this same list, so the
    picker's filter and the server's check can never disagree about what lies
    inside the Wing's area.
    """
    polygons, mother, scoped = _wing_coverage(user)

    def covered(latitude, longitude) -> bool:
        if latitude is None or longitude is None:
            return False
        if not scoped:
            return True
        return any(
            point_in_geometry(longitude, latitude, geometry)
            for geometry in polygons
        )

    rows: list[dict] = []
    for feature in OperationalMapFeature.objects.filter(
        feature_type=OperationalMapFeature.TYPE_FRIENDLY_TROOP
    ).order_by("name"):
        point = _point_geometry(feature.geometry)
        if point is None or not covered(*point):
            continue
        rows.append(
            {
                "id": f"feature-{feature.id}",
                "label": feature.name,
                "type": "Friendly troops",
                "latitude": point[0],
                "longitude": point[1],
            }
        )
    for airfield in Airfield.objects.filter(is_active=True).order_by("name"):
        if not covered(airfield.latitude, airfield.longitude):
            continue
        rows.append(
            {
                "id": f"airfield-{airfield.id}",
                "label": f"{airfield.name} ({airfield.code})",
                "type": (
                    "Air strip"
                    if airfield.kind == Airfield.KIND_AIRSTRIP
                    else "Airport"
                ),
                "latitude": airfield.latitude,
                "longitude": airfield.longitude,
            }
        )
    for marker in UnitMapMarker.objects.select_related("unit").filter(
        latitude__isnull=False, longitude__isnull=False
    ):
        if not covered(marker.latitude, marker.longitude):
            continue
        rows.append(
            {
                "id": f"unit-{marker.unit_id}",
                "label": marker.place_name or marker.unit.name,
                "type": "Unit",
                "latitude": marker.latitude,
                "longitude": marker.longitude,
            }
        )

    rows.sort(
        key=lambda row: (
            TRANSFER_TYPES.index(row["type"]),
            row["label"].lower(),
        )
    )
    return rows, mother, scoped


class FuelDestinationView(APIView):
    """Where drum stock may be transferred: inside the mother Wing's AOR.

    Only the places the operational map knows are offered - friendly troops,
    airports, air strips and units - and only those the Wing covers, so a store
    can never be sent to a field another Wing answers for.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows, mother, scoped = _transfer_destinations(request.user)
        return Response(
            {
                "aor_scoped": scoped,
                "mother_unit": (
                    (mother.acronym or mother.name) if mother is not None else ""
                ),
                "count": len(rows),
                "results": rows,
            }
        )


class FuelTransferView(APIView):
    """Move drum stock from one location to another.

    A transfer moves drums, not the register row: the source batch is drawn
    down and the same grade, expiry date and drum capacity is added to a batch
    standing at the destination, so both ends stay on the board and the map.
    The ledger records the movement against the source store. The destination
    must be one of the places the mother Wing covers, and its position is the
    one the operational map holds, so a crafted request cannot post drums
    outside the Wing's area or forge where they landed.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        _require_manage(request.user)
        destination = str(request.data.get("destination") or "").strip()
        if not destination:
            raise ValidationError({"destination": "Choose where the drums are going."})
        moves = request.data.get("moves") or []
        if not isinstance(moves, list) or not moves:
            raise ValidationError(
                {"moves": "Choose at least one drum batch to transfer."}
            )
        # The picker is a convenience, not the guard: the destination is
        # resolved against the same list the picker offers, and the position
        # stored is the one the operational map holds rather than the one the
        # request claims. Anything else is refused here, before any drum moves,
        # so a crafted body cannot reach a field the Wing does not cover.
        destinations, mother, scoped = _transfer_destinations(request.user)
        match = next(
            (row for row in destinations if row["label"] == destination), None
        )
        if match is None:
            wing = (
                (mother.acronym or mother.name) if mother is not None else "The unit"
            )
            raise ValidationError(
                {
                    "destination": (
                        f"{wing} does not cover that destination."
                        if scoped
                        else "That destination is not on the operational map."
                    )
                }
            )
        destination = match["label"]
        latitude = match["latitude"]
        longitude = match["longitude"]

        # The batches are fetched by id under the caller's own scope, so a
        # crafted request cannot move stock from a store it cannot see.
        visible_ids = list(scoped_items(request.user).values_list("id", flat=True))
        moved: list[dict] = []
        with transaction.atomic():
            for move in moves:
                try:
                    item_id = int(move.get("item_id"))
                    drums = int(move.get("drums"))
                except (AttributeError, TypeError, ValueError):
                    raise ValidationError(
                        {
                            "moves": (
                                "Each batch needs a store and a whole number of drums."
                            )
                        }
                    ) from None
                if drums <= 0:
                    continue
                item = (
                    InventoryItem.objects.select_for_update()
                    .filter(pk=item_id, pk__in=visible_ids)
                    .first()
                )
                if item is None:
                    raise ValidationError(
                        {"moves": "One of those batches is no longer in this register."}
                    )
                if item.category.kind != "fuel" or item.fuel_form != "drum":
                    raise ValidationError(
                        {"moves": f"{item.name} is not drum stock."}
                    )
                if drums > item.quantity_available:
                    raise ValidationError(
                        {
                            "moves": (
                                f"Only {item.quantity_available} drum(s) of "
                                f"{item.name} are available."
                            )
                        }
                    )

                item.quantity_available -= drums
                item.quantity_total = max(0, item.quantity_total - drums)
                item.save(update_fields=["quantity_available", "quantity_total"])

                # The destination batch is the same grade expiring on the same
                # day, so a merged batch never hides a shelf life.
                target = (
                    InventoryItem.objects.select_for_update()
                    .filter(
                        category=item.category,
                        fuel_form="drum",
                        fuel_grade=item.fuel_grade,
                        storage_location=destination,
                        expiry_date=item.expiry_date,
                        date_issued=item.date_issued,
                        drum_capacity_litres=item.drum_capacity_litres,
                        unit_id=item.unit_id,
                    )
                    .exclude(pk=item.pk)
                    .first()
                )
                if target is None:
                    target = InventoryItem.objects.create(
                        name=item.name,
                        description=item.description,
                        category=item.category,
                        unit_id=item.unit_id,
                        custodian_id=item.custodian_id,
                        storage_location=destination,
                        quantity_total=drums,
                        quantity_available=drums,
                        unit_of_measure=item.unit_of_measure,
                        fuel_grade=item.fuel_grade,
                        fuel_form="drum",
                        drum_capacity_litres=item.drum_capacity_litres,
                        date_issued=item.date_issued,
                        testing_date=item.testing_date,
                        expiry_date=item.expiry_date,
                        location_latitude=latitude,
                        location_longitude=longitude,
                        serviceability=item.serviceability,
                        is_sensitive=item.is_sensitive,
                    )
                else:
                    target.quantity_total += drums
                    target.quantity_available += drums
                    if latitude is not None and longitude is not None:
                        target.location_latitude = latitude
                        target.location_longitude = longitude
                    target.save()

                InventoryTransaction.objects.create(
                    item=item,
                    kind="transfer",
                    quantity=drums,
                    actor=request.user,
                    from_unit_id=item.unit_id,
                    to_unit_id=item.unit_id,
                    reference=destination,
                    notes=f"{drums} drum(s) transferred to {destination}.",
                )
                moved.append(
                    {
                        "from_item": item.id,
                        "to_item": target.id,
                        "drums": drums,
                        "grade": item.get_fuel_grade_display(),
                        "expiry_date": (
                            item.expiry_date.isoformat() if item.expiry_date else None
                        ),
                    }
                )

        total = sum(row["drums"] for row in moved)
        return Response(
            {
                "moved": moved,
                "destination": destination,
                "detail": f"Transferred {total} drum(s) to {destination}.",
                "stats": _statistics(request.user, ["fuel"]),
            },
            status=201,
        )


class InventoryStatsView(APIView):
    """Aggregates for the register's statistics block."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # ``?kind=fuel`` gives the POL tab its own cards; without it the figures
        # cover the whole store.
        return Response(_statistics(request.user, kind_values(request.query_params)))


class InventoryTemplateView(APIView):
    """The downloadable import template, with every picker in place."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_manage(request.user)
        # A template downloaded from a section tab offers only that section's
        # categories and classes, so the workbook matches the screen it came from.
        response = HttpResponse(
            inventory_excel.build_template(request.user, kind_values(request.query_params)),
            content_type=XLSX_MIME,
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
            # Rows must belong to the section the workbook was uploaded from.
            result = inventory_excel.import_workbook(
                request.user, upload, kind_values(request.query_params)
            )
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

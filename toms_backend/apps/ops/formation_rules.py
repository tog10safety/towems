"""Aircraft formation rules for OPCON units.

Only aircraft that are physically co-located may fly together as an Element.
This module is the single place that turns an air asset's recorded location
into coordinates, measures the distance between selected aircraft, and reports
whether a prepositioning mission is required.

The rule is enforced only when every aircraft in an element can be resolved to
an Air Asset *and* every one of those assets has a usable location. Recorded
data is never guessed: an unknown location is reported as unverified rather
than silently treated as co-located.
"""

from __future__ import annotations

import math
import re

from apps.core.mgrs import MGRSError, from_mgrs, validate_mgrs

#: Maximum separation, in kilometres, for aircraft in one Element.
COLOCATION_RADIUS_KM = 5.0

_EARTH_RADIUS_KM = 6371.0088
_MGRS_IN_TEXT = re.compile(
    r"\b\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z][A-HJ-NP-V]\d{2,10}\b",
    re.IGNORECASE,
)
#: Statuses that mean the airframe cannot be planned into a sortie at all.
UNAVAILABLE_STATUSES = frozenset({"maintenance", "grounded", "unserviceable"})


def mgrs_from_location(value: str) -> str:
    """Return the last valid MGRS grid embedded in a location string."""
    for candidate in reversed(_MGRS_IN_TEXT.findall(value or "")):
        try:
            return validate_mgrs(candidate)
        except MGRSError:
            continue
    return ""


def coordinates_from_location(value: str) -> tuple[float, float] | None:
    """Return ``(lat, lon)`` for a recorded location, or ``None``."""
    mgrs = mgrs_from_location(value)
    if not mgrs:
        return None
    try:
        return from_mgrs(mgrs)
    except MGRSError:
        return None


def distance_km(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    """Great-circle distance between two WGS84 points, in kilometres."""
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def normalize_place(value: str) -> str:
    """A comparison key for a named airfield or assembly location."""
    text = re.sub(r"\([^)]*\)", " ", value or "")
    text = re.sub(r"[^0-9a-z]+", " ", text.casefold())
    return " ".join(text.split())


def same_place(first: str, second: str) -> bool:
    """True when two location descriptions identify the same place.

    A shared MGRS grid is authoritative. Otherwise the names are compared
    after punctuation and parenthetical grid references are removed, allowing
    "HTOG 10" to match "HTOG 10, Brgy. Bancasi".
    """
    first_mgrs = mgrs_from_location(first)
    second_mgrs = mgrs_from_location(second)
    if first_mgrs and second_mgrs:
        return first_mgrs == second_mgrs
    left = normalize_place(first)
    right = normalize_place(second)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def asset_label(asset) -> str:
    if asset is None:
        return ""
    return " ".join(
        part for part in (asset.model, asset.tail_number) if part
    ).strip()


def _unique_assets(assets):
    seen: set[int] = set()
    rows = []
    for asset in assets:
        if asset is None or asset.pk in seen:
            continue
        seen.add(asset.pk)
        rows.append(asset)
    return rows


def _empty_analysis() -> dict:
    return {
        "checked": False,
        "separated": False,
        "co_located": None,
        "max_distance_km": None,
        "assets": [],
        "asset_ids": [],
        "incoming_assets": [],
        "incoming_asset_ids": [],
        "unresolved": [],
        "outside_scope": [],
        "missing_locations": [],
        "elements": [],
    }


def analyze_assets(assets, *, lead=None) -> dict:
    """Measure a group of linked Air Assets against the 5 km rule."""
    rows = _unique_assets(assets)
    result = _empty_analysis()
    result["assets"] = rows
    result["asset_ids"] = [asset.pk for asset in rows]
    if len(rows) < 2:
        result.update({"checked": True, "co_located": True})
        return result

    positions = {}
    missing = []
    for asset in rows:
        position = coordinates_from_location(asset.current_location)
        if position is None:
            missing.append(asset)
        else:
            positions[asset.pk] = position
    if missing:
        result["missing_locations"] = [asset_label(asset) for asset in missing]
        return result

    lead = lead if lead is not None and lead.pk in positions else rows[0]
    lead_position = positions[lead.pk]
    distances = [
        (asset, distance_km(lead_position, positions[asset.pk]))
        for asset in rows
        if asset.pk != lead.pk
    ]
    incoming = [asset for asset, value in distances if value > COLOCATION_RADIUS_KM]
    max_distance = max((value for _, value in distances), default=0.0)

    result.update(
        {
            "checked": True,
            "separated": bool(incoming),
            "co_located": not incoming,
            "max_distance_km": round(max_distance, 3),
            "incoming_assets": incoming,
            "incoming_asset_ids": [asset.pk for asset in incoming],
            "lead": lead,
        }
    )
    return result


def _row_value(row, key, default=""):
    if isinstance(row, dict):
        return row.get(key) or default
    return getattr(row, key, default) or default


def _resolve_manual_asset(row, cache, allowed_unit_ids=None):
    """Resolve a typed Flight Operations aircraft to its Air Asset row."""
    model = (_row_value(row, "aircraft_type") or "").strip()
    tail = (_row_value(row, "tail_number") or "").strip()
    key = (model.casefold(), tail.casefold(), tuple(sorted(allowed_unit_ids or [])))
    if key in cache:
        return cache[key]
    if not model or not tail:
        cache[key] = None
        return None

    from apps.ops.models import AirAsset

    candidates = list(
        AirAsset.objects.filter(
            model__iexact=model, tail_number__iexact=tail
        ).order_by("pk")[:5]
    )
    if allowed_unit_ids is not None:
        scoped = [
            asset
            for asset in candidates
            if asset.opcon_unit_id is not None
            and asset.opcon_unit_id in allowed_unit_ids
        ]
        if scoped:
            candidates = scoped
    asset = candidates[0] if candidates else None
    cache[key] = asset
    return asset


def _element_rows(element):
    if isinstance(element, dict):
        return element.get("aircraft") or []
    return list(element.aircraft.all())


def analyze_elements(elements, *, allowed_unit_ids=None) -> dict:
    """Analyze every numbered Element on a Flight Operations sheet."""
    result = _empty_analysis()
    cache = {}
    all_assets = []
    incoming = []
    element_results = []
    any_checked = False
    max_distance = None

    for element in elements or []:
        rows = _element_rows(element)
        resolved = []
        unresolved = []
        outside = []
        for row in rows:
            asset = _resolve_manual_asset(row, cache, allowed_unit_ids)
            if asset is None:
                unresolved.append(asset_label_from_row(row))
                continue
            resolved.append(asset)
            all_assets.append(asset)
            if (
                allowed_unit_ids is not None
                and (
                    asset.opcon_unit_id is None
                    or asset.opcon_unit_id not in allowed_unit_ids
                )
            ):
                outside.append(asset_label(asset))
        if unresolved:
            result["unresolved"].extend(unresolved)
        if outside:
            result["outside_scope"].extend(outside)
        if len(rows) < 2:
            continue
        if unresolved:
            continue

        lead_asset = None
        for row, asset in zip(rows, resolved):
            if bool(_row_value(row, "is_lead", False)):
                lead_asset = asset
                break
        lead_asset = lead_asset or resolved[0]
        analysis = analyze_assets(resolved, lead=lead_asset)
        element_results.append(analysis)
        if not analysis["checked"]:
            result["missing_locations"].extend(analysis["missing_locations"])
            continue
        any_checked = True
        if analysis["separated"]:
            incoming.extend(analysis["incoming_assets"])
        if analysis["max_distance_km"] is not None:
            max_distance = max(
                max_distance or 0.0, analysis["max_distance_km"]
            )

    unique_assets = _unique_assets(all_assets)
    unique_incoming = _unique_assets(incoming)
    result.update(
        {
            "checked": any_checked,
            "separated": bool(unique_incoming),
            "co_located": (not unique_incoming) if any_checked else None,
            "max_distance_km": (
                round(max_distance, 3) if max_distance is not None else None
            ),
            "assets": unique_assets,
            "asset_ids": [asset.pk for asset in unique_assets],
            "incoming_assets": unique_incoming,
            "incoming_asset_ids": [asset.pk for asset in unique_incoming],
            "unresolved": list(dict.fromkeys(result["unresolved"])),
            "outside_scope": list(dict.fromkeys(result["outside_scope"])),
            "missing_locations": list(
                dict.fromkeys(result["missing_locations"])
            ),
            "elements": element_results,
        }
    )
    return result


def asset_label_from_row(row) -> str:
    model = (_row_value(row, "aircraft_type") or "").strip()
    tail = (_row_value(row, "tail_number") or "").strip()
    return " ".join(part for part in (model, tail) if part) or "unidentified aircraft"


def analyze_payload(detail_data, *, allowed_unit_ids=None) -> dict:
    """Analyze a validated ``air_detail`` payload before it is saved."""
    if not detail_data:
        return _empty_analysis()
    elements = detail_data.get("elements")
    if elements is not None:
        return analyze_elements(elements, allowed_unit_ids=allowed_unit_ids)
    selected = detail_data.get("air_asset_ids")
    if selected is not None:
        lead = detail_data.get("air_asset")
        return analyze_assets(selected, lead=lead)
    return _empty_analysis()


def detail_elements(detail):
    if detail is None:
        return []
    return list(detail.elements.all())


def analyze_instance(detail, *, allowed_unit_ids=None) -> dict:
    """Analyze an existing Air Mission Detail."""
    if detail is None:
        return _empty_analysis()
    elements = detail_elements(detail)
    if elements:
        return analyze_elements(elements, allowed_unit_ids=allowed_unit_ids)
    assets = [
        row.air_asset
        for row in detail.assigned_aircraft.all()
        if row.air_asset_id is not None
    ]
    if len(assets) < 2 and detail.air_asset_id:
        assets.append(detail.air_asset)
    return analyze_assets(assets, lead=detail.air_asset)


def assets_from_detail(detail) -> list:
    """Every Air Asset linked to a detail, including single-aircraft rows."""
    if detail is None:
        return []
    elements = detail_elements(detail)
    if elements:
        cache = {}
        assets = []
        for element in elements:
            for row in _element_rows(element):
                asset = _resolve_manual_asset(row, cache)
                if asset is not None:
                    assets.append(asset)
        return _unique_assets(assets)
    assets = [
        row.air_asset
        for row in detail.assigned_aircraft.all()
        if row.air_asset_id is not None
    ]
    if detail.air_asset_id:
        assets.append(detail.air_asset)
    return _unique_assets(assets)


def unavailable_assets(assets) -> list:
    return [
        asset
        for asset in _unique_assets(assets)
        if asset.operational_status in UNAVAILABLE_STATUSES
    ]


def asset_conflicts(
    asset_ids, start, end, *, exclude_mission_id=None
) -> list[dict]:
    """Assignments that overlap ``start``-``end`` for the same airframes."""
    if not asset_ids or start is None or end is None:
        return []

    from django.utils import timezone

    from apps.ops.models import AirAsset, AirMissionAircraft, AirMissionAsset

    if timezone.is_aware(start):
        start = timezone.make_naive(start)
    if timezone.is_aware(end):
        end = timezone.make_naive(end)
    if end <= start:
        return []

    queryset = (
        AirMissionAsset.objects.filter(
            air_asset_id__in=list(asset_ids),
            air_detail__scheduled_departure__isnull=False,
            air_detail__scheduled_arrival__isnull=False,
            air_detail__scheduled_departure__lt=end,
            air_detail__scheduled_arrival__gt=start,
        )
        .exclude(
            air_detail__mission__status__in=["completed", "cancelled"]
        )
        .select_related("air_detail__mission", "air_asset")
        .order_by("air_detail__scheduled_departure", "air_detail__mission_id")
    )
    if exclude_mission_id:
        queryset = queryset.exclude(air_detail__mission_id=exclude_mission_id)

    rows = []
    seen: set[tuple[int, int]] = set()
    for assignment in queryset:
        if assignment.air_asset_id is None:
            continue
        mission = assignment.air_detail.mission
        key = (assignment.air_asset_id, mission.pk)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "air_asset_id": assignment.air_asset_id,
                "tail_number": assignment.tail_number
                or getattr(assignment.air_asset, "tail_number", ""),
                "mission_id": mission.pk,
                "mission_number": mission.mission_number,
                "title": mission.title,
                "status": mission.status,
            }
        )

    # The Flight Operations form stores its aircraft as manual element rows.
    # Those rows carry type and tail, not an AirAsset FK, so check them against
    # the register as well; otherwise the server would enforce availability for
    # linked formations only while the actual form bypassed it.
    manual_queryset = (
        AirMissionAircraft.objects.filter(
            element__air_detail__scheduled_departure__isnull=False,
            element__air_detail__scheduled_arrival__isnull=False,
            element__air_detail__scheduled_departure__lt=end,
            element__air_detail__scheduled_arrival__gt=start,
        )
        .exclude(
            element__air_detail__mission__status__in=["completed", "cancelled"]
        )
        .select_related("element__air_detail__mission")
        .order_by(
            "element__air_detail__scheduled_departure",
            "element__air_detail__mission_id",
        )
    )
    if exclude_mission_id:
        manual_queryset = manual_queryset.exclude(
            element__air_detail__mission_id=exclude_mission_id
        )

    wanted = {int(value) for value in asset_ids}
    cache: dict[tuple[str, str], object | None] = {}
    for aircraft in manual_queryset:
        model = (aircraft.aircraft_type or "").strip()
        tail = (aircraft.tail_number or "").strip()
        cache_key = (model.casefold(), tail.casefold())
        if cache_key not in cache:
            candidates = (
                AirAsset.objects.filter(
                    model__iexact=model, tail_number__iexact=tail
                )
                if model and tail
                else []
            )
            cache[cache_key] = next(
                (candidate for candidate in candidates if candidate.pk in wanted),
                None,
            )
        asset = cache[cache_key]
        if asset is None:
            continue
        mission = aircraft.element.air_detail.mission
        key = (asset.pk, mission.pk)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "air_asset_id": asset.pk,
                "tail_number": tail or asset.tail_number,
                "mission_id": mission.pk,
                "mission_number": mission.mission_number,
                "title": mission.title,
                "status": mission.status,
            }
        )
    return rows

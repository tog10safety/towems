"""Unit map markers: where a unit sits, and the seal drawn as its pin.

The legacy ``units`` table cannot gain columns, so the coordinate, the place
description and the uploaded logo live in :class:`apps.command.models.UnitMapMarker`.
This module owns validation, the payload shape used by every map, and the small
amount of file handling for the logo.
"""

from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.http import FileResponse, Http404

from apps.accounts.profile import MAX_UPLOAD_BYTES, _read_upload
from apps.core import roles
from apps.core.audit import log_action
from apps.core.mgrs import MGRSError, from_mgrs, to_mgrs, validate_mgrs
from apps.command.models import UnitMapMarker

AUDIT_FIELDS = [
    "unit_id",
    "latitude",
    "longitude",
    "mgrs",
    "place_name",
    "logo_filename",
]


class MarkerError(ValueError):
    """A refusal that should be shown to the operator as a field message."""

    def __init__(self, message: str, field: str = "latitude"):
        super().__init__(message)
        self.message = message
        self.field = field


def logo_directory():
    path = settings.LEGACY_UPLOAD_DIR / "unit_logos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def marker_for(unit) -> UnitMapMarker | None:
    return UnitMapMarker.objects.filter(unit_id=unit.id).first()


def _logo_url(unit_id: int, marker: UnitMapMarker | None) -> str:
    if marker is None or not marker.logo_filename:
        return ""
    # The filename doubles as a cache buster: a new seal is a new URL.
    return f"/api/units/{unit_id}/marker-logo/?v={marker.logo_filename}"


def payload(unit, marker: UnitMapMarker | None = None) -> dict:
    """The marker as the maps and the unit table need it."""
    marker = marker if marker is not None else marker_for(unit)
    has_point = bool(
        marker and marker.latitude is not None and marker.longitude is not None
    )
    return {
        "unit_id": unit.id,
        "unit_name": unit.name,
        "unit_acronym": unit.acronym,
        "latitude": marker.latitude if has_point else None,
        "longitude": marker.longitude if has_point else None,
        "mgrs": marker.mgrs if marker else "",
        "place_name": marker.place_name if marker else "",
        "has_marker": has_point,
        "has_logo": bool(marker and marker.logo_filename),
        "logo_url": _logo_url(unit.id, marker),
        "updated_at": marker.updated_at if marker else None,
    }


def markers_for_units(units) -> list[dict]:
    """Payloads for the given units, in one query."""
    unit_list = list(units)
    if not unit_list:
        return []
    rows = {
        row.unit_id: row
        for row in UnitMapMarker.objects.filter(
            unit_id__in=[unit.id for unit in unit_list]
        )
    }
    return [payload(unit, rows.get(unit.id)) for unit in unit_list]


def markers_with_coordinates(units) -> list[dict]:
    """Only the markers that can actually be drawn."""
    return [row for row in markers_for_units(units) if row["has_marker"]]


def _resolve_point(data) -> tuple[float | None, float | None, str]:
    """Accept lat/lon, an MGRS grid, or both, and keep them consistent."""
    raw_lat = data.get("latitude", None)
    raw_lon = data.get("longitude", None)
    mgrs = str(data.get("mgrs") or "").strip().upper()

    has_lat = raw_lat not in (None, "")
    has_lon = raw_lon not in (None, "")

    if has_lat != has_lon:
        raise MarkerError("Give both latitude and longitude, or neither.")

    if has_lat and has_lon:
        try:
            lat = float(raw_lat)
            lon = float(raw_lon)
        except (TypeError, ValueError):
            raise MarkerError("Latitude and longitude must be numbers.")
        if not -90 <= lat <= 90:
            raise MarkerError("Latitude must be between -90 and 90.")
        if not -180 <= lon <= 180:
            raise MarkerError("Longitude must be between -180 and 180.")
        if mgrs:
            try:
                validate_mgrs(mgrs)
            except MGRSError as exc:
                raise MarkerError(str(exc), field="mgrs") from exc
        else:
            mgrs = to_mgrs(lat, lon)
        return lat, lon, mgrs

    if mgrs:
        try:
            lat, lon = from_mgrs(mgrs)
        except MGRSError as exc:
            raise MarkerError(str(exc), field="mgrs") from exc
        return lat, lon, mgrs

    return None, None, ""


def set_marker(*, actor, unit, data, request=None) -> dict:
    """Create or update a unit's pin. Administrator-only at the view layer."""
    if unit is None:
        raise MarkerError("Choose a unit.", field="unit")
    if not unit.is_active:
        raise MarkerError("That unit is inactive.", field="unit")

    latitude, longitude, mgrs = _resolve_point(data)
    place_name = str(data.get("place_name") or "").strip()[:200]

    marker, created = UnitMapMarker.objects.get_or_create(unit_id=unit.id)
    before = {field: getattr(marker, field, None) for field in AUDIT_FIELDS} if not created else {}
    marker.latitude = latitude
    marker.longitude = longitude
    marker.mgrs = mgrs
    marker.place_name = place_name
    marker.save(update_fields=["latitude", "longitude", "mgrs", "place_name", "updated_at"])
    after = {field: getattr(marker, field, None) for field in AUDIT_FIELDS}
    log_action(
        actor=actor,
        action="unit_marker_update",
        entity_type="unit",
        entity_id=unit.id,
        before=before,
        after=after,
        note=(
            f"{unit.acronym} pinned at {mgrs or 'no coordinates'}"
            + (f" ({place_name})" if place_name else "")
        ),
        request=request,
    )
    return payload(unit, marker)


def upload_logo(*, actor, unit, request) -> dict:
    """Store the unit's logo/seal and use it as the map pin image."""
    if not unit.is_active:
        raise MarkerError("That unit is inactive.", field="unit")
    data, extension = _read_upload(request, "logo")
    if len(data) > MAX_UPLOAD_BYTES:  # pragma: no cover - _read_upload already checks
        raise MarkerError("The image is too large.", field="logo")

    marker, _created = UnitMapMarker.objects.get_or_create(unit_id=unit.id)
    previous = marker.logo_filename or ""
    filename = f"unit_{unit.id}_{uuid.uuid4().hex[:10]}.{extension}"
    with open(logo_directory() / filename, "wb") as handle:
        handle.write(data)

    marker.logo_filename = filename
    marker.logo_content_type = f"image/{extension}"
    marker.save(update_fields=["logo_filename", "logo_content_type", "updated_at"])
    if previous and previous != filename:
        try:
            (logo_directory() / previous).unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort
            pass
    log_action(
        actor=actor,
        action="unit_logo_update",
        entity_type="unit",
        entity_id=unit.id,
        after={"logo_filename": filename},
        note=f"{unit.acronym} logo replaced; used as the map pin",
        request=request,
    )
    return payload(unit, marker)


def remove_logo(*, actor, unit, request=None) -> dict:
    marker = marker_for(unit)
    if marker is None or not marker.logo_filename:
        return payload(unit, marker)
    previous = marker.logo_filename
    marker.logo_filename = ""
    marker.logo_content_type = ""
    marker.save(update_fields=["logo_filename", "logo_content_type", "updated_at"])
    try:
        (logo_directory() / previous).unlink(missing_ok=True)
    except OSError:  # pragma: no cover - best effort
        pass
    log_action(
        actor=actor,
        action="unit_logo_remove",
        entity_type="unit",
        entity_id=unit.id,
        note=f"{unit.acronym} logo removed",
        request=request,
    )
    return payload(unit, marker)


def logo_response(unit) -> FileResponse:
    marker = marker_for(unit)
    filename = os.path.basename((marker.logo_filename if marker else "") or "")
    if not filename:
        raise Http404("No logo has been uploaded for this unit.")
    path = logo_directory() / filename
    if not path.exists():
        raise Http404("The stored logo file is missing.")
    content_type = (marker.logo_content_type if marker else "") or "image/png"
    response = FileResponse(open(path, "rb"), content_type=content_type)
    response["Cache-Control"] = "private, max-age=3600"
    return response


def can_maintain(user) -> bool:
    """Unit pins and seals are unit-management data: Administrator only."""
    return roles.has_capability(user, roles.CAP_SYSTEM_CONFIG)

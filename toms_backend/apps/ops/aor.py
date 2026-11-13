"""AOR assignment and conflict detection.

Rules (spec §4, default no-overlap policy):

* Two different units may not claim the same territory. Every candidate
  geometry is compared against the *actual polygons* of other units' active
  AORs, not only against administrative names.
* Reassigning a whole administrative area that another unit holds is a
  *transfer*: it needs ``confirm_transfer`` and is applied transactionally,
  deactivating the previous row and recording both sides in the audit trail.
* Any other geometric overlap (partial coverage, or a province on one unit and
  a city inside it on another) is refused. The existing AORs are never trimmed
  or deleted to make room.
* Rows are locked while validating, so two concurrent assignments cannot both
  pass the check; the partial unique index on ``admin_area`` is the final
  backstop.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.core import geo
from apps.core.audit import log_action, snapshot

from .models import AdministrativeArea, AreaOfResponsibility

DEFAULT_COLOR = "#2b6cb0"

#: How much shared area still counts as "the boundaries merely touch".
#:
#: The bundled PSGC boundaries are simplified polygons: two neighbouring
#: provinces routinely overlap by a few square kilometres along their shared
#: edge (measured up to ~3.6 km2, i.e. under 0.1% of either province). Treating
#: those slivers as a genuine conflict made it impossible to give adjacent
#: provinces to different units, so anything below this share of the smaller
#: area is ignored as boundary imprecision. A real overlap - the same province
#: claimed twice, or a drawn polygon taking a meaningful bite out of another
#: unit's area - is orders of magnitude larger and still refused.
SLIVER_RATIO = 0.005  # 0.5% of the smaller area

#: Distinct, mid-tone colours so neighbouring units stay readable on both the
#: light and the night basemap. A unit's colour is derived from its id, which
#: makes it stable across sessions and identical in every view.
UNIT_COLOR_PALETTE = [
    "#2b6cb0",  # blue
    "#2f855a",  # green
    "#b7791f",  # amber
    "#c53030",  # red
    "#6b46c1",  # violet
    "#0f766e",  # teal
    "#b83280",  # magenta
    "#4a5568",  # slate
    "#c05621",  # orange
    "#2c5282",  # navy
    "#276749",  # forest
    "#805ad5",  # purple
]


def unit_color(unit) -> str:
    """The colour that represents ``unit`` on the map.

    Administrative-area coverage takes this colour automatically, so every area
    a unit holds is drawn in the same tone and two units never look alike.
    """
    if unit is None or getattr(unit, "id", None) is None:
        return DEFAULT_COLOR
    return UNIT_COLOR_PALETTE[int(unit.id) % len(UNIT_COLOR_PALETTE)]

AUDIT_FIELDS = [
    "name",
    "code",
    "unit",
    "admin_area",
    "is_active",
    "min_lon",
    "min_lat",
    "max_lon",
    "max_lat",
]


class AORError(ValueError):
    """Raised when an AOR change is refused; carries structured detail."""

    def __init__(self, message: str, *, conflicts: list[dict] | None = None, code="invalid"):
        super().__init__(message)
        self.message = message
        self.conflicts = conflicts or []
        self.code = code

    def as_dict(self) -> dict:
        return {"detail": self.message, "code": self.code, "conflicts": self.conflicts}


def is_significant_overlap(detail: dict) -> bool:
    """True when an overlap is real rather than a shared-border sliver."""
    return detail["overlaps"] and detail["ratio"] >= SLIVER_RATIO


def _bbox_intersects(aor: AreaOfResponsibility, box: dict) -> bool:
    if None in (aor.min_lon, aor.min_lat, aor.max_lon, aor.max_lat):
        return True
    return not (
        aor.max_lon < box["min_lon"]
        or aor.min_lon > box["max_lon"]
        or aor.max_lat < box["min_lat"]
        or aor.min_lat > box["max_lat"]
    )


def conflict_payload(aor: AreaOfResponsibility, detail: dict, *, candidate_name: str) -> dict:
    return {
        "aor_id": aor.id,
        "aor_name": aor.name,
        "aor_code": aor.code,
        "unit_id": aor.unit_id,
        "unit_name": aor.unit.name if aor.unit_id else "",
        "unit_acronym": aor.unit.acronym if aor.unit_id else "",
        "admin_psgc": aor.admin_area_id,
        "admin_area_name": aor.admin_area.name if aor.admin_area_id else "",
        "candidate": candidate_name,
        "relation": detail["relation"],
        "overlap_km2": detail["area_km2"],
        "overlap_ratio": detail["ratio"],
    }


def find_conflicts(
    *,
    unit_id: int,
    geometry: dict,
    name: str,
    exclude_ids: list[int] | None = None,
    lock: bool = False,
) -> list[dict]:
    """Overlaps between ``geometry`` and other units' active AORs."""
    box = geo.bounding_box(geometry)
    queryset = AreaOfResponsibility.objects.filter(is_active=True).exclude(
        unit_id=unit_id
    )
    if exclude_ids:
        queryset = queryset.exclude(pk__in=exclude_ids)
    queryset = queryset.select_related("unit", "admin_area").only(
        "id",
        "name",
        "code",
        "unit_id",
        "admin_area_id",
        "geometry",
        "min_lon",
        "min_lat",
        "max_lon",
        "max_lat",
    )
    if lock:
        # ``of=("self",)`` locks only the AOR rows: PostgreSQL refuses
        # ``FOR UPDATE`` on the nullable side of the selects above (unit and
        # admin_area are both nullable foreign keys).
        queryset = queryset.select_for_update(of=("self",))

    conflicts = []
    for other in queryset:
        if not _bbox_intersects(other, box):
            continue
        try:
            detail = geo.overlap_detail(geometry, other.geometry)
        except geo.GeometryError:
            continue
        if is_significant_overlap(detail):
            conflicts.append(conflict_payload(other, detail, candidate_name=name))
    return conflicts


def list_overlaps(unit_ids: set[int] | None = None) -> list[dict]:
    """Report existing overlaps between units without changing anything."""
    queryset = AreaOfResponsibility.objects.filter(is_active=True).select_related(
        "unit", "admin_area"
    )
    if unit_ids is not None:
        queryset = queryset.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
    rows = list(queryset.order_by("unit_id", "name"))

    found = []
    seen = set()
    for index, first in enumerate(rows):
        for second in rows[index + 1 :]:
            if first.id in (second.id,) or (first.unit_id or 0) == (second.unit_id or 0):
                continue
            if None in (first.min_lon, first.min_lat, first.max_lon, first.max_lat):
                continue
            if (
                first.max_lon < second.min_lon
                or first.min_lon > second.max_lon
                or first.max_lat < second.min_lat
                or first.min_lat > second.max_lat
            ):
                continue
            try:
                detail = geo.overlap_detail(first.geometry, second.geometry)
            except geo.GeometryError:
                continue
            if not is_significant_overlap(detail):
                continue
            key = tuple(sorted((first.id, second.id)))
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "aors": [
                        {
                            "id": first.id,
                            "name": first.name,
                            "code": first.code,
                            "unit_id": first.unit_id,
                            "unit_acronym": first.unit.acronym if first.unit_id else "",
                            "admin_psgc": first.admin_area_id,
                        },
                        {
                            "id": second.id,
                            "name": second.name,
                            "code": second.code,
                            "unit_id": second.unit_id,
                            "unit_acronym": second.unit.acronym if second.unit_id else "",
                            "admin_psgc": second.admin_area_id,
                        },
                    ],
                    "relation": detail["relation"],
                    "overlap_km2": detail["area_km2"],
                }
            )
    return found


def _derived(geometry: dict) -> dict:
    box = geo.bounding_box(geometry)
    centre = geo.centroid(geometry)
    return {
        "min_lon": box["min_lon"],
        "min_lat": box["min_lat"],
        "max_lon": box["max_lon"],
        "max_lat": box["max_lat"],
        "centroid_lon": centre[0],
        "centroid_lat": centre[1],
    }


def _aor_code(area: AdministrativeArea) -> str:
    return f"PSGC-{area.psgc}"


@transaction.atomic
def assign_areas(
    *,
    actor,
    unit,
    psgcs: list[int],
    confirm_transfer: bool = False,
    color: str | None = None,
    request=None,
) -> dict:
    """Assign administrative areas to a unit, or refuse with conflicts.

    Every row takes the unit's own colour (``unit_color``) unless a caller
    passes an explicit one, so areas of the same unit are visually identical and
    different units are distinguishable on the map.
    """
    # The target unit's colour is what the polygons are drawn in, including when
    # an area is transferred away from another unit.
    color = color or unit_color(unit)
    if not psgcs:
        raise AORError("Select at least one province or city.", code="no_areas")
    if not unit.is_active:
        raise AORError("The selected unit is inactive.", code="inactive_unit")

    areas = list(AdministrativeArea.objects.filter(psgc__in=psgcs))
    missing = sorted(set(psgcs) - {area.psgc for area in areas})
    if missing:
        raise AORError(
            "No boundary is available for: " + ", ".join(str(m) for m in missing),
            code="missing_boundary",
        )

    # Lock every active row that already represents one of these areas, so a
    # concurrent request cannot assign the same area here and there at once.
    AreaOfResponsibility.objects.select_for_update().filter(
        admin_area_id__in=[area.psgc for area in areas], is_active=True
    ).exists()

    assignments, transferred, unchanged = [], [], []
    conflicts: list[dict] = []

    for area in areas:
        existing = (
            AreaOfResponsibility.objects.select_for_update()
            .filter(admin_area=area)
            .order_by("-is_active", "-updated_at")
            .first()
        )
        if existing is not None and existing.is_active and existing.unit_id == unit.id:
            unchanged.append(area)
            continue

        # Compare against other units' coverage, ignoring the row we are about
        # to take over so a transfer is not reported as a conflict with itself.
        relevant = find_conflicts(
            unit_id=unit.id,
            geometry=area.geometry,
            name=area.name,
            exclude_ids=[existing.id] if existing and not existing.is_active else None,
            lock=True,
        )
        same_area = [
            row for row in relevant if row["admin_psgc"] == area.psgc
        ]
        other_overlap = [row for row in relevant if row["admin_psgc"] != area.psgc]

        if other_overlap:
            conflicts.extend(other_overlap)
            continue
        if same_area and not confirm_transfer:
            conflicts.extend(same_area)
            continue

        if existing is None:
            row = AreaOfResponsibility.objects.create(
                name=area.name,
                code=_aor_code(area),
                unit=unit,
                admin_area=area,
                geometry=area.geometry,
                color=color,
                description=(
                    f"{area.get_level_display()} · {area.region_name}".strip(" ·")
                ),
                created_by=actor,
                **_derived(area.geometry),
            )
            assignments.append(row)
            log_action(
                actor=actor,
                action="aor_assign",
                entity_type="aor",
                entity_id=row.pk,
                after=snapshot(row, AUDIT_FIELDS),
                note=f"{area.name} ({area.psgc}) assigned to {unit.acronym}",
                request=request,
            )
            continue

        before = snapshot(existing, AUDIT_FIELDS)
        previous_unit = existing.unit
        existing.unit = unit
        existing.name = area.name
        existing.admin_area = area
        existing.geometry = area.geometry
        existing.color = color or existing.color
        existing.is_active = True
        for field, value in _derived(area.geometry).items():
            setattr(existing, field, value)
        existing.save()
        transferred.append(existing)
        log_action(
            actor=actor,
            action="aor_transfer" if same_area else "aor_assign",
            entity_type="aor",
            entity_id=existing.pk,
            before=before,
            after=snapshot(existing, AUDIT_FIELDS),
            note=(
                f"{area.name} ({area.psgc}) transferred from "
                f"{getattr(previous_unit, 'acronym', '') or 'unassigned'} to {unit.acronym}"
                if same_area
                else f"{area.name} ({area.psgc}) assigned to {unit.acronym}"
            ),
            request=request,
        )

    if conflicts:
        # Nothing has been committed yet; the surrounding atomic block rolls
        # back every row created or transferred in this call.
        names = ", ".join(sorted({row["candidate"] for row in conflicts})[:3])
        raise AORError(
            f"These areas overlap another unit's AOR: {names}. "
            "Resolve the conflict or confirm a transfer before saving.",
            conflicts=conflicts,
            code="overlap",
        )

    try:
        # Force the partial unique index to answer now, inside the transaction.
        AreaOfResponsibility.objects.filter(
            admin_area_id__in=[area.psgc for area in areas], is_active=True
        ).values_list("admin_area_id", flat=True).count()
    except IntegrityError as exc:  # pragma: no cover - race backstop
        raise AORError(
            "Another assignment changed at the same time. Reload and try again.",
            code="conflict",
        ) from exc

    unit_ids = {unit.id}
    return {
        "unit": {"id": unit.id, "name": unit.name, "acronym": unit.acronym},
        "assigned": [_aor_payload(row) for row in assignments],
        "transferred": [_aor_payload(row) for row in transferred],
        "unchanged": [
            {"psgc": area.psgc, "name": area.name, "level": area.level}
            for area in unchanged
        ],
        "unit_coverage_km2": round(
            sum(
                # Manually drawn AORs have no admin area and therefore no
                # authoritative area figure; they add nothing to this total.
                (getattr(row.admin_area, "area_km2", None) or 0.0)
                for row in AreaOfResponsibility.objects.filter(
                    is_active=True, unit_id__in=unit_ids
                ).select_related("admin_area")
            ),
            2,
        ),
    }


ARCHIVE_FIELDS = AUDIT_FIELDS + ["description", "color", "geometry"]


@transaction.atomic
def archive_area(
    *, actor, aor: AreaOfResponsibility, reason: str = "", request=None
) -> AreaOfResponsibility:
    """Take an AOR out of active coverage without destroying the record.

    Archiving is the reversible half of removal: the row, its geometry and its
    history stay in place, it simply stops counting as coverage. ``reason`` is
    kept in the audit note so the decision can be reviewed later.
    """
    if not aor.is_active:
        return aor
    before = snapshot(aor, AUDIT_FIELDS)
    aor.is_active = False
    aor.save(update_fields=["is_active", "updated_at"])
    note = f"{aor.name} archived from {getattr(aor.unit, 'acronym', '') or 'unassigned'}"
    if reason.strip():
        note += f": {reason.strip()}"
    log_action(
        actor=actor,
        action="aor_archive",
        entity_type="aor",
        entity_id=aor.pk,
        before=before,
        after=snapshot(aor, AUDIT_FIELDS),
        note=note,
        request=request,
    )
    return aor


@transaction.atomic
def restore_area(*, actor, aor: AreaOfResponsibility, request=None) -> AreaOfResponsibility:
    """Put an archived AOR back into active coverage.

    An administrative-area row cannot be restored while another unit holds that
    same area (the partial unique index would refuse it), so the conflict is
    reported as a normal refusal instead of an integrity error.
    """
    if aor.is_active:
        return aor
    if aor.admin_area_id:
        holder = (
            AreaOfResponsibility.objects.filter(
                admin_area_id=aor.admin_area_id, is_active=True
            )
            .exclude(pk=aor.pk)
            .first()
        )
        if holder is not None:
            raise AORError(
                f"{aor.name} is currently held by "
                f"{getattr(holder.unit, 'acronym', '') or 'another unit'}. "
                "Transfer it back instead of restoring this record.",
                code="held_elsewhere",
            )
    before = snapshot(aor, AUDIT_FIELDS)
    aor.is_active = True
    aor.save(update_fields=["is_active", "updated_at"])
    log_action(
        actor=actor,
        action="aor_restore",
        entity_type="aor",
        entity_id=aor.pk,
        before=before,
        after=snapshot(aor, AUDIT_FIELDS),
        note=f"{aor.name} restored to {getattr(aor.unit, 'acronym', '') or 'unassigned'}",
        request=request,
    )
    return aor


@transaction.atomic
def delete_area(*, actor, aor: AreaOfResponsibility, request=None) -> int:
    """Permanently delete an AOR record (Administrator only).

    The full row - geometry included - is written to the audit trail *before* it
    is removed, so the boundary can be reconstructed even though the record is
    gone. Archiving is the recommended action; deleting is for records created in
    error.
    """
    entity_id = aor.pk
    before = snapshot(aor, ARCHIVE_FIELDS)
    label = f"{aor.code} {aor.name}"
    unit_label = getattr(aor.unit, "acronym", "") or "unassigned"
    aor.delete()
    log_action(
        actor=actor,
        action="aor_delete",
        entity_type="aor",
        entity_id=entity_id,
        before=before,
        note=f"{label} permanently deleted from {unit_label} (full snapshot retained)",
        request=request,
    )
    return entity_id


@transaction.atomic
def remove_area(*, actor, aor: AreaOfResponsibility, request=None) -> AreaOfResponsibility:
    """Deactivate an AOR row, preserving it for history and audit."""
    if not aor.is_active:
        return aor
    before = snapshot(aor, AUDIT_FIELDS)
    aor.is_active = False
    aor.save(update_fields=["is_active", "updated_at"])
    log_action(
        actor=actor,
        action="aor_remove",
        entity_type="aor",
        entity_id=aor.pk,
        before=before,
        after=snapshot(aor, AUDIT_FIELDS),
        note=f"{aor.name} removed from {getattr(aor.unit, 'acronym', '')}",
        request=request,
    )
    return aor


@transaction.atomic
def remove_areas(
    *, actor, unit, psgcs: list[int], request=None
) -> list[AreaOfResponsibility]:
    rows = list(
        AreaOfResponsibility.objects.select_for_update()
        .filter(unit=unit, admin_area_id__in=psgcs, is_active=True)
        .select_related("unit", "admin_area")
    )
    for row in rows:
        remove_area(actor=actor, aor=row, request=request)
    return rows


def _aor_payload(aor: AreaOfResponsibility) -> dict:
    return {
        "id": aor.id,
        "name": aor.name,
        "code": aor.code,
        "unit_id": aor.unit_id,
        "admin_psgc": aor.admin_area_id,
        "admin_area_name": aor.admin_area.name if aor.admin_area_id else "",
    }

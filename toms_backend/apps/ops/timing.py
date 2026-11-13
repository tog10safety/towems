"""Actual-versus-planned timing checks for air missions.

The board records ATD/ATA without rewriting the planned ETD/ETA.  A recorded
time can legitimately differ from the plan, but a large difference is usually a
typing, date, or timezone mistake.  These warnings make that difference visible
without blocking the operator: the actual time remains the authoritative record
used by the map and reports.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings


def _minutes(value: dt.timedelta) -> int:
    return int(round(abs(value.total_seconds()) / 60))


def _duration_label(value: dt.timedelta) -> str:
    minutes = _minutes(value)
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours} h {remainder} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


def _aircraft_label(aircraft) -> str:
    return (
        getattr(aircraft, "tail_number", "")
        or getattr(aircraft, "callsign", "")
        or f"aircraft {getattr(aircraft, 'pk', '')}"
    )


def mission_warnings(mission) -> list[dict]:
    """Return non-blocking timing inconsistencies for an air mission.

    The threshold is deliberately configurable because units may have different
    dispatch tolerances.  Warnings are deduplicated so a formation that shares
    one schedule does not produce the same message for every airframe.
    """
    if getattr(mission, "category", "") != "air":
        return []
    detail = getattr(mission, "air_detail", None)
    if detail is None:
        return []

    threshold = int(getattr(settings, "AIR_ACTUAL_TIME_WARNING_MINUTES", 120))
    if threshold <= 0:
        return []

    warnings: list[dict] = []
    seen: set[tuple] = set()

    for element in detail.elements.all():
        for aircraft in element.aircraft.all():
            label = _aircraft_label(aircraft)
            for leg in aircraft.itineraries.all():
                sequence = getattr(leg, "sequence", 0) or 0
                checks = (
                    (
                        "actual_departure_outside_plan",
                        "atd",
                        "etd",
                        leg.atd,
                        leg.etd,
                        "actual takeoff",
                        "ETD",
                    ),
                    (
                        "actual_arrival_outside_plan",
                        "ata",
                        "eta",
                        leg.ata,
                        leg.eta,
                        "actual landing",
                        "ETA",
                    ),
                )
                for code, actual_field, planned_field, actual, planned, actual_label, planned_label in checks:
                    if actual is None or planned is None:
                        continue
                    difference = actual - planned
                    if abs(difference) <= dt.timedelta(minutes=threshold):
                        continue
                    key = (code, sequence, planned, actual)
                    if key in seen:
                        continue
                    seen.add(key)
                    direction = "after" if difference.total_seconds() > 0 else "before"
                    warnings.append(
                        {
                            "code": code,
                            "severity": "warning",
                            "aircraft": label,
                            "leg": sequence,
                            "field": actual_field,
                            "actual": actual,
                            "planned": planned,
                            "difference_minutes": _minutes(difference),
                            "message": (
                                f"{label} leg {sequence}: {actual_label} is "
                                f"{_duration_label(difference)} {direction} the "
                                f"planned {planned_label}."
                            ),
                        }
                    )

                if leg.atd and leg.ata and leg.ata < leg.atd:
                    key = ("arrival_before_departure", sequence, leg.atd, leg.ata)
                    if key not in seen:
                        seen.add(key)
                        warnings.append(
                            {
                                "code": "arrival_before_departure",
                                "severity": "error",
                                "aircraft": label,
                                "leg": sequence,
                                "field": "ata",
                                "actual": leg.ata,
                                "planned": leg.atd,
                                "difference_minutes": _minutes(leg.atd - leg.ata),
                                "message": (
                                    f"{label} leg {sequence}: actual landing "
                                    "is before actual takeoff."
                                ),
                            }
                        )

    return warnings

"""Time-based, read-only aircraft simulation for the Air Operations map.

Nothing in this module writes a position or changes a mission. Every state is
derived from the persisted flight plan and the authoritative server clock, so
two operators looking at the same mission see the same simulated aircraft and a
browser refresh cannot restart a flight at its origin.

The map is explicitly a planning aid, not GPS or ADS-B. Actual ATD/ATA remain
the Daily Operations board's records and are never replaced by this simulation.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from types import SimpleNamespace

from django.conf import settings

from apps.core import timeutils
from apps.ops import approvals, formation_rules
from apps.ops.models import AirAsset


def buffer_minutes() -> int:
    """The configured arrival buffer, defaulting to ten minutes."""
    try:
        value = int(
            getattr(settings, "AIR_SIMULATION_ARRIVAL_BUFFER_MINUTES", 10)
        )
    except (TypeError, ValueError):
        value = 10
    return max(0, min(value, 240))


def _bearing(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return round((math.degrees(math.atan2(y, x)) + 360) % 360, 1)


def _interpolate(
    first: tuple[float, float], second: tuple[float, float], fraction: float
) -> tuple[float, float]:
    fraction = max(0.0, min(1.0, fraction))
    return (
        first[0] + (second[0] - first[0]) * fraction,
        first[1] + (second[1] - first[1]) * fraction,
    )


def _route_with_points(legs, detail, asset):
    """Return the planned route as ``[lat, lng]`` points.

    The itinerary is the source of truth. An aircraft's last known location is
    used only when the first leg has no usable grid reference, so a planned
    route is never replaced with an unrecorded position.
    """
    points: list[tuple[float, float]] = []

    def add(point):
        if point is None:
            return
        rounded = (round(float(point[0]), 6), round(float(point[1]), 6))
        if not points or points[-1] != rounded:
            points.append(rounded)

    legs = list(legs or [])
    for leg in legs:
        add(formation_rules.coordinates_from_location(leg.origin or ""))
        add(formation_rules.coordinates_from_location(leg.destination or ""))

    # An older or partially filled plan can name the airfield on the detail but
    # leave the itinerary rows blank. Complete the line from those fields, but
    # only when the itinerary did not already provide both ends.
    if len(points) < 2:
        departure = formation_rules.coordinates_from_location(
            getattr(detail, "departure_location", "") or ""
        )
        destination = formation_rules.coordinates_from_location(
            getattr(detail, "destination_location", "") or ""
        )
        if departure is not None and (not points or points[0] != departure):
            points.insert(0, (round(departure[0], 6), round(departure[1], 6)))
        if destination is not None:
            add(destination)

    if not points:
        add(
            formation_rules.coordinates_from_location(
                getattr(asset, "current_location", "") or ""
            )
        )
    return points


def _route_measurements(points):
    segments = []
    total = 0.0
    for first, second in zip(points, points[1:]):
        length = formation_rules.distance_km(first, second)
        if length <= 0:
            continue
        segments.append((first, second, length, total))
        total += length
    return segments, total


def _position_at(points, segments, total, distance):
    """Return ``(position, heading, traveled, remaining)`` at a route distance."""
    if not points:
        return None, 0.0, [], []
    if not segments or total <= 0:
        return [points[0][0], points[0][1]], 0.0, [[points[0][0], points[0][1]]], [
            [points[0][0], points[0][1]]
        ]

    distance = max(0.0, min(total, distance))
    chosen = segments[-1]
    for segment in segments:
        first, second, length, offset = segment
        if distance <= offset + length or segment is segments[-1]:
            chosen = segment
            break

    first, second, length, offset = chosen
    fraction = 0.0 if length <= 0 else (distance - offset) / length
    current = _interpolate(first, second, fraction)
    heading = _bearing(first, second)
    current_pair = [round(current[0], 6), round(current[1], 6)]

    traveled = []
    remaining = []
    for segment in segments:
        seg_start, seg_end, _seg_length, seg_offset = segment
        if seg_offset < distance:
            if not traveled:
                traveled.append([seg_start[0], seg_start[1]])
            traveled.append([seg_end[0], seg_end[1]])
        if seg_offset + _seg_length >= distance:
            if not remaining:
                remaining.append(current_pair)
            remaining.append([seg_end[0], seg_end[1]])

    if not traveled:
        traveled = [current_pair]
    else:
        traveled[-1] = current_pair
    if not remaining:
        remaining = [current_pair]
    return current_pair, heading, traveled, remaining


def _minutes(value) -> float:
    return round(max(0.0, value), 1)


def _status_label(status: str) -> str:
    return {
        "planned": "Planned",
        "en_route": "En Route",
        "arrived": "Arrived",
        "landed": "Landed at Stop",
        "on_hold": "On Hold",
        "cancelled": "Cancelled",
        "delayed": "Delayed",
        "pending_dependency": "Pending Dependency",
        "unavailable": "Unavailable",
        "timing_missing": "Timing Not Set",
        "position_missing": "Position Not Available",
        "draft": "Draft",
    }.get(status, status.replace("_", " ").title())


def _leg_window(leg) -> tuple[bool, bool]:
    """Return ``(departed, finished)`` for one leg.

    An overflight leg is flown TOT to TOOT; every other leg is flown ATD to
    ATA, which is the same vocabulary the Daily Operations board records.
    """
    if getattr(leg, "overflight", False):
        return bool(leg.tot), bool(leg.toot)
    return bool(leg.atd), bool(leg.ata)


def _leg_lengths(legs) -> list[float]:
    """Planned distance of every leg, in itinerary order."""
    lengths = []
    for leg in legs:
        start = formation_rules.coordinates_from_location(leg.origin or "")
        end = formation_rules.coordinates_from_location(leg.destination or "")
        lengths.append(
            formation_rules.distance_km(start, end)
            if start is not None and end is not None
            else 0.0
        )
    return lengths


def _current_leg_index(legs, progress: float) -> int | None:
    """The leg an aircraft is on now, or the next one it has to fly.

    Recorded actuals decide first -- an airborne leg is the current enroute --
    and a landed leg hands over to the next unflown one. A plan with no actual
    times yet follows the simulated position instead, so the side panel and the
    marker never disagree about which leg is being tracked.
    """
    legs = list(legs or [])
    if not legs:
        return None

    airborne = None
    next_open = None
    recorded = False
    for index, leg in enumerate(legs):
        departed, finished = _leg_window(leg)
        recorded = recorded or departed or finished
        if departed and not finished:
            airborne = index
        if next_open is None and not finished:
            next_open = index

    if airborne is not None:
        return airborne
    if recorded:
        return next_open if next_open is not None else len(legs) - 1

    spans = []
    total = 0.0
    for length in _leg_lengths(legs):
        spans.append((total, total + length, length))
        total += length
    if total <= 0:
        return 0
    travelled = total * max(0.0, min(1.0, float(progress or 0.0)))
    for index, (start, end, length) in enumerate(spans):
        if length > 0 and travelled < end:
            return index
    return len(legs) - 1


def _asset_lookup(assets):
    lookup = {}
    for asset in assets:
        model = (asset.model or "").strip().casefold()
        tail = (asset.tail_number or "").strip().casefold()
        if model and tail:
            lookup[(model, tail)] = asset
    return lookup


def _asset_for(plane, lookup):
    model = (plane.aircraft_type or "").strip().casefold()
    tail = (plane.tail_number or "").strip().casefold()
    if not model or not tail:
        return None
    return lookup.get((model, tail))


def _resolved_dependency(mission) -> dict:
    try:
        return approvals.dependency_state(mission)
    except Exception:
        # A malformed historical dependency must not remove its aircraft from
        # the map. Treat it as an unresolved gate and let the operator see why.
        return {
            "enabled": bool(getattr(mission, "depends_on_id", None)),
            "satisfied": False,
            "state": "waiting",
            "label": "Waiting for the prerequisite mission.",
        }


def _mission_rows(mission, now, buffer_mins, lookup, allowed_unit_ids=None):
    detail = getattr(mission, "air_detail", None)
    if detail is None:
        return []

    elements = list(detail.elements.all())
    if not elements:
        # Older air missions can carry the assigned aircraft without the newer
        # numbered Flight Operations elements. Keep them on the map using the
        # detail's schedule and departure/destination fields.
        assigned = list(detail.assigned_aircraft.all())
        if assigned:
            fallback_planes = []
            for assignment in assigned:
                asset = assignment.air_asset
                fallback_planes.append(
                    SimpleNamespace(
                        pk=assignment.pk,
                        aircraft_type=(
                            asset.model
                            if asset is not None and asset.model
                            else assignment.aircraft_model
                        ),
                        tail_number=(
                            assignment.tail_number
                            or (asset.tail_number if asset is not None else "")
                        ),
                        callsign=(
                            assignment.callsign
                            or (asset.callsign if asset is not None else "")
                        ),
                        is_lead=assignment.is_lead,
                        itineraries=[],
                    )
                )
            elements = [
                SimpleNamespace(
                    pk=None,
                    sequence=1,
                    mission="",
                    aircraft=fallback_planes,
                )
            ]
    dependency = _resolved_dependency(mission)
    dependency_ready = not dependency.get("enabled") or dependency.get("satisfied")
    closed = mission.status == "cancelled"
    held = bool(mission.is_held)
    try:
        formation = formation_rules.analyze_instance(
            detail, allowed_unit_ids=allowed_unit_ids
        )
    except Exception:
        formation = {}
    formation_blocked = bool(
        formation.get("checked") and formation.get("separated")
    )

    rows = []
    for element in elements:
        planes = (
            list(element.aircraft.all())
            if hasattr(element.aircraft, "all")
            else list(element.aircraft)
        )
        formation_size = len(planes)
        for index, plane in enumerate(planes):
            asset = _asset_for(plane, lookup)
            if (
                asset is not None
                and allowed_unit_ids is not None
                and asset.opcon_unit_id is not None
                and asset.opcon_unit_id not in allowed_unit_ids
            ):
                continue

            legs = (
                list(plane.itineraries.all())
                if hasattr(plane.itineraries, "all")
                else list(plane.itineraries)
            )
            final_leg = legs[-1] if legs else None
            # A landing at an intermediate stop does not close the mission: the
            # remaining legs still have to be flown. Completion needs that
            # aircraft's last leg to be arrived, an explicitly completed
            # mission, or -- for an older plan saved without itineraries -- the
            # mission-level arrival time.
            final_landed = bool(
                final_leg
                and (
                    final_leg.toot if final_leg.overflight else final_leg.ata
                )
            )
            completed = mission.status == "completed" or (
                final_landed if legs else bool(detail.actual_arrival)
            )
            etd = next((leg.etd for leg in legs if leg.etd), None)
            etd = etd or detail.scheduled_departure or mission.start_at
            eta = next((leg.eta for leg in reversed(legs) if leg.eta), None)
            eta = eta or detail.scheduled_arrival
            actual_departure = next((leg.atd for leg in legs if leg.atd), None)
            points = _route_with_points(legs, detail, asset)
            segments, distance = _route_measurements(points)
            speed_knots = getattr(asset, "cruising_speed_knots", None)
            flight_minutes = None
            if etd and eta and eta > etd:
                flight_minutes = (eta - etd).total_seconds() / 60
            elif etd and distance > 0 and speed_knots:
                flight_minutes = distance / (float(speed_knots) * 1.852) * 60
            # An actual takeoff is stronger evidence than the plan. The board
            # may have dispatched early or late; using the scheduled ETD after
            # an ATD exists would leave an airborne aircraft parked on the map.
            simulation_start = actual_departure or etd

            status = "planned"
            reason = "Waiting for the scheduled departure."
            if closed:
                status, reason = "cancelled", "Mission cancelled."
            elif completed:
                status, reason = "arrived", "Mission marked complete."
            elif held:
                status, reason = "on_hold", "Held by the Operations Center."
            elif not dependency_ready:
                status = "pending_dependency"
                reason = dependency.get("label") or "Waiting for the prerequisite mission."
            elif formation_blocked:
                status = "unavailable"
                reason = (
                    "Element aircraft are recorded more than 5 km apart; "
                    "update their assembly location before flight."
                )
            elif asset is not None and asset.operational_status in formation_rules.UNAVAILABLE_STATUSES:
                status = "unavailable"
                reason = f"Aircraft is {asset.get_operational_status_display().lower()}."
            elif simulation_start is None:
                status = "planned"
                reason = "No ETD or actual takeoff is recorded for this aircraft."
            elif flight_minutes is None:
                status = "timing_missing"
                reason = "An ETA or cruising speed is required for the route."
            elif distance <= 0:
                status = "position_missing"
                reason = (
                    "Origin or destination coordinates are not recorded, "
                    "so no simulated path can be drawn."
                )
            elif now < simulation_start:
                status = "planned"
                reason = (
                    "Waiting for the scheduled ETD."
                    if actual_departure is None
                    else "Waiting for the recorded takeoff."
                )
            else:
                status = "en_route"
                reason = (
                    "Simulated flight in progress from the recorded takeoff."
                    if actual_departure is not None
                    else "Simulated flight in progress."
                )

            arrival_at = None
            progress = 0.0
            remaining_enroute = None
            remaining_to_arrival = None
            elapsed_minutes = 0.0
            current = points[0] if points else None
            heading = 0.0
            traveled = [list(current)] if current else []
            remaining = [[point[0], point[1]] for point in points]

            if simulation_start and flight_minutes is not None:
                arrival_at = simulation_start + dt.timedelta(
                    minutes=float(flight_minutes) + buffer_mins
                )
                total_minutes = float(flight_minutes) + buffer_mins
                elapsed_minutes = _minutes(
                    (now - simulation_start).total_seconds() / 60
                )
                if completed or now >= arrival_at:
                    progress = 1.0
                elif now > simulation_start and total_minutes > 0:
                    progress = max(
                        0.0,
                        min(1.0, elapsed_minutes / total_minutes),
                    )
                remaining_to_arrival = _minutes(
                    (arrival_at - now).total_seconds() / 60
                )
                enroute_end = (
                    simulation_start + dt.timedelta(minutes=float(flight_minutes))
                    if actual_departure is not None
                    else eta
                )
                if enroute_end:
                    remaining_enroute = _minutes(
                        (enroute_end - now).total_seconds() / 60
                    )
                current, heading, traveled, remaining = _position_at(
                    points, segments, distance, distance * progress
                )
                if status == "en_route" and now >= arrival_at:
                    status = "arrived"
                    reason = "Simulated arrival time reached at the destination."

            # The board owns the actuals; the simulation only fills the gaps.
            # Once any leg has a recorded departure or landing, the aircraft is
            # drawn only along the leg it is on: a landed leg parks it at that
            # stop until the next departure is recorded, and elapsed time alone
            # never carries it past a stop the board has already closed.
            recorded_legs = any(
                _leg_window(leg)[0] or _leg_window(leg)[1] for leg in legs
            )
            current_index = _current_leg_index(legs, progress)
            if recorded_legs and points and current_index is not None:
                lengths = _leg_lengths(legs)
                span_total = sum(lengths)
                leg = legs[current_index]
                departed, finished = _leg_window(leg)
                # Timing is anchored to the leg the aircraft is on: the actual
                # departure from that leg's origin plus the leg's own flight
                # time. A late takeoff therefore moves the ETA instead of
                # leaving a stale mission-level estimate on the card.
                leg_departure = (
                    (leg.tot if leg.overflight else leg.atd)
                    or leg.etd
                    or simulation_start
                )
                leg_minutes = None
                if leg.etd and leg.eta and leg.eta > leg.etd:
                    leg_minutes = (leg.eta - leg.etd).total_seconds() / 60
                elif lengths and lengths[current_index] and speed_knots:
                    leg_minutes = (
                        lengths[current_index]
                        / (float(speed_knots) * 1.852)
                        * 60
                    )
                leg_fraction = 0.0
                if leg_departure and leg_minutes is not None:
                    arrival_at = leg_departure + dt.timedelta(
                        minutes=float(leg_minutes) + buffer_mins
                    )
                    leg_total = float(leg_minutes) + buffer_mins
                    elapsed_minutes = _minutes(
                        (now - leg_departure).total_seconds() / 60
                    )
                    if finished:
                        leg_fraction = 1.0
                    elif departed and leg_total > 0:
                        leg_fraction = max(
                            0.0, min(1.0, elapsed_minutes / leg_total)
                        )
                    remaining_to_arrival = _minutes(
                        (arrival_at - now).total_seconds() / 60
                    )
                    remaining_enroute = _minutes(
                        (
                            leg_departure
                            + dt.timedelta(minutes=float(leg_minutes))
                            - now
                        ).total_seconds()
                        / 60
                    )
                if span_total > 0:
                    start_fraction = sum(lengths[:current_index]) / span_total
                    end_fraction = (
                        start_fraction + lengths[current_index] / span_total
                    )
                else:
                    start_fraction, end_fraction = 0.0, 1.0
                progress = start_fraction + (
                    end_fraction - start_fraction
                ) * leg_fraction
                if points and segments:
                    current, heading, traveled, remaining = _position_at(
                        points, segments, distance, distance * progress
                    )
                can_report = status in {"planned", "en_route", "arrived"}
                if can_report and not (closed or held or not dependency_ready):
                    if finished and current_index == len(legs) - 1:
                        status = "arrived"
                        reason = "Landing recorded on the board."
                    elif departed:
                        status = "en_route"
                        reason = (
                            "In flight on this leg; the landing is recorded "
                            "on the board."
                        )
                    elif current_index > 0:
                        stop = legs[current_index - 1].destination or "the stop"
                        status = "landed"
                        reason = (
                            f"Landed at {stop}; waiting for the next leg's "
                            "departure."
                        )

            if closed or held or not dependency_ready or status in {
                "unavailable",
                "timing_missing",
                "position_missing",
            }:
                progress = 0.0
                current = points[0] if points else current
                traveled = [list(current)] if current else []
                remaining = [[point[0], point[1]] for point in points]
                if status == "arrived":
                    progress = 1.0
            if status == "arrived" and points:
                current = points[-1]
                traveled = [[point[0], point[1]] for point in points]
                remaining = [list(current)]

            # Each leg carries its own endpoints and times so the map can show
            # that leg's itinerary details when an operator clicks its line.
            # Geometry comes from the same coordinate lookup used for the
            # whole route, so a clickable leg always matches the drawn track.
            leg_rows = []
            for leg_index, leg in enumerate(legs):
                leg_points = [
                    [round(point[0], 6), round(point[1], 6)]
                    for point in (
                        formation_rules.coordinates_from_location(leg.origin or ""),
                        formation_rules.coordinates_from_location(
                            leg.destination or ""
                        ),
                    )
                    if point is not None
                ]
                leg_rows.append(
                    {
                        "id": leg.pk,
                        "sequence": leg_index + 1,
                        "origin": leg.origin or "",
                        "destination": leg.destination or "",
                        "point_role": getattr(leg, "point_role", "") or "",
                        "mission": leg.mission or "",
                        "overflight": bool(leg.overflight),
                        "return_to_origin": bool(leg.return_to_origin),
                        "status": leg.status,
                        "etd": leg.etd,
                        "eta": leg.eta,
                        "atd": leg.atd,
                        "ata": leg.ata,
                        "tot": leg.tot,
                        "toot": leg.toot,
                        "points": leg_points,
                    }
                )

            current_leg = (
                leg_rows[current_index] if current_index is not None else None
            )

            opcon = getattr(asset, "opcon_unit", None)
            mission_type = (
                (element.mission or "").strip()
                or next((leg.mission for leg in legs if leg.mission), "")
                or mission.title
            )
            origin = (legs[0].origin if legs else "") or detail.departure_location
            destination = (
                (legs[-1].destination if legs else "")
                or detail.destination_location
            )

            rows.append(
                {
                    "id": f"{mission.pk}-{element.pk}-{plane.pk}",
                    "mission_id": mission.pk,
                    "mission_number": mission.mission_number,
                    "mission_title": mission.title,
                    "mission_type": mission_type,
                    "mission_status": mission.status,
                    "mission_status_label": mission.get_status_display(),
                    "element_id": element.pk,
                    "element_sequence": element.sequence,
                    "formation_index": index,
                    "formation_size": formation_size,
                    "is_lead": bool(plane.is_lead),
                    "aircraft_type": getattr(asset, "aircraft_type", "") or "other",
                    "aircraft_type_label": (
                        asset.get_aircraft_type_display()
                        if asset is not None
                        else plane.aircraft_type
                    ),
                    "aircraft_model": getattr(asset, "model", "") or plane.aircraft_type,
                    "tail_number": plane.tail_number,
                    "callsign": plane.callsign,
                    "opcon_unit": (
                        {
                            "id": opcon.pk,
                            "name": opcon.name,
                            "acronym": opcon.acronym,
                        }
                        if opcon is not None
                        else None
                    ),
                    "opcon_unit_label": (
                        " — ".join(
                            part for part in (opcon.acronym, opcon.name) if part
                        )
                        if opcon is not None
                        else ""
                    ),
                    "origin": origin,
                    "destination": destination,
                    "etd": etd,
                    "actual_departure_at": actual_departure,
                    "simulation_started_at": simulation_start,
                    "scheduled_arrival_at": eta,
                    "simulated_arrival_at": arrival_at,
                    "estimated_enroute_minutes": _minutes(flight_minutes or 0),
                    "buffer_minutes": buffer_mins,
                    "elapsed_minutes": elapsed_minutes,
                    "remaining_enroute_minutes": remaining_enroute,
                    "remaining_to_arrival_minutes": remaining_to_arrival,
                    "progress_percent": round(progress * 100, 1),
                    "position": current,
                    "heading_degrees": heading,
                    "planned_route": [[point[0], point[1]] for point in points],
                    "traveled_route": traveled,
                    "remaining_route": remaining,
                    "legs": leg_rows,
                    "current_leg": current_leg,
                    "route_distance_km": round(distance, 3),
                    "status": status,
                    "status_label": _status_label(status),
                    "status_reason": reason,
                    "is_simulated": True,
                    "is_available": not formation_blocked
                    and not (
                        asset is not None
                        and asset.operational_status in formation_rules.UNAVAILABLE_STATUSES
                    ),
                    "dependency": dependency,
                    "cruising_speed_knots": speed_knots,
                }
            )
    return rows


def snapshot(missions, *, now=None, allowed_unit_ids=None):
    """Build one consistent map snapshot for a visible mission queryset."""
    now = timeutils.to_manila(now) or timeutils.now()
    buffer_mins = buffer_minutes()
    assets = list(AirAsset.objects.select_related("opcon_unit"))
    lookup = _asset_lookup(assets)
    rows = []
    for mission in missions:
        rows.extend(
            _mission_rows(
                mission,
                now,
                buffer_mins,
                lookup,
                allowed_unit_ids=allowed_unit_ids,
            )
        )
    counts = Counter(row["status"] for row in rows)
    return {
        "server_time": now,
        "buffer_minutes": buffer_mins,
        "is_simulated": True,
        "disclaimer": (
            "Simulated aircraft positions derived from scheduled ETD or recorded "
            "actual takeoff, route and cruising speed. Not live GPS or ADS-B "
            "tracking."
        ),
        "aircraft": rows,
        "summary": {
            "total": len(rows),
            "planned": counts["planned"],
            "en_route": counts["en_route"],
            "arrived": counts["arrived"],
            "on_hold": counts["on_hold"],
            "cancelled": counts["cancelled"],
            "pending_dependency": counts["pending_dependency"],
            "unavailable": counts["unavailable"],
            "timing_missing": counts["timing_missing"],
            "position_missing": counts["position_missing"],
        },
    }
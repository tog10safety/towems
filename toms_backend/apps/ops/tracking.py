"""Vehicle movement tracking for ground operations.

The planned route belongs to ``VehicleTrip``. This module adds the execution
record beside it: one ``VehicleRun`` per vehicle, append-only movement events,
and GPS pings from the driver's phone. It deliberately does not modify the
planned route or the last-known ``VehiclePosition`` snapshot used by the
operational picture.
"""

from __future__ import annotations

import datetime as dt
import math

from django.db import transaction
from django.db.models import F, Subquery, Window
from django.db.models.functions import RowNumber
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core import roles, timeutils
from apps.core.permissions import can
from apps.ops.models import (
    Mission,
    MovementEvent,
    Notification,
    TrackingPing,
    Vehicle,
    VehicleRun,
    VehicleTripVehicle,
)


# Roles that may watch the movement board. The Operations Center also manages
# runs; command/oversight roles are read-only.
MONITOR_ROLES = (
    roles.ROLE_OPERATIONS_CENTER,
    roles.ROLE_COMMANDER,
    roles.ROLE_DEPUTY_COMMANDER,
    roles.ROLE_DIRECTOR_OPERATIONS,
    roles.ROLE_SAFETY_OFFICER,
    roles.ROLE_OPERATIONS_OFFICER,
)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def can_manage(user, mission) -> bool:
    """The Operations Center (and Administrator) may start/close/correct."""
    return bool(user and mission and can(user, roles.CAP_MANAGE_MISSIONS, mission.unit_id))


def can_monitor(user, mission, *, capabilities=None, handoff_ids=None) -> bool:
    """Read the whole board: explicit monitoring roles, a manager, or the
    destination unit's Operations Center that received the handoff.

    Monitoring is the destination Operations Center's work alone. The command
    posts it forwards the details to may read the mission, but not its live
    board; recording departures, stops and corrections stays with the owning
    unit, its drivers and the convoy lead.

    A caller drawing a whole board may pass ``capabilities`` for the mission's
    unit and the set of ``handoff_ids`` it has already loaded, so a poll costs
    one permission read per unit instead of one per mission.
    """
    if not user or not mission:
        return False
    caps = (
        capabilities
        if capabilities is not None
        else roles.capabilities_for(user, mission.unit_id)
    )
    if roles.CAP_MANAGE_MISSIONS in caps or roles.CAP_MONITOR_VEHICLE_TRACKING in caps:
        return True

    if handoff_ids is not None:
        return str(mission.id) in handoff_ids

    from apps.ops import aor_handoff

    return Notification.objects.filter(
        recipient_id=getattr(user, "id", None),
        category=aor_handoff.HANDOFF_CATEGORY,
        related_type="mission",
        related_id=str(mission.id),
    ).exists()


# ---------------------------------------------------------------------------
# Abort order
# ---------------------------------------------------------------------------

ABORT_CATEGORY = "mission_abort"


def abort_payload(mission) -> dict | None:
    """The abort order on a mission, or None while it is still running."""
    if getattr(mission, "aborted_at", None) is None:
        return None
    return {
        "at": mission.aborted_at,
        "reason": mission.abort_reason,
        "destination": mission.abort_destination,
        "target": {
            "name": mission.abort_target_name,
            "latitude": mission.abort_target_latitude,
            "longitude": mission.abort_target_longitude,
        },
    }


def _abort_runs(mission):
    return list(
        VehicleRun.objects.filter(mission=mission)
        .exclude(status=VehicleRun.STATUS_CANCELLED)
        .select_related("vehicle", "driver", "trip")
        .prefetch_related("events", "trip__itinerary")
        .order_by("-is_lead", "vehicle__registration_number", "id")
    )


#: How each end point is worded in the order sent to the drivers.
ABORT_INSTRUCTIONS = {
    Mission.ABORT_STATION: "return to station",
    Mission.ABORT_LAST_STOP: "proceed to the last stop you have reached",
    Mission.ABORT_FINAL_STOP: "continue to the planned final stop",
}


def _abort_target(mission, destination, runs):
    """The pin an abort ends on.

    ``last_stop`` is the stop the vehicles have actually recorded reaching, so
    an abort never sends a convoy further out; the plan's final stop is a
    separate, explicitly labelled choice.
    """
    stops = list(runs[0].trip.itinerary.all())
    if not stops:
        raise ValidationError(
            {"detail": "This mission's itinerary has no stops to end on."}
        )

    if destination == Mission.ABORT_STATION:
        stop = stops[0]
    elif destination == Mission.ABORT_FINAL_STOP:
        stop = stops[-1]
    else:
        reached = None
        for run in runs:
            for row in _run_stop_state(run):
                if row["arrived_at"] is None:
                    continue
                if reached is None or row["arrived_at"] > reached[0]:
                    reached = (row["arrived_at"], row["stop"])
        if reached is None:
            raise ValidationError(
                {
                    "destination": "No stop has been recorded as reached yet; "
                    "order a return to station or the planned final stop instead."
                }
            )
        stop = reached[1]

    if stop.latitude is None or stop.longitude is None:
        raise ValidationError(
            {"detail": f"{stop.location_name} has no coordinates to pin."}
        )
    return stop


def abort_mission(mission, actor, *, destination, reason=""):
    """The Operations Center aborts a mission in progress and pins its end.

    The mission is not cancelled by the order: its vehicles are still out, so
    the board keeps watching them while the drivers make for the pinned point.
    Closing the mission stays the Operations Center's separate decision.
    """
    if mission.status != "active":
        raise ValidationError(
            {"detail": "Only a mission in progress can be aborted."}
        )
    if mission.aborted_at is not None:
        raise ValidationError({"detail": "This mission has already been aborted."})

    destination = str(destination or "").strip()
    if destination not in ABORT_INSTRUCTIONS:
        raise ValidationError(
            {
                "destination": "Choose a return to station, the last stop reached, "
                "or the planned final stop."
            }
        )
    text = " ".join(str(reason or "").split())[:500]
    if not text:
        raise ValidationError(
            {"reason": "Tell the drivers why the mission is aborted."}
        )

    runs = _abort_runs(mission)
    if not runs:
        raise ValidationError(
            {"detail": "This mission has no vehicle movement to end."}
        )
    stop = _abort_target(mission, destination, runs)

    mission.aborted_at = timeutils.now()
    mission.abort_reason = text
    mission.abort_destination = destination
    mission.abort_target_name = stop.location_name
    mission.abort_target_latitude = stop.latitude
    mission.abort_target_longitude = stop.longitude
    mission.aborted_by = actor
    mission.save(
        update_fields=[
            "aborted_at",
            "abort_reason",
            "abort_destination",
            "abort_target_name",
            "abort_target_latitude",
            "abort_target_longitude",
            "aborted_by",
            "updated_at",
        ]
    )

    instruction = f"{ABORT_INSTRUCTIONS[destination]} ({stop.location_name})"
    for run in runs:
        _event(
            run,
            MovementEvent.KIND_EXCEPTION,
            actor=actor,
            note=(
                "Mission aborted by the Operations Center: "
                f"{text} Instructed to {instruction}."
            ),
        )

    from apps.ops.notifications import notify_many

    drivers = {}
    for run in runs:
        if run.driver_id:
            drivers[run.driver_id] = run.driver
    return notify_many(
        list(drivers.values()),
        title=f"MISSION ABORTED: {mission.mission_number}",
        message=(
            f"The Operations Center aborted {mission.mission_number} "
            f"({mission.title}). {text} You are instructed to {instruction} "
            "and report when you arrive."
        ),
        level="danger",
        category=ABORT_CATEGORY,
        priority="urgent",
        related=("mission", mission.id),
        actor=actor,
        action_url=f"/missions/{mission.id}/track/driver",
        requires_action=False,
    )


def _handoff_mission_ids(user) -> set[str]:
    """Mission ids whose handoff reached ``user``, in one read for the board."""
    from apps.ops import aor_handoff

    return set(
        Notification.objects.filter(
            recipient_id=getattr(user, "id", None),
            category=aor_handoff.HANDOFF_CATEGORY,
            related_type="mission",
        ).values_list("related_id", flat=True)
    )


def _lead_run(user, mission):
    return (
        VehicleRun.objects.filter(mission=mission, is_lead=True, driver_id=user.id)
        .only("id", "driver_id", "is_lead")
        .first()
    )


def can_act(user, run) -> bool:
    """Record a departure/stop/completion event for this run."""
    if not user or not run:
        return False
    if can_manage(user, run.mission):
        return True
    if run.driver_id == user.id:
        return True
    # The convoy lead may record convoy milestones for the other vehicles.
    return bool(_lead_run(user, run.mission))


def can_ping(user, run) -> bool:
    """Only the assigned driver sends that vehicle's GPS; managers may correct."""
    if not user or not run:
        return False
    return run.driver_id == user.id or can_manage(user, run.mission)


def source_for(user, run) -> str:
    """Who is recording this event.

    A driver recording their own vehicle is always the driver, even when that
    vehicle is the only one and therefore carries the lead flag. ``lead`` means
    the convoy lead recording a *different* vehicle's milestone.
    """
    if can_manage(user, run.mission):
        return MovementEvent.SOURCE_OPERATIONS_CENTER
    if run.driver_id == user.id:
        return MovementEvent.SOURCE_DRIVER
    if _lead_run(user, run.mission):
        return MovementEvent.SOURCE_LEAD
    return MovementEvent.SOURCE_DRIVER


def _get_run(mission, run_id) -> VehicleRun:
    return get_object_or_404(VehicleRun, mission=mission, pk=run_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _actual(value=None) -> dt.datetime:
    if value is None:
        return timeutils.now()
    if isinstance(value, dt.datetime):
        return timeutils.to_manila(value)
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise ValidationError({"actual_at": "Use an ISO date and time."})
    return timeutils.to_manila(parsed)


def _stop_events(run, kind=None):
    queryset = MovementEvent.objects.filter(run=run)
    if kind is not None:
        queryset = queryset.filter(kind=kind)
    return queryset


def _event(
    run,
    kind,
    *,
    actor,
    source=None,
    stop=None,
    actual_at=None,
    planned_at=None,
    note="",
    latitude=None,
    longitude=None,
):
    return MovementEvent.objects.create(
        run=run,
        stop=stop,
        kind=kind,
        planned_at=planned_at,
        actual_at=_actual(actual_at),
        source=source or source_for(actor, run),
        recorded_by=actor,
        latitude=latitude,
        longitude=longitude,
        note=note,
    )


def _driver_notification(run, mission, actor):
    """Create one durable driver action for this run, without duplicates."""
    if not run.driver_id:
        return None
    from apps.ops.notifications import notify

    already = Notification.objects.filter(
        recipient_id=run.driver_id,
        related_type="vehicle_run",
        related_id=str(run.id),
        requires_action=True,
        action_status__in=[Notification.ACTION_OPEN, Notification.ACTION_IN_PROGRESS],
    ).exists()
    if already:
        return None
    label = f"{run.vehicle.registration_number} · {mission.mission_number}"
    return notify(
        run.driver,
        title=f"Movement task: {label}",
        message=(
            f"Confirm departure for {label}. The planned ETD is "
            f"{timeutils.format_datetime(run.etd) or 'not set'}."
        ),
        category="movement",
        priority="high",
        related=("vehicle_run", run.id),
        actor=actor,
        requires_action=True,
        action_label="Open driver tracking",
        action_url=f"/missions/{mission.id}/track/driver",
        due_at=run.etd,
    )


def _lead_notification(run, mission, actor):
    if not run.driver_id:
        return None
    from apps.ops.notifications import notify

    already = Notification.objects.filter(
        recipient_id=run.driver_id,
        related_type="vehicle_run",
        related_id=str(run.id),
        requires_action=True,
        action_status__in=[Notification.ACTION_OPEN, Notification.ACTION_IN_PROGRESS],
    ).exists()
    if already:
        return None
    return notify(
        run.driver,
        title=f"Convoy lead: {mission.mission_number}",
        message=(
            f"{run.vehicle.registration_number} is the lead vehicle. "
            "Keep the convoy board open and confirm departure when the convoy rolls."
        ),
        category="movement",
        priority="high",
        related=("vehicle_run", run.id),
        actor=actor,
        requires_action=True,
        action_label="Open convoy board",
        # The lead designation opens maximised: the map is the working screen a
        # convoy lead needs, not a page to hunt for.
        action_url=f"/missions/{mission.id}/track/driver?fullscreen=1",
        due_at=run.etd,
    )


def monitor_recipients(mission):
    from apps.command.services import responsible_users

    return responsible_users(mission.unit_id, *MONITOR_ROLES)


def _notify_monitors(mission, actor, *, title, message, priority="normal"):
    from apps.ops.notifications import notify_many

    return notify_many(
        monitor_recipients(mission),
        title=title,
        message=message,
        category="movement",
        priority=priority,
        related=("mission", mission.id),
        actor=actor,
        action_url=f"/missions/{mission.id}/track",
        requires_action=False,
    )


def _assign_lead(mission, lead, actor=None):
    """Make ``lead`` the single convoy lead, clearing the previous one first."""
    if lead is None:
        return None
    if lead.mission_id != mission.id:
        raise ValidationError({"run": "That vehicle run is not part of this mission."})
    if lead.driver_id is None:
        raise ValidationError(
            {"run": "The lead vehicle needs a designated driver before it can lead."}
        )
    previous = VehicleRun.objects.filter(mission=mission, is_lead=True).first()
    if previous and previous.id == lead.id:
        return lead
    VehicleRun.objects.filter(mission=mission, is_lead=True).exclude(pk=lead.pk).update(
        is_lead=False
    )
    if not lead.is_lead:
        lead.is_lead = True
        lead.save(update_fields=["is_lead", "updated_at"])
    if actor is not None:
        _lead_notification(lead, mission, actor)
    return lead


def _set_vehicle_assignment(run):
    """Keep the trip and vehicle statuses aligned with the run's state."""
    trip = run.trip
    if trip.status in {"draft", "planned", "approved"}:
        trip.status = "in_progress"
        trip.save(update_fields=["status", "updated_at"])
    vehicle = run.vehicle
    if vehicle.status == "available":
        vehicle.status = "on_trip"
        vehicle.save(update_fields=["status", "updated_at"])


# ---------------------------------------------------------------------------
# Start and finish
# ---------------------------------------------------------------------------


@transaction.atomic
def start_for_mission(mission, actor, *, lead_run_id=None):
    """Create one active run per planned vehicle and notify its driver.

    The Operations Center has just dispatched the mission, so the dispatch time
    is recorded as the run's initial ATD with ``atd_source='auto_dispatch'``.
    This gives the board an ATD immediately, while the driver's later
    confirmation replaces it with the actual wheel-roll time and leaves both
    entries in the event log. Nothing is labelled as a driver-confirmed time.
    """
    if mission.status != "active":
        raise ValidationError({"detail": "Only an active mission can start tracking."})

    started_at = mission.dispatched_at or timeutils.now()
    if mission.dispatched_at is None:
        mission.dispatched_at = started_at
        mission.save(update_fields=["dispatched_at", "updated_at"])

    runs = []
    created_runs = []
    trips = (
        mission.trips.exclude(status="cancelled")
        .select_related("mission")
        .prefetch_related("vehicles__vehicle", "vehicles__driver", "itinerary")
    )
    for trip in trips:
        etd = trip.departure_at or mission.start_at
        for link in trip.vehicles.all():
            run, created = VehicleRun.objects.get_or_create(
                mission=mission,
                trip=trip,
                vehicle=link.vehicle,
                defaults={
                    "driver_id": link.driver_id,
                    "etd": etd,
                    "status": VehicleRun.STATUS_ACTIVE,
                    "atd": started_at,
                    "atd_source": VehicleRun.ATD_AUTO_DISPATCH,
                    "notes": "ATD set from the Operations Center dispatch time.",
                },
            )
            if not created:
                updates = []
                if run.driver_id is None and link.driver_id:
                    run.driver_id = link.driver_id
                    updates.append("driver")
                if run.etd is None and etd:
                    run.etd = etd
                    updates.append("etd")
                if run.atd is None:
                    run.atd = started_at
                    run.atd_source = VehicleRun.ATD_AUTO_DISPATCH
                    updates.extend(["atd", "atd_source"])
                if updates:
                    run.save(update_fields=list(dict.fromkeys(updates)) + ["updated_at"])
            else:
                _event(
                    run,
                    MovementEvent.KIND_DEPART_ORIGIN,
                    actor=actor,
                    source=MovementEvent.SOURCE_AUTO_DISPATCH,
                    actual_at=started_at,
                    planned_at=etd,
                    note="Dispatch time recorded automatically; awaiting driver confirmation.",
                )
                created_runs.append(run)
            runs.append(run)

    runs.sort(key=lambda row: (row.vehicle.registration_number, row.id))

    lead = None
    if lead_run_id is not None:
        lead = get_object_or_404(VehicleRun, mission=mission, pk=lead_run_id)
    else:
        lead = next((row for row in runs if row.is_lead and row.driver_id), None)
        if lead is None:
            lead = next((row for row in runs if row.driver_id), None)
        if lead is None:
            lead = runs[0] if runs else None
    if lead is not None and lead.driver_id:
        _assign_lead(mission, lead, actor)

    for run in runs:
        if run.driver_id:
            _driver_notification(run, mission, actor)
        _set_vehicle_assignment(run)

    return runs


@transaction.atomic
def set_lead(mission, run, actor):
    """Operations Center changes the vehicle that coordinates the convoy."""
    if mission.status != "active":
        raise ValidationError({"detail": "The mission must be active before a lead is set."})
    if run.mission_id != mission.id:
        raise ValidationError({"run": "That vehicle run is not part of this mission."})
    return _assign_lead(mission, run, actor)


@transaction.atomic
def depart(run, actor, *, at=None):
    """Confirm the actual departure; replaces an auto-dispatch ATD if present."""
    if run.mission.status != "active":
        raise ValidationError({"detail": "This mission is not active."})
    if run.status in {VehicleRun.STATUS_COMPLETED, VehicleRun.STATUS_CANCELLED}:
        raise ValidationError({"detail": "This vehicle run is closed."})
    if run.atd is not None and run.atd_source != VehicleRun.ATD_AUTO_DISPATCH:
        # A confirmed ATD is idempotent for a double tap.
        return run

    actual = _actual(at)
    source = source_for(actor, run)
    run.atd = actual
    run.atd_source = source
    run.status = VehicleRun.STATUS_ACTIVE
    run.save(update_fields=["atd", "atd_source", "status", "updated_at"])
    _set_vehicle_assignment(run)
    _event(
        run,
        MovementEvent.KIND_DEPART_ORIGIN,
        actor=actor,
        source=source,
        actual_at=actual,
        planned_at=run.etd,
        note="Driver/lead confirmed departure.",
    )
    from apps.ops.notifications import complete_action

    complete_action(
        related_type="vehicle_run",
        related_ids=[run.id],
        recipients=[run.driver] if run.driver_id else None,
        note="Departure confirmed.",
        actor=actor,
    )
    return run


#: How far the driver's own last position may be from a stop and still count as
#: being at it, for an arrival or a departure. Wide enough for a convoy halted
#: across a camp gate or on a fix with poor accuracy, narrow enough that a stop
#: in another town cannot be claimed by mistake. The fix's own accuracy is added.
STOP_RADIUS_M = 300

#: How recently the vehicle must have reported a position for a claim about being
#: at a stop to be checked against it. An older fix is no evidence at all: the
#: claim is recorded as unverified rather than judged near or far from the stop.
FIX_MAX_AGE_SECONDS = 300


def _distance_metres(first, second) -> float:
    """Great-circle distance between two ``(latitude, longitude)`` pairs."""
    lat1, lon1 = math.radians(first[0]), math.radians(first[1])
    lat2, lon2 = math.radians(second[0]), math.radians(second[1])
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(a)))


def _reported_position(run, stop) -> tuple[float | None, float | None, bool]:
    """Where the driver was when they tapped, and whether it was reported.

    Movement events keep the vehicle's own last fix, so the log says where the
    driver actually was at the tap. A stop's coordinates are only a placeholder
    for a phone that has never reported a position; the caller notes that.
    """
    if run.last_latitude is not None and run.last_longitude is not None:
        return run.last_latitude, run.last_longitude, True
    return stop.latitude, stop.longitude, False


def _age_text(seconds) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} hours"
    if seconds >= 90:
        return f"{seconds / 60:.0f} minutes"
    return f"{seconds:.0f} seconds"


def _stop_fix(run, stop, *, at=None) -> tuple[float | None, float, float | None]:
    """Distance from the stop to the vehicle's last fix, the allowed radius, age.

    ``distance_m`` is None when the claim cannot be checked at all - the vehicle
    has never reported a position, or the stop has none - which is never treated
    as a match. ``age_s`` is None when no fix time is stored; the caller treats
    an old or missing timestamp as unverified rather than as a pass.
    """
    allowed = STOP_RADIUS_M + max(0.0, float(run.last_accuracy_m or 0))
    age = (
        max(0.0, (_actual(at) - run.last_ping_at).total_seconds())
        if run.last_ping_at is not None
        else None
    )
    if (
        run.last_latitude is None
        or run.last_longitude is None
        or stop.latitude is None
        or stop.longitude is None
    ):
        return None, allowed, age
    return (
        _distance_metres(
            (run.last_latitude, run.last_longitude), (stop.latitude, stop.longitude)
        ),
        allowed,
        age,
    )


def _is_stale(age) -> bool:
    return age is None or age > FIX_MAX_AGE_SECONDS


def _unverified_note(opening, stop, distance, allowed, age):
    if distance is None:
        return (
            f"{opening}: this vehicle has not reported a position at all; the claim "
            "could not be verified."
        )
    parts = []
    if distance is not None:
        parts.append(
            f"the last reported position was {distance / 1000:.2f} km from "
            f"{stop.location_name} (accepted radius {allowed / 1000:.1f} km)"
        )
    parts.append(
        f"{_age_text(age)} old (accepted age {FIX_MAX_AGE_SECONDS // 60} minutes)"
        if age is not None
        else "no position time was reported"
    )
    return f"{opening}: " + " and ".join(parts) + "; the claim could not be verified."


def _mismatch_note(opening, stop, distance, allowed):
    return (
        f"{opening}: the last reported position was {distance / 1000:.2f} km from "
        f"{stop.location_name} (accepted radius {allowed / 1000:.1f} km); the claim "
        "does not match that position."
    )


def _raise_to_operations_center(run, actor, title, note):
    """Put a claim that does not hold up in front of the Operations Center."""
    _notify_monitors(
        run.mission,
        actor,
        title=f"{title} · {run.mission.mission_number}",
        message=note,
        priority="high",
    )


@transaction.atomic
def arrive_stop(run, stop, actor, *, at=None, force=False):
    if run.mission.status != "active":
        raise ValidationError({"detail": "This mission is not active."})
    if run.status != VehicleRun.STATUS_ACTIVE:
        raise ValidationError({"detail": "This vehicle run has not started."})
    if stop.trip_id != run.trip_id:
        raise ValidationError({"stop": "That stop is not on this vehicle's itinerary."})

    stops = list(run.trip.itinerary.order_by("sequence"))
    if stop not in stops:
        raise ValidationError({"stop": "That stop is not on this vehicle's itinerary."})

    arrived = _stop_events(run, MovementEvent.KIND_ARRIVE_STOP).filter(stop=stop).exists() or _stop_events(
        run, MovementEvent.KIND_ARRIVE_DESTINATION
    ).filter(stop=stop).exists()
    if arrived:
        raise ValidationError({"detail": "Arrival at this stop is already recorded."})
    if _stop_events(run, MovementEvent.KIND_DEPART_STOP).filter(stop=stop).exists():
        raise ValidationError({"detail": "This stop has already been departed."})

    # An abort replaces whatever was left of the plan: the driver heads for the
    # pinned point, so the stops the order skipped are no longer a condition.
    aborted = run.mission.aborted_at is not None
    previous = [] if aborted else [row for row in stops if row.sequence < stop.sequence]
    origin_id = stops[0].id if stops else None
    for row in previous:
        # The first itinerary row is the origin. Its departure is the run's
        # confirmed wheel-roll, not an arrival that the driver has to record.
        if row.id == origin_id and _confirmed_origin_departure(run) is not None:
            continue
        if not (
            _stop_events(run, MovementEvent.KIND_ARRIVE_STOP).filter(stop=row).exists()
            or _stop_events(run, MovementEvent.KIND_ARRIVE_DESTINATION)
            .filter(stop=row)
            .exists()
        ):
            raise ValidationError(
                {"detail": f"Record arrival at stop {row.sequence} ({row.location_name}) first."}
            )
    later = [] if aborted else [row for row in stops if row.sequence > stop.sequence]
    for row in later:
        if (
            _stop_events(run, MovementEvent.KIND_ARRIVE_STOP).filter(stop=row).exists()
            or _stop_events(run, MovementEvent.KIND_ARRIVE_DESTINATION)
            .filter(stop=row)
            .exists()
        ):
            raise ValidationError(
                {"detail": f"Stop {row.sequence} is already recorded out of order."}
            )

    # The arrival is a claim; the vehicle's own last fix is the evidence for it,
    # but only while that fix is recent enough to be evidence at all.
    distance, allowed, age = _stop_fix(run, stop, at=at)
    far = distance is not None and distance > allowed
    stale = distance is not None and _is_stale(age)
    if (distance is None or far or stale) and not force:
        if distance is None:
            detail = (
                f"This vehicle has not reported a position, so an arrival at "
                f"{stop.location_name} cannot be checked against it."
            )
        elif far and stale:
            detail = (
                f"The vehicle's last reported position is {distance / 1000:.2f} km "
                f"from {stop.location_name} and {_age_text(age)} old; an arrival "
                f"needs a position within {allowed / 1000:.1f} km reported no more "
                f"than {FIX_MAX_AGE_SECONDS // 60} minutes ago."
            )
        elif stale:
            detail = (
                f"The vehicle last reported its position {_age_text(age)} ago; an "
                f"arrival needs a position no older than "
                f"{FIX_MAX_AGE_SECONDS // 60} minutes. The fix cannot say whether "
                f"the convoy is at {stop.location_name}."
            )
        else:
            detail = (
                f"The vehicle's last reported position is {distance / 1000:.2f} km "
                f"from {stop.location_name}; an arrival is only accepted within "
                f"{allowed / 1000:.1f} km."
            )
        raise ValidationError(
            {
                "detail": detail + " Record the arrival with an override if the stop has been reached.",
                "override": (
                    "Send force=true to record this arrival as unverified; it is "
                    "noted for the Operations Center."
                ),
            }
        )
    if distance is None or stale:
        note = _unverified_note(
            "Arrival recorded by override", stop, distance, allowed, age
        )
    elif far:
        note = _mismatch_note(
            "Arrival recorded by override", stop, distance, allowed
        )
    else:
        note = ""

    actual = _actual(at)
    kind = (
        MovementEvent.KIND_ARRIVE_DESTINATION
        if stops and stop.id == stops[-1].id
        else MovementEvent.KIND_ARRIVE_STOP
    )
    source = source_for(actor, run)
    latitude, longitude, _reported = _reported_position(run, stop)
    event = _event(
        run,
        kind,
        actor=actor,
        source=source,
        stop=stop,
        actual_at=actual,
        planned_at=stop.planned_arrival or run.etd,
        note=note,
        latitude=latitude,
        longitude=longitude,
    )

    registration = run.vehicle.registration_number
    if distance is None or stale:
        _raise_to_operations_center(
            run,
            actor,
            f"{registration} arrival at {stop.location_name} could not be verified",
            note,
        )
    elif far:
        _raise_to_operations_center(
            run,
            actor,
            f"{registration} arrival does not match {stop.location_name}",
            note,
        )
    return event


@transaction.atomic
def depart_stop(run, stop, actor, *, at=None):
    if run.mission.status != "active":
        raise ValidationError({"detail": "This mission is not active."})
    if run.status != VehicleRun.STATUS_ACTIVE:
        raise ValidationError({"detail": "This vehicle run is not active."})
    if stop.trip_id != run.trip_id:
        raise ValidationError({"stop": "That stop is not on this vehicle's itinerary."})
    if _stop_events(run, MovementEvent.KIND_DEPART_STOP).filter(stop=stop).exists():
        raise ValidationError({"detail": "Departure from this stop is already recorded."})
    if not _stop_events(run, MovementEvent.KIND_ARRIVE_STOP).filter(stop=stop).exists() and not _stop_events(
        run, MovementEvent.KIND_ARRIVE_DESTINATION
    ).filter(stop=stop).exists():
        raise ValidationError({"detail": "Record arrival at this stop before departing."})

    # A departure is a claim too: the log keeps the fix beside it, and a claim
    # that disagrees with the position is raised rather than silently recorded.
    distance, allowed, age = _stop_fix(run, stop, at=at)
    far = distance is not None and distance > allowed
    stale = distance is not None and _is_stale(age)
    if far:
        note = _mismatch_note("Departure recorded", stop, distance, allowed)
    elif stale:
        note = _unverified_note("Departure recorded", stop, distance, allowed, age)
    elif distance is None:
        note = "Departure recorded without a reported position."
    else:
        note = ""

    actual = _actual(at)
    latitude, longitude, _reported = _reported_position(run, stop)
    event = _event(
        run,
        MovementEvent.KIND_DEPART_STOP,
        actor=actor,
        source=source_for(actor, run),
        stop=stop,
        actual_at=actual,
        planned_at=stop.planned_departure or stop.planned_arrival or run.etd,
        note=note,
        latitude=latitude,
        longitude=longitude,
    )
    if far:
        _raise_to_operations_center(
            run,
            actor,
            (
                f"{run.vehicle.registration_number} departure does not match "
                f"{stop.location_name}"
            ),
            note,
        )
    return event


@transaction.atomic
def _abort_stop(run):
    """This run's own copy of the pinned abort target, if its plan has one.

    Each vehicle keeps its own trip and itinerary, so the target is matched by
    the name and coordinates the order pinned rather than by a foreign key.
    """
    mission = run.mission
    if mission.aborted_at is None:
        return None
    for stop in run.trip.itinerary.all():
        if stop.location_name != mission.abort_target_name:
            continue
        if (
            mission.abort_target_latitude is None
            or stop.latitude is None
            or (
                abs(stop.latitude - mission.abort_target_latitude) < 1e-6
                and abs(stop.longitude - mission.abort_target_longitude) < 1e-6
            )
        ):
            return stop
    return None


def complete_run(run, actor, *, at=None, note=""):
    if run.mission.status != "active":
        raise ValidationError({"detail": "This mission is not active."})
    if run.status != VehicleRun.STATUS_ACTIVE:
        raise ValidationError({"detail": "This vehicle run is not active."})
    stops = list(run.trip.itinerary.all())
    if stops:
        last = stops[-1]
        # After an abort the run ends at the pinned point, not at the plan's
        # final stop. A return to station needs no arrival tap: closing the run
        # is the record of the return.
        target = _abort_stop(run)
        if target is None and run.mission.aborted_at is None:
            target = last
        if (
            target is not None
            and run.mission.abort_destination != Mission.ABORT_STATION
            and not (
                _stop_events(run, MovementEvent.KIND_ARRIVE_STOP)
                .filter(stop=target)
                .exists()
                or _stop_events(run, MovementEvent.KIND_ARRIVE_DESTINATION)
                .filter(stop=target)
                .exists()
            )
        ):
            raise ValidationError(
                {
                    "detail": (
                        f"Record arrival at {target.location_name} before completing "
                        "this run."
                        if run.mission.aborted_at is not None
                        else "Record arrival at the final stop before completing this run."
                    )
                }
            )

    actual = _actual(at)
    run.status = VehicleRun.STATUS_COMPLETED
    run.completed_at = actual
    run.save(update_fields=["status", "completed_at", "updated_at"])
    trip = run.trip
    trip.status = "completed"
    trip.save(update_fields=["status", "updated_at"])
    if not VehicleRun.objects.filter(
        vehicle=run.vehicle, status=VehicleRun.STATUS_ACTIVE
    ).exclude(pk=run.pk).exists():
        vehicle = run.vehicle
        vehicle.status = "available"
        vehicle.save(update_fields=["status", "updated_at"])
    _event(
        run,
        MovementEvent.KIND_COMPLETE,
        actor=actor,
        actual_at=actual,
        planned_at=run.trip.return_at,
        note=note,
    )
    from apps.ops.notifications import complete_action

    complete_action(
        related_type="vehicle_run",
        related_ids=[run.id],
        recipients=[run.driver] if run.driver_id else None,
        note="Vehicle run completed.",
        actor=actor,
    )
    _notify_monitors(
        run.mission,
        actor,
        title=f"{run.vehicle.registration_number} completed {run.mission.mission_number}",
        message=f"{run.vehicle.registration_number} completed its movement for {run.mission.title}.",
    )
    return run


@transaction.atomic
def complete_mission(mission, actor, *, force=False, note=""):
    """Close the mission after all vehicle runs are complete.

    ``force`` is an explicit Operations Center override for a missing vehicle
    or an unusable log; it cancels the open runs and keeps the override note.
    """
    if mission.status != "active":
        raise ValidationError({"detail": "Only an active mission can be completed."})
    runs = list(mission.vehicle_runs.all())
    open_runs = [run for run in runs if run.status not in {
        VehicleRun.STATUS_COMPLETED,
        VehicleRun.STATUS_CANCELLED,
    }]
    if open_runs and not force:
        labels = ", ".join(
            f"{run.vehicle.registration_number} ({run.status})" for run in open_runs
        )
        raise ValidationError(
            {
                "detail": (
                    "Every vehicle run must be completed or cancelled before the "
                    f"mission can be closed. Open: {labels}."
                )
            }
        )
    actual = timeutils.now()
    for run in open_runs:
        run.status = VehicleRun.STATUS_CANCELLED
        run.completed_at = actual
        run.save(update_fields=["status", "completed_at", "updated_at"])
        _event(
            run,
            MovementEvent.KIND_CANCEL,
            actor=actor,
            source=MovementEvent.SOURCE_OPERATIONS_CENTER,
            actual_at=actual,
            note=note or "Mission closed by the Operations Center with an open run.",
        )

    # A run cancelled by the override puts its vehicle back in the register:
    # nothing is still tracking it.
    if open_runs:
        released = {run.vehicle_id for run in open_runs}
        held = set(
            VehicleRun.objects.filter(
                vehicle_id__in=released, status=VehicleRun.STATUS_ACTIVE
            ).values_list("vehicle_id", flat=True)
        )
        Vehicle.objects.filter(pk__in=released - held, status="on_trip").update(
            status="available", updated_at=actual
        )

    mission.status = "completed"
    mission.completed_at = actual
    mission.save(update_fields=["status", "completed_at", "updated_at"])
    for trip in mission.trips.exclude(status="cancelled"):
        if trip.status != "completed":
            trip.status = "completed"
            trip.save(update_fields=["status", "updated_at"])
    from apps.ops.notifications import complete_action

    if runs:
        complete_action(
            related_type="vehicle_run",
            related_ids=[run.id for run in runs],
            note="Mission completed.",
            actor=actor,
        )
    _notify_monitors(
        mission,
        actor,
        title=f"Mission {mission.mission_number} completed",
        message=(
            f"{mission.title} was closed"
            + (" with an Operations Center override." if open_runs else ".")
        ),
        priority="normal",
    )
    return mission


def _release_vehicles(vehicle_ids, *, exclude_mission=None, exclude_trip=None):
    """Put vehicles back in the register unless a live run still holds them.

    The runs about to be deleted are named by the caller so they do not count
    as holders: they are removed straight afterwards and can never finish.
    """
    vehicle_ids = set(vehicle_ids)
    if not vehicle_ids:
        return set()
    active = VehicleRun.objects.filter(
        vehicle_id__in=vehicle_ids, status=VehicleRun.STATUS_ACTIVE
    )
    if exclude_mission is not None:
        active = active.exclude(mission=exclude_mission)
    if exclude_trip is not None:
        active = active.exclude(trip=exclude_trip)
    released = vehicle_ids - set(active.values_list("vehicle_id", flat=True))
    Vehicle.objects.filter(pk__in=released, status="on_trip").update(
        status="available", updated_at=timeutils.now()
    )
    return released


@transaction.atomic
def release_trip_vehicles(trip):
    """Free the vehicles of a trip that is being deleted.

    Deleting a trip cascades to its runs, and a run is the only record that
    ever cleared a vehicle's ``on_trip`` status. Without this the truck stays
    out for a trip that no longer exists.
    """
    vehicle_ids = set(trip.vehicles.values_list("vehicle_id", flat=True)) | set(
        trip.runs.values_list("vehicle_id", flat=True)
    )
    return _release_vehicles(vehicle_ids, exclude_trip=trip)


def _mission_vehicle_ids(mission, trip_ids):
    """Every vehicle the mission moved: its trips and its runs."""
    return set(
        VehicleTripVehicle.objects.filter(trip_id__in=trip_ids).values_list(
            "vehicle_id", flat=True
        )
    ) | set(mission.vehicle_runs.values_list("vehicle_id", flat=True))


@transaction.atomic
def cancel_for_mission(mission, actor, *, note=""):
    """Close the ground movement of a mission that has just been cancelled.

    A cancelled mission records no further actual times, so an open run can
    never be completed or pinged again. Leaving it "in progress" would hold
    the trip and its truck out for a plan that cannot run, so unfinished trips
    and their open runs are cancelled and the vehicles returned to the
    register.
    """
    actual = timeutils.now()
    open_runs = list(mission.vehicle_runs.filter(status=VehicleRun.STATUS_ACTIVE))
    for run in open_runs:
        run.status = VehicleRun.STATUS_CANCELLED
        run.completed_at = actual
        run.save(update_fields=["status", "completed_at", "updated_at"])
        _event(
            run,
            MovementEvent.KIND_CANCEL,
            actor=actor,
            source=MovementEvent.SOURCE_OPERATIONS_CENTER,
            actual_at=actual,
            note=note or "Mission cancelled.",
        )

    trips = mission.trips.all()
    trip_ids = list(trips.values_list("id", flat=True))
    unfinished = trips.exclude(status__in=["completed", "cancelled"])
    if unfinished.exists():
        unfinished.update(status="cancelled", updated_at=actual)
    _release_vehicles(_mission_vehicle_ids(mission, trip_ids), exclude_mission=mission)
    return trip_ids


@transaction.atomic
def reset_trips_for_mission(mission, actor=None):
    """Close the movement records of a mission that is being erased.

    Deleting a mission removes its vehicle runs (``CASCADE``) but its trips
    survive without a mission (``SET_NULL``). Left alone they would stay "in
    progress" for ever and keep their vehicles out of the register, so every
    unfinished trip is cancelled and the vehicles it held are released - unless
    another active run still has them.
    """
    trips = mission.trips.all()
    trip_ids = list(trips.values_list("id", flat=True))
    # Every vehicle the mission moved comes back, not only those on trips that
    # were still running: a mission can be erased after its trips closed, and
    # the fleet must not stay out for a record that no longer exists.
    vehicle_ids = _mission_vehicle_ids(mission, trip_ids)
    unfinished = trips.exclude(status__in=["completed", "cancelled"])
    if unfinished.exists():
        unfinished.update(status="cancelled", updated_at=timeutils.now())
    _release_vehicles(vehicle_ids, exclude_mission=mission)
    return trip_ids


# ---------------------------------------------------------------------------
# Pings
# ---------------------------------------------------------------------------


def _parse_ping(row, now):
    if not isinstance(row, dict):
        raise ValueError("Each ping must be an object.")
    try:
        lat = float(row.get("latitude"))
        lon = float(row.get("longitude"))
    except (TypeError, ValueError):
        raise ValueError("Latitude and longitude are required numbers.")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Latitude or longitude is outside its valid range.")
    recorded_raw = row.get("recorded_at")
    recorded = _actual(recorded_raw) if recorded_raw else now
    if recorded > now + dt.timedelta(minutes=10):
        raise ValueError("A ping cannot be in the future.")
    accuracy = row.get("accuracy_m")
    speed = row.get("speed_mps")
    heading = row.get("heading_deg")
    try:
        accuracy = None if accuracy in (None, "") else max(0.0, float(accuracy))
        speed = None if speed in (None, "") else float(speed)
        heading = None if heading in (None, "") else float(heading)
    except (TypeError, ValueError):
        raise ValueError("Accuracy, speed and heading must be numbers when supplied.")
    if heading is not None and not 0 <= heading <= 360:
        raise ValueError("Heading must be 0..360.")
    return {
        "latitude": lat,
        "longitude": lon,
        "accuracy_m": accuracy,
        "speed_mps": speed,
        "heading_deg": heading,
        "recorded_at": recorded,
    }


@transaction.atomic
def record_pings(run, actor, rows, *, source=TrackingPing.SOURCE_MOBILE):
    if run.mission.status != "active":
        raise ValidationError({"detail": "This mission is not active."})
    if run.status in {VehicleRun.STATUS_COMPLETED, VehicleRun.STATUS_CANCELLED}:
        raise ValidationError({"detail": "This vehicle run is closed."})
    if not isinstance(rows, list):
        raise ValidationError({"pings": "Send a list of pings."})
    if len(rows) > 300:
        raise ValidationError({"pings": "Send at most 300 pings in one batch."})

    now = timeutils.now()
    cleaned = []
    rejected = 0
    for row in rows:
        try:
            cleaned.append(_parse_ping(row, now))
        except (ValueError, ValidationError):
            rejected += 1
    if not cleaned:
        raise ValidationError({"pings": "No valid ping was supplied."})

    TrackingPing.objects.bulk_create(
        [
            TrackingPing(
                run=run,
                latitude=row["latitude"],
                longitude=row["longitude"],
                accuracy_m=row["accuracy_m"],
                speed_mps=row["speed_mps"],
                heading_deg=row["heading_deg"],
                recorded_at=row["recorded_at"],
                source=source,
            )
            for row in cleaned
        ]
    )
    latest = max(cleaned, key=lambda row: row["recorded_at"])
    run.last_ping_at = now
    run.last_latitude = latest["latitude"]
    run.last_longitude = latest["longitude"]
    run.last_accuracy_m = latest["accuracy_m"]
    run.save(
        update_fields=[
            "last_ping_at",
            "last_latitude",
            "last_longitude",
            "last_accuracy_m",
            "updated_at",
        ]
    )

    # Keep the dashboard's last-known snapshot fed without making the ping
    # history its source of truth.
    from apps.command.models import VehiclePosition

    VehiclePosition.objects.create(
        vehicle=run.vehicle,
        latitude=latest["latitude"],
        longitude=latest["longitude"],
        reported_at=now,
        source="driver_report",
        reported_by=actor if getattr(actor, "id", None) else None,
        note=f"VehicleRun {run.id} · {run.trip.trip_number}",
    )
    # A ping batch is the driver's phone checking in, so it is also the natural
    # moment to notice that the vehicle has fallen behind its plan.
    alert_delays(run.mission, actor)
    return {"accepted": len(cleaned), "rejected": rejected, "run": run}


# ---------------------------------------------------------------------------
# Emergency
# ---------------------------------------------------------------------------

#: A driver's SOS is one event, not a stream: a repeat press inside this window
#: is the same emergency and must not page the Operations Center twice.
EMERGENCY_REPEAT_MINUTES = 5
EMERGENCY_CATEGORY = "emergency"


@transaction.atomic
def send_emergency(mission, run, actor, *, note=""):
    """The driver's SOS: recorded in the log and alerted to the Operations Center.

    The alert is durable and critical, so it reaches the Operations Center as a
    notification that deep-links to the mission map, whether or not anyone is
    watching the board at that moment. The movement log keeps the exception
    event with the vehicle's last known position as the evidence.
    """
    if run.mission_id != mission.id:
        raise ValidationError(
            {"run": "That vehicle run is not part of this mission."}
        )

    cutoff = timeutils.now() - dt.timedelta(minutes=EMERGENCY_REPEAT_MINUTES)
    if Notification.objects.filter(
        category=EMERGENCY_CATEGORY,
        related_type="vehicle_run",
        related_id=str(run.id),
        created_at__gte=cutoff,
    ).exists():
        return []

    text = " ".join(str(note or "").split())[:500]
    label = f"{run.vehicle.registration_number} · {mission.mission_number}"
    reporter = run.driver.full_name if run.driver_id else "A driver"
    position = ""
    if run.last_latitude is not None and run.last_longitude is not None:
        position = (
            f" Last reported position: {run.last_latitude:.5f}, "
            f"{run.last_longitude:.5f}."
        )
    message = (
        f"{reporter} reported an emergency on {label} ({mission.title})."
        f"{' ' + text if text else ''}{position}"
    )

    _event(
        run,
        MovementEvent.KIND_EXCEPTION,
        actor=actor,
        note=text or "Emergency reported by the driver.",
        latitude=run.last_latitude,
        longitude=run.last_longitude,
    )

    from apps.ops.notifications import notify_many

    return notify_many(
        monitor_recipients(mission),
        title=f"EMERGENCY: {label}",
        message=message,
        level="danger",
        category=EMERGENCY_CATEGORY,
        priority="urgent",
        related=("vehicle_run", run.id),
        actor=actor,
        action_url=f"/missions/{mission.id}/track",
        requires_action=False,
    )


# ---------------------------------------------------------------------------
# Live ground picture
# ---------------------------------------------------------------------------

#: A driver's feed stops being "live" after this long without a fix. The phone
#: sends a batch every few seconds, so a minute of silence means the console
#: should see "not reporting" rather than a stale dot labelled live.
LIVE_AFTER_SECONDS = 60
#: How much of the driven trail the map draws per vehicle.
LIVE_TRAIL_LIMIT = 60


def _live_trails(run_ids):
    """The last ``LIVE_TRAIL_LIMIT`` pings of every run, in one query.

    A window function numbers each run's own pings newest first, so the limit
    is applied per vehicle inside the database instead of asking for one
    vehicle's pings at a time.
    """
    run_ids = list(run_ids)
    if not run_ids:
        return {}

    numbered = (
        TrackingPing.objects.filter(run_id__in=run_ids)
        .annotate(
            row=Window(
                expression=RowNumber(),
                partition_by="run_id",
                order_by=[F("received_at").desc(), F("id").desc()],
            )
        )
        .values("id", "row")
    )
    recent = (
        TrackingPing.objects.filter(
            id__in=Subquery(numbered.filter(row__lte=LIVE_TRAIL_LIMIT).values("id"))
        )
        .order_by("run_id", "-received_at", "-id")
        .values_list("run_id", "latitude", "longitude")
    )

    trails: dict[int, list[list[float]]] = {}
    for run_id, latitude, longitude in recent:
        trails.setdefault(run_id, []).append([latitude, longitude])
    for trail in trails.values():
        trail.reverse()
    return trails


def live_run(run, *, now=None, stop_states=None, trail=None):
    """One vehicle run as the live ground map draws it.

    Positions are the driver's own pings, never inferred from the plan. The
    planned route and the next stop are included so the map can show what the
    driver is trying to do beside where they actually are.
    """
    now = now or timeutils.now()
    stops = stop_states if stop_states is not None else _run_stop_state(run)
    pending = next(
        (row for row in stops if row["arrived_at"] is None and row["departed_at"] is None),
        None,
    )
    if trail is None:
        # Only reached when one run is read on its own; the board passes the
        # whole set from a single batched query.
        trail = [
            [ping.latitude, ping.longitude]
            for ping in run.pings.order_by("-received_at", "-id")[:LIVE_TRAIL_LIMIT]
        ]
        trail.reverse()
    else:
        trail = list(trail)
    age = (now - run.last_ping_at).total_seconds() if run.last_ping_at else None
    route = (run.trip.route_geometry or {}).get("coordinates") or []
    next_stop = pending["stop"] if pending else None
    # The same lateness the driver's sheet and the delay alerts use: a convoy
    # must be visible as late on the map, not only after somebody opens it.
    delay = delay_for_run(run, now=now, stop_states=stops)

    return {
        "id": run.id,
        "registration_number": run.vehicle.registration_number,
        "vehicle_type": run.vehicle.vehicle_type,
        "driver_name": run.driver.full_name if run.driver_id else "",
        "is_lead": bool(run.is_lead),
        "status": run.status,
        "etd": run.etd,
        "atd": run.atd,
        "last_ping_at": run.last_ping_at,
        "latitude": run.last_latitude,
        "longitude": run.last_longitude,
        "accuracy_m": run.last_accuracy_m,
        "age_seconds": int(age) if age is not None else None,
        "is_live": bool(age is not None and age <= LIVE_AFTER_SECONDS),
        "recorded_stops": sum(
            1 for row in stops if row["departed_at"] is not None or row["arrived_at"] is not None
        ),
        "stop_count": len(stops),
        "next_stop": (
            {
                "sequence": next_stop.sequence,
                "location_name": next_stop.location_name,
                "latitude": next_stop.latitude,
                "longitude": next_stop.longitude,
                "planned_arrival": next_stop.planned_arrival,
                "planned_departure": next_stop.planned_departure,
            }
            if next_stop is not None
            else None
        ),
        # The plan is stored as GeoJSON [longitude, latitude]; the map wants
        # [latitude, longitude] and the browser should not have to guess.
        "route": [
            [pair[1], pair[0]]
            for pair in route
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        ],
        "trail": trail,
        "delay": {
            "status": delay["status"],
            "minutes": delay["minutes"],
            "kind": delay["kind"],
            "reason": delay["reason"],
            "stop_sequence": delay["stop_sequence"],
            "planned_at": delay["planned_at"],
        },
    }


def live_board(user):
    """Every in-progress ground mission this caller may watch, with feeds.

    Only monitoring authority reads this picture: a unit's drivers appear on
    their own Operations Center's map, never on an unrelated account's.
    Raising an alert is deliberately not part of a read - opening the map must
    not page anybody.
    """
    from apps.ops import services

    missions = list(
        services.visible_missions(user)
        .filter(category=Mission.CATEGORY_GROUND, status="active")
        .select_related("unit")
        .order_by("mission_number")
    )
    now = timeutils.now()

    # One read per thing, never one per vehicle: this board is polled every few
    # seconds, so a mission's runs, events, itinerary and pings are all fetched
    # for the whole picture and then shared out in memory.
    runs = list(
        VehicleRun.objects.filter(mission__in=missions)
        .exclude(status=VehicleRun.STATUS_CANCELLED)
        .select_related("vehicle", "driver", "trip")
        .prefetch_related("events", "trip__itinerary")
        .order_by("vehicle__registration_number", "id")
    )
    stop_states = {run.id: _run_stop_state(run) for run in runs}
    trails = _live_trails(run.id for run in runs)

    runs_by_mission: dict[int, list[VehicleRun]] = {}
    for run in runs:
        runs_by_mission.setdefault(run.mission_id, []).append(run)

    handoff_ids = _handoff_mission_ids(user)
    caps_by_unit: dict[int, set[str]] = {}

    rows = []
    for mission in missions:
        if mission.unit_id not in caps_by_unit:
            caps_by_unit[mission.unit_id] = roles.capabilities_for(user, mission.unit_id)
        if not can_monitor(
            user,
            mission,
            capabilities=caps_by_unit[mission.unit_id],
            handoff_ids=handoff_ids,
        ):
            continue
        rows.append(
            {
                "id": mission.id,
                "mission_number": mission.mission_number,
                "title": mission.title,
                "unit_acronym": mission.unit.acronym if mission.unit_id else "",
                "location_name": mission.location_name,
                "latitude": mission.latitude,
                "longitude": mission.longitude,
                "start_at": mission.start_at,
                "dispatched_at": mission.dispatched_at,
                "abort": abort_payload(mission),
                "runs": [
                    live_run(
                        run,
                        now=now,
                        stop_states=stop_states[run.id],
                        trail=trails.get(run.id, []),
                    )
                    for run in runs_by_mission.get(mission.id, [])
                ],
            }
        )
    return {"generated_at": now, "missions": rows}


# ---------------------------------------------------------------------------
# Delay monitoring
# ---------------------------------------------------------------------------

#: A vehicle may run this far behind its plan before the Operations Center is
#: told. Tighter than this is noise on Philippine roads.
DELAY_GRACE_MINUTES = 15
#: A long delay produces a reminder, not a stream: one alert per run per window.
DELAY_RENOTIFY_MINUTES = 45
DELAY_CATEGORY = "movement_delay"

DELAY_AWAITING = "awaiting_departure"
DELAY_ON_TIME = "on_time"
DELAY_LATE = "late"
DELAY_CLOSED = "closed"

_ARRIVAL_KINDS = (
    MovementEvent.KIND_ARRIVE_STOP,
    MovementEvent.KIND_ARRIVE_DESTINATION,
)


def _lateness(actual, planned):
    """Whole minutes ``actual`` is after ``planned``; ``None`` when unknown."""
    if actual is None or planned is None:
        return None
    return int(round((actual - planned).total_seconds() / 60))


def _confirmed_origin_departure(run):
    """The confirmed wheel-roll time that departs the origin (stop 1).

    Dispatch writes an ATD as a fallback so the run can start, but that is not
    the driver's confirmation. Until someone confirms it, the origin remains a
    departure point awaiting confirmation, never an arrival.
    """
    if run.atd is None or run.atd_source == VehicleRun.ATD_AUTO_DISPATCH:
        return None
    return run.atd


def _run_stop_state(run, events=None, stops=None):
    """The itinerary with the last recorded arrival/departure per stop.

    ``stops`` may be supplied by a caller that has already loaded the itinerary
    for a whole board. Reading the prefetched list reuses its cache, whereas
    calling ``order_by`` on the manager would issue a query per run.
    """
    events = list(events if events is not None else run.events.all())
    stops = list(stops) if stops is not None else list(run.trip.itinerary.all())
    origin_departure = _confirmed_origin_departure(run)
    rows = []
    for index, stop in enumerate(stops):
        arrivals = [
            event
            for event in events
            if event.stop_id == stop.id and event.kind in _ARRIVAL_KINDS
        ]
        departures = [
            event
            for event in events
            if event.stop_id == stop.id and event.kind == MovementEvent.KIND_DEPART_STOP
        ]
        arrival = max(arrivals, key=lambda event: event.actual_at) if arrivals else None
        departure = (
            max(departures, key=lambda event: event.actual_at) if departures else None
        )
        departed_at = departure.actual_at if departure else None
        if index == 0 and departed_at is None:
            departed_at = origin_departure
        rows.append(
            {
                "stop": stop,
                "arrived_at": arrival.actual_at if arrival else None,
                "departed_at": departed_at,
            }
        )
    return rows


def delay_for_run(run, *, now=None, stop_states=None):
    """How far behind its own plan one vehicle is, and why.

    The comparison is always against a time a person planned (the trip's ETD,
    then each stop's planned arrival). An overdue stop means the run has not
    recorded its arrival by the planned time - never that a GPS breadcrumb was
    promoted to an official time.
    """
    now = now or timeutils.now()
    grace = dt.timedelta(minutes=DELAY_GRACE_MINUTES)
    report = {
        "run": run.id,
        "vehicle": run.vehicle.registration_number,
        "driver": run.driver.full_name if run.driver_id else "",
        "is_lead": bool(run.is_lead),
        "status": DELAY_ON_TIME,
        "minutes": 0,
        "kind": "",
        "reason": "",
        "stop": None,
        "stop_sequence": None,
        "stop_name": "",
        "planned_at": None,
    }
    if run.status in {VehicleRun.STATUS_COMPLETED, VehicleRun.STATUS_CANCELLED}:
        report["status"] = DELAY_CLOSED
        report["reason"] = "This vehicle run is closed."
        return report

    stops = stop_states if stop_states is not None else _run_stop_state(run)
    # The origin has no arrival; a confirmed departure satisfies it.
    pending = next(
        (row for row in stops if row["arrived_at"] is None and row["departed_at"] is None),
        None,
    )

    # Not yet rolled: lateness is measured from the planned departure.
    if run.atd is None:
        if run.etd is not None and now > run.etd + grace:
            late = _lateness(now, run.etd)
            report.update(
                status=DELAY_LATE,
                minutes=late,
                kind="departure",
                planned_at=run.etd,
                reason=(
                    f"Departure has not been confirmed. The planned ETD was "
                    f"{timeutils.format_datetime(run.etd)}, {late} min ago."
                ),
            )
        else:
            report.update(
                status=DELAY_AWAITING, planned_at=run.etd, kind="departure"
            )
        return report

    departure_late = _lateness(run.atd, run.etd)

    if pending is not None:
        stop = pending["stop"]
        planned = stop.planned_arrival or stop.planned_departure or run.etd
        if planned is not None and now > planned + grace:
            late = _lateness(now, planned)
            report.update(
                status=DELAY_LATE,
                minutes=late,
                kind="stop",
                stop=stop.id,
                stop_sequence=stop.sequence,
                stop_name=stop.location_name,
                planned_at=planned,
                reason=(
                    f"Stop {stop.sequence} ({stop.location_name}) has not been "
                    f"recorded as reached. It was planned for "
                    f"{timeutils.format_datetime(planned)}, {late} min ago."
                ),
            )
        elif pending is stops[0] and departure_late is not None and departure_late > DELAY_GRACE_MINUTES:
            report.update(
                status=DELAY_LATE,
                minutes=departure_late,
                kind="departure",
                planned_at=run.etd,
                reason=(
                    f"Departed {departure_late} min after the planned ETD "
                    f"{timeutils.format_datetime(run.etd)}."
                ),
            )
        else:
            report["minutes"] = max(0, departure_late or 0)
            report["planned_at"] = planned
            report["kind"] = "stop"
        return report

    # Every stop has been reached: judge the final arrival and the completion.
    last = stops[-1]
    stop = last["stop"]
    arrival_late = _lateness(last["arrived_at"], stop.planned_arrival or run.etd)
    if arrival_late is not None and arrival_late > DELAY_GRACE_MINUTES:
        report.update(
            status=DELAY_LATE,
            minutes=arrival_late,
            kind="arrival",
            stop=stop.id,
            stop_sequence=stop.sequence,
            stop_name=stop.location_name,
            planned_at=stop.planned_arrival or run.etd,
            reason=(
                f"Reached the final stop {arrival_late} min after it was planned "
                f"({timeutils.format_datetime(stop.planned_arrival or run.etd)})."
            ),
        )
    else:
        report["minutes"] = max(0, arrival_late or 0)
        report["planned_at"] = stop.planned_arrival or run.etd
        report["kind"] = "arrival"
        report["stop"] = stop.id
        report["stop_sequence"] = stop.sequence
        report["stop_name"] = stop.location_name
    return report


def delay_report(mission, *, now=None):
    """Every open vehicle's position against its plan, in vehicle order."""
    now = now or timeutils.now()
    runs = (
        VehicleRun.objects.filter(mission=mission)
        .exclude(status=VehicleRun.STATUS_CANCELLED)
        .select_related("vehicle", "driver", "trip")
        .prefetch_related("events", "trip__itinerary")
        .order_by("vehicle__registration_number", "id")
    )
    return [delay_for_run(run, now=now) for run in runs]


def alert_delays(mission, actor=None, *, now=None):
    """Tell the monitoring roles once when a vehicle falls behind its plan.

    Callers are the points at which the system actually learns something: a
    driver's ping batch, a recorded movement event, or a monitor opening the
    board. The alert is de-duplicated per run and window, so a two-hour delay
    is a reminder rather than a stream of notifications.
    """
    if mission.status != "active":
        return 0
    now = now or timeutils.now()
    runs = (
        VehicleRun.objects.filter(mission=mission, status=VehicleRun.STATUS_ACTIVE)
        .select_related("vehicle", "driver", "trip")
        .prefetch_related("events", "trip__itinerary")
        .order_by("vehicle__registration_number", "id")
    )
    late = [
        report
        for report in (delay_for_run(run, now=now) for run in runs)
        if report["status"] == DELAY_LATE
    ]
    if not late:
        return 0

    cutoff = now - dt.timedelta(minutes=DELAY_RENOTIFY_MINUTES)
    fresh = []
    for report in late:
        already = Notification.objects.filter(
            category=DELAY_CATEGORY,
            related_type="vehicle_run",
            related_id=str(report["run"]),
            created_at__gte=cutoff,
        ).exists()
        if not already:
            fresh.append(report)
    if not fresh:
        return 0

    from apps.ops.notifications import notify_many

    recipients = monitor_recipients(mission)
    created = 0
    for report in fresh:
        rows = notify_many(
            recipients,
            title=f"{report['vehicle']} is {report['minutes']} min behind plan",
            message=report["reason"],
            level="warning",
            category=DELAY_CATEGORY,
            priority="high",
            related=("vehicle_run", report["run"]),
            actor=actor,
            action_url=f"/missions/{mission.id}/track",
            requires_action=False,
        )
        created += len(rows)
    return created


# ---------------------------------------------------------------------------
# Read model
# ---------------------------------------------------------------------------


def snapshot(mission, user):
    """Role-scoped tracking snapshot for the monitor board and driver page."""
    from apps.ops.serializers import VehicleRunSerializer

    runs = list(
        VehicleRun.objects.filter(mission=mission)
        .select_related("vehicle", "driver", "trip", "mission")
        .prefetch_related("events__stop", "trip__itinerary")
        .order_by("vehicle__registration_number", "id")
    )
    manage = can_manage(user, mission)
    monitor = can_monitor(user, mission)
    lead = _lead_run(user, mission)
    mine = [run for run in runs if run.driver_id == getattr(user, "id", None)]
    if not (manage or monitor or lead or mine):
        raise PermissionDenied("You are not assigned to monitor this movement.")

    if manage or monitor or lead:
        visible = runs
    else:
        visible = mine

    # Opening the board is also a check-in: a vehicle already behind its plan
    # is reported to the monitoring roles even if its phone never streamed.
    if manage or monitor:
        alert_delays(mission, user)

    return {
        "mission": {
            "id": mission.id,
            "mission_number": mission.mission_number,
            "title": mission.title,
            "status": mission.status,
            "category": mission.category,
            "unit": mission.unit_id,
            "unit_acronym": getattr(mission.unit, "acronym", "") if mission.unit_id else "",
            "start_at": mission.start_at,
            "end_at": mission.end_at,
            "dispatched_at": mission.dispatched_at,
            "completed_at": mission.completed_at,
            "location_name": mission.location_name,
        },
        "runs": VehicleRunSerializer(visible, many=True).data,
        "abort": abort_payload(mission),
        "delays": delay_report(mission) if (manage or monitor or lead or mine) else [],
        "delay_grace_minutes": DELAY_GRACE_MINUTES,
        "can_monitor": monitor or manage,
        "can_manage": manage,
        "is_driver": bool(mine),
        "is_lead": bool(lead),
        "lead_run_id": lead.id if lead else next(
            (run.id for run in runs if run.is_lead), None
        ),
        "server_time": timeutils.now(),
    }

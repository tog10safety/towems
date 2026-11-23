"""Vehicle movement tracking for ground operations.

The planned route belongs to ``VehicleTrip``. This module adds the execution
record beside it: one ``VehicleRun`` per vehicle, append-only movement events,
and GPS pings from the driver's phone. It deliberately does not modify the
planned route or the last-known ``VehiclePosition`` snapshot used by the
operational picture.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
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


def can_monitor(user, mission) -> bool:
    """Read the whole board: explicit monitoring roles, a manager, or the
    destination unit's Operations Center that received the handoff.

    Monitoring is the destination Operations Center's work alone. The command
    posts it forwards the details to may read the mission, but not its live
    board; recording departures, stops and corrections stays with the owning
    unit, its drivers and the convoy lead.
    """
    if not user or not mission:
        return False
    if can_manage(user, mission) or can(
        user, roles.CAP_MONITOR_VEHICLE_TRACKING, mission.unit_id
    ):
        return True

    from apps.ops import aor_handoff

    return Notification.objects.filter(
        recipient_id=getattr(user, "id", None),
        category=aor_handoff.HANDOFF_CATEGORY,
        related_type="mission",
        related_id=str(mission.id),
    ).exists()


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
        action_url=f"/missions/{mission.id}/track/driver",
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


@transaction.atomic
def arrive_stop(run, stop, actor, *, at=None):
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

    previous = [row for row in stops if row.sequence < stop.sequence]
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
    later = [row for row in stops if row.sequence > stop.sequence]
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

    actual = _actual(at)
    kind = (
        MovementEvent.KIND_ARRIVE_DESTINATION
        if stops and stop.id == stops[-1].id
        else MovementEvent.KIND_ARRIVE_STOP
    )
    source = source_for(actor, run)
    return _event(
        run,
        kind,
        actor=actor,
        source=source,
        stop=stop,
        actual_at=actual,
        planned_at=stop.planned_arrival or run.etd,
        latitude=stop.latitude,
        longitude=stop.longitude,
    )


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

    actual = _actual(at)
    return _event(
        run,
        MovementEvent.KIND_DEPART_STOP,
        actor=actor,
        source=source_for(actor, run),
        stop=stop,
        actual_at=actual,
        planned_at=stop.planned_departure or stop.planned_arrival or run.etd,
        latitude=stop.latitude,
        longitude=stop.longitude,
    )


@transaction.atomic
def complete_run(run, actor, *, at=None, note=""):
    if run.mission.status != "active":
        raise ValidationError({"detail": "This mission is not active."})
    if run.status != VehicleRun.STATUS_ACTIVE:
        raise ValidationError({"detail": "This vehicle run is not active."})
    stops = list(run.trip.itinerary.all())
    if stops:
        last = stops[-1]
        arrived = _stop_events(run, MovementEvent.KIND_ARRIVE_STOP).filter(stop=last).exists() or _stop_events(
            run, MovementEvent.KIND_ARRIVE_DESTINATION
        ).filter(stop=last).exists()
        if not arrived:
            raise ValidationError(
                {"detail": "Record arrival at the final stop before completing this run."}
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


def _run_stop_state(run, events=None):
    """The itinerary with the last recorded arrival/departure per stop."""
    events = list(events if events is not None else run.events.all())
    stops = list(run.trip.itinerary.order_by("sequence"))
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


def delay_for_run(run, *, now=None):
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

    stops = _run_stop_state(run)
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

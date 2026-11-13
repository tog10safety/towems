"""Delay monitoring and the movement-log PDF.

A vehicle is only ever called late when a *person-planned* time has passed
without the matching recorded movement: the trip's ETD, or a stop's planned
arrival. This module pins that rule, the de-duplication of the alert, and the
document a safety officer reads afterwards.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command

from apps.core import timeutils
from apps.core.factories import make_user
from apps.ops import tracking
from apps.ops.models import (
    Mission,
    MovementEvent,
    Notification,
    TripItinerary,
    Vehicle,
    VehicleRun,
    VehicleTrip,
    VehicleTripVehicle,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def driver(db, unit):
    """The person driving the vehicle, not necessarily a mission planner."""
    return make_user(unit=unit, authority="Creator")


@pytest.fixture
def late_mission(db, planner, unit, driver):
    """An active ground mission whose only vehicle has missed its destination."""
    now = timeutils.now()
    mission = Mission.objects.create(
        mission_number="TOWEMS-GO-LATE-001",
        category=Mission.CATEGORY_GROUND,
        title="Convoy running behind",
        unit=unit,
        created_by=planner,
        status="active",
        start_at=now - dt.timedelta(hours=3),
        end_at=now + dt.timedelta(hours=3),
        dispatched_at=now - dt.timedelta(hours=3),
    )
    vehicle = Vehicle.objects.create(
        registration_number="KM450-LATE-1",
        vehicle_type="KM450",
        unit=unit,
        capacity=12,
    )
    trip = VehicleTrip.objects.create(
        trip_number="TRIP-LATE-1",
        title="Behind schedule",
        mission=mission,
        created_by=planner,
        status="planned",
        departure_at=now - dt.timedelta(hours=3),
        return_at=now + dt.timedelta(hours=3),
    )
    VehicleTripVehicle.objects.create(trip=trip, vehicle=vehicle, driver=driver)
    origin = TripItinerary.objects.create(
        trip=trip,
        sequence=1,
        location_name="Camp Aguinaldo",
        latitude=14.6091,
        longitude=121.0622,
        planned_departure=now - dt.timedelta(hours=3),
    )
    destination = TripItinerary.objects.create(
        trip=trip,
        sequence=2,
        location_name="North Ridge",
        latitude=14.75,
        longitude=121.2,
        planned_arrival=now - dt.timedelta(hours=1),
    )
    return mission, trip, vehicle, origin, destination


def started(mission, planner):
    runs = tracking.start_for_mission(mission, planner)
    assert runs
    return runs[0]


# ---------------------------------------------------------------------------
# What counts as late
# ---------------------------------------------------------------------------


def test_a_missed_stop_is_reported_to_the_monitoring_roles(
    late_mission, planner, commander, driver
):
    mission, _trip, _vehicle, origin, destination = late_mission
    run = started(mission, planner)
    # The origin was reached late; the destination is now overdue.
    tracking.arrive_stop(run, origin, driver, at=timeutils.now() - dt.timedelta(hours=2))

    created = tracking.alert_delays(mission, planner)

    assert created >= 1
    note = Notification.objects.filter(
        recipient=commander, category=tracking.DELAY_CATEGORY
    ).first()
    assert note is not None
    assert "behind plan" in note.title
    assert destination.location_name in note.message
    # A delay is information, not a work item: it must not sit in the bell as
    # an action the recipient is expected to complete.
    assert note.requires_action is False
    assert note.action_url == f"/missions/{mission.id}/track"


def test_the_same_delay_is_reported_once(
    late_mission, planner, commander, driver
):
    mission, _trip, _vehicle, origin, _destination = late_mission
    run = started(mission, planner)
    tracking.arrive_stop(run, origin, driver, at=timeutils.now() - dt.timedelta(hours=2))

    assert tracking.alert_delays(mission, planner) >= 1
    assert tracking.alert_delays(mission, planner) == 0
    assert (
        Notification.objects.filter(
            recipient=commander, category=tracking.DELAY_CATEGORY
        ).count()
        == 1
    )


def test_a_vehicle_that_has_not_left_yet_is_reported_separately(
    late_mission, planner, driver
):
    """An unconfirmed departure is not the same thing as a missed stop."""
    mission, _trip, _vehicle, _origin, _destination = late_mission
    run = started(mission, planner)
    # Remove the dispatch fallback: nobody has confirmed the wheel-roll.
    run.atd = None
    run.atd_source = ""
    run.save(update_fields=["atd", "atd_source", "updated_at"])

    report = tracking.delay_for_run(run, now=timeutils.now())

    assert report["status"] == tracking.DELAY_LATE
    assert report["kind"] == "departure"
    assert "Departure has not been confirmed" in report["reason"]


def test_a_punctual_vehicle_is_not_called_late(
    late_mission, planner, driver
):
    """Times recorded at the planned moment are on time, however old the plan."""
    mission, _trip, _vehicle, origin, destination = late_mission
    run = started(mission, planner)
    tracking.arrive_stop(run, origin, driver, at=timeutils.now() - dt.timedelta(hours=3))
    tracking.arrive_stop(run, destination, driver, at=timeutils.now() - dt.timedelta(hours=1))

    report = tracking.delay_for_run(run, now=timeutils.now())

    assert report["status"] == tracking.DELAY_ON_TIME
    assert tracking.alert_delays(mission, planner) == 0


def test_early_departure_is_never_reported_as_behind_plan(
    late_mission, planner, driver
):
    mission, _trip, _vehicle, origin, _destination = late_mission
    run = started(mission, planner)
    tracking.arrive_stop(run, origin, driver, at=timeutils.now() - dt.timedelta(hours=4))

    report = tracking.delay_for_run(run, now=timeutils.now() - dt.timedelta(hours=2, minutes=30))

    assert report["status"] != tracking.DELAY_LATE
    assert report["minutes"] >= 0


def test_the_board_exposes_the_delay_beside_the_plan(as_user, late_mission, planner, driver):
    mission, _trip, _vehicle, origin, _destination = late_mission
    run = started(mission, planner)
    tracking.arrive_stop(run, origin, driver, at=timeutils.now() - dt.timedelta(hours=2))

    body = as_user(planner).get(f"/api/missions/{mission.id}/tracking/").json()

    assert body["delay_grace_minutes"] == tracking.DELAY_GRACE_MINUTES
    row = next(item for item in body["delays"] if item["run"] == run.id)
    assert row["status"] == tracking.DELAY_LATE
    assert row["minutes"] >= 55
    assert row["stop_sequence"] == 2


def test_the_scheduled_sweep_reports_active_missions(
    late_mission, planner, commander, driver
):
    mission, _trip, _vehicle, origin, _destination = late_mission
    run = started(mission, planner)
    tracking.arrive_stop(run, origin, driver, at=timeutils.now() - dt.timedelta(hours=2))

    call_command("check_vehicle_delays")

    assert Notification.objects.filter(
        recipient=commander, category=tracking.DELAY_CATEGORY
    ).exists()


# ---------------------------------------------------------------------------
# The movement log document
# ---------------------------------------------------------------------------


def test_the_movement_log_pdf_renders_the_actual_record(as_user, late_mission, planner, driver):
    mission, _trip, _vehicle, origin, _destination = late_mission
    run = started(mission, planner)
    tracking.arrive_stop(run, origin, driver, at=timeutils.now() - dt.timedelta(hours=2))

    response = as_user(planner).get(f"/api/missions/{mission.id}/tracking/log/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 2000
    assert f"MOVEMENT_{mission.mission_number}.pdf" in response["Content-Disposition"]


def test_the_movement_log_can_be_downloaded(as_user, late_mission, planner):
    mission, *_ = late_mission
    started(mission, planner)

    response = as_user(planner).get(
        f"/api/missions/{mission.id}/tracking/log/?download=1"
    )

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")


def test_a_driver_may_read_their_own_movement_log(as_user, late_mission, planner, driver):
    mission, *_ = late_mission
    started(mission, planner)

    assert (
        as_user(driver)
        .get(f"/api/missions/{mission.id}/tracking/log/")
        .status_code
        == 200
    )


def test_an_outsider_cannot_read_the_movement_log(as_user, late_mission, planner, other_unit):
    mission, *_ = late_mission
    started(mission, planner)
    outsider = make_user(unit=other_unit, authority="Creator")

    response = as_user(outsider).get(f"/api/missions/{mission.id}/tracking/log/")

    assert response.status_code in {403, 404}


def test_the_log_pdf_builds_without_any_recorded_event(as_user, late_mission, planner):
    """A mission opened but not yet moved still prints, rather than failing."""
    mission, *_ = late_mission
    started(mission, planner)

    response = as_user(planner).get(f"/api/missions/{mission.id}/tracking/log/")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert not MovementEvent.objects.filter(
        run__mission=mission, kind=MovementEvent.KIND_COMPLETE
    ).exists()


def test_the_movement_log_excludes_cancelled_vehicles_from_the_delay_board(
    late_mission, planner, driver
):
    mission, _trip, _vehicle, origin, _destination = late_mission
    run = started(mission, planner)
    tracking.arrive_stop(run, origin, driver, at=timeutils.now() - dt.timedelta(hours=2))
    VehicleRun.objects.filter(pk=run.pk).update(status=VehicleRun.STATUS_CANCELLED)

    report = tracking.delay_for_run(VehicleRun.objects.get(pk=run.pk))

    assert report["status"] == tracking.DELAY_CLOSED

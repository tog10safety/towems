"""Live vehicle movement: ETD/ATD, stop log, GPS pings and completion.

The planned route stays on the trip; these tests only exercise the execution
record (``VehicleRun`` / ``MovementEvent`` / ``TrackingPing``) and the
role-scoped API around it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.command.models import VehiclePosition
from apps.core import timeutils
from apps.core.factories import make_user
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

START = "2026-11-01T06:00:00"
END = "2026-11-01T18:00:00"


@pytest.fixture
def driver(db, unit):
    user = make_user(unit=unit, authority="Creator")
    return user


@pytest.fixture
def tracked_mission(db, planner, unit, driver):
    mission = Mission.objects.create(
        mission_number="TOWEMS-GO-TRK-001",
        category=Mission.CATEGORY_GROUND,
        title="Convoy to North Ridge",
        unit=unit,
        created_by=planner,
        status="active",
        start_at=dt.datetime(2026, 11, 1, 6, 0),
        end_at=dt.datetime(2026, 11, 1, 18, 0),
    )
    vehicle = Vehicle.objects.create(
        registration_number="KM450-1234567",
        vehicle_type="KM450",
        unit=unit,
        capacity=12,
    )
    trip = VehicleTrip.objects.create(
        trip_number="TRIP-TRK-001",
        title="Convoy move",
        mission=mission,
        created_by=planner,
        status="planned",
        departure_at=dt.datetime(2026, 11, 1, 6, 0),
        return_at=dt.datetime(2026, 11, 1, 17, 0),
    )
    VehicleTripVehicle.objects.create(trip=trip, vehicle=vehicle, driver=driver)
    origin = TripItinerary.objects.create(
        trip=trip,
        sequence=1,
        location_name="Camp Aguinaldo",
        latitude=14.6091,
        longitude=121.0622,
        planned_departure=dt.datetime(2026, 11, 1, 6, 0),
    )
    destination = TripItinerary.objects.create(
        trip=trip,
        sequence=2,
        location_name="North Ridge",
        latitude=14.75,
        longitude=121.2,
        planned_arrival=dt.datetime(2026, 11, 1, 10, 0),
    )
    return mission, trip, vehicle, origin, destination


def start(client, mission):
    response = client.post(
        f"/api/missions/{mission.id}/tracking/start/", {}, format="json"
    )
    assert response.status_code == 200, response.content
    return response.json()


def test_the_operations_center_starts_one_run_per_vehicle(as_user, planner, tracked_mission):
    mission, trip, vehicle, _origin, _destination = tracked_mission

    body = start(as_user(planner), mission)

    assert len(body["runs"]) == 1
    run = body["runs"][0]
    assert run["registration_number"] == vehicle.registration_number
    assert run["status"] == "active"
    # The dispatch time is an unconfirmed ATD, never presented as the actual
    # wheel-roll time.
    assert run["atd"] is not None
    assert run["atd_source"] == "auto_dispatch"
    assert run["etd"].startswith("2026-11-01T06:00")
    assert [stop["status"] for stop in run["stops"]] == ["pending", "pending"]
    trip.refresh_from_db()
    assert trip.status == "in_progress"
    vehicle.refresh_from_db()
    assert vehicle.status == "on_trip"


def test_the_driver_run_carries_the_road_route_and_stop_positions(
    as_user, planner, tracked_mission
):
    mission, trip, _vehicle, _origin, _destination = tracked_mission
    trip.route_status = "calculated"
    trip.route_geometry = {
        "type": "LineString",
        "coordinates": [[121.0622, 14.6091], [121.13, 14.68], [121.2, 14.75]],
    }
    trip.route_distance_m = 21000
    trip.route_duration_s = 1800
    trip.save(
        update_fields=[
            "route_status",
            "route_geometry",
            "route_distance_m",
            "route_duration_s",
        ]
    )

    body = start(as_user(planner), mission)
    run = body["runs"][0]

    # The driver's map draws the road route that belongs to their own run, and
    # every mappable stop carries its position.
    assert run["route"]["status"] == "calculated"
    assert len(run["route"]["geometry"]["coordinates"]) == 3
    assert run["route"]["distance_m"] == 21000
    assert all(stop["latitude"] is not None for stop in run["stops"])


def test_starting_tracking_notifies_the_driver(as_user, planner, driver, tracked_mission):
    mission, *_ = tracked_mission

    start(as_user(planner), mission)

    note = Notification.objects.get(recipient=driver, category="movement")
    assert note.requires_action is True
    assert note.action_url == f"/missions/{mission.id}/track/driver"


def test_the_driver_confirms_the_actual_departure(
    as_user, planner, driver, tracked_mission
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)

    response = as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart", "at": "2026-11-01T06:12:00"},
        format="json",
    )

    assert response.status_code == 200, response.content
    run.refresh_from_db()
    assert run.atd == dt.datetime(2026, 11, 1, 6, 12)
    assert run.atd_source == VehicleRun.ATD_DRIVER
    kinds = list(MovementEvent.objects.filter(run=run).values_list("kind", flat=True))
    assert MovementEvent.KIND_DEPART_ORIGIN in kinds
    # A second tap cannot rewrite the confirmed time.
    as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart", "at": "2026-11-01T07:00:00"},
        format="json",
    )
    run.refresh_from_db()
    assert run.atd == dt.datetime(2026, 11, 1, 6, 12)


def test_the_confirmed_origin_departure_resolves_stop_one(
    as_user, planner, driver, tracked_mission
):
    """The origin is a departure point, not a stop that needs an arrival."""
    mission, _trip, _vehicle, _origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    url = f"/api/missions/{mission.id}/tracking/event/"

    departed = client.post(
        url,
        {"run": run.id, "action": "depart", "at": "2026-11-01T06:12:00"},
        format="json",
    )
    assert departed.status_code == 200, departed.content
    stops = departed.json()["runs"][0]["stops"]
    assert [stop["status"] for stop in stops] == ["departed", "pending"]
    assert stops[0]["arrived_at"] is None

    # Stop 2 can now be reached without a false arrival at the origin.
    arrived = client.post(
        url,
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )
    assert arrived.status_code == 200, arrived.content
    assert [
        stop["status"] for stop in arrived.json()["runs"][0]["stops"]
    ] == ["departed", "arrived"]


def test_stop_arrivals_must_follow_the_itinerary_order(
    as_user, planner, driver, tracked_mission
):
    mission, _trip, _vehicle, origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)

    out_of_order = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )
    assert out_of_order.status_code == 400
    assert "first" in str(out_of_order.json()).lower()

    first = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": origin.id},
        format="json",
    )
    assert first.status_code == 200, first.content
    second = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )
    assert second.status_code == 200, second.content
    stops = second.json()["runs"][0]["stops"]
    assert [stop["status"] for stop in stops] == ["arrived", "arrived"]
    assert MovementEvent.objects.filter(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    ).exists()


def test_a_run_completes_only_after_the_final_stop_and_closes_the_mission(
    as_user, planner, driver, tracked_mission
):
    mission, trip, vehicle, origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    url = f"/api/missions/{mission.id}/tracking/event/"

    early = client.post(url, {"run": run.id, "action": "complete"}, format="json")
    assert early.status_code == 400

    for stop in (origin, destination):
        assert (
            client.post(
                url, {"run": run.id, "action": "arrive_stop", "stop": stop.id}, format="json"
            ).status_code
            == 200
        )
    done = client.post(url, {"run": run.id, "action": "complete"}, format="json")
    assert done.status_code == 200, done.content
    assert done.json()["runs"][0]["status"] == "completed"
    trip.refresh_from_db()
    vehicle.refresh_from_db()
    assert trip.status == "completed"
    assert vehicle.status == "available"

    # The mission itself is closed by the Operations Center, not the driver.
    assert (
        client.post(
            f"/api/missions/{mission.id}/tracking/complete/", {}, format="json"
        ).status_code
        == 403
    )
    closed = as_user(planner).post(
        f"/api/missions/{mission.id}/tracking/complete/", {}, format="json"
    )
    assert closed.status_code == 200, closed.content
    mission.refresh_from_db()
    assert mission.status == "completed"
    assert mission.completed_at is not None


def test_completing_a_mission_with_an_open_run_needs_an_override(
    as_user, planner, tracked_mission
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    client = as_user(planner)

    blocked = client.post(
        f"/api/missions/{mission.id}/tracking/complete/", {}, format="json"
    )
    assert blocked.status_code == 400
    assert "open" in str(blocked.json()).lower()

    forced = client.post(
        f"/api/missions/{mission.id}/tracking/complete/",
        {"force": True, "note": "Vehicle radio failure"},
        format="json",
    )
    assert forced.status_code == 200, forced.content
    assert forced.json()["runs"][0]["status"] == "cancelled"


def test_the_driver_sends_gps_pings_and_the_board_shows_them(
    as_user, planner, driver, tracked_mission
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    now = timeutils.now()
    earlier = (now - dt.timedelta(minutes=2)).isoformat()
    later = (now - dt.timedelta(minutes=1)).isoformat()

    response = as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": 14.61,
                    "longitude": 121.07,
                    "accuracy_m": 8,
                    "recorded_at": earlier,
                },
                {
                    "latitude": 14.62,
                    "longitude": 121.08,
                    "accuracy_m": 6,
                    "recorded_at": later,
                },
            ],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["accepted"] == 2

    run.refresh_from_db()
    assert run.last_latitude == pytest.approx(14.62)
    # The dashboard's last-known snapshot is kept fed, but stays separate.
    assert VehiclePosition.objects.filter(vehicle=run.vehicle).count() == 1

    board = as_user(planner).get(f"/api/missions/{mission.id}/tracking/").json()
    row = board["runs"][0]
    assert len(row["recent_pings"]) == 2
    assert row["is_live"] is True


def test_only_the_assigned_driver_reports_that_vehicle(
    as_user, planner, driver, tracked_mission, unit
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    stranger = make_user(unit=unit, authority="Creator")

    response = as_user(stranger).post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {"run": run.id, "pings": [{"latitude": 14.6, "longitude": 121.0}]},
        format="json",
    )
    # The stranger cannot even see the mission, so the API answers 404 rather
    # than confirming it exists.
    assert response.status_code in {403, 404}


def test_an_outsider_cannot_read_the_board(as_user, tracked_mission, other_unit):
    mission, *_ = tracked_mission
    outsider = make_user(unit=other_unit, authority="Creator")

    response = as_user(outsider).get(f"/api/missions/{mission.id}/tracking/")
    assert response.status_code in {403, 404}


def test_the_lead_vehicle_is_unique_and_may_record_convoy_milestones(
    as_user, planner, driver, tracked_mission, unit
):
    mission, trip, vehicle, origin, _destination = tracked_mission
    second_vehicle = Vehicle.objects.create(
        registration_number="KM450-7654321",
        vehicle_type="KM450",
        unit=unit,
        capacity=12,
    )
    second_driver = make_user(unit=unit, authority="Creator")
    VehicleTripVehicle.objects.create(
        trip=trip, vehicle=second_vehicle, driver=second_driver
    )
    start(as_user(planner), mission)
    runs = list(VehicleRun.objects.filter(mission=mission).order_by("id"))
    assert len(runs) == 2
    lead, other = runs[0], runs[1]

    response = as_user(planner).post(
        f"/api/missions/{mission.id}/tracking/lead/",
        {"run": other.id},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["lead_run_id"] == other.id
    other.refresh_from_db()
    lead.refresh_from_db()
    assert other.is_lead is True
    assert lead.is_lead is False

    # The lead driver records the other vehicle's origin departure.
    recorded = as_user(second_driver).post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": lead.id, "action": "depart", "at": "2026-11-01T06:05:00"},
        format="json",
    )
    assert recorded.status_code == 200, recorded.content
    # Newest row, not "latest by actual_at": dispatch also stamps a fallback
    # departure with the server clock, which would otherwise win the ordering
    # whenever the suite runs after the hardcoded 06:05 recorded here.
    event = (
        MovementEvent.objects.filter(run=lead, kind=MovementEvent.KIND_DEPART_ORIGIN)
        .order_by("id")
        .last()
    )
    assert event.source == MovementEvent.SOURCE_LEAD


def test_a_ping_with_a_bad_position_is_rejected(
    as_user, planner, driver, tracked_mission
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)

    response = as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {"run": run.id, "pings": [{"latitude": 999, "longitude": 121.0}]},
        format="json",
    )
    assert response.status_code == 400
    run.refresh_from_db()
    assert run.last_ping_at is None


def test_the_server_stamps_pings_when_the_phone_sends_no_time(
    as_user, planner, driver, tracked_mission
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    before = timeutils.now()

    response = as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {"run": run.id, "pings": [{"latitude": 14.6, "longitude": 121.0}]},
        format="json",
    )

    assert response.status_code == 200, response.content
    run.refresh_from_db()
    assert run.last_ping_at >= before


def test_force_closing_a_mission_frees_the_cancelled_run_vehicle(
    as_user, planner, tracked_mission
):
    """A closed mission must not leave a truck "on trip" with nothing running."""
    mission, trip, vehicle, _origin, _destination = tracked_mission
    start(as_user(planner), mission)
    vehicle.refresh_from_db()
    assert vehicle.status == "on_trip"

    response = as_user(planner).post(
        f"/api/missions/{mission.id}/tracking/complete/",
        {"force": True, "note": "Closed with the truck still out."},
        format="json",
    )
    assert response.status_code == 200, response.content

    vehicle.refresh_from_db()
    trip.refresh_from_db()
    assert vehicle.status == "available"
    assert trip.status == "completed"


def test_deleting_a_trip_frees_its_vehicle(as_user, planner, tracked_mission):
    """Removing a running trip must put its truck back in the register."""
    mission, trip, vehicle, _origin, _destination = tracked_mission
    start(as_user(planner), mission)
    vehicle.refresh_from_db()
    assert vehicle.status == "on_trip"

    response = as_user(planner).delete(f"/api/trips/{trip.id}/")

    assert response.status_code == 204, response.content
    assert not VehicleTrip.objects.filter(pk=trip.id).exists()
    vehicle.refresh_from_db()
    assert vehicle.status == "available"


def test_cancelling_a_mission_closes_its_trip_and_frees_the_truck(
    as_user, planner, tracked_mission
):
    """A cancelled mission can take no actual times, so nothing stays running."""
    mission, trip, vehicle, _origin, _destination = tracked_mission
    start(as_user(planner), mission)

    response = as_user(planner).post(
        f"/api/missions/{mission.id}/cancel/",
        {"reason": "Plan dropped."},
        format="json",
    )
    assert response.status_code == 200, response.content

    mission.refresh_from_db()
    trip.refresh_from_db()
    vehicle.refresh_from_db()
    run = VehicleRun.objects.get(trip=trip)

    assert mission.status == "cancelled"
    assert trip.status == "cancelled"
    assert run.status == "cancelled"
    assert vehicle.status == "available"
    # The cancellation is evidence, not a silent status flip.
    assert MovementEvent.objects.filter(
        run=run, kind=MovementEvent.KIND_CANCEL
    ).exists()

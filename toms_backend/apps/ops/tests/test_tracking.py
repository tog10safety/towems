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
    TrackingPing,
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
    # The only vehicle is the lead, so this is the lead designation. Opening
    # it must take the driver straight to the maximised driving screen.
    assert note.title.startswith("Convoy lead")
    assert note.action_url == f"/missions/{mission.id}/track/driver?fullscreen=1"


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
    # No fix has been reported, so the arrival is an explicit override.
    arrived = client.post(
        url,
        {"run": run.id, "action": "arrive_stop", "stop": destination.id, "force": True},
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
        {"run": run.id, "action": "arrive_stop", "stop": origin.id, "force": True},
        format="json",
    )
    assert first.status_code == 200, first.content
    second = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id, "force": True},
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
                url,
                {
                    "run": run.id,
                    "action": "arrive_stop",
                    "stop": stop.id,
                    "force": True,
                },
                format="json",
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


def test_the_driver_reports_an_emergency_to_the_operations_center(
    as_user, planner, driver, tracked_mission
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    run.last_latitude = 14.61
    run.last_longitude = 121.07
    run.save(update_fields=["last_latitude", "last_longitude"])

    response = as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/emergency/",
        {"run": run.id, "note": "Ambush on the highway"},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["notified"] == 1
    alert = Notification.objects.get(recipient=planner, category="emergency")
    assert alert.level == "danger"
    assert alert.priority == "urgent"
    assert "Ambush on the highway" in alert.message
    assert "14.61000, 121.07000" in alert.message
    assert alert.action_url == f"/missions/{mission.id}/track"
    # The log keeps the exception beside the vehicle's last known position.
    event = MovementEvent.objects.get(run=run, kind=MovementEvent.KIND_EXCEPTION)
    assert event.latitude == pytest.approx(14.61)
    assert event.source == MovementEvent.SOURCE_DRIVER

    # A second press inside the window is the same emergency.
    again = as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/emergency/",
        {"run": run.id},
        format="json",
    )
    assert again.status_code == 200, again.content
    assert again.json()["notified"] == 0
    assert Notification.objects.filter(category="emergency").count() == 1
    assert (
        MovementEvent.objects.filter(
            run=run, kind=MovementEvent.KIND_EXCEPTION
        ).count()
        == 1
    )


def test_an_outsider_cannot_report_an_emergency(
    as_user, planner, tracked_mission, unit
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    stranger = make_user(unit=unit, authority="Creator")

    response = as_user(stranger).post(
        f"/api/missions/{mission.id}/tracking/emergency/",
        {"run": run.id},
        format="json",
    )

    assert response.status_code in {403, 404}
    assert not Notification.objects.filter(category="emergency").exists()


def test_the_ground_map_shows_running_missions_with_the_drivers_feed(
    as_user, planner, driver, tracked_mission
):
    mission, trip, vehicle, _origin, _destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    now = timeutils.now()
    as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": 14.61,
                    "longitude": 121.07,
                    "recorded_at": (now - dt.timedelta(minutes=2)).isoformat(),
                },
                {
                    "latitude": 14.62,
                    "longitude": 121.08,
                    "recorded_at": (now - dt.timedelta(minutes=1)).isoformat(),
                },
            ],
        },
        format="json",
    )

    body = as_user(planner).get("/api/gis/ground-live/").json()

    assert [row["id"] for row in body["missions"]] == [mission.id]
    row = body["missions"][0]["runs"][0]
    assert row["registration_number"] == vehicle.registration_number
    assert row["is_live"] is True
    assert row["latitude"] == pytest.approx(14.62)
    assert row["trail"] == [[14.61, 121.07], [14.62, 121.08]]
    # The origin is a departure point awaiting the driver's confirmation, so it
    # is the next stop and nothing is recorded yet.
    assert row["stop_count"] == 2
    assert row["next_stop"]["sequence"] == 1
    assert row["recorded_stops"] == 0


def test_the_ground_map_flags_a_vehicle_behind_its_plan(
    as_user, planner, tracked_mission
):
    """The map must show a late convoy, not hide it until the sheet is opened."""
    mission, _trip, _vehicle, origin, _destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    behind = timeutils.now() - dt.timedelta(hours=2)
    origin.planned_departure = behind
    origin.save(update_fields=["planned_departure"])
    run.etd = behind
    run.save(update_fields=["etd"])

    row = as_user(planner).get("/api/gis/ground-live/").json()["missions"][0]["runs"][0]

    assert row["delay"]["status"] == "late"
    assert row["delay"]["minutes"] >= 100
    assert row["delay"]["reason"]
    assert row["delay"]["stop_sequence"] == 1


def test_the_ground_map_reads_the_whole_board_without_a_query_per_vehicle(
    as_user, planner, driver, unit, tracked_mission, django_assert_max_num_queries
):
    """This board is polled every few seconds, so it must not fan out per run."""
    mission, trip, _vehicle, _origin, _destination = tracked_mission
    for index in range(5):
        extra = Vehicle.objects.create(
            registration_number=f"KM450-77{index:05d}",
            vehicle_type="KM450",
            unit=unit,
        )
        VehicleTripVehicle.objects.create(trip=trip, vehicle=extra, driver=driver)
    start(as_user(planner), mission)

    now = timeutils.now()
    for run in VehicleRun.objects.filter(mission=mission):
        for index in range(3):
            TrackingPing.objects.create(
                run=run,
                latitude=14.6 + index / 100,
                longitude=121.0,
                recorded_at=now - dt.timedelta(seconds=30 * index),
                source=TrackingPing.SOURCE_MOBILE,
            )
    client = as_user(planner)

    # Flat in vehicles: six runs and their pings cost the same as one. A query
    # per run (itinerary, delay recalculation, trail) would exceed this.
    with django_assert_max_num_queries(22):
        body = client.get("/api/gis/ground-live/").json()

    rows = body["missions"][0]["runs"]
    assert len(rows) == 6
    assert all(len(row["trail"]) == 3 for row in rows)


def test_the_ground_map_drops_a_mission_that_is_no_longer_running(
    as_user, planner, tracked_mission
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    assert as_user(planner).get("/api/gis/ground-live/").json()["missions"]

    as_user(planner).post(
        f"/api/missions/{mission.id}/tracking/complete/",
        {"force": True, "note": "Vehicle returned"},
        format="json",
    )

    assert as_user(planner).get("/api/gis/ground-live/").json()["missions"] == []


def test_the_operations_center_aborts_to_the_last_stop_the_convoy_has_reached(
    as_user, planner, driver, tracked_mission
):
    mission, trip, _vehicle, _origin, destination = tracked_mission
    planned_final = TripItinerary.objects.create(
        trip=trip,
        sequence=3,
        location_name="Rally Point",
        latitude=14.82,
        longitude=121.31,
        planned_arrival=dt.datetime(2026, 11, 1, 12, 0),
    )
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    # The convoy has reached the second stop; the planned final stop is ahead.
    MovementEvent.objects.create(
        run=run,
        stop=destination,
        kind=MovementEvent.KIND_ARRIVE_STOP,
        actual_at=timeutils.now(),
        source=MovementEvent.SOURCE_OPERATIONS_CENTER,
    )

    response = as_user(planner).post(
        f"/api/missions/{mission.id}/tracking/abort/",
        {"destination": "last_stop", "reason": "Road blocked by a landslide"},
        format="json",
    )

    assert response.status_code == 200, response.content
    mission.refresh_from_db()
    assert mission.aborted_at is not None
    assert mission.abort_destination == "last_stop"
    assert mission.abort_target_name == destination.location_name
    assert mission.abort_target_latitude == pytest.approx(destination.latitude)
    assert mission.abort_target_longitude == pytest.approx(destination.longitude)
    # Never further out than the convoy already is: the planned final stop is
    # still ahead and must not be the pin.
    assert mission.abort_target_name != planned_final.location_name

    # The drivers are told at once, and the order is part of the movement log.
    assert Notification.objects.filter(
        category="mission_abort", recipient=driver, related_id=str(mission.id)
    ).exists()
    note = (
        MovementEvent.objects.filter(
            run__mission=mission, kind=MovementEvent.KIND_EXCEPTION
        )
        .values_list("note", flat=True)
        .first()
    )
    assert "last stop you have reached" in note

    board = as_user(planner).get("/api/gis/ground-live/").json()
    row = board["missions"][0]
    assert row["abort"]["destination"] == "last_stop"
    assert row["abort"]["target"]["name"] == destination.location_name

    snapshot = as_user(driver).get(f"/api/missions/{mission.id}/tracking/").json()
    assert snapshot["abort"]["reason"] == "Road blocked by a landslide"
    assert snapshot["mission"]["status"] == "active"


def test_an_abort_never_targets_a_stop_the_convoy_has_not_reached(
    as_user, planner, tracked_mission
):
    mission, trip, _vehicle, _origin, _destination = tracked_mission
    planned_final = TripItinerary.objects.create(
        trip=trip,
        sequence=3,
        location_name="Rally Point",
        latitude=14.82,
        longitude=121.31,
        planned_arrival=dt.datetime(2026, 11, 1, 12, 0),
    )
    start(as_user(planner), mission)
    url = f"/api/missions/{mission.id}/tracking/abort/"

    blocked = as_user(planner).post(
        url, {"destination": "last_stop", "reason": "Weather"}, format="json"
    )
    assert blocked.status_code == 400
    assert "destination" in str(blocked.json())

    # The plan's own final stop is a separate, explicit choice.
    allowed = as_user(planner).post(
        url, {"destination": "final_stop", "reason": "Weather"}, format="json"
    )
    assert allowed.status_code == 200, allowed.content
    mission.refresh_from_db()
    assert mission.abort_destination == "final_stop"
    assert mission.abort_target_name == planned_final.location_name


def test_an_arrival_records_the_position_the_driver_tapped_from(
    as_user, planner, driver, tracked_mission
):
    mission, _trip, _vehicle, _origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart"},
        format="json",
    )
    tapped_at = (destination.latitude + 0.0008, destination.longitude + 0.0008)
    client.post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": tapped_at[0],
                    "longitude": tapped_at[1],
                    "recorded_at": timeutils.now().isoformat(),
                }
            ],
        },
        format="json",
    )

    accepted = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )

    assert accepted.status_code == 200, accepted.content
    event = MovementEvent.objects.get(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    )
    # The tap is logged where the phone was, not where the stop is drawn.
    assert event.latitude == pytest.approx(tapped_at[0])
    assert event.longitude == pytest.approx(tapped_at[1])
    assert event.latitude != pytest.approx(destination.latitude)


def test_a_departure_records_the_position_the_driver_tapped_from(
    as_user, planner, driver, tracked_mission
):
    mission, _trip, _vehicle, _origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart"},
        format="json",
    )
    for latitude, longitude in (
        (destination.latitude, destination.longitude),
        (destination.latitude + 0.0006, destination.longitude + 0.0006),
    ):
        client.post(
            f"/api/missions/{mission.id}/tracking/ping/",
            {
                "run": run.id,
                "pings": [
                    {
                        "latitude": latitude,
                        "longitude": longitude,
                        "recorded_at": timeutils.now().isoformat(),
                    }
                ],
            },
            format="json",
        )
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )
    left_from = (destination.latitude + 0.0012, destination.longitude + 0.0012)
    client.post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": left_from[0],
                    "longitude": left_from[1],
                    "recorded_at": timeutils.now().isoformat(),
                }
            ],
        },
        format="json",
    )

    accepted = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart_stop", "stop": destination.id},
        format="json",
    )

    assert accepted.status_code == 200, accepted.content
    event = MovementEvent.objects.get(run=run, kind=MovementEvent.KIND_DEPART_STOP)
    assert event.latitude == pytest.approx(left_from[0])
    assert event.longitude == pytest.approx(left_from[1])
    assert event.note == ""


def _depart_and_ping_at(as_user, planner, driver, tracked_mission, point):
    """Start the run and leave the vehicle reporting ``point``."""
    mission, _trip, _vehicle, _origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart"},
        format="json",
    )
    client.post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": point[0],
                    "longitude": point[1],
                    "recorded_at": timeutils.now().isoformat(),
                }
            ],
        },
        format="json",
    )
    return mission, run, client, destination


def test_an_arrival_without_any_fix_needs_an_override(
    as_user, planner, driver, tracked_mission
):
    mission, _trip, _vehicle, _origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart"},
        format="json",
    )

    refused = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )

    assert refused.status_code == 400
    body = refused.json()
    assert "not reported a position" in body["detail"]
    assert body["errors"]["override"]
    assert not MovementEvent.objects.filter(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    ).exists()

    accepted = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {
            "run": run.id,
            "action": "arrive_stop",
            "stop": destination.id,
            "force": True,
        },
        format="json",
    )

    assert accepted.status_code == 200, accepted.content
    event = MovementEvent.objects.get(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    )
    assert "has not reported a position" in event.note
    assert "could not be verified" in event.note
    assert Notification.objects.filter(
        category="movement", related_id=str(mission.id), title__icontains="verified"
    ).exists()


def test_an_arrival_on_a_fresh_fix_is_verified(as_user, planner, driver, tracked_mission):
    _mission_obj, _trip, _vehicle, _origin, destination = tracked_mission
    mission, run, client, destination = _depart_and_ping_at(
        as_user, planner, driver, tracked_mission, (destination.latitude, destination.longitude)
    )

    accepted = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )

    assert accepted.status_code == 200, accepted.content
    event = MovementEvent.objects.get(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    )
    assert event.note == ""
    assert not Notification.objects.filter(
        category="movement", related_id=str(mission.id), title__icontains="verified"
    ).exists()


def test_the_track_timeline_carries_the_position_of_each_tap(
    as_user, planner, driver, tracked_mission
):
    _mission_obj, _trip, _vehicle, _origin, destination = tracked_mission
    tapped_at = (destination.latitude + 0.0004, destination.longitude + 0.0004)
    mission, run, client, destination = _depart_and_ping_at(
        as_user, planner, driver, tracked_mission, tapped_at
    )
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart_stop", "stop": destination.id},
        format="json",
    )

    snapshot = client.get(f"/api/missions/{mission.id}/tracking/").json()

    row = next(
        stop for stop in snapshot["runs"][0]["stops"] if stop["id"] == destination.id
    )
    # Both taps carry the phone's position, and a grid reference to read it by.
    assert row["arrived_position"]["latitude"] == pytest.approx(tapped_at[0])
    assert row["arrived_position"]["mgrs"]
    assert row["departed_position"]["longitude"] == pytest.approx(tapped_at[1])
    assert row["arrived_position"]["latitude"] != pytest.approx(destination.latitude)


def test_an_arrival_on_a_stale_fix_is_unverified_until_overridden(
    as_user, planner, driver, tracked_mission
):
    _mission_obj, _trip, _vehicle, _origin, destination = tracked_mission
    mission, run, client, destination = _depart_and_ping_at(
        as_user, planner, driver, tracked_mission, (destination.latitude, destination.longitude)
    )
    # The convoy sat on a fix taken 45 minutes ago: near the stop, but too old to
    # count as evidence of being there.
    VehicleRun.objects.filter(pk=run.pk).update(
        last_ping_at=timeutils.now() - dt.timedelta(minutes=45)
    )

    refused = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )

    assert refused.status_code == 400
    body = refused.json()
    assert "old" in body["detail"] or "ago" in body["detail"]
    assert "override" in body.get("errors", body)
    assert not MovementEvent.objects.filter(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    ).exists()

    accepted = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {
            "run": run.id,
            "action": "arrive_stop",
            "stop": destination.id,
            "force": True,
        },
        format="json",
    )

    assert accepted.status_code == 200, accepted.content
    event = MovementEvent.objects.get(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    )
    assert "could not be verified" in event.note
    assert "45 minutes" in event.note
    raised = Notification.objects.filter(
        category="movement", related_id=str(mission.id), title__icontains="verified"
    )
    assert raised.exists()


def test_a_departure_far_from_the_stop_is_raised_to_the_operations_center(
    as_user, planner, driver, tracked_mission
):
    mission, _trip, _vehicle, origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart"},
        format="json",
    )
    client.post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": destination.latitude,
                    "longitude": destination.longitude,
                    "recorded_at": timeutils.now().isoformat(),
                }
            ],
        },
        format="json",
    )
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )
    # The phone is still at the origin while the driver claims to leave North Ridge.
    client.post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": origin.latitude,
                    "longitude": origin.longitude,
                    "recorded_at": timeutils.now().isoformat(),
                }
            ],
        },
        format="json",
    )

    accepted = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart_stop", "stop": destination.id},
        format="json",
    )

    assert accepted.status_code == 200, accepted.content
    event = MovementEvent.objects.get(run=run, kind=MovementEvent.KIND_DEPART_STOP)
    assert "does not match" in event.note
    assert "km from" in event.note
    # The Operations Center sees the claim and the position disagree.
    raised = Notification.objects.filter(
        category="movement", related_id=str(mission.id), title__icontains="departure"
    )
    assert raised.exists()
    assert "does not match" in raised.first().message


def test_an_arrival_far_from_the_stop_is_refused(as_user, planner, driver, tracked_mission):
    mission, _trip, _vehicle, _origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart"},
        format="json",
    )
    client.post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": 14.6091,
                    "longitude": 121.0622,
                    "recorded_at": timeutils.now().isoformat(),
                }
            ],
        },
        format="json",
    )

    refused = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )

    assert refused.status_code == 400
    assert "override" in str(refused.json())
    assert not MovementEvent.objects.filter(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    ).exists()


def test_an_arrival_can_be_recorded_by_override_and_is_noted(
    as_user, planner, driver, tracked_mission
):
    mission, _trip, _vehicle, _origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart"},
        format="json",
    )
    client.post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": 14.6091,
                    "longitude": 121.0622,
                    "recorded_at": timeutils.now().isoformat(),
                }
            ],
        },
        format="json",
    )

    accepted = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {
            "run": run.id,
            "action": "arrive_stop",
            "stop": destination.id,
            "force": True,
        },
        format="json",
    )

    assert accepted.status_code == 200, accepted.content
    event = MovementEvent.objects.get(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    )
    assert "override" in event.note.lower()
    assert "km from" in event.note
    # The Operations Center is told that the arrival was not verified.
    assert Notification.objects.filter(
        category="movement", related_id=str(mission.id)
    ).exists()


def test_an_arrival_next_to_the_stop_needs_no_override(
    as_user, planner, driver, tracked_mission
):
    mission, _trip, _vehicle, _origin, destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    client = as_user(driver)
    client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "depart"},
        format="json",
    )
    client.post(
        f"/api/missions/{mission.id}/tracking/ping/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": destination.latitude + 0.0002,
                    "longitude": destination.longitude + 0.0002,
                    "recorded_at": timeutils.now().isoformat(),
                }
            ],
        },
        format="json",
    )

    accepted = client.post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": destination.id},
        format="json",
    )

    assert accepted.status_code == 200, accepted.content
    event = MovementEvent.objects.get(
        run=run, kind=MovementEvent.KIND_ARRIVE_DESTINATION
    )
    assert "override" not in event.note.lower()


def test_after_an_abort_the_driver_arrives_at_the_pinned_point_and_closes_the_run(
    as_user, planner, driver, tracked_mission
):
    mission, trip, _vehicle, _origin, _destination = tracked_mission
    rally = TripItinerary.objects.create(
        trip=trip,
        sequence=3,
        location_name="Rally Point",
        latitude=14.82,
        longitude=121.31,
        planned_arrival=dt.datetime(2026, 11, 1, 12, 0),
    )
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    as_user(planner).post(
        f"/api/missions/{mission.id}/tracking/abort/",
        {"destination": "final_stop", "reason": "Bridge out"},
        format="json",
    )

    # The stops the order skipped no longer stand between the driver and the
    # pinned point.
    arrived = as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "arrive_stop", "stop": rally.id, "force": True},
        format="json",
    )
    assert arrived.status_code == 200, arrived.content

    closed = as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "complete", "note": "At the rally point"},
        format="json",
    )
    assert closed.status_code == 200, closed.content
    run.refresh_from_db()
    assert run.status == "completed"


def test_a_return_to_station_abort_is_closed_by_the_return_itself(
    as_user, planner, driver, tracked_mission
):
    mission, _trip, _vehicle, _origin, _destination = tracked_mission
    start(as_user(planner), mission)
    run = VehicleRun.objects.get(mission=mission)
    as_user(planner).post(
        f"/api/missions/{mission.id}/tracking/abort/",
        {"destination": "station", "reason": "Weather closing in"},
        format="json",
    )

    closed = as_user(driver).post(
        f"/api/missions/{mission.id}/tracking/event/",
        {"run": run.id, "action": "complete", "note": "Back at the station"},
        format="json",
    )
    assert closed.status_code == 200, closed.content
    run.refresh_from_db()
    assert run.status == "completed"


def test_an_abort_returns_the_convoy_to_station_and_is_the_operations_center_s_alone(
    as_user, planner, driver, tracked_mission
):
    mission, _trip, _vehicle, origin, _destination = tracked_mission
    start(as_user(planner), mission)
    url = f"/api/missions/{mission.id}/tracking/abort/"

    denied = as_user(driver).post(
        url, {"destination": "station", "reason": "Weather"}, format="json"
    )
    assert denied.status_code == 403
    mission.refresh_from_db()
    assert mission.aborted_at is None

    mission.status = "planned"
    mission.save(update_fields=["status"])
    early = as_user(planner).post(
        url, {"destination": "station", "reason": "Weather"}, format="json"
    )
    assert early.status_code == 400

    mission.status = "active"
    mission.save(update_fields=["status"])
    ok = as_user(planner).post(
        url, {"destination": "station", "reason": "Weather"}, format="json"
    )
    assert ok.status_code == 200, ok.content
    mission.refresh_from_db()
    assert mission.abort_target_name == origin.location_name
    assert mission.abort_target_latitude == pytest.approx(origin.latitude)

    # Only one abort order per mission.
    again = as_user(planner).post(
        url, {"destination": "station", "reason": "Weather"}, format="json"
    )
    assert again.status_code == 400


def test_the_ground_map_is_empty_for_an_unrelated_unit(
    as_user, planner, tracked_mission, other_unit
):
    mission, *_ = tracked_mission
    start(as_user(planner), mission)
    outsider = make_user(unit=other_unit, authority="Creator")

    response = as_user(outsider).get("/api/gis/ground-live/")

    assert response.status_code == 200, response.content
    assert response.json()["missions"] == []


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

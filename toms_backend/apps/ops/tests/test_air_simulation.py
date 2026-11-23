"""Time-based aircraft simulation for the Air Operations map."""

from __future__ import annotations

import datetime as dt

import pytest

from apps.ops import air_simulation
from apps.ops.models import (
    AirAsset,
    AirMissionAircraft,
    AirMissionDetail,
    AirMissionElement,
    AirMissionItinerary,
    Mission,
)

pytestmark = pytest.mark.django_db

ORIGIN = "Bancasi, Butuan City, 51PYK7316589902"
DESTINATION = "Sasa, Davao City, 51NYH9319389013"
ETD = dt.datetime(2026, 11, 13, 6, 0)
ETA = dt.datetime(2026, 11, 13, 7, 0)


def make_aircraft(unit, tail="001", speed=120, status="available"):
    return AirAsset.objects.create(
        aircraft_type="rotary_wing",
        categories=["utility"],
        model="S-70i",
        tail_number=tail,
        callsign=f"HAWK {tail}",
        cruising_speed_knots=speed,
        current_location=ORIGIN,
        assigned_unit="205th_thw",
        opcon_unit=unit,
        operational_status=status,
    )


def make_mission(unit, planner, *, number="AO-TEST-001", depends_on=None):
    mission = Mission.objects.create(
        mission_number=number,
        category=Mission.CATEGORY_AIR,
        title=f"Simulation {number}",
        unit=unit,
        status="planned",
        start_at=ETD,
        end_at=ETA,
        created_by=planner,
        depends_on=depends_on,
        dependency_release=Mission.DEPENDENCY_COMPLETION,
        dependency_buffer_minutes=0,
    )
    detail = AirMissionDetail.objects.create(
        mission=mission,
        operation_date=ETD.date(),
        mission_configuration="single",
        aircraft_count=1,
        departure_location=ORIGIN,
        destination_location=DESTINATION,
        scheduled_departure=ETD,
        scheduled_arrival=ETA,
    )
    element = AirMissionElement.objects.create(
        air_detail=detail, sequence=1, mission="Simulated sortie"
    )
    aircraft = AirMissionAircraft.objects.create(
        element=element,
        sequence=1,
        aircraft_type="S-70i",
        tail_number=number[-3:],
        callsign=f"HAWK {number[-3:]}",
        is_lead=True,
    )
    AirMissionItinerary.objects.create(
        aircraft=aircraft,
        sequence=1,
        etd=ETD,
        eta=ETA,
        origin=ORIGIN,
        destination=DESTINATION,
    )
    return mission


def test_before_etd_the_aircraft_waits_at_the_origin(planner, unit):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)

    result = air_simulation.snapshot(
        [mission], now=dt.datetime(2026, 11, 13, 5, 30), allowed_unit_ids={unit.id}
    )
    row = result["aircraft"][0]

    assert row["status"] == "planned"
    assert row["progress_percent"] == 0
    assert row["position"] == row["planned_route"][0]
    assert row["traveled_route"] == [row["planned_route"][0]]
    assert len(row["planned_route"]) > 1


def test_flight_progress_and_the_ten_minute_buffer_are_time_based(planner, unit):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)

    at_eta = air_simulation.snapshot(
        [mission], now=ETA, allowed_unit_ids={unit.id}
    )["aircraft"][0]
    assert at_eta["status"] == "en_route"
    assert at_eta["progress_percent"] == pytest.approx(85.7, abs=0.2)
    assert at_eta["position"] != at_eta["planned_route"][-1]

    after_buffer = air_simulation.snapshot(
        [mission], now=ETA + dt.timedelta(minutes=10), allowed_unit_ids={unit.id}
    )["aircraft"][0]
    assert after_buffer["status"] == "arrived"
    assert after_buffer["progress_percent"] == 100
    assert list(after_buffer["position"]) == after_buffer["planned_route"][-1]


def test_a_recorded_takeoff_starts_the_simulation_before_the_scheduled_etd(
    planner, unit
):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)
    leg = AirMissionItinerary.objects.get(
        aircraft__element__air_detail__mission=mission
    )
    actual_takeoff = ETD - dt.timedelta(hours=8)
    leg.atd = actual_takeoff
    leg.save(update_fields=["atd"])

    row = air_simulation.snapshot(
        [mission],
        now=actual_takeoff + dt.timedelta(minutes=30),
        allowed_unit_ids={unit.id},
    )["aircraft"][0]

    assert row["status"] == "en_route"
    assert row["actual_departure_at"] == actual_takeoff
    assert row["simulation_started_at"] == actual_takeoff
    assert row["progress_percent"] == pytest.approx(42.9, abs=0.2)


def test_dependency_and_availability_block_movement(planner, unit):
    predecessor = make_mission(unit, planner, number="AO-TEST-001")
    make_aircraft(unit, tail="001")
    dependent = make_mission(
        unit, planner, number="AO-TEST-002", depends_on=predecessor
    )
    blocked = air_simulation.snapshot(
        [dependent], now=dt.datetime(2026, 11, 13, 6, 30), allowed_unit_ids={unit.id}
    )["aircraft"][0]
    assert blocked["status"] == "pending_dependency"
    assert blocked["progress_percent"] == 0

    predecessor.status = "completed"
    predecessor.save(update_fields=["status"])
    make_aircraft(unit, tail="002", status="maintenance")
    unavailable = air_simulation.snapshot(
        [dependent], now=dt.datetime(2026, 11, 13, 6, 30), allowed_unit_ids={unit.id}
    )["aircraft"][0]
    assert unavailable["status"] == "unavailable"
    assert unavailable["progress_percent"] == 0


def test_air_simulation_endpoint_returns_the_snapshot(as_user, planner, unit):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)

    response = as_user(planner).get(
        f"/api/missions/air-simulation/?date={mission.air_detail.operation_date}"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["is_simulated"] is True
    assert body["buffer_minutes"] == 10
    assert body["server_time"]
    assert body["aircraft"][0]["mission_number"] == "AO-TEST-001"
    assert body["aircraft"][0]["is_simulated"] is True


MIDDLE = "Valencia City, 51PXM1234567890"


def add_intermediate_stop(mission):
    """Turn the single-leg plan into a two-leg plan with a stop in between."""
    aircraft = AirMissionAircraft.objects.get(
        element__air_detail__mission=mission
    )
    first = AirMissionItinerary.objects.get(aircraft=aircraft, sequence=1)
    first.destination = MIDDLE
    first.save(update_fields=["destination"])
    second = AirMissionItinerary.objects.create(
        aircraft=aircraft,
        sequence=2,
        etd=ETA,
        eta=ETA + dt.timedelta(hours=1),
        origin=MIDDLE,
        destination=DESTINATION,
    )
    return first, second


def test_arriving_at_an_intermediate_stop_does_not_close_the_mission(
    planner, unit
):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)
    first, second = add_intermediate_stop(mission)

    first.ata = ETA
    first.save(update_fields=["ata"])
    detail = mission.air_detail
    detail.actual_arrival = ETA
    detail.save(update_fields=["actual_arrival"])

    row = air_simulation.snapshot(
        [mission], now=ETA + dt.timedelta(minutes=5), allowed_unit_ids={unit.id}
    )["aircraft"][0]

    # The leg after the stop has not been flown, so the mission is still open
    # and the rest of the itinerary must not be drawn as traveled.
    assert second.ata is None
    assert row["status"] != "arrived"
    assert row["progress_percent"] < 100
    assert len(row["remaining_route"]) > 1
    assert row["position"] != row["planned_route"][-1]


def test_an_intermediate_landing_does_not_set_the_mission_arrival(
    as_user, planner, unit
):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)
    first, second = add_intermediate_stop(mission)
    client = as_user(planner)

    takeoff = client.post(
        f"/api/missions/{mission.id}/itineraries/{first.id}/actual/",
        {"event": "takeoff", "at": ETD.isoformat()},
        format="json",
    )
    assert takeoff.status_code == 200, takeoff.content
    landing = client.post(
        f"/api/missions/{mission.id}/itineraries/{first.id}/actual/",
        {"event": "landing", "at": ETA.isoformat()},
        format="json",
    )
    assert landing.status_code == 200, landing.content

    mission.refresh_from_db()
    first.refresh_from_db()
    assert first.ata is not None
    assert mission.air_detail.actual_arrival is None
    assert mission.status != "completed"


def test_each_leg_is_returned_with_its_own_details_and_points(planner, unit):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)
    first, second = add_intermediate_stop(mission)
    first.atd = ETD
    first.ata = ETA
    first.save(update_fields=["atd", "ata"])

    row = air_simulation.snapshot(
        [mission], now=ETA + dt.timedelta(minutes=30), allowed_unit_ids={unit.id}
    )["aircraft"][0]

    legs = row["legs"]
    assert [leg["sequence"] for leg in legs] == [1, 2]
    assert legs[0]["origin"] == ORIGIN
    assert legs[0]["destination"] == MIDDLE
    assert legs[0]["ata"] == ETA
    assert legs[1]["origin"] == MIDDLE
    assert legs[1]["destination"] == DESTINATION
    assert legs[1]["ata"] is None
    # Every leg must be clickable on the map, so it carries its own endpoints.
    assert len(legs[0]["points"]) == 2
    assert len(legs[1]["points"]) == 2


def test_the_current_leg_follows_the_flown_itinerary(planner, unit):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)
    first, second = add_intermediate_stop(mission)

    # A plan with no actual times follows the simulated position, so a freshly
    # departed aircraft is shown tracking leg 1, not the whole itinerary.
    early = air_simulation.snapshot(
        [mission], now=ETD + dt.timedelta(minutes=2), allowed_unit_ids={unit.id}
    )["aircraft"][0]
    assert early["current_leg"]["sequence"] == 1
    assert early["current_leg"]["origin"] == ORIGIN
    assert early["current_leg"]["destination"] == MIDDLE

    # Once leg 1 is landed, the stop over hands to leg 2.
    first.atd = ETD
    first.ata = ETA
    first.save(update_fields=["atd", "ata"])
    after_stop = air_simulation.snapshot(
        [mission], now=ETA + dt.timedelta(minutes=5), allowed_unit_ids={unit.id}
    )["aircraft"][0]
    assert after_stop["current_leg"]["sequence"] == 2
    assert after_stop["current_leg"]["origin"] == MIDDLE
    assert after_stop["current_leg"]["destination"] == DESTINATION


def test_a_landed_leg_parks_the_aircraft_until_the_next_departure(
    planner, unit
):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)
    first, second = add_intermediate_stop(mission)
    first.atd = ETD
    first.ata = ETA
    first.save(update_fields=["atd", "ata"])

    # The board has closed leg 1 only. The simulation must hold the aircraft at
    # the intermediate stop instead of flying it on to the final destination.
    row = air_simulation.snapshot(
        [mission], now=ETA + dt.timedelta(minutes=30), allowed_unit_ids={unit.id}
    )["aircraft"][0]

    stop = row["legs"][0]["points"][-1]
    assert row["status"] == "landed"
    assert row["status_label"] == "Landed at Stop"
    assert row["current_leg"]["sequence"] == 2
    assert row["progress_percent"] < 100
    assert list(row["position"]) != row["planned_route"][-1]
    assert row["position"][0] == pytest.approx(stop[0], abs=1e-4)
    assert row["position"][1] == pytest.approx(stop[1], abs=1e-4)

    # Once the board records leg 2's departure the aircraft flies that leg.
    second.atd = ETA + dt.timedelta(minutes=20)
    second.save(update_fields=["atd"])
    moving = air_simulation.snapshot(
        [mission], now=ETA + dt.timedelta(minutes=45), allowed_unit_ids={unit.id}
    )["aircraft"][0]
    assert moving["status"] == "en_route"
    assert moving["current_leg"]["sequence"] == 2
    assert moving["progress_percent"] > row["progress_percent"]


def test_the_eta_is_recalculated_from_the_legs_actual_departure(planner, unit):
    make_aircraft(unit, tail="001")
    mission = make_mission(unit, planner)
    first, second = add_intermediate_stop(mission)
    first.atd = ETD
    first.ata = ETA
    first.save(update_fields=["atd", "ata"])

    # Leg 2 is planned for an hour. A late takeoff must move its ETA, not leave
    # the mission-level estimate on the card.
    late = ETA + dt.timedelta(minutes=90)
    second.atd = late
    second.save(update_fields=["atd"])

    row = air_simulation.snapshot(
        [mission], now=late + dt.timedelta(minutes=10), allowed_unit_ids={unit.id}
    )["aircraft"][0]

    assert row["current_leg"]["sequence"] == 2
    assert row["status"] == "en_route"
    assert row["simulated_arrival_at"] == late + dt.timedelta(minutes=70)
    assert row["remaining_to_arrival_minutes"] == pytest.approx(60, abs=0.2)

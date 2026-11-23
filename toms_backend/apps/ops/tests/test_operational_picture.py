"""Dashboard operational map: unit scoping and honest data contracts (spec §6)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.command.models import HazardLocation, VehiclePosition
from apps.core.factories import make_unit, make_user
from apps.legacy.models import HazardRegistry
from apps.ops.models import AreaOfResponsibility, Mission, Vehicle

pytestmark = pytest.mark.django_db

POLYGON = [
    [120.90, 14.50],
    [121.00, 14.50],
    [121.00, 14.60],
    [120.90, 14.60],
]


@pytest.fixture
def child_unit(db, unit):
    return make_unit(name="Alpha Detachment", acronym="ALPHA-1", parent=unit)


@pytest.fixture
def mission(db, creator, unit):
    return Mission.objects.create(
        mission_number="MSN-MAP-1",
        category="ground",
        title="Patrol",
        purpose="Presence",
        unit=unit,
        created_by=creator,
        start_at=timezone.now(),
        end_at=timezone.now() + dt.timedelta(hours=4),
        location_name="North Ridge",
        latitude=14.5995,
        longitude=120.9842,
        status="approved",
    )


def test_map_requires_authentication(api):
    assert api.get("/api/gis/operational-picture/").status_code in (401, 403)


def test_scope_is_the_users_unit_and_subordinates(as_user, creator, unit, child_unit):
    from apps.core.factories import make_user

    make_user(unit=child_unit)
    make_user(unit=make_unit(name="Bravo", acronym="BRAVO"))

    body = as_user(creator).get("/api/gis/operational-picture/").json()
    assert body["scope"]["unit_id"] == unit.id
    assert set(body["scope"]["unit_ids"]) == {unit.id, child_unit.id}
    assert body["scope"]["subordinate_count"] == 1


def test_client_cannot_widen_scope_with_a_unit_parameter(as_user, creator, other_unit):
    response = as_user(creator).get(f"/api/gis/operational-picture/?unit={other_unit.id}")
    assert response.status_code == 403


def test_client_may_narrow_scope(as_user, creator, unit, child_unit):
    response = as_user(creator).get(f"/api/gis/operational-picture/?unit={child_unit.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["unit_ids"] == [child_unit.id]
    # The header must describe the narrowed unit, not the user's home unit.
    assert body["scope"]["unit_id"] == child_unit.id
    assert body["scope"]["unit_acronym"] == child_unit.acronym


def test_missions_without_coordinates_are_not_plotted(as_user, creator, unit):
    Mission.objects.create(
        mission_number="MSN-NOCOORD",
        category="ground",
        title="No location",
        purpose="x",
        unit=unit,
        created_by=creator,
        start_at=timezone.now(),
        end_at=timezone.now(),
        status="approved",
    )
    body = as_user(creator).get("/api/gis/operational-picture/").json()
    assert body["counts"]["missions"] == 0
    assert body["counts"]["missions_missing_coordinates"] == 1


def test_ground_and_air_layers_split_missions(as_user, creator, unit, mission):
    Mission.objects.create(
        mission_number="MSN-AIR-1",
        category="air",
        title="Recon",
        purpose="x",
        unit=unit,
        created_by=creator,
        start_at=timezone.now(),
        end_at=timezone.now(),
        latitude=14.5,
        longitude=121.0,
        status="approved",
    )
    body = as_user(creator).get("/api/gis/operational-picture/").json()
    assert [row["mission_number"] for row in body["ground"]] == ["MSN-MAP-1"]
    assert [row["mission_number"] for row in body["air"]] == ["MSN-AIR-1"]


def test_layers_parameter_limits_the_payload(as_user, creator, unit, mission):
    AreaOfResponsibility.objects.create(
        unit=unit,
        name="Alpha AOR",
        code="AOR-A",
        geometry={"type": "Polygon", "coordinates": [POLYGON]},
    )
    body = as_user(creator).get("/api/gis/operational-picture/?layers=missions").json()
    assert body["layers"] == ["missions"]
    assert body["counts"]["aors"] == 0
    assert body["counts"]["missions"] == 1


def test_aor_polygon_is_returned_for_the_scope(as_user, creator, unit):
    AreaOfResponsibility.objects.create(
        unit=unit,
        name="Alpha AOR",
        code="AOR-A",
        geometry={"type": "Polygon", "coordinates": [POLYGON]},
        centroid_lat=14.55,
        centroid_lon=120.95,
    )
    body = as_user(creator).get("/api/gis/operational-picture/").json()
    assert body["counts"]["aors"] == 1
    assert body["aors"][0]["geometry"]["type"] == "Polygon"
    assert body["center"] is not None


def test_vehicle_reports_last_known_position_and_never_claims_live(
    as_user, creator, unit
):
    vehicle = Vehicle.objects.create(
        registration_number="V-MAP-1", vehicle_type="Truck", unit=unit
    )
    old = timezone.now() - dt.timedelta(days=3)
    VehiclePosition.objects.create(
        vehicle=vehicle, latitude=14.6, longitude=121.0, reported_at=old, source="manual"
    )
    Vehicle.objects.create(registration_number="V-MAP-2", vehicle_type="Truck", unit=unit)

    body = as_user(creator).get("/api/gis/operational-picture/").json()
    positions = body["vehicle_positions"]
    assert len(positions) == 1
    assert positions[0]["unit_id"] == unit.id
    assert positions[0]["is_live"] is False
    assert positions[0]["is_stale"] is True
    assert positions[0]["reported_at"]
    assert body["vehicles_without_position"] == 1


def test_hazards_without_georeference_are_counted_not_invented(as_user, creator, unit):
    hazard = HazardRegistry.objects.create(
        unit=unit.name,
        unit_id=unit,
        activity_code="PATROL",
        hazard_description="Steep drainage ditch",
        before_likelihood=4,
        before_severity="B",
        before_risk_rating="4B",
        mitigations="Mark the ditch and brief all personnel.",
        after_likelihood=2,
        after_severity="B",
        after_risk_rating="2B",
        created_by=creator,
    )
    body = as_user(creator).get("/api/gis/operational-picture/").json()
    assert body["counts"]["hazards"] == 0
    assert body["hazards_without_location"] == 1

    HazardLocation.objects.create(
        hazard=hazard, unit=unit, latitude=14.61, longitude=121.01, radius_metres=50
    )
    body = as_user(creator).get("/api/gis/operational-picture/").json()
    assert body["counts"]["hazards"] == 1
    assert body["hazards"][0]["radius_metres"] == 50


def test_a_completed_mission_leaves_no_route_trace(as_user, creator, unit, mission):
    """A finished mission's route is history, not something to watch."""
    from apps.ops.models import TripItinerary, VehicleTrip

    trip = VehicleTrip.objects.create(
        trip_number="TRIP-MAP-1",
        title="Convoy",
        mission=mission,
        status="approved",
        created_by=creator,
    )
    TripItinerary.objects.create(
        trip=trip, sequence=1, location_name="Start", latitude=14.5, longitude=121.0
    )
    TripItinerary.objects.create(
        trip=trip, sequence=2, location_name="End", latitude=14.6, longitude=121.1
    )

    body = as_user(creator).get("/api/gis/operational-picture/").json()
    assert body["counts"]["trip_routes"] == 1
    # The route names its mission, so the map can judge it without a lookup.
    assert body["trip_routes"][0]["mission_number"] == "MSN-MAP-1"
    assert body["trip_routes"][0]["mission_status"] == "approved"

    Mission.objects.filter(pk=mission.pk).update(status="completed")

    body = as_user(creator).get("/api/gis/operational-picture/").json()
    assert body["trip_routes"] == []
    assert body["counts"]["trip_routes"] == 0
    # The pin leaves the map too: the dashboard is the operational picture, not
    # the record. The audit path is the Missions page, which still lists it.
    assert body["missions"] == []
    assert body["counts"]["missions"] == 0

    listed = as_user(creator).get("/api/missions/?page_size=50").json()
    assert "MSN-MAP-1" in [row["mission_number"] for row in listed["results"]]


def test_a_completed_mission_is_off_the_map_even_when_it_is_the_only_one(
    as_user, creator, unit, mission
):
    Mission.objects.filter(pk=mission.pk).update(status="completed")

    body = as_user(creator).get("/api/gis/operational-picture/?status=completed").json()
    assert body["missions"] == []
    assert body["air"] == [] and body["ground"] == []
    assert body["counts"]["missions"] == 0


def test_a_status_filter_does_not_leave_another_missions_route(as_user, creator, unit, mission):
    """The route layer obeys the same mission filters as the pins."""
    from apps.ops.models import TripItinerary, VehicleTrip

    trip = VehicleTrip.objects.create(
        trip_number="TRIP-FILTER-1",
        title="Convoy",
        mission=mission,
        status="planned",
        created_by=creator,
    )
    TripItinerary.objects.create(
        trip=trip, sequence=1, location_name="Start", latitude=14.5, longitude=121.0
    )
    TripItinerary.objects.create(
        trip=trip, sequence=2, location_name="End", latitude=14.6, longitude=121.1
    )

    approved = as_user(creator).get("/api/gis/operational-picture/?status=approved").json()
    assert approved["counts"]["trip_routes"] == 1

    # The only mission in this filter is finished, so nothing is traced.
    completed = as_user(creator).get("/api/gis/operational-picture/?status=completed").json()
    assert completed["missions"] == []
    assert completed["trip_routes"] == []

    air = as_user(creator).get("/api/gis/operational-picture/?category=air").json()
    assert air["trip_routes"] == []


def test_unrelated_unit_missions_are_excluded(as_user, creator, unit, mission):
    other = make_unit(name="Bravo", acronym="BRAVO")
    Mission.objects.create(
        mission_number="MSN-OTHER",
        category="ground",
        title="Other unit",
        purpose="x",
        unit=other,
        created_by=creator,
        start_at=timezone.now(),
        end_at=timezone.now(),
        latitude=14.7,
        longitude=121.1,
        status="approved",
    )
    body = as_user(creator).get("/api/gis/operational-picture/").json()
    assert [row["mission_number"] for row in body["missions"]] == ["MSN-MAP-1"]


def test_the_fuel_layer_pins_scoped_pol_stock(as_user, creator, unit, other_unit):
    """Fuel pins come from the register, inside the map's own scope."""
    from apps.ops.models import InventoryCategory, InventoryItem

    aviation = InventoryCategory.objects.get(code="POL-01")
    drums = InventoryItem.objects.create(
        category=aviation,
        name="Jet A1 drums",
        unit=unit,
        quantity_total=12,
        unit_of_measure="drum",
        fuel_grade="jet_a1",
        fuel_form="drum",
        drum_capacity_litres=200,
        quantity_available=9,
        location_latitude=14.6,
        location_longitude=120.98,
        storage_location="Forward POL point",
    )
    # Nothing left in it: not a place on the map.
    InventoryItem.objects.create(
        category=aviation,
        name="Empty AVGas drums",
        unit=unit,
        quantity_total=0,
        quantity_available=0,
        unit_of_measure="drum",
        fuel_grade="avgas",
        fuel_form="drum",
        drum_capacity_litres=200,
        location_latitude=14.61,
        location_longitude=120.99,
    )
    # Stock with nowhere to sit: counted, not invented onto the map.
    InventoryItem.objects.create(
        category=aviation,
        name="Diesel drums",
        unit=unit,
        quantity_total=4,
        quantity_available=4,
        unit_of_measure="drum",
        fuel_form="drum",
        drum_capacity_litres=200,
    )
    # Non-aviation POL stock: real fuel, but not a "fuel location" on the
    # flight picture, so it is neither drawn nor reported as unplaced.
    automotive = InventoryCategory.objects.get(code="POL-02")
    InventoryItem.objects.create(
        category=automotive,
        name="Diesel bulk tank",
        unit=unit,
        quantity_total=5000,
        quantity_available=5000,
        unit_of_measure="litre",
        fuel_grade="diesel",
        fuel_form="bulk",
        location_latitude=14.62,
        location_longitude=121.0,
    )

    response = as_user(creator).get("/api/gis/operational-picture/?layers=fuel")
    assert response.status_code == 200, response.content
    body = response.json()

    assert [row["id"] for row in body["fuel_positions"]] == [drums.id]
    row = body["fuel_positions"][0]
    assert row["form"] == "drum"
    assert row["quantity_available"] == 9
    assert row["latitude"] == 14.6 and row["longitude"] == 120.98
    assert row["position_source"] == "stored"
    assert body["fuel_without_position"] == 1
    assert body["counts"]["fuel_positions"] == 1
    # The diesel tank holds thousands of litres at its own point and is still
    # absent: this layer is aviation fuel only.
    assert all(row["name"] != "Diesel bulk tank" for row in body["fuel_positions"])

    denied = as_user(creator).get(
        f"/api/gis/operational-picture/?layers=fuel&unit={other_unit.id}"
    )
    assert denied.status_code == 403


def test_mobility_layer_draws_the_fleet_and_never_a_pov(
    as_user, creator, unit
):
    """The mobility layer is the unit's establishment, not personal cars."""
    from apps.command.models import VehiclePosition
    from apps.ops.models import Vehicle

    fleet = Vehicle.objects.create(
        registration_number="KM450-001",
        vehicle_type="KM450",
        unit=unit,
        status="available",
    )
    pov = Vehicle.objects.create(
        registration_number="POV-0009",
        vehicle_type="Car",
        unit=unit,
        status="available",
        is_pov=True,
    )
    for vehicle in (fleet, pov):
        VehiclePosition.objects.create(
            vehicle=vehicle,
            latitude=8.95,
            longitude=125.48,
            reported_at=timezone.now(),
            source="manual",
        )

    body = as_user(creator).get("/api/gis/operational-picture/?layers=vehicles").json()

    drawn = {row["registration_number"] for row in body["vehicle_positions"]}
    assert drawn == {"KM450-001"}
    assert all(
        row["vehicle_id"] != pov.id for row in body["vehicle_positions"]
    )
    # A POV is not a mobility asset at all, so it cannot inflate the "without a
    # position" count either.
    assert body["vehicles_without_position"] == 0
    assert body["counts"]["vehicle_positions"] == 1


def test_operations_center_sees_every_boundary_but_stays_scoped_for_data(
    as_user, creator, unit, other_unit
):
    """An Ops Center reads the whole area map; its data layers stay its own."""
    from apps.command.models import (
        OfficeSection,
        SectionAssignment,
        UnitMapMarker,
        VehiclePosition,
    )
    from apps.core.factories import make_user
    from apps.ops.models import Vehicle

    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    member = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(
        user=member, section=office, assignment_type="primary"
    )

    for holder, code in ((unit, "AOR-OWN"), (other_unit, "AOR-NEAR")):
        AreaOfResponsibility.objects.create(
            unit=holder,
            name=f"{code} area",
            code=code,
            geometry={"type": "Polygon", "coordinates": [POLYGON]},
        )
        UnitMapMarker.objects.create(unit=holder, latitude=14.55, longitude=120.95)

    mine = Vehicle.objects.create(
        registration_number="KM450-OWN", vehicle_type="KM450", unit=unit
    )
    theirs = Vehicle.objects.create(
        registration_number="KM450-FAR", vehicle_type="KM450", unit=other_unit
    )
    for vehicle, longitude in ((mine, 120.95), (theirs, 121.10)):
        VehiclePosition.objects.create(
            vehicle=vehicle,
            latitude=14.55,
            longitude=longitude,
            reported_at=timezone.now(),
            source="manual",
        )

    body = as_user(member).get(
        "/api/gis/operational-picture/?layers=aor,units,vehicles"
    ).json()

    # Boundaries and seals of every unit, with the centre's own scope intact.
    assert {row["code"] for row in body["aors"]} == {"AOR-OWN", "AOR-NEAR"}
    assert {"ALPHA", "BRAVO"} <= {row["unit_acronym"] for row in body["units"]}
    assert body["scope"]["unit_ids"] == [unit.id]
    # Another unit's vehicle is still not the centre's to watch.
    assert [row["registration_number"] for row in body["vehicle_positions"]] == [
        "KM450-OWN"
    ]

    # A plain account keeps the scoped picture.
    scoped = as_user(creator).get("/api/gis/operational-picture/?layers=aor").json()
    assert {row["code"] for row in scoped["aors"]} == {"AOR-OWN"}

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

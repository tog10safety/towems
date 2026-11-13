"""Vehicle assignment, ordered itineraries and planned road routes (spec §5).

The routing engine is stubbed at the HTTP boundary, which is exactly where the
real one is called: these tests assert the request that would be sent, that the
returned road geometry is what gets stored, and that a failure is reported as a
failure rather than replaced with a straight line.
"""

from __future__ import annotations

import pytest

from apps.core.factories import make_user
from apps.ops import routing
from apps.ops.models import Vehicle, VehicleTrip, VehicleTripVehicle
from apps.ops.tests.test_missions import ground_payload

pytestmark = pytest.mark.django_db

OSRM_PAYLOAD = {
    "code": "Ok",
    "routes": [
        {
            "distance": 21450.0,
            "duration": 1830.0,
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [125.5406, 8.9475],
                    [125.5530, 8.9510],
                    [125.6010, 8.9900],
                ],
            },
            "legs": [
                {"distance": 10200.0, "duration": 900.0},
                {"distance": 11250.0, "duration": 930.0},
            ],
        }
    ],
}


def create_mission(client, unit, **extra):
    response = client.post("/api/missions/", ground_payload(unit, **extra), format="json")
    assert response.status_code == 201, response.content
    return response.json()


@pytest.fixture
def vehicle(db, unit):
    return Vehicle.objects.create(
        registration_number="ABC-1234",
        vehicle_type="Truck",
        unit=unit,
        status="available",
    )


@pytest.fixture
def second_vehicle(db, unit):
    return Vehicle.objects.create(
        registration_number="XYZ-9876",
        vehicle_type="Ambulance",
        unit=unit,
        status="available",
    )


def assign(client, mission_id, vehicle_ids):
    return client.post(
        f"/api/missions/{mission_id}/vehicles/",
        {"vehicles": vehicle_ids},
        format="json",
    )


STOPS = [
    {
        "location_name": "Headquarters",
        "latitude": 8.9475,
        "longitude": 125.5406,
    },
    {"location_name": "Stop 1", "latitude": 8.9510, "longitude": 125.5530},
    {"location_name": "Final destination", "latitude": 8.9900, "longitude": 125.6010},
]


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def test_vehicle_is_assigned_from_existing_inventory(as_user, planner, unit, vehicle):
    client = as_user(planner)
    mission = create_mission(client, unit)

    response = assign(client, mission["id"], [vehicle.id])
    assert response.status_code == 200, response.content
    payload = response.json()
    assert payload["vehicles"][0]["registration_number"] == "ABC-1234"
    assert VehicleTrip.objects.filter(mission_id=mission["id"]).count() == 1
    # The inventory record itself is reused, never duplicated.
    assert Vehicle.objects.filter(registration_number="ABC-1234").count() == 1


def test_each_vehicle_keeps_its_own_route(as_user, planner, unit, vehicle, second_vehicle):
    client = as_user(planner)
    mission = create_mission(client, unit)

    response = assign(client, mission["id"], [vehicle.id, second_vehicle.id])
    assert response.status_code == 200, response.content
    trips = VehicleTrip.objects.filter(mission_id=mission["id"])
    assert trips.count() == 2
    trip_ids = {payload["trip_id"] for payload in response.json()["vehicles"]}
    assert len(trip_ids) == 2


def test_removing_a_vehicle_cancels_its_route(as_user, planner, unit, vehicle, second_vehicle):
    client = as_user(planner)
    mission = create_mission(client, unit)
    assign(client, mission["id"], [vehicle.id, second_vehicle.id])

    response = assign(client, mission["id"], [vehicle.id])
    assert response.status_code == 200, response.content
    assert response.json()["vehicles"].__len__() == 1
    assert (
        VehicleTrip.objects.filter(
            mission_id=mission["id"], status="cancelled"
        ).count()
        == 1
    )


def test_unserviceable_vehicle_is_refused(as_user, planner, unit, vehicle):
    client = as_user(planner)
    mission = create_mission(client, unit)
    vehicle.status = "maintenance"
    vehicle.save(update_fields=["status"])

    response = assign(client, mission["id"], [vehicle.id])
    assert response.status_code == 400
    assert "cannot be assigned" in str(response.json()["errors"]["vehicles"])


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------


def add_to_mission(client, mission_id, people):
    response = client.post(
        f"/api/missions/{mission_id}/assign-personnel/",
        {"personnel": [{"user_id": person.id, "role": "Driver"} for person in people]},
        format="json",
    )
    assert response.status_code == 200, response.content


def assign_with_drivers(client, mission_id, drivers_by_vehicle):
    return client.post(
        f"/api/missions/{mission_id}/vehicles/",
        {
            "vehicles": [int(vehicle_id) for vehicle_id in drivers_by_vehicle],
            "drivers": {
                vehicle_id: person.id for vehicle_id, person in drivers_by_vehicle.items()
            },
        },
        format="json",
    )


def test_each_vehicle_is_saved_with_its_designated_driver(
    as_user, planner, unit, vehicle, second_vehicle
):
    client = as_user(planner)
    mission = create_mission(client, unit)
    first_driver = make_user(unit=unit, rank="Sgt")
    second_driver = make_user(unit=unit, rank="A1C")
    add_to_mission(client, mission["id"], [first_driver, second_driver])

    response = assign_with_drivers(
        client,
        mission["id"],
        {str(vehicle.id): first_driver, str(second_vehicle.id): second_driver},
    )
    assert response.status_code == 200, response.content

    rows = {row["vehicle_id"]: row for row in response.json()["vehicles"]}
    assert rows[vehicle.id]["driver_id"] == first_driver.id
    assert rows[vehicle.id]["driver_name"] == first_driver.full_name
    assert rows[second_vehicle.id]["driver_id"] == second_driver.id

    links = VehicleTripVehicle.objects.filter(trip__mission_id=mission["id"])
    assert links.get(vehicle=vehicle).driver_id == first_driver.id
    assert links.get(vehicle=second_vehicle).driver_id == second_driver.id


def test_a_mission_with_personnel_cannot_save_vehicles_without_drivers(
    as_user, planner, unit, vehicle
):
    client = as_user(planner)
    mission = create_mission(client, unit)
    add_to_mission(client, mission["id"], [make_user(unit=unit)])

    response = assign(client, mission["id"], [vehicle.id])
    assert response.status_code == 400
    assert "Designate one driver" in str(response.json()["errors"]["drivers"])


def test_a_selected_vehicle_cannot_be_saved_without_its_driver(
    as_user, planner, unit, vehicle, second_vehicle
):
    client = as_user(planner)
    mission = create_mission(client, unit)
    driver = make_user(unit=unit)
    add_to_mission(client, mission["id"], [driver])

    # The second vehicle is present in the request but has no driver.
    response = client.post(
        f"/api/missions/{mission['id']}/vehicles/",
        {
            "vehicles": [vehicle.id, second_vehicle.id],
            "drivers": {str(vehicle.id): driver.id},
        },
        format="json",
    )
    assert response.status_code == 400
    assert "Designate a driver" in str(response.json()["errors"]["drivers"])


def test_one_driver_cannot_operate_two_vehicles(
    as_user, planner, unit, vehicle, second_vehicle
):
    client = as_user(planner)
    mission = create_mission(client, unit)
    driver = make_user(unit=unit)
    add_to_mission(client, mission["id"], [driver])

    response = assign_with_drivers(
        client,
        mission["id"],
        {str(vehicle.id): driver, str(second_vehicle.id): driver},
    )
    assert response.status_code == 400
    assert "cannot operate more than one vehicle" in str(
        response.json()["errors"]["drivers"]
    )


def test_a_driver_must_already_be_on_the_mission_roster(
    as_user, planner, unit, vehicle
):
    client = as_user(planner)
    mission = create_mission(client, unit)
    stranger = make_user(unit=unit)

    response = assign_with_drivers(client, mission["id"], {str(vehicle.id): stranger})
    assert response.status_code == 400
    assert "assigned to this mission's personnel" in str(
        response.json()["errors"]["drivers"]
    )


# ---------------------------------------------------------------------------
# Itinerary
# ---------------------------------------------------------------------------


def test_itinerary_stops_are_saved_in_order(as_user, planner, unit, vehicle):
    client = as_user(planner)
    mission = create_mission(client, unit)
    trip_id = assign(client, mission["id"], [vehicle.id]).json()["vehicles"][0]["trip_id"]

    response = client.put(
        f"/api/trips/{trip_id}/itinerary/", {"stops": STOPS}, format="json"
    )
    assert response.status_code == 200, response.content
    rows = response.json()
    assert [row["sequence"] for row in rows] == [1, 2, 3]
    assert [row["location_name"] for row in rows] == [
        "Headquarters",
        "Stop 1",
        "Final destination",
    ]


def test_reordering_replaces_the_saved_order(as_user, planner, unit, vehicle):
    client = as_user(planner)
    mission = create_mission(client, unit)
    trip_id = assign(client, mission["id"], [vehicle.id]).json()["vehicles"][0]["trip_id"]
    client.put(f"/api/trips/{trip_id}/itinerary/", {"stops": STOPS}, format="json")

    reversed_stops = list(reversed(STOPS))
    response = client.put(
        f"/api/trips/{trip_id}/itinerary/", {"stops": reversed_stops}, format="json"
    )
    assert response.status_code == 200, response.content
    assert [row["location_name"] for row in response.json()] == [
        "Final destination",
        "Stop 1",
        "Headquarters",
    ]


def test_invalid_coordinates_are_rejected(as_user, planner, unit, vehicle):
    client = as_user(planner)
    mission = create_mission(client, unit)
    trip_id = assign(client, mission["id"], [vehicle.id]).json()["vehicles"][0]["trip_id"]

    bad = [STOPS[0], {"location_name": "Nowhere", "latitude": 999, "longitude": 125.0}]
    response = client.put(
        f"/api/trips/{trip_id}/itinerary/", {"stops": bad}, format="json"
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Road route
# ---------------------------------------------------------------------------


def test_route_follows_the_road_network_in_itinerary_order(
    as_user, planner, unit, vehicle, monkeypatch
):
    client = as_user(planner)
    mission = create_mission(client, unit)
    trip_id = assign(client, mission["id"], [vehicle.id]).json()["vehicles"][0]["trip_id"]
    client.put(f"/api/trips/{trip_id}/itinerary/", {"stops": STOPS}, format="json")

    seen = {}

    def fake_get(url, timeout=10):
        seen["url"] = url
        return OSRM_PAYLOAD

    monkeypatch.setattr(routing, "_http_get_json", fake_get)

    response = client.post(f"/api/trips/{trip_id}/route/", {}, format="json")
    assert response.status_code == 200, response.content
    payload = response.json()["route"]

    # The engine was asked for the stops in the saved order.
    coordinates = seen["url"].split("/route/v1/driving/")[1].split("?")[0]
    assert coordinates == "125.5406,8.9475;125.553,8.951;125.601,8.99"

    # The road geometry is stored, not a straight line between the stops.
    assert payload["geometry"]["type"] == "LineString"
    assert len(payload["geometry"]["coordinates"]) == 3
    assert [leg["distance_m"] for leg in payload["legs"]] == [10200.0, 11250.0]
    assert payload["distance_m"] == 21450.0
    assert payload["duration_s"] == 1830.0

    trip = VehicleTrip.objects.get(pk=trip_id)
    assert trip.route_status == VehicleTrip.ROUTE_CALCULATED
    assert trip.route_distance_m == 21450.0
    assert trip.route_geometry["type"] == "LineString"
    assert "OSRM" in trip.route_provider


def test_an_unroutable_trip_reports_the_failure_without_substitution(
    as_user, planner, unit, vehicle, monkeypatch
):
    client = as_user(planner)
    mission = create_mission(client, unit)
    trip_id = assign(client, mission["id"], [vehicle.id]).json()["vehicles"][0]["trip_id"]
    client.put(f"/api/trips/{trip_id}/itinerary/", {"stops": STOPS}, format="json")

    monkeypatch.setattr(
        routing,
        "_http_get_json",
        lambda url, timeout=10: {"code": "NoRoute", "message": "Impossible route"},
    )

    response = client.post(f"/api/trips/{trip_id}/route/", {}, format="json")
    assert response.status_code == 400
    assert "No road route" in str(response.json()["errors"]["detail"])

    trip = VehicleTrip.objects.get(pk=trip_id)
    assert trip.route_status == VehicleTrip.ROUTE_FAILED
    assert trip.route_geometry is None
    assert trip.route_distance_m is None


def test_route_needs_two_located_stops(as_user, planner, unit, vehicle):
    client = as_user(planner)
    mission = create_mission(client, unit)
    trip_id = assign(client, mission["id"], [vehicle.id]).json()["vehicles"][0]["trip_id"]
    client.put(
        f"/api/trips/{trip_id}/itinerary/", {"stops": [STOPS[0]]}, format="json"
    )

    response = client.post(f"/api/trips/{trip_id}/route/", {}, format="json")
    assert response.status_code == 400
    assert "two itinerary stops" in str(response.json()["errors"]["detail"])


def test_editing_the_itinerary_discards_the_old_route(
    as_user, planner, unit, vehicle, monkeypatch
):
    client = as_user(planner)
    mission = create_mission(client, unit)
    trip_id = assign(client, mission["id"], [vehicle.id]).json()["vehicles"][0]["trip_id"]
    client.put(f"/api/trips/{trip_id}/itinerary/", {"stops": STOPS}, format="json")
    monkeypatch.setattr(routing, "_http_get_json", lambda url, timeout=10: OSRM_PAYLOAD)
    assert client.post(f"/api/trips/{trip_id}/route/", {}, format="json").status_code == 200

    client.put(
        f"/api/trips/{trip_id}/itinerary/", {"stops": STOPS[:2]}, format="json"
    )
    trip = VehicleTrip.objects.get(pk=trip_id)
    assert trip.route_status == VehicleTrip.ROUTE_NOT_CALCULATED
    assert trip.route_geometry is None


def test_routing_module_refuses_a_straight_line_substitute(monkeypatch):
    """Legs that do not cover every pair of stops are an error, not a shortcut."""
    partial = {
        "code": "Ok",
        "routes": [
            {
                "distance": 1000.0,
                "duration": 60.0,
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                "legs": [{"distance": 1000.0, "duration": 60.0}],
            }
        ],
    }
    monkeypatch.setattr(routing, "_http_get_json", lambda url, timeout=10: partial)

    with pytest.raises(routing.RoutingError, match="incomplete route"):
        routing.route(
            [
                {"latitude": 0, "longitude": 0},
                {"latitude": 1, "longitude": 1},
                {"latitude": 2, "longitude": 2},
            ]
        )

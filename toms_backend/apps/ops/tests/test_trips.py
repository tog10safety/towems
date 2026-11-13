"""Vehicle trips and itineraries (optional feature of Ground Operations)."""

from __future__ import annotations

import pytest

from apps.ops.models import Vehicle, VehicleTrip

pytestmark = pytest.mark.django_db


@pytest.fixture
def vehicles(db, unit):
    return [
        Vehicle.objects.create(
            registration_number="PMV-001", vehicle_type="Truck", unit=unit, capacity=20
        ),
        Vehicle.objects.create(
            registration_number="PMV-002", vehicle_type="Ambulance", unit=unit, capacity=6
        ),
    ]


def trip_payload(vehicles, **overrides):
    payload = {
        "title": "Logistics convoy",
        "purpose": "Resupply",
        "status": "draft",
        "departure_at": "2026-11-02T05:00:00",
        "return_at": "2026-11-02T17:00:00",
        "vehicle_ids": [v.id for v in vehicles],
        "itinerary": [
            {"location_name": "Camp Aguinaldo", "latitude": 14.6091, "longitude": 121.0622},
            {"location_name": "Checkpoint 2", "mgrs": "51PTS8284814992"},
            {"location_name": "Forward Base", "latitude": 14.75, "longitude": 121.2,
             "purpose": "Unload", "remarks": "Report to logistics officer"},
        ],
    }
    payload.update(overrides)
    return payload


def test_create_trip_with_vehicles_and_itinerary(as_user, planner, vehicles):
    client = as_user(planner)
    response = client.post("/api/trips/", trip_payload(vehicles), format="json")
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["trip_number"].startswith("TRIP-")
    assert len(body["vehicles"]) == 2
    assert [entry["sequence"] for entry in body["itinerary"]] == [1, 2, 3]
    assert body["itinerary"][1]["mgrs"] == "51PTS8284814992"
    # Derived MGRS for the coordinate-only stops
    assert body["itinerary"][0]["mgrs"].startswith("51P")


def test_trip_requires_known_vehicles(as_user, planner, vehicles):
    response = as_user(planner).post(
        "/api/trips/", trip_payload(vehicles, vehicle_ids=[999999]), format="json"
    )
    assert response.status_code == 400


def test_trip_rejects_invalid_mgrs(as_user, planner, vehicles):
    payload = trip_payload(vehicles)
    payload["itinerary"] = [{"location_name": "Bad", "mgrs": "ZZZ-BAD"}]
    response = as_user(planner).post("/api/trips/", payload, format="json")
    assert response.status_code == 400


def test_trip_rejects_duplicate_sequences(as_user, planner, vehicles):
    payload = trip_payload(vehicles)
    payload["itinerary"] = [
        {"location_name": "A", "sequence": 1},
        {"location_name": "B", "sequence": 1},
    ]
    response = as_user(planner).post("/api/trips/", payload, format="json")
    assert response.status_code == 400


def test_trip_rejects_return_before_departure(as_user, planner, vehicles):
    payload = trip_payload(vehicles, return_at="2026-11-01T00:00:00")
    response = as_user(planner).post("/api/trips/", payload, format="json")
    assert response.status_code == 400


def test_trip_can_be_linked_to_a_mission(as_user, planner, vehicles, unit):
    from apps.ops.tests.test_missions import ground_payload

    client = as_user(planner)
    mission = client.post("/api/missions/", ground_payload(unit), format="json").json()
    payload = trip_payload(vehicles, mission=mission["id"])
    response = client.post("/api/trips/", payload, format="json")
    assert response.status_code == 201
    body = response.json()
    assert body["mission"] == mission["id"]
    assert body["mission_number"] == mission["mission_number"]


def test_append_itinerary_entry(as_user, planner, vehicles):
    client = as_user(planner)
    trip = client.post("/api/trips/", trip_payload(vehicles), format="json").json()
    response = client.post(
        f"/api/trips/{trip['id']}/itinerary/",
        [{"location_name": "Return stop", "latitude": 14.6, "longitude": 121.0}],
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()[0]["sequence"] == 4
    assert VehicleTrip.objects.get(pk=trip["id"]).itinerary.count() == 4


def test_approve_trip_permissions(as_user, planner, safety, vehicles):
    client = as_user(planner)
    trip = client.post("/api/trips/", trip_payload(vehicles), format="json").json()
    assert client.post(f"/api/trips/{trip['id']}/approve/", {}, format="json").status_code == 403
    response = as_user(safety).post(f"/api/trips/{trip['id']}/approve/", {}, format="json")
    assert response.status_code == 200
    assert response.json()["status"] == "approved"

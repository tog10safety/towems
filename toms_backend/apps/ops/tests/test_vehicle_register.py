"""The vehicle register is maintained by the Logistics Officer alone.

The Operations Center plans missions and therefore still *reads* the register
and assigns vehicles to trips; it just cannot add, edit or retire a vehicle.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.command.models import VehiclePosition
from apps.core import timeutils
from apps.ops.models import Vehicle

pytestmark = pytest.mark.django_db


def payload(unit, registration="KM450-1234567"):
    return {
        "registration_number": registration,
        "vehicle_type": "Truck",
        "make_model": "KM450",
        "unit": unit.id,
        "capacity": 12,
    }


def test_logistics_officer_adds_a_vehicle(as_user, logistics, unit):
    client = as_user(logistics)

    created = client.post("/api/vehicles/", payload(unit), format="json")
    assert created.status_code == 201, created.content
    vehicle_id = created.json()["id"]
    assert Vehicle.objects.filter(pk=vehicle_id, unit=unit).exists()

    edited = client.patch(
        f"/api/vehicles/{vehicle_id}/", {"status": "maintenance"}, format="json"
    )
    assert edited.status_code == 200, edited.content
    assert Vehicle.objects.get(pk=vehicle_id).status == "maintenance"

    assert client.delete(f"/api/vehicles/{vehicle_id}/").status_code == 204
    assert not Vehicle.objects.filter(pk=vehicle_id).exists()


def test_operations_center_reads_but_cannot_add_a_vehicle(as_user, planner, unit):
    client = as_user(planner)

    # Mission planning needs the list.
    assert client.get("/api/vehicles/?is_active=true").status_code == 200

    denied = client.post("/api/vehicles/", payload(unit, "OPS-0001"), format="json")
    assert denied.status_code == 403
    assert not Vehicle.objects.filter(registration_number="OPS-0001").exists()


def test_other_roles_cannot_add_a_vehicle(as_user, creator, safety, commander, unit):
    for index, user in enumerate((creator, safety, commander)):
        response = as_user(user).post(
            "/api/vehicles/", payload(unit, f"DENIED-{index}"), format="json"
        )
        assert response.status_code == 403, (user.authority, response.content)
    assert not Vehicle.objects.filter(registration_number__startswith="DENIED").exists()


def test_logistics_officer_does_not_gain_mission_planning(as_user, logistics):
    """Owning the register does not confer the Operations Center's planning right."""
    assert as_user(logistics).get("/api/vehicles/").status_code == 200

    from apps.core.roles import capabilities_for

    caps = capabilities_for(logistics)
    assert "manage_vehicles" in caps
    assert "manage_missions" not in caps


def test_register_shows_the_last_reported_position(as_user, logistics, unit):
    """Location is the newest report with its age - never called live tracking."""
    client = as_user(logistics)
    created = client.post("/api/vehicles/", payload(unit, "POS-0001"), format="json")
    assert created.status_code == 201, created.content
    vehicle_id = created.json()["id"]
    # A vehicle that has never reported has no location, not a blank string.
    assert created.json()["last_position"] is None

    now = timeutils.now()
    VehiclePosition.objects.create(
        vehicle_id=vehicle_id,
        latitude=7.1,
        longitude=125.6,
        mgrs="51P XM 1234 5678",
        reported_at=now - dt.timedelta(hours=30),
        source="manual",
    )
    VehiclePosition.objects.create(
        vehicle_id=vehicle_id,
        latitude=7.2,
        longitude=125.7,
        mgrs="51P XM 2222 3333",
        reported_at=now - dt.timedelta(hours=2),
        source="trip_arrival",
    )

    body = client.get("/api/vehicles/?page_size=200").json()
    rows = body.get("results", body)
    position = next(row for row in rows if row["id"] == vehicle_id)["last_position"]
    assert position["mgrs"] == "51P XM 2222 3333"
    assert position["source"] == "trip_arrival"
    assert position["is_stale"] is False
    # The age is computed at read time, so allow for the test's own runtime.
    assert 2 * 3600 <= position["age_seconds"] <= 2 * 3600 + 60

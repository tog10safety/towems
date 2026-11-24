"""Privately owned vehicles (POV) on a mission trip."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.ops.models import (
    Mission,
    MissionPersonnel,
    Vehicle,
    VehicleTripVehicle,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def mission(db, creator, unit):
    return Mission.objects.create(
        mission_number="MSN-POV-1",
        category="ground",
        title="Patrol",
        purpose="Presence",
        unit=unit,
        created_by=creator,
        start_at=timezone.now(),
        end_at=timezone.now() + dt.timedelta(hours=4),
        status="approved",
    )


@pytest.fixture
def crew(db, mission, creator, unit):
    from apps.core.factories import make_user

    owner = creator
    MissionPersonnel.objects.create(mission=mission, user=owner, role="Driver", is_active=True)
    other = make_user(unit=unit)
    MissionPersonnel.objects.create(mission=mission, user=other, role="Member", is_active=True)
    return {"owner": owner, "other": other}


def assign(as_user, admin, mission, povs, drivers=None):
    return as_user(admin).post(
        f"/api/missions/{mission.id}/vehicles/",
        {"vehicles": [], "drivers": drivers or {}, "povs": povs},
        format="json",
    )


def test_a_pov_is_described_on_the_trip_and_stays_out_of_the_register(
    as_user, admin, mission, crew
):
    response = assign(
        as_user,
        admin,
        mission,
        [
            {
                "client_ref": "pov:1",
                "vehicle_type": "Motorcycle",
                "make_model": "Honda XR150",
                "plate_number": "PN-12315",
                "fuel_type": "Gasoline",
                "owner": crew["owner"].id,
            }
        ],
    )
    assert response.status_code == 200, response.content
    row = response.json()["pov_vehicles"][0]
    assert row["client_ref"] == "pov:1"
    # The plate the planner typed identifies the car, not an internal POV number.
    assert row["registration_number"] == "PN-12315"
    assert row["plate_number"] == "PN-12315"
    assert row["trip_id"]

    vehicle = Vehicle.objects.get(pk=row["vehicle_id"])
    assert vehicle.is_pov is True
    assert vehicle.owner_id == crew["owner"].id
    assert vehicle.fuel_type == "Gasoline"
    assert vehicle.vehicle_type == "Motorcycle"
    assert vehicle.make_model == "Honda XR150"
    assert vehicle.registration_no == "PN-12315"
    assert vehicle.unit_id == mission.unit_id

    # The owner drives their own vehicle unless somebody else is named.
    link = VehicleTripVehicle.objects.get(trip_id=row["trip_id"], vehicle=vehicle)
    assert link.driver_id == crew["owner"].id

    # The register is the unit's fleet; the POV is read through its trip.
    listed = as_user(admin).get("/api/vehicles/?page_size=200").json()
    assert row["vehicle_id"] not in [item["id"] for item in listed["results"]]

    # ...but the mission's own record shows it as a POV with its owner.
    detail = as_user(admin).get(f"/api/missions/{mission.id}/").json()
    shown = [item for item in detail["vehicles"] if item["vehicle_id"] == vehicle.id][0]
    assert shown["is_pov"] is True
    assert shown["plate_number"] == "PN-12315"
    assert shown["owner_name"] == crew["owner"].full_name
    assert shown["fuel_type"] == "Gasoline"


def test_a_pov_without_a_plate_keeps_an_internal_reference(as_user, admin, mission, crew):
    entry = {
        "client_ref": "pov:1",
        "vehicle_type": "Motorcycle",
        "make_model": "Honda XR150",
        "fuel_type": "Gasoline",
        "owner": crew["owner"].id,
    }

    row = assign(as_user, admin, mission, [entry]).json()["pov_vehicles"][0]

    assert row["registration_number"].startswith("POV-")


def test_a_pov_does_not_take_a_plate_already_on_the_register(as_user, admin, mission, crew):
    from apps.ops.models import Vehicle as VehicleModel

    VehicleModel.objects.create(
        registration_number="PN-12315", vehicle_type="Truck", unit=mission.unit
    )
    entry = {
        "client_ref": "pov:1",
        "vehicle_type": "Motorcycle",
        "make_model": "Honda XR150",
        "plate_number": "PN-12315",
        "fuel_type": "Gasoline",
        "owner": crew["owner"].id,
    }

    row = assign(as_user, admin, mission, [entry]).json()["pov_vehicles"][0]

    assert row["registration_number"].startswith("POV-")
    assert row["plate_number"] == "PN-12315"


def test_re_saving_moves_an_older_pov_onto_its_plate(as_user, admin, mission, crew):
    entry = {
        "client_ref": "pov:1",
        "vehicle_type": "Motorcycle",
        "make_model": "Honda XR150",
        "fuel_type": "Gasoline",
        "owner": crew["owner"].id,
    }
    first = assign(as_user, admin, mission, [entry]).json()["pov_vehicles"][0]
    assert first["registration_number"].startswith("POV-")

    second = assign(
        as_user,
        admin,
        mission,
        [{**entry, "client_ref": "pov:2", "plate_number": "PN-777"}],
    )
    assert second.status_code == 200, second.content
    again = second.json()["pov_vehicles"][0]

    assert again["vehicle_id"] == first["vehicle_id"]
    assert again["registration_number"] == "PN-777"
    assert Vehicle.objects.filter(is_pov=True).count() == 1


def test_the_owner_may_be_overridden_as_driver(as_user, admin, mission, crew):
    response = assign(
        as_user,
        admin,
        mission,
        [
            {
                "client_ref": "pov:1",
                "vehicle_type": "Pickup",
                "make_model": "Toyota Hilux",
                "fuel_type": "Diesel",
                "owner": crew["owner"].id,
                "driver": crew["other"].id,
            }
        ],
    )
    assert response.status_code == 200, response.content
    row = response.json()["pov_vehicles"][0]
    link = VehicleTripVehicle.objects.get(trip_id=row["trip_id"])
    assert link.driver_id == crew["other"].id


def test_the_owner_must_be_on_the_missions_personnel(as_user, admin, mission, crew, unit):
    from apps.core.factories import make_user

    stranger = make_user(unit=unit)
    response = assign(
        as_user,
        admin,
        mission,
        [
            {
                "client_ref": "pov:1",
                "vehicle_type": "Van",
                "make_model": "Nissan Urvan",
                "fuel_type": "Diesel",
                "owner": stranger.id,
            }
        ],
    )
    assert response.status_code == 400
    body = response.json()
    assert "owner" in str(body).lower(), body


@pytest.mark.parametrize(
    "missing",
    [
        {"vehicle_type": ""},
        {"make_model": ""},
        {"owner": None},
    ],
)
def test_a_pov_needs_a_type_a_model_and_an_owner(as_user, admin, mission, crew, missing):
    entry = {
        "client_ref": "pov:1",
        "vehicle_type": "Motorcycle",
        "make_model": "Honda XR150",
        "fuel_type": "Gasoline",
        "owner": crew["owner"].id,
    }
    entry.update(missing)
    response = assign(as_user, admin, mission, [entry])
    assert response.status_code == 400


def test_a_driver_cannot_operate_a_pov_and_another_vehicle(as_user, admin, mission, crew):
    from apps.ops.models import Vehicle as VehicleModel

    register = VehicleModel.objects.create(
        registration_number="REG-1", vehicle_type="Truck", unit=mission.unit
    )
    response = as_user(admin).post(
        f"/api/missions/{mission.id}/vehicles/",
        {
            "vehicles": [register.id],
            "drivers": {str(register.id): crew["owner"].id},
            "povs": [
                {
                    "client_ref": "pov:1",
                    "vehicle_type": "Motorcycle",
                    "make_model": "Honda XR150",
                    "fuel_type": "Gasoline",
                    "owner": crew["owner"].id,
                    "driver": crew["owner"].id,
                }
            ],
        },
        format="json",
    )
    assert response.status_code == 400
    assert "driver" in str(response.json()).lower()


def test_re_saving_the_plan_reuses_the_same_pov(as_user, admin, mission, crew):
    entry = {
        "client_ref": "pov:1",
        "vehicle_type": "Motorcycle",
        "make_model": "Honda XR150",
        "fuel_type": "Gasoline",
        "owner": crew["owner"].id,
    }
    first = assign(as_user, admin, mission, [entry]).json()["pov_vehicles"][0]
    second = assign(as_user, admin, mission, [{**entry, "client_ref": "pov:2"}])
    assert second.status_code == 200, second.content
    again = second.json()["pov_vehicles"][0]

    assert again["vehicle_id"] == first["vehicle_id"]
    assert Vehicle.objects.filter(is_pov=True).count() == 1

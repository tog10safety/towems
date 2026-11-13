"""Vehicle register: the accountable details, and how a vehicle reads."""

from __future__ import annotations

import pytest

from apps.ops.models import Vehicle

pytestmark = pytest.mark.django_db


def payload(unit, plate="12389"):
    return {
        "registration_number": plate,
        "wheels": 4,
        "make": "KIA",
        "model": "KM450",
        "registration_no": f"CR-2026-{plate}",
        "official_receipt_no": f"OR-2026-{plate}",
        "unit": unit.id,
        "capacity": 12,
    }


def test_a_vehicle_records_its_register_details(as_user, admin, unit):
    response = as_user(admin).post("/api/vehicles/", payload(unit), format="json")
    assert response.status_code == 201, response.content
    body = response.json()

    assert body["display_name"] == "KIA KM450 12389"
    assert body["wheels"] == 4
    assert body["make"] == "KIA"
    assert body["model"] == "KM450"
    assert body["registration_no"] == "CR-2026-12389"
    assert body["official_receipt_no"] == "OR-2026-12389"
    assert body["capacity"] == 12

    # The older combined columns follow make/model, so documents and labels that
    # still read them keep saying the same thing.
    row = Vehicle.objects.get(registration_number="12389")
    assert row.vehicle_type == "KM450"
    assert row.make_model == "KIA"
    assert row.display_name == "KIA KM450 12389"


def test_make_and_model_are_separate(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/vehicles/",
        {**payload(unit, "99887"), "make": "Toyota", "model": "Hilux"},
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["make"] == "Toyota"
    assert body["model"] == "Hilux"
    assert body["display_name"] == "Toyota Hilux 99887"


def test_search_finds_a_vehicle_by_make_model_or_plate(as_user, admin, unit):
    as_user(admin).post("/api/vehicles/", payload(unit, "12389"), format="json")
    as_user(admin).post(
        "/api/vehicles/",
        {**payload(unit, "55443"), "make": "Honda", "model": "XR150"},
        format="json",
    )

    for query in ("KIA", "KM450", "12389"):
        found = as_user(admin).get(f"/api/vehicles/?search={query}").json()
        assert [row["registration_number"] for row in found["results"]] == ["12389"], query

    by_cr = as_user(admin).get("/api/vehicles/?search=CR-2026-12389").json()
    assert by_cr["count"] == 1


def test_an_old_row_without_make_and_model_still_reads_sensibly(as_user, admin, unit):
    """Rows written before the split keep their old columns; display falls back."""
    row = Vehicle.objects.create(
        registration_number="OLD-1", vehicle_type="KM450", make_model="KIA", unit=unit
    )
    assert row.display_name == "KIA KM450 OLD-1"

    body = as_user(admin).get(f"/api/vehicles/?search=OLD-1").json()
    assert body["results"][0]["display_name"] == "KIA KM450 OLD-1"

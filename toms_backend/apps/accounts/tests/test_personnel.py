"""Personnel management: creation rules, password policy, deactivation."""

from __future__ import annotations

import pytest

from apps.accounts.models import User
from apps.core.factories import unique_serial

pytestmark = pytest.mark.django_db


def payload(unit, **overrides):
    body = {
        "username": "new.person",
        "password": "Passw0rd!23",
        "authority": "Creator",
        "category": "Officer",
        "rank": "Lt",
        "designation": "Plans Officer",
        "first_name": "New",
        "last_name": "Person",
        "serial_number": unique_serial(),
        "branch_of_service": "PAF",
        "unit_id": unit.id,
        "contact_number": "0917",
        "is_active": True,
    }
    body.update(overrides)
    return body


def test_admin_can_create_personnel(as_user, admin, unit):
    response = as_user(admin).post("/api/personnel/", payload(unit), format="json")
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["username"] == "new.person"
    assert body["full_name"].endswith("Person")

    created = User.objects.get(username="new.person")
    assert created.check_password("Passw0rd!23")


def test_password_is_required_on_create(as_user, admin, unit):
    body = payload(unit)
    body.pop("password")
    response = as_user(admin).post("/api/personnel/", body, format="json")
    assert response.status_code == 400


def test_weak_password_is_rejected(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/personnel/", payload(unit, password="weakpass"), format="json"
    )
    assert response.status_code == 400
    assert "password" in response.json()["errors"]


def test_username_defaults_to_serial_and_details_are_saved(as_user, admin, unit):
    body = payload(unit)
    body.pop("username")
    body.update(
        {
            "email": "new.person@example.mil.ph",
            "sex": "Female",
            "date_of_birth": "1992-03-04",
            "blood_type": "AB+",
            "home_address": "Cagayan de Oro City",
            "emergency_contact_name": "Maria Person",
            "emergency_contact_number": "09170001111",
            "date_enlisted": "2014-01-06",
            "date_commissioned": "2016-01-06",
            "afsc_primary": "21A3",
            "afsc_secondary": "21B3",
            "afsc_tertiary": "21C3",
            "highest_education": "Bachelor's Degree",
            "civil_status": "Married",
        }
    )
    response = as_user(admin).post("/api/personnel/", body, format="json")
    assert response.status_code == 201, response.content
    serial = body["serial_number"]
    created = User.objects.get(serial_number=serial)
    assert created.username == serial
    assert created.force_password_change is True
    assert created.profile.email == "new.person@example.mil.ph"
    assert created.profile.blood_type == "AB+"
    assert created.profile.afsc_secondary == "21B3"
    assert created.profile.onboarding_required is True

    detail = response.json()
    assert detail["email"] == "new.person@example.mil.ph"
    assert detail["afsc_primary"] == "21A3"
    assert detail["onboarding_required"] is True


def test_duplicate_username_is_rejected(as_user, admin, unit, creator):
    response = as_user(admin).post(
        "/api/personnel/", payload(unit, username=creator.username), format="json"
    )
    assert response.status_code == 400


def test_unknown_unit_is_rejected(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/personnel/", payload(unit, unit_id=999999), format="json"
    )
    assert response.status_code == 400


def test_creator_cannot_create_personnel(as_user, creator, unit):
    response = as_user(creator).post("/api/personnel/", payload(unit), format="json")
    assert response.status_code == 403


def test_personnel_list_includes_accessible_units(as_user, safety, creator):
    response = as_user(safety).get("/api/personnel/")
    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["results"]]
    assert creator.id in ids
    first = next(row for row in response.json()["results"] if row["id"] == creator.id)
    assert creator.unit_id in first["accessible_unit_ids"]


def test_delete_deactivates_instead_of_removing(as_user, admin, unit):
    from apps.core.factories import make_user

    target = make_user(unit=unit)
    response = as_user(admin).delete(f"/api/personnel/{target.id}/")
    assert response.status_code == 204
    target.refresh_from_db()
    assert target.is_active is False
    assert User.objects.filter(pk=target.id).exists()


def test_personnel_list_is_unit_scoped(as_user, safety, other_unit):
    from apps.core.factories import make_user

    outsider = make_user(unit=other_unit)
    response = as_user(safety).get("/api/personnel/")
    assert response.status_code == 200, response.content
    ids = [row["id"] for row in response.json()["results"]]
    assert outsider.id not in ids


def test_assignable_endpoint(as_user, creator, unit):
    response = as_user(creator).get("/api/personnel/assignable/")
    assert response.status_code == 200
    assert response.json()["count"] >= 1
    first = response.json()["results"][0]
    assert "Team Leader" in first["role_suggestions"]

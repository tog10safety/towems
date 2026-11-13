"""Unit appointments, cardinality rules and acting authority (spec §7, §12B/D)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.command.models import ActingAppointment, UnitAppointment
from apps.core.factories import make_user
from apps.core.roles import capabilities_for, effective_roles, has_capability

pytestmark = pytest.mark.django_db


def appoint(unit, user, role, actor=None):
    return UnitAppointment.objects.create(unit=unit, user=user, role=role, appointed_by=actor)


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------


def test_admin_can_appoint_a_commander(as_user, admin, unit):
    officer = make_user(unit=unit, authority="Creator")
    response = as_user(admin).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": officer.id, "role": "commander"},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["role"] == "commander"
    assert response.json()["username"] == officer.username


def test_only_one_active_commander_per_unit(as_user, admin, unit):
    first = make_user(unit=unit, authority="Creator")
    second = make_user(unit=unit, authority="Creator")
    appoint(unit, first, "commander", admin)

    response = as_user(admin).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": second.id, "role": "commander"},
        format="json",
    )
    assert response.status_code == 400
    assert "one active holder" in response.json()["detail"]
    assert UnitAppointment.objects.filter(unit=unit, role="commander", is_active=True).count() == 1


def test_commander_deputy_and_director_are_distinct_posts(as_user, admin, unit):
    for role in ("commander", "deputy_commander", "director_operations"):
        person = make_user(unit=unit, authority="Creator")
        response = as_user(admin).post(
            "/api/appointments/",
            {"unit_id": unit.id, "user_id": person.id, "role": role},
            format="json",
        )
        assert response.status_code == 201, response.content

    assert UnitAppointment.objects.filter(unit=unit, is_active=True).count() == 3


def test_sharing_a_post_is_allowed_for_non_exclusive_roles(as_user, admin, unit):
    for _ in range(2):
        person = make_user(unit=unit, authority="Creator")
        response = as_user(admin).post(
            "/api/appointments/",
            {"unit_id": unit.id, "user_id": person.id, "role": "safety_officer"},
            format="json",
        )
        assert response.status_code == 201, response.content


def test_ending_an_appointment_frees_the_post(as_user, admin, unit):
    first = make_user(unit=unit, authority="Creator")
    second = make_user(unit=unit, authority="Creator")
    appointment = appoint(unit, first, "commander", admin)

    assert as_user(admin).delete(f"/api/appointments/{appointment.id}/").status_code == 200
    appointment.refresh_from_db()  # the endpoint returns a fresh instance

    assert appointment.is_active is False
    assert appointment.ended_at is not None

    response = as_user(admin).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": second.id, "role": "commander"},
        format="json",
    )
    assert response.status_code == 201


def test_non_administrator_cannot_create_appointments(as_user, creator, unit):
    person = make_user(unit=unit, authority="Creator")
    response = as_user(creator).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": person.id, "role": "commander"},
        format="json",
    )
    assert response.status_code == 403


def test_appointments_are_unit_scoped(as_user, admin, unit, other_unit):
    from apps.core.factories import make_unit

    outsider = make_user(unit=other_unit, authority="Creator")
    appoint(unit, outsider, "commander", admin)

    body = as_user(admin).get("/api/appointments/").json()
    assert body["count"] >= 1

    # A creator in another unit never sees it.
    stranger = make_user(unit=make_unit(name="Charlie", acronym="CHAR"), authority="Creator")
    assert as_user(stranger).get("/api/appointments/").json()["count"] == 0


# ---------------------------------------------------------------------------
# Role capabilities
# ---------------------------------------------------------------------------


def test_commander_cannot_create_missions_but_can_comment(unit):
    person = make_user(unit=unit, authority="Creator")
    appoint(unit, person, "commander")

    caps = capabilities_for(person, unit.id)
    assert "approve_missions" in caps
    assert "comment" in caps
    assert "manage_missions" not in caps
    assert "manage_inventory" not in caps
    assert "system_config" not in caps


def test_appointments_only_add_capabilities_and_never_remove_base_permissions(commander, unit):
    """An appointment grants command functions; the base account still governs
    ordinary record CRUD. This is the documented boundary (see the role matrix)."""
    assert has_capability(commander, "approve_missions", unit.id)
    assert has_capability(commander, "route", unit.id)
    assert not has_capability(commander, "manage_appointments", unit.id)
    assert not has_capability(commander, "system_config", unit.id)


def test_admin_officer_manages_sections_but_not_inventory(unit):
    person = make_user(unit=unit, authority="Creator")
    appoint(unit, person, "admin_officer")

    assert has_capability(person, "manage_sections", unit.id)
    assert has_capability(person, "manage_personnel", unit.id)
    assert not has_capability(person, "manage_inventory", unit.id)
    assert not has_capability(person, "approve_missions", unit.id)


def test_logistics_officer_can_approve_inventory_but_not_missions(unit):
    person = make_user(unit=unit, authority="Creator")
    appoint(unit, person, "logistics_officer")

    assert has_capability(person, "approve_inventory", unit.id)
    assert has_capability(person, "view_sensitive_inventory", unit.id)
    assert not has_capability(person, "approve_missions", unit.id)


def test_appointment_does_not_reach_unrelated_units(unit, other_unit):
    person = make_user(unit=unit, authority="Creator")
    appoint(unit, person, "commander")

    assert has_capability(person, "approve_missions", unit.id)
    assert not has_capability(person, "approve_missions", other_unit.id)


def test_base_role_is_preserved_without_any_appointment(unit):
    person = make_user(unit=unit, authority="Unit Safety Officer")
    assert effective_roles(person, unit.id) == {"safety_officer"}
    assert has_capability(person, "approve_missions", unit.id)


# ---------------------------------------------------------------------------
# Acting authority
# ---------------------------------------------------------------------------


def test_acting_commander_grants_command_capabilities_temporarily(as_user, admin, unit):
    deputy = make_user(unit=unit, authority="Creator")
    now = timezone.now()

    response = as_user(admin).post(
        "/api/acting-appointments/",
        {
            "unit_id": unit.id,
            "user_id": deputy.id,
            "acting_role": "commander",
            "reason": "Commander on leave",
            "starts_at": now.isoformat(),
            "ends_at": (now + dt.timedelta(days=7)).isoformat(),
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert has_capability(deputy, "approve_missions", unit.id)


def test_acting_authority_never_grants_system_config(as_user, admin, unit):
    deputy = make_user(unit=unit, authority="Creator")
    now = timezone.now()
    as_user(admin).post(
        "/api/acting-appointments/",
        {
            "unit_id": unit.id,
            "user_id": deputy.id,
            "acting_role": "commander",
            "reason": "Temporary",
            "starts_at": now.isoformat(),
            "ends_at": (now + dt.timedelta(days=1)).isoformat(),
            "delegated_capabilities": ["system_config", "approve_missions"],
        },
        format="json",
    )
    assert not has_capability(deputy, "system_config", unit.id)


def test_expired_acting_authority_stops_applying(unit):
    deputy = make_user(unit=unit, authority="Creator")
    now = timezone.now()
    ActingAppointment.objects.create(
        unit=unit,
        user=deputy,
        acting_role="commander",
        appointed_by=deputy,
        reason="Past window",
        starts_at=now - dt.timedelta(days=10),
        ends_at=now - dt.timedelta(days=2),
    )
    assert not has_capability(deputy, "approve_missions", unit.id)
    assert "commander" not in effective_roles(deputy, unit.id)


def test_overlapping_acting_appointments_are_refused(as_user, admin, unit):
    first = make_user(unit=unit, authority="Creator")
    second = make_user(unit=unit, authority="Creator")
    now = timezone.now()

    payload = {
        "unit_id": unit.id,
        "user_id": first.id,
        "acting_role": "commander",
        "reason": "Leave",
        "starts_at": now.isoformat(),
        "ends_at": (now + dt.timedelta(days=5)).isoformat(),
    }
    assert as_user(admin).post("/api/acting-appointments/", payload, format="json").status_code == 201

    payload["user_id"] = second.id
    payload["starts_at"] = (now + dt.timedelta(days=1)).isoformat()
    response = as_user(admin).post("/api/acting-appointments/", payload, format="json")
    assert response.status_code == 400
    assert "Conflicting command authority" in response.json()["detail"]


def test_revocation_removes_the_delegation_immediately(as_user, admin, unit):
    deputy = make_user(unit=unit, authority="Creator")
    now = timezone.now()
    created = as_user(admin).post(
        "/api/acting-appointments/",
        {
            "unit_id": unit.id,
            "user_id": deputy.id,
            "acting_role": "deputy_commander",
            "reason": "Cover",
            "starts_at": now.isoformat(),
            "ends_at": (now + dt.timedelta(days=3)).isoformat(),
        },
        format="json",
    ).json()

    assert has_capability(deputy, "comment", unit.id)
    response = as_user(admin).post(
        f"/api/acting-appointments/{created['id']}/revoke/",
        {"reason": "Returned early"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None
    assert not has_capability(deputy, "approve_missions", unit.id)


def test_acting_appointment_requires_a_reason(as_user, admin, unit):
    deputy = make_user(unit=unit, authority="Creator")
    now = timezone.now()
    response = as_user(admin).post(
        "/api/acting-appointments/",
        {
            "unit_id": unit.id,
            "user_id": deputy.id,
            "acting_role": "commander",
            "reason": "   ",
            "starts_at": now.isoformat(),
            "ends_at": (now + dt.timedelta(days=1)).isoformat(),
        },
        format="json",
    )
    assert response.status_code == 400

"""Unit management: add, edit, deactivate and reactivate a unit.

The legacy ``units`` table is edited in place - its columns are neither added to
nor renamed - and a unit is never deleted, because missions, MRM forms,
personnel and audit history reference it by id. Every write is Administrator-only
and audited.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import User
from apps.core.factories import make_unit, make_user
from apps.legacy.models import Unit
from apps.ops.models import AuditLog, Mission

pytestmark = pytest.mark.django_db


def create(client, **payload):
    body = {
        "name": "Charlie Group",
        "acronym": "CHARLIE",
        "category": "Group",
        "location": "Camp Servillano Aquino",
        "commanding_officer": "Lt Col Dela Cruz",
    }
    body.update(payload)
    return client.post("/api/units/", body, format="json")


def test_an_administrator_adds_a_unit(as_user, admin):
    response = create(as_user(admin))
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["acronym"] == "CHARLIE"
    assert body["is_active"] is True

    unit = Unit.objects.get(pk=body["id"])
    assert unit.date_created is not None
    assert AuditLog.objects.filter(
        entity_type="unit", entity_id=unit.pk, action="create"
    ).exists()


def test_only_an_administrator_can_add_a_unit(as_user, creator, safety):
    for user in (creator, safety):
        assert create(as_user(user)).status_code == 403


def test_an_administrator_edits_a_unit(as_user, admin, unit):
    response = as_user(admin).patch(
        f"/api/units/{unit.id}/",
        {"acronym": "alpha-1", "location": "Basa Air Base"},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["acronym"] == "ALPHA-1"  # normalised by the serializer
    assert body["location"] == "Basa Air Base"
    assert AuditLog.objects.filter(
        entity_type="unit", entity_id=unit.id, action="update"
    ).exists()


def test_edits_are_refused_for_other_roles(as_user, commander, unit):
    response = as_user(commander).patch(
        f"/api/units/{unit.id}/", {"location": "Elsewhere"}, format="json"
    )
    assert response.status_code == 403


def test_a_duplicate_unit_name_is_refused(as_user, admin, unit):
    response = create(as_user(admin), name=unit.name.lower())
    assert response.status_code == 400, response.content
    assert "name" in response.json()["errors"]


def test_a_unit_cannot_become_its_own_parent(as_user, admin, unit):
    response = as_user(admin).patch(
        f"/api/units/{unit.id}/", {"parent": unit.id}, format="json"
    )
    assert response.status_code == 400, response.content
    assert "parent" in response.json()["errors"]


def test_a_hierarchy_loop_is_refused(as_user, admin, unit):
    child = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    response = as_user(admin).patch(
        f"/api/units/{unit.id}/", {"parent": child.id}, format="json"
    )
    assert response.status_code == 400, response.content
    assert "parent" in response.json()["errors"]


def test_the_it_department_is_hidden_from_unit_selectors(as_user, seat_admin):
    """The system-administration unit is offered to the seat holder alone."""
    it_unit = make_unit(name="IT Department", acronym="ITD")
    ordinary = make_unit(name="Grounded Wing", acronym="GW")
    other_admin = make_user(authority="Administrator")

    seen = as_user(other_admin).get("/api/units/?page_size=200")
    ids = [row["id"] for row in seen.json()["results"]]
    assert it_unit.id not in ids
    assert ordinary.id in ids

    seat = as_user(seat_admin).get("/api/units/?page_size=200")
    seat_ids = [row["id"] for row in seat.json()["results"]]
    assert it_unit.id in seat_ids

    # The hierarchy selector follows the same rule.
    tree = as_user(other_admin).get("/api/units/tree/")
    assert "IT Department" not in tree.content.decode()


def test_a_unit_is_never_deleted(as_user, admin, unit):
    response = as_user(admin).delete(f"/api/units/{unit.id}/")
    assert response.status_code == 405, response.content
    assert Unit.objects.filter(pk=unit.id).exists()


def test_deactivation_requires_a_reason(as_user, admin):
    bare = make_unit(name="Delta Company", acronym="DELTA")
    response = as_user(admin).post(f"/api/units/{bare.id}/deactivate/", {}, format="json")
    assert response.status_code == 400, response.content
    assert "reason" in response.json()["errors"]


def test_deactivation_is_refused_while_subordinate_units_are_active(as_user, admin, unit):
    make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    response = as_user(admin).post(
        f"/api/units/{unit.id}/deactivate/", {"reason": "Drawdown"}, format="json"
    )
    assert response.status_code == 400, response.content
    assert "subordinate" in str(response.json()).lower()


def test_deactivation_is_refused_while_missions_are_open(as_user, admin, unit):
    Mission.objects.create(
        mission_number="MSN-UNIT-1",
        title="Patrol",
        category="ground",
        status="planned",
        unit=unit,
        created_by=admin,
    )
    response = as_user(admin).post(
        f"/api/units/{unit.id}/deactivate/", {"reason": "Drawdown"}, format="json"
    )
    assert response.status_code == 400, response.content
    assert "mission" in str(response.json()).lower()


def test_a_unit_deactivates_and_reactivates_with_its_records_intact(as_user, admin):
    idle = make_unit(name="Echo Flight", acronym="ECHO")
    owner = make_user(unit=idle, authority="Creator")
    mission = Mission.objects.create(
        mission_number="MSN-UNIT-2",
        title="Completed patrol",
        category="ground",
        status="completed",
        unit=idle,
        created_by=owner,
    )

    client = as_user(admin)
    off = client.post(
        f"/api/units/{idle.id}/deactivate/", {"reason": "Merged with Foxtrot"}, format="json"
    )
    assert off.status_code == 200, off.content
    assert off.json()["is_active"] is False

    idle.refresh_from_db()
    assert idle.is_active is False
    # Nothing about the unit's people or history is removed; the account keeps
    # its unit and stays usable.
    owner_row = User.objects.get(pk=owner.pk)
    assert owner_row.is_active is True
    assert owner_row.unit_id == idle.id
    mission.refresh_from_db()
    assert mission.status == "completed"
    assert AuditLog.objects.filter(
        entity_type="unit", entity_id=idle.pk, action="deactivate"
    ).exists()

    back = client.post(f"/api/units/{idle.id}/activate/", {}, format="json")
    assert back.status_code == 200, back.content
    assert back.json()["is_active"] is True
    assert AuditLog.objects.filter(
        entity_type="unit", entity_id=idle.pk, action="activate"
    ).exists()


def test_only_an_administrator_can_deactivate(as_user, safety, unit):
    response = as_user(safety).post(
        f"/api/units/{unit.id}/deactivate/", {"reason": "Test"}, format="json"
    )
    assert response.status_code == 403


def test_the_register_stays_readable_for_selectors(as_user, creator, unit):
    response = as_user(creator).get(f"/api/units/{unit.id}/")
    assert response.status_code == 200, response.content
    assert response.json()["acronym"] == "ALPHA"

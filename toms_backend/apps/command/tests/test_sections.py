"""Office/Section management and personnel assignments (spec §5)."""

from __future__ import annotations

import pytest

from apps.command.models import OfficeSection, SectionAssignment, UnitAppointment
from apps.core.factories import make_unit, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_officer(db, unit):
    person = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=person, role="admin_officer")
    return person


@pytest.fixture
def section(db, unit):
    return OfficeSection.objects.create(unit=unit, name="Operations", code="OPS", kind="section")


def test_admin_officer_creates_an_office_under_their_unit(as_user, admin_officer, unit):
    response = as_user(admin_officer).post(
        "/api/offices/",
        {"unit": unit.id, "name": "Logistics", "code": "LOG", "kind": "office"},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["unit_name"] == unit.name


def test_plain_creator_cannot_create_an_office(as_user, creator, unit):
    response = as_user(creator).post(
        "/api/offices/",
        {"unit": unit.id, "name": "Logistics", "code": "LOG", "kind": "office"},
        format="json",
    )
    assert response.status_code == 403


def test_office_in_an_unrelated_unit_is_refused(as_user, admin_officer, other_unit):
    response = as_user(admin_officer).post(
        "/api/offices/",
        {"unit": other_unit.id, "name": "Foreign", "code": "FRN", "kind": "office"},
        format="json",
    )
    assert response.status_code == 403


def test_duplicate_code_in_the_same_unit_is_refused(as_user, admin_officer, unit, section):
    response = as_user(admin_officer).post(
        "/api/offices/",
        {"unit": unit.id, "name": "Another", "code": "OPS", "kind": "office"},
        format="json",
    )
    assert response.status_code == 400
    assert "unique" in response.json()["detail"]


def test_primary_assignment_and_replacement_preserve_history(
    as_user, admin_officer, unit, section
):
    person = make_user(unit=unit)
    other = OfficeSection.objects.create(unit=unit, name="Plans", code="PLN")

    client = as_user(admin_officer)
    first = client.post(
        "/api/section-assignments/",
        {"user_id": person.id, "section_id": section.id, "assignment_type": "primary"},
        format="json",
    )
    assert first.status_code == 201, first.content

    second = client.post(
        "/api/section-assignments/",
        {"user_id": person.id, "section_id": other.id, "assignment_type": "primary"},
        format="json",
    )
    assert second.status_code == 201

    active = SectionAssignment.objects.filter(user=person, assignment_type="primary", is_active=True)
    assert active.count() == 1
    assert active.first().section_id == other.id

    ended = SectionAssignment.objects.get(id=first.json()["id"])
    assert ended.is_active is False
    assert ended.ended_at is not None


def test_primary_and_secondary_are_independent(as_user, admin_officer, unit, section):
    person = make_user(unit=unit)
    second = OfficeSection.objects.create(unit=unit, name="Plans", code="PLN")
    client = as_user(admin_officer)

    assert client.post(
        "/api/section-assignments/",
        {"user_id": person.id, "section_id": section.id, "assignment_type": "primary"},
        format="json",
    ).status_code == 201
    assert client.post(
        "/api/section-assignments/",
        {"user_id": person.id, "section_id": second.id, "assignment_type": "secondary"},
        format="json",
    ).status_code == 201

    assert SectionAssignment.objects.filter(user=person, is_active=True).count() == 2


def test_secondary_is_optional(as_user, admin_officer, unit, section):
    person = make_user(unit=unit)
    assert as_user(admin_officer).post(
        "/api/section-assignments/",
        {"user_id": person.id, "section_id": section.id, "assignment_type": "primary"},
        format="json",
    ).status_code == 201
    assert not SectionAssignment.objects.filter(
        user=person, assignment_type="secondary"
    ).exists()


def test_same_section_cannot_be_assigned_twice(as_user, admin_officer, unit, section):
    person = make_user(unit=unit)
    client = as_user(admin_officer)
    client.post(
        "/api/section-assignments/",
        {"user_id": person.id, "section_id": section.id, "assignment_type": "primary"},
        format="json",
    )
    response = client.post(
        "/api/section-assignments/",
        {"user_id": person.id, "section_id": section.id, "assignment_type": "secondary"},
        format="json",
    )
    assert response.status_code == 400
    assert "already assigned" in response.json()["detail"]


def test_section_from_another_unit_is_refused(as_user, admin_officer, unit):
    foreign_unit = make_unit(name="Charlie", acronym="CHAR")
    person = make_user(unit=unit)
    foreign = OfficeSection.objects.create(unit=foreign_unit, name="Foreign", code="FRN")

    response = as_user(admin_officer).post(
        "/api/section-assignments/",
        {"user_id": person.id, "section_id": foreign.id, "assignment_type": "primary"},
        format="json",
    )
    assert response.status_code == 403


def test_filters_by_unit_and_assignment_type(as_user, admin_officer, unit, section):
    person = make_user(unit=unit)
    client = as_user(admin_officer)
    client.post(
        "/api/section-assignments/",
        {"user_id": person.id, "section_id": section.id, "assignment_type": "primary"},
        format="json",
    )

    body = client.get(f"/api/section-assignments/?unit={unit.id}&assignment_type=primary").json()
    assert body["count"] == 1
    assert body["results"][0]["section_code"] == "OPS"

    assert (
        client.get("/api/section-assignments/?assignment_type=secondary").json()["count"] == 0
    )


def test_an_unused_office_may_not_be_deleted_by_the_admin_officer(
    as_user, admin_officer, section
):
    response = as_user(admin_officer).delete(f"/api/offices/{section.id}/")
    assert response.status_code == 403
    assert OfficeSection.objects.filter(id=section.id).exists()


def test_the_admin_officer_cannot_delete_an_assigned_office(
    as_user, admin_officer, creator, section
):
    """Deletion belongs to the Super Administrator; office and membership stay."""
    from apps.command.models import SectionAssignment

    as_user(admin_officer).post(
        "/api/section-assignments/",
        {"user_id": creator.id, "section_id": section.id, "assignment_type": "primary"},
        format="json",
    )

    response = as_user(admin_officer).delete(f"/api/offices/{section.id}/")

    assert response.status_code == 403, response.content
    assert OfficeSection.objects.filter(id=section.id).exists()
    assert SectionAssignment.objects.filter(section_id=section.id).exists()


def test_a_catalogue_office_cannot_be_added_twice(as_user, admin_officer, unit):
    payload = {"unit": unit.id, "catalogue_code": "A2"}

    first = as_user(admin_officer).post("/api/offices/", payload, format="json")
    assert first.status_code == 201, first.content

    second = as_user(admin_officer).post("/api/offices/", payload, format="json")
    assert second.status_code == 400, second.content
    # Keyed to the picker so the interface can show it beside the field.
    assert "catalogue_code" in second.json()["errors"]
    assert OfficeSection.objects.filter(unit=unit, code="A2").count() == 1


def test_a_deactivated_catalogue_office_points_at_reactivation(as_user, admin_officer, unit):
    OfficeSection.objects.create(
        unit=unit, name="Intelligence", code="A2", kind="office", is_active=False
    )

    response = as_user(admin_officer).post(
        "/api/offices/", {"unit": unit.id, "catalogue_code": "A2"}, format="json"
    )

    assert response.status_code == 400, response.content
    assert "reactivate" in response.json()["errors"]["catalogue_code"][0].lower()


def test_the_unit_picker_returns_only_the_callers_scope(as_user, admin_officer, unit, other_unit):
    subordinate = make_unit(name="Alpha Flight", acronym="ALPHA-F", parent=unit)

    body = as_user(admin_officer).get("/api/units/?scope=mine&page_size=200").json()
    ids = {row["id"] for row in body["results"]}

    assert unit.id in ids  # their own unit
    assert subordinate.id in ids  # its subordinate
    assert other_unit.id not in ids  # an unrelated unit


def test_the_administrator_sees_every_unit_in_the_picker(as_user, admin, unit, other_unit):
    body = as_user(admin).get("/api/units/?scope=mine&page_size=200").json()
    ids = {row["id"] for row in body["results"]}

    assert {unit.id, other_unit.id} <= ids


def test_an_admin_officer_lists_only_their_units_offices(as_user, admin_officer, unit, other_unit):
    OfficeSection.objects.create(unit=unit, name="Mine", code="MNE", kind="office")
    OfficeSection.objects.create(unit=other_unit, name="Foreign", code="FRN", kind="office")

    body = as_user(admin_officer).get("/api/offices/?page_size=200").json()

    assert body["results"]
    assert {row["unit"] for row in body["results"]} == {unit.id}


def test_section_appears_on_the_persons_profile(as_user, admin_officer, creator, unit, section):
    as_user(admin_officer).post(
        "/api/section-assignments/",
        {"user_id": creator.id, "section_id": section.id, "assignment_type": "primary"},
        format="json",
    )
    body = as_user(creator).get("/api/auth/profile/").json()
    assert body["sections"][0]["code"] == "OPS"
    assert body["sections"][0]["assignment_type"] == "primary"

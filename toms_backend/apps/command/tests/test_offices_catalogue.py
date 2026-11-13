"""Default office catalogue, its permissions, and A3 - Operations planning.

Spec: Offices & Sections enhancement §1-§4 and the Mission Planning access rule.
"""

from __future__ import annotations

import pytest

from apps.command import offices
from apps.command.models import OfficeSection, SectionAssignment
from apps.core import roles
from apps.core.factories import make_user
from apps.ops.models import AuditLog

pytestmark = pytest.mark.django_db


def assign(unit, person, code, name, kind="office", assignment_type="primary"):
    section, _ = OfficeSection.objects.get_or_create(
        unit=unit, code=code, defaults={"name": name, "kind": kind}
    )
    SectionAssignment.objects.create(
        user=person, section=section, assignment_type=assignment_type
    )
    return section


# ---------------------------------------------------------------------------
# Catalogue and seeding
# ---------------------------------------------------------------------------


def test_catalogue_has_the_twelve_standard_entries():
    payload = offices.catalogue_payload()
    assert len(payload) == 12
    assert [row["code"] for row in payload][:3] == ["A1", "A2", "A3"]
    assert payload[2]["name"] == "Operations"
    assert payload[-1]["code"] == "OTHER"
    assert payload[-1]["custom"] is True


def test_seeding_creates_the_catalogue_and_is_idempotent(unit):
    first = offices.seed_default_offices(unit)
    assert len(first["created"]) == 12
    assert OfficeSection.objects.filter(unit=unit).count() == 12

    second = offices.seed_default_offices(unit)
    assert second["created"] == []
    assert OfficeSection.objects.filter(unit=unit).count() == 12


def test_reseeding_keeps_a_renamed_office(unit):
    offices.seed_default_offices(unit)
    OfficeSection.objects.filter(unit=unit, code="A1").update(name="Personnel Branch")

    offices.seed_default_offices(unit)

    assert OfficeSection.objects.get(unit=unit, code="A1").name == "Personnel Branch"


def test_retiring_old_entries_deactivates_them_and_ends_assignments(unit):
    old = OfficeSection.objects.create(unit=unit, name="Legacy S3", code="S3")
    person = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(user=person, section=old, assignment_type="primary")

    result = offices.seed_default_offices(unit, retire_others=True)

    old.refresh_from_db()
    assert old.is_active is False
    assert result["retired"] == 1
    assert result["assignments_ended"] == 1
    assert SectionAssignment.objects.get(user=person).is_active is False
    # History is retained, never deleted.
    assert SectionAssignment.objects.filter(user=person).count() == 1


def test_retiring_never_removes_the_operations_center(unit):
    operations_center = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )

    offices.seed_default_offices(unit, retire_others=True)

    operations_center.refresh_from_db()
    assert operations_center.is_active is True


# ---------------------------------------------------------------------------
# API: catalogue selection, Specify, duplicates
# ---------------------------------------------------------------------------


def test_catalogue_endpoint_is_readable_by_a_resource_viewer(as_user, safety):
    response = as_user(safety).get("/api/offices/catalogue/")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 12
    assert body["catalogue"][3]["code"] == "A4"


def test_admin_creates_an_office_from_the_catalogue(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "A5"},
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["code"] == "A5"
    assert body["name"] == "Plans"
    assert body["is_custom"] is False


def test_specify_requires_a_name(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "OTHER"},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert "custom_name" in str(response.json())


def test_specify_creates_a_custom_office(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "OTHER", "custom_name": "Band Section"},
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["name"] == "Band Section"
    assert body["is_custom"] is True
    assert body["code"].startswith("X-")


def test_custom_codes_are_unique_per_unit(as_user, admin, unit):
    first = as_user(admin).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "OTHER", "custom_name": "Band Section"},
        format="json",
    ).json()
    second = as_user(admin).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "OTHER", "custom_name": "Dental Detachment"},
        format="json",
    ).json()
    assert first["code"] != second["code"]


def test_duplicate_office_names_are_rejected(as_user, admin, unit):
    offices.seed_default_offices(unit)
    response = as_user(admin).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "OTHER", "custom_name": "Operations"},
        format="json",
    )
    assert response.status_code == 400, response.content
    assert "name" in response.json()["errors"]


def test_an_unknown_catalogue_code_is_rejected(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "Z9"},
        format="json",
    )
    assert response.status_code == 400, response.content


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def test_the_admin_officer_may_manage_offices(as_user, unit):
    from apps.command.models import UnitAppointment

    officer = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=officer, role="admin_officer")

    response = as_user(officer).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "A8"},
        format="json",
    )
    assert response.status_code == 201, response.content


def test_a_resource_viewer_can_read_but_not_change_offices(as_user, safety, unit):
    offices.seed_default_offices(unit)
    assert as_user(safety).get("/api/offices/").status_code == 200
    before = OfficeSection.objects.filter(unit=unit).count()

    response = as_user(safety).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "OTHER", "custom_name": "Unauthorised Cell"},
        format="json",
    )
    assert response.status_code == 403
    assert OfficeSection.objects.filter(unit=unit).count() == before
    assert not OfficeSection.objects.filter(unit=unit, name="Unauthorised Cell").exists()


def test_a_commander_cannot_manage_offices(as_user, unit):
    from apps.command.models import UnitAppointment

    commander = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=commander, role="commander")

    response = as_user(commander).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "A2"},
        format="json",
    )
    assert response.status_code == 403


def test_offices_of_another_unit_are_not_manageable(as_user, admin, unit, other_unit):
    from apps.command.models import UnitAppointment

    outsider = make_user(unit=other_unit, authority="Creator")
    UnitAppointment.objects.create(unit=other_unit, user=outsider, role="admin_officer")

    response = as_user(outsider).post(
        "/api/offices/",
        {"unit": unit.id, "catalogue_code": "A2"},
        format="json",
    )
    assert response.status_code == 403, response.content


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


def test_an_unused_office_can_be_deleted_and_is_audited(
    as_user, admin, seat_admin, unit
):
    office = OfficeSection.objects.create(unit=unit, name="Ad hoc cell", code="X-99")

    response = as_user(admin).delete(f"/api/offices/{office.id}/")

    assert response.status_code == 204, response.content
    assert not OfficeSection.objects.filter(pk=office.id).exists()
    entry = AuditLog.objects.get(entity_type="office_section", action="delete")
    assert entry.before["code"] == "X-99"


def test_deleting_an_office_removes_its_assignments_and_audits_both(
    as_user, admin, seat_admin, unit
):
    office = OfficeSection.objects.create(unit=unit, name="Old cell", code="X-98")
    person = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(user=person, section=office, assignment_type="primary")

    response = as_user(admin).delete(f"/api/offices/{office.id}/")

    assert response.status_code == 204, response.content
    assert not OfficeSection.objects.filter(pk=office.id).exists()
    # The assignment belonged to that office, so it goes with it - and the
    # audit entry records exactly which assignments were removed.
    assert not SectionAssignment.objects.filter(section_id=office.id).exists()
    entry = AuditLog.objects.get(entity_type="office_section", action="delete")
    assert entry.before["code"] == "X-98"
    assert [row["user_id"] for row in entry.before["section_assignments"]] == [person.id]


def test_the_admin_officer_may_not_delete_an_office(as_user, unit):
    from apps.command.models import UnitAppointment

    officer = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=officer, role="admin_officer")
    office = OfficeSection.objects.create(unit=unit, name="Ad hoc cell", code="X-97")

    response = as_user(officer).delete(f"/api/offices/{office.id}/")

    assert response.status_code == 403, response.content
    assert OfficeSection.objects.filter(pk=office.id).exists()


def test_an_ordinary_user_cannot_delete_an_office(as_user, creator, unit):
    office = OfficeSection.objects.create(unit=unit, name="Ad hoc cell", code="X-96")

    response = as_user(creator).delete(f"/api/offices/{office.id}/")

    assert response.status_code == 403
    assert OfficeSection.objects.filter(pk=office.id).exists()


def test_the_operations_center_can_be_deleted_by_the_super_administrator(
    as_user, admin, seat_admin, unit
):
    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )

    response = as_user(admin).delete(f"/api/offices/{office.id}/")

    assert response.status_code == 204, response.content
    assert not OfficeSection.objects.filter(pk=office.id).exists()
    assert AuditLog.objects.filter(entity_type="office_section", action="delete").exists()


def test_the_administrator_can_reset_the_catalogue(as_user, admin, unit, other_unit):
    """Every office row is replaced; other tables keep their own data."""
    from apps.ops.models import Mission

    OfficeSection.objects.create(unit=unit, name="Legacy cell", code="LEGACY")
    person = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(
        user=person, section=OfficeSection.objects.get(unit=unit, code="LEGACY"),
        assignment_type="primary",
    )
    mission = Mission.objects.create(
        title="Route security patrol",
        unit_id=unit.id,
        office_section="Legacy cell",
        category="ground",
        created_by=person,
    )

    response = as_user(admin).post("/api/offices/reset-catalogue/", {}, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["removed"] == 1
    assert body["assignments_removed"] == 1
    for scope in (unit, other_unit):
        assert list(
            OfficeSection.objects.filter(unit=scope).order_by("code").values_list(
                "code", flat=True
            )
        ) == sorted(offices.DEFAULT_OFFICE_CODES)
    # Operational records are untouched, and the stored office label survives.
    mission.refresh_from_db()
    assert mission.office_section == "Legacy cell"
    assert not SectionAssignment.objects.exists()
    entry = AuditLog.objects.get(action="reset_office_catalogue")
    assert entry.before["offices"][0]["code"] == "LEGACY"


def test_a_non_administrator_cannot_reset_the_catalogue(
    as_user, admin, unit
):
    from apps.command.models import UnitAppointment

    officer = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=officer, role="admin_officer")

    response = as_user(officer).post("/api/offices/reset-catalogue/", {}, format="json")

    assert response.status_code == 403


def test_seed_defaults_endpoint_creates_the_catalogue(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/offices/seed-defaults/", {"unit": unit.id}, format="json"
    )
    assert response.status_code == 200, response.content
    assert len(response.json()["created"]) == 12
    assert OfficeSection.objects.filter(unit=unit).count() == 12

    # Re-running adds nothing and leaves the rows in place.
    again = as_user(admin).post(
        "/api/offices/seed-defaults/", {"unit": unit.id}, format="json"
    )
    assert again.json()["created"] == []


# ---------------------------------------------------------------------------
# A3 - Operations and mission planning
# ---------------------------------------------------------------------------


def planning_flags(as_user, person):
    return as_user(person).get("/api/auth/me/").json()["permissions"]


def test_a3_operations_grants_mission_planning(as_user, unit):
    person = make_user(unit=unit, authority="Creator")
    assign(unit, person, "A3", "Operations")

    flags = planning_flags(as_user, person)

    assert flags["can_plan_missions"] is True
    assert flags["can_view_missions"] is True
    # The office carries planning, not administration.
    assert flags["manage_users"] is False
    assert flags["manage_aor"] is False
    assert roles.CAP_MANAGE_SECTIONS not in {
        name for name, granted in flags["capabilities"].items() if granted
    }


def test_a3_operations_can_create_a_mission(as_user, unit):
    from apps.ops.tests.test_missions import ground_payload

    person = make_user(unit=unit, authority="Creator")
    assign(unit, person, "A3", "Operations")

    response = as_user(person).post("/api/missions/", ground_payload(unit), format="json")
    assert response.status_code == 201, response.content


def test_another_office_does_not_grant_planning(as_user, unit):
    from apps.ops.tests.test_missions import ground_payload

    person = make_user(unit=unit, authority="Creator")
    assign(unit, person, "A1", "Personnel / Administration")

    flags = planning_flags(as_user, person)
    assert flags["can_plan_missions"] is False

    response = as_user(person).post("/api/missions/", ground_payload(unit), format="json")
    assert response.status_code == 403


def test_ending_the_a3_assignment_removes_planning_immediately(as_user, unit):
    from apps.ops.tests.test_missions import ground_payload

    person = make_user(unit=unit, authority="Creator")
    section = assign(unit, person, "A3", "Operations")

    assignment = SectionAssignment.objects.get(user=person, section=section)
    assignment.is_active = False
    assignment.save(update_fields=["is_active"])

    assert planning_flags(as_user, person)["can_plan_missions"] is False
    assert (
        as_user(person).post("/api/missions/", ground_payload(unit), format="json").status_code
        == 403
    )


def test_an_a3_office_in_another_unit_does_not_plan_this_unit(as_user, unit, other_unit):
    from apps.ops.tests.test_missions import ground_payload

    outsider = make_user(unit=other_unit, authority="Creator")
    assign(other_unit, outsider, "A3", "Operations")

    response = as_user(outsider).post("/api/missions/", ground_payload(unit), format="json")
    # Either the permission check or the unit-scope validation must refuse it.
    assert response.status_code in (400, 403), response.content


def test_a3_is_an_office_grant_not_a_role(unit):
    """A3 carries no role, so the role catalogue is unchanged by this grant."""
    values = {role for role in roles.ROLE_CAPABILITIES}
    assert "A3" not in values
    assert roles.OFFICE_CODE_CAPABILITIES["A3"] == frozenset(
        {roles.CAP_VIEW_MISSIONS, roles.CAP_MANAGE_MISSIONS}
    )

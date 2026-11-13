"""Role catalogue and administrator-controlled role assignment (spec §2-§9).

The four legacy account roles (``users.authority``) and the unit appointments
in ``command_unit_appointment`` are two halves of one authority model. These
tests pin the parts the interface depends on: that every role is described by
the server, that an administrator can assign each of them, and that assignment
and revocation take effect on the next request.
"""

from __future__ import annotations

import pytest

from apps.command.models import OfficeSection, SectionAssignment, UnitAppointment
from apps.core.factories import make_user
from apps.core.roles import (
    ACCOUNT_ROLE_VALUES,
    APPOINTMENT_ROLE_VALUES,
    EXCLUSIVE_APPOINTMENT_ROLES,
    OFFICE_ROLE_VALUES,
)
from apps.ops.models import AuditLog

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def test_catalogue_lists_every_role_exactly_once(as_user, admin):
    body = as_user(admin).get("/api/roles/").json()
    values = [row["value"] for row in body["roles"]]

    assert len(values) == len(set(values))
    assert set(values) == (
        set(ACCOUNT_ROLE_VALUES) | set(APPOINTMENT_ROLE_VALUES) | set(OFFICE_ROLE_VALUES)
    )


def test_catalogue_includes_the_four_account_roles_and_the_command_posts(as_user, admin):
    values = {row["value"] for row in as_user(admin).get("/api/roles/").json()["roles"]}

    # The four roles the interface used to show are still assignable...
    assert {"administrator", "safety_officer", "evaluator", "creator"} <= values
    # ...alongside every operational post, including the Operations Center.
    assert {
        "commander",
        "deputy_commander",
        "director_operations",
        "operations_center",
        "operations_officer",
        "admin_officer",
        "logistics_officer",
        "logistics_ncoic",
    } <= values


def test_catalogue_describes_scope_and_exclusivity(as_user, admin):
    rows = {row["value"]: row for row in as_user(admin).get("/api/roles/").json()["roles"]}

    assert rows["administrator"]["kind"] == "account"
    assert rows["administrator"]["scope"] == "organization"
    assert rows["commander"]["kind"] == "appointment"
    assert rows["commander"]["scope"] == "unit_and_subordinates"

    for role in EXCLUSIVE_APPOINTMENT_ROLES:
        assert rows[role]["exclusive"] is True, role
    assert rows["operations_center"]["exclusive"] is False

    # Carries the legacy enum value so an account role can be saved by the UI.
    assert rows["administrator"]["account_value"] == "Administrator"
    assert rows["commander"]["account_value"] == ""
    # Safety Officer is both an account role and a unit post, listed once.
    assert rows["safety_officer"]["kind"] == "both"
    assert rows["safety_officer"]["account_value"] == "Unit Safety Officer"

    for row in rows.values():
        assert row["description"], row["value"]
        assert row["capability_labels"], row["value"]
        assert row["scope_label"], row["value"]

    # Authority scope and record visibility are different questions: every
    # account reads its own unit tree, but only some roles act below their unit.
    assert rows["operations_center"]["scope"] == "unit"
    assert rows["operations_center"]["read_scope"] == "unit_and_subordinates"
    assert rows["commander"]["read_scope"] == "unit_and_subordinates"
    assert rows["administrator"]["read_scope"] == "organization"
    assert rows["administrator"]["read_scope_label"], "administrator"

    # The interface builds its post picker from ``appointment`` and its account
    # picker from ``account_value``; both flags must be present on every row.
    assert rows["commander"]["appointment"] is True
    assert rows["evaluator"]["appointment"] is False
    assert rows["safety_officer"]["appointment"] is True

    # The Operations Center is an office, not a post: it is never assignable
    # through /appointments/, only through office membership.
    assert rows["operations_center"]["kind"] == "office"
    assert rows["operations_center"]["appointment"] is False
    assert rows["operations_center"]["account_value"] == ""
    assert rows["operations_center"]["label"] == "Operations Center"


def test_catalogue_keeps_aor_management_with_the_administrator(as_user, admin):
    rows = {row["value"]: row for row in as_user(admin).get("/api/roles/").json()["roles"]}

    assert "manage_aor" in rows["administrator"]["capabilities"]
    for value, row in rows.items():
        if value == "administrator":
            continue
        assert "manage_aor" not in row["capabilities"], value
        assert "manage_users" not in row["capabilities"], value


def test_only_the_operations_center_and_administrator_plan_missions(as_user, admin):
    """Mission planning is the Operations Center's authority (spec §7)."""
    rows = {row["value"]: row for row in as_user(admin).get("/api/roles/").json()["roles"]}

    planners = {value for value, row in rows.items() if "manage_missions" in row["capabilities"]}
    assert planners == {"administrator", "operations_center"}

    # Every command and staff officer post sees missions...
    for value in (
        "commander",
        "deputy_commander",
        "director_operations",
        "operations_officer",
        "admin_officer",
        "logistics_officer",
        "safety_officer",
    ):
        assert "view_missions" in rows[value]["capabilities"], value
        # ...but none of them may create or change one.
        assert "manage_missions" not in rows[value]["capabilities"], value

    # MRM roles reach a mission through assignment, not through a blanket grant.
    assert "view_missions" not in rows["creator"]["capabilities"]
    assert "view_missions" not in rows["evaluator"]["capabilities"]


def test_operations_center_routes_but_holds_no_decision_authority(as_user, admin):
    rows = {row["value"]: row for row in as_user(admin).get("/api/roles/").json()["roles"]}
    watch = rows["operations_center"]["capabilities"]
    officer = rows["operations_officer"]["capabilities"]

    assert "route" in watch
    assert "comment" in watch
    # It may not review at the Operations Officer stage, evaluate MRM, or
    # give the final mission decision.
    assert "review_missions" not in watch
    assert "approve_missions" not in watch
    assert "evaluate" not in watch
    # The Operations Officer does hold the review stage.
    assert "review_missions" in officer


def test_catalogue_is_available_to_every_authenticated_user(as_user, creator):
    assert as_user(creator).get("/api/roles/").status_code == 200


def test_catalogue_is_not_available_anonymously(api):
    assert api.get("/api/roles/").status_code in (401, 403)


# ---------------------------------------------------------------------------
# Assigning roles
# ---------------------------------------------------------------------------


def test_administrator_can_assign_every_appointment_role(as_user, admin, unit):
    for role in APPOINTMENT_ROLE_VALUES:
        person = make_user(unit=unit, authority="Creator")
        response = as_user(admin).post(
            "/api/appointments/",
            {"unit_id": unit.id, "user_id": person.id, "role": role},
            format="json",
        )
        assert response.status_code == 201, (role, response.content)

        me = as_user(person).get("/api/auth/me/").json()
        assert role in me["permissions"]["roles"], role


def test_account_role_change_takes_effect_on_the_next_request(as_user, admin, creator):
    from rest_framework.test import APIClient

    response = as_user(admin).patch(
        f"/api/admin/users/{creator.id}/", {"authority": "Evaluator"}, format="json"
    )
    assert response.status_code == 200, response.content

    # A new request re-reads the account, exactly as session authentication
    # does in production (the forced client would reuse a stale instance).
    following = APIClient()
    following.force_authenticate(user=type(creator).objects.get(pk=creator.pk))
    me = following.get("/api/auth/me/").json()
    assert me["authority"] == "Evaluator"
    assert "evaluator" in me["permissions"]["roles"]
    assert me["permissions"]["can_review_risk"] is True
    assert me["permissions"]["can_view_resources"] is False


def test_revocation_is_audited_with_the_previous_assignment(as_user, admin, unit):
    person = make_user(unit=unit, authority="Creator")
    created = as_user(admin).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": person.id, "role": "admin_officer"},
        format="json",
    ).json()
    assert as_user(admin).delete(f"/api/appointments/{created['id']}/").status_code == 200

    entry = AuditLog.objects.filter(
        action="end_appointment", entity_id=str(created["id"])
    ).first()
    assert entry is not None
    assert entry.actor_id == admin.id
    assert entry.before["role"] == "admin_officer"
    assert entry.before["unit_id"] == unit.id
    assert entry.after["is_active"] is False


def test_revoking_an_appointment_removes_its_authority(as_user, admin, unit):
    person = make_user(unit=unit, authority="Creator")
    created = as_user(admin).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": person.id, "role": "commander"},
        format="json",
    ).json()

    roles_before = as_user(person).get("/api/auth/me/").json()["permissions"]["roles"]
    assert "commander" in roles_before

    assert as_user(admin).delete(f"/api/appointments/{created['id']}/").status_code == 200

    me = as_user(person).get("/api/auth/me/").json()
    assert "commander" not in me["permissions"]["roles"]
    assert me["permissions"]["can_approve_operations"] is False


def test_an_appointment_applies_to_an_already_signed_in_session(login, api, admin, unit):
    person = make_user(unit=unit, authority="Creator")
    login(person)
    assert api.get("/api/auth/me/").json()["permissions"]["roles"] == ["creator"]

    UnitAppointment.objects.create(
        unit=unit, user=person, role="logistics_officer", appointed_by=admin
    )

    body = api.get("/api/auth/me/").json()
    assert "logistics_officer" in body["permissions"]["roles"]
    assert body["permissions"]["can_approve_inventory"] is True


def test_office_membership_grants_the_operations_center_role(login, api, admin, unit):
    """The Operations Center is an office: its members hold the authority."""
    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    person = make_user(unit=unit, authority="Creator")
    login(person)
    assert "operations_center" not in api.get("/api/auth/me/").json()["permissions"]["roles"]

    assignment = SectionAssignment.objects.create(
        user=person, section=office, assignment_type="primary"
    )

    body = api.get("/api/auth/me/").json()
    assert "operations_center" in body["permissions"]["roles"]
    assert body["permissions"]["can_route"] is True
    assert body["permissions"]["can_review_missions"] is False

    # Ending the assignment removes the authority on the next request; the
    # office itself is untouched and its other members keep it.
    assignment.is_active = False
    assignment.save(update_fields=["is_active"])
    body = api.get("/api/auth/me/").json()
    assert "operations_center" not in body["permissions"]["roles"]
    assert body["permissions"]["can_route"] is False


def test_a_plain_evaluator_does_not_open_the_resource_registers(as_user, unit):
    person = make_user(unit=unit, authority="Evaluator")
    permissions = as_user(person).get("/api/auth/me/").json()["permissions"]

    assert permissions["can_view_resources"] is False
    # Hiding the four resource menus does not take away the Evaluator's
    # existing instruction authority.
    assert permissions["can_issue_instructions"] is True


@pytest.mark.parametrize(
    "post",
    [
        "commander",
        "deputy_commander",
        "director_operations",
        "admin_officer",
        "logistics_officer",
        "safety_officer",
        "operations_officer",
    ],
)
def test_an_evaluator_with_a_command_or_staff_post_sees_the_resources(
    as_user, unit, post
):
    person = make_user(unit=unit, authority="Evaluator")
    assert as_user(person).get("/api/auth/me/").json()["permissions"]["can_view_resources"] is False

    UnitAppointment.objects.create(unit=unit, user=person, role=post)

    permissions = as_user(person).get("/api/auth/me/").json()["permissions"]
    assert post in permissions["roles"]
    assert permissions["can_view_resources"] is True


def test_an_evaluator_in_the_operations_center_sees_the_resources(as_user, unit):
    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    person = make_user(unit=unit, authority="Evaluator")
    SectionAssignment.objects.create(user=person, section=office, assignment_type="primary")

    permissions = as_user(person).get("/api/auth/me/").json()["permissions"]
    assert "operations_center" in permissions["roles"]
    assert permissions["can_view_resources"] is True


def test_an_evaluator_with_only_the_logistics_nco_post_keeps_resources_hidden(
    as_user, unit
):
    person = make_user(unit=unit, authority="Evaluator")
    UnitAppointment.objects.create(unit=unit, user=person, role="logistics_ncoic")

    permissions = as_user(person).get("/api/auth/me/").json()["permissions"]
    assert "logistics_ncoic" in permissions["roles"]
    assert permissions["can_view_resources"] is False


@pytest.mark.parametrize(
    "post",
    [
        "commander",
        "deputy_commander",
        "director_operations",
        "operations_officer",
        "safety_officer",
    ],
)
def test_command_and_operations_posts_open_the_aor_viewer(as_user, unit, post):
    person = make_user(unit=unit, authority="Evaluator")
    UnitAppointment.objects.create(unit=unit, user=person, role=post)

    permissions = as_user(person).get("/api/auth/me/").json()["permissions"]
    assert post in permissions["roles"]
    assert permissions["can_view_aor"] is True


def test_operations_center_office_opens_the_aor_viewer(as_user, unit):
    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    person = make_user(unit=unit, authority="Evaluator")
    SectionAssignment.objects.create(user=person, section=office, assignment_type="primary")

    permissions = as_user(person).get("/api/auth/me/").json()["permissions"]
    assert "operations_center" in permissions["roles"]
    assert permissions["can_view_aor"] is True


def test_administrator_keeps_the_aor_viewer(as_user, admin):
    permissions = as_user(admin).get("/api/auth/me/").json()["permissions"]
    assert permissions["can_view_aor"] is True


@pytest.mark.parametrize(
    "post", ["admin_officer", "logistics_officer", "logistics_ncoic"]
)
def test_other_staff_posts_do_not_open_the_aor_viewer(as_user, unit, post):
    person = make_user(unit=unit, authority="Evaluator")
    UnitAppointment.objects.create(unit=unit, user=person, role=post)

    permissions = as_user(person).get("/api/auth/me/").json()["permissions"]
    assert permissions["can_view_aor"] is False


def test_a_plain_evaluator_does_not_open_the_aor_viewer(as_user, unit):
    person = make_user(unit=unit, authority="Evaluator")

    permissions = as_user(person).get("/api/auth/me/").json()["permissions"]
    assert permissions["can_view_aor"] is False


def test_deactivating_the_office_removes_its_members_authority(as_user, admin, unit):
    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    person = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(user=person, section=office, assignment_type="primary")

    assert "operations_center" in as_user(person).get("/api/auth/me/").json()["permissions"]["roles"]

    office.is_active = False
    office.save(update_fields=["is_active"])

    me = as_user(person).get("/api/auth/me/").json()
    assert "operations_center" not in me["permissions"]["roles"]


def test_assignment_is_audited_with_the_administrator(as_user, admin, unit):
    person = make_user(unit=unit, authority="Creator")
    created = as_user(admin).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": person.id, "role": "logistics_officer", "notes": "Posted"},
        format="json",
    ).json()

    entry = AuditLog.objects.filter(
        action="create_appointment", entity_id=str(created["id"])
    ).first()
    assert entry is not None
    assert entry.actor_id == admin.id
    assert entry.after["role"] == "logistics_officer"
    assert entry.after["role_label"] == "Logistics Officer"
    assert entry.after["unit_id"] == unit.id
    assert entry.after["user_id"] == person.id
    assert entry.note == "Posted"


def test_only_the_office_members_unit_is_in_scope(as_user, unit, other_unit):
    """An office at one unit does not authorise another unit's records."""
    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    person = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(user=person, section=office, assignment_type="primary")

    from apps.core.roles import has_capability

    assert has_capability(person, "route", unit.id) is True
    assert has_capability(person, "route", other_unit.id) is False


def test_user_list_shows_effective_roles_and_appointments(as_user, admin, unit):
    person = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(
        unit=unit, user=person, role="deputy_commander", appointed_by=admin
    )

    body = as_user(admin).get(f"/api/admin/users/?search={person.username}").json()
    row = next(item for item in body["results"] if item["id"] == person.id)

    assert row["account_role"] == "Creator"
    assert "deputy_commander" in row["roles"]
    assert "Deputy Commander" in row["role_labels"]
    assert row["appointments"][0]["role_label"] == "Deputy Commander"
    assert row["appointments"][0]["unit_acronym"] == unit.acronym


def test_user_list_shows_office_membership(as_user, admin, unit):
    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    person = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(
        user=person, section=office, assignment_type="secondary"
    )

    body = as_user(admin).get(f"/api/admin/users/?search={person.username}").json()
    row = next(item for item in body["results"] if item["id"] == person.id)

    assert "operations_center" in row["roles"]
    assert row["sections"][0]["code"] == "OPSCTR"
    assert row["sections"][0]["assignment_type"] == "secondary"
    assert row["sections"][0]["unit_acronym"] == unit.acronym


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


def test_account_roles_are_not_unit_appointments(as_user, admin, unit):
    person = make_user(unit=unit, authority="Creator")
    for role in ("administrator", "evaluator", "creator"):
        response = as_user(admin).post(
            "/api/appointments/",
            {"unit_id": unit.id, "user_id": person.id, "role": role},
            format="json",
        )
        assert response.status_code == 400, (role, response.content)


def test_the_operations_center_is_not_appointable(as_user, admin, unit):
    """It is an office: the endpoint must say so, not just fail."""
    person = make_user(unit=unit, authority="Creator")
    response = as_user(admin).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": person.id, "role": "operations_center"},
        format="json",
    )
    assert response.status_code == 400
    assert "is an office, not a post" in response.json()["detail"]


def test_inactive_accounts_cannot_be_appointed(as_user, admin, unit):
    person = make_user(unit=unit, authority="Creator", is_active=False)
    response = as_user(admin).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": person.id, "role": "admin_officer"},
        format="json",
    )
    assert response.status_code == 400
    assert "Inactive" in response.json()["detail"]


def test_one_active_commander_and_deputy_per_unit(as_user, admin, unit):
    for role in ("commander", "deputy_commander", "director_operations"):
        first = make_user(unit=unit, authority="Creator")
        second = make_user(unit=unit, authority="Creator")
        assert (
            as_user(admin)
            .post(
                "/api/appointments/",
                {"unit_id": unit.id, "user_id": first.id, "role": role},
                format="json",
            )
            .status_code
            == 201
        )
        response = as_user(admin).post(
            "/api/appointments/",
            {"unit_id": unit.id, "user_id": second.id, "role": role},
            format="json",
        )
        assert response.status_code == 400, role
        assert "one active holder" in response.json()["detail"]


def test_non_administrators_cannot_assign_or_change_roles(as_user, creator, commander, unit):
    victim = make_user(unit=unit, authority="Creator")

    for actor in (creator, commander):
        assert (
            as_user(actor)
            .post(
                "/api/appointments/",
                {"unit_id": unit.id, "user_id": actor.id, "role": "commander"},
                format="json",
            )
            .status_code
            == 403
        )
        assert (
            as_user(actor)
            .patch(f"/api/admin/users/{victim.id}/", {"authority": "Administrator"}, format="json")
            .status_code
            == 403
        )
        assert as_user(actor).get("/api/admin/users/").status_code == 403


def test_a_commander_cannot_promote_themselves(as_user, commander, unit):
    """Holding command does not confer account administration."""
    response = as_user(commander).patch(
        f"/api/admin/users/{commander.id}/", {"authority": "Administrator"}, format="json"
    )
    assert response.status_code == 403
    commander.refresh_from_db()
    assert commander.authority == "Creator"


def test_administrator_cannot_change_their_own_role(as_user, admin):
    response = as_user(admin).patch(
        f"/api/admin/users/{admin.id}/", {"authority": "Creator"}, format="json"
    )
    assert response.status_code == 400

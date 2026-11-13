"""The Operations Center as an office of its unit (spec §2, §5F, §9A).

The routing chain is Operations Center -> Operations Officer -> Decision
Authority. The Operations Center is not a personal post: it is an office inside
a unit, and every active member of it holds the authority for that unit. These
tests pin the separation - the office that receives and routes a mission cannot
review it at the Officer stage and cannot give the final decision - and that
membership, not an appointment, is what confers the role.
"""

from __future__ import annotations

import pytest

from apps.command.models import OfficeSection, SectionAssignment
from apps.core.factories import make_user
from apps.ops.models import Notification
from apps.ops.tests.test_missions import ground_payload

pytestmark = pytest.mark.django_db


def make_office(unit, code="OPSCTR"):
    return OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code=code,
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )


def make_member(unit, office=None, authority="Creator"):
    """A person assigned to the unit's Operations Center office."""
    office = office or OfficeSection.objects.filter(
        unit=unit, function=OfficeSection.FUNCTION_OPERATIONS_CENTER, is_active=True
    ).first() or make_office(unit)
    person = make_user(unit=unit, authority=authority)
    SectionAssignment.objects.create(user=person, section=office, assignment_type="primary")
    return person


def submitted_mission(as_user, planner, unit):
    """A mission created and submitted by an Operations Center member."""
    response = as_user(planner).post("/api/missions/", ground_payload(unit), format="json")
    assert response.status_code == 201, response.content
    mission_id = response.json()["id"]
    assert (
        as_user(planner).post(f"/api/missions/{mission_id}/submit/", {}, format="json").status_code
        == 200
    )
    return mission_id


def test_operations_center_screens_and_routes(as_user, unit):
    from apps.command.models import UnitAppointment

    member = make_member(unit)
    commander = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=commander, role="commander")
    mission_id = submitted_mission(as_user, member, unit)

    response = as_user(member).post(
        f"/api/missions/{mission_id}/route/",
        {
            "stage": "operations_center",
            "action": "routed",
            "note": "Complete",
            "approving_authority_role": "commander",
            "approving_authority": commander.id,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    stages = {(row["stage"], row["action"]) for row in response.json()["routing"]}
    assert ("operations_center", "routed") in stages
    assert ("operations_officer", "routed") in stages


def test_operations_center_cannot_review_at_the_officer_stage(as_user, unit):
    member = make_member(unit)
    mission_id = submitted_mission(as_user, member, unit)

    as_user(member).post(
        f"/api/missions/{mission_id}/route/",
        {"stage": "operations_center", "action": "routed"},
        format="json",
    )
    response = as_user(member).post(
        f"/api/missions/{mission_id}/route/",
        {"stage": "operations_officer", "action": "approved"},
        format="json",
    )
    assert response.status_code == 403, response.content
    assert "mission review authority" in response.json()["detail"]


def test_operations_center_cannot_give_the_final_decision(as_user, ops_officer, unit):
    member = make_member(unit)
    mission_id = submitted_mission(as_user, member, unit)

    as_user(member).post(
        f"/api/missions/{mission_id}/route/",
        {"stage": "operations_center", "action": "routed"},
        format="json",
    )
    as_user(ops_officer).post(
        f"/api/missions/{mission_id}/route/",
        {"stage": "operations_officer", "action": "approved"},
        format="json",
    )

    response = as_user(member).post(f"/api/missions/{mission_id}/approve/", {}, format="json")
    assert response.status_code == 403


def test_ending_the_assignment_removes_the_authority(as_user, unit):
    member = make_member(unit)
    mission_id = submitted_mission(as_user, member, unit)

    assignment = SectionAssignment.objects.get(user=member, is_active=True)
    assignment.is_active = False
    assignment.save(update_fields=["is_active"])

    response = as_user(member).post(
        f"/api/missions/{mission_id}/route/",
        {"stage": "operations_center", "action": "routed"},
        format="json",
    )
    assert response.status_code == 403


def test_a_user_without_the_office_cannot_route(as_user, creator, unit):
    """A plain Creator account does not hold the Operations Center."""
    member = make_member(unit)
    mission_id = submitted_mission(as_user, member, unit)

    response = as_user(creator).post(
        f"/api/missions/{mission_id}/route/",
        {"stage": "operations_center", "action": "routed"},
        format="json",
    )
    assert response.status_code == 403


def test_an_office_member_of_another_unit_cannot_route(as_user, creator, unit, other_unit):
    """Office authority is unit-only; the other unit's mission is not even visible."""
    outsider = make_member(other_unit, office=make_office(other_unit, code="OPSCTR-B"))
    member = make_member(unit)
    mission_id = submitted_mission(as_user, member, unit)

    response = as_user(outsider).post(
        f"/api/missions/{mission_id}/route/",
        {"stage": "operations_center", "action": "routed"},
        format="json",
    )
    # 404 because the unit-scoped queryset hides the mission entirely.
    assert response.status_code in (403, 404), response.content


def test_operations_officer_cannot_screen_on_behalf_of_the_center(
    as_user, ops_officer, unit
):
    """The Officer may route, but the mission still must be submitted first."""
    member = make_member(unit)
    mission_id = submitted_mission(as_user, member, unit)
    response = as_user(ops_officer).post(
        f"/api/missions/{mission_id}/route/",
        {"stage": "operations_officer", "action": "approved"},
        format="json",
    )
    assert response.status_code == 400
    assert "Operations Center" in response.json()["detail"]


def test_submission_notifies_the_operations_center(as_user, unit):
    planner = make_member(unit)
    watcher = make_member(unit)
    mission_id = submitted_mission(as_user, planner, unit)

    note = Notification.objects.filter(
        recipient_id=watcher.id, related_id=str(mission_id), requires_action=True
    ).first()
    assert note is not None
    assert note.requires_action is True
    assert note.action_status == Notification.ACTION_OPEN
    assert "Operations Center" in note.title


def test_one_operations_center_office_per_unit(as_user, admin, unit):
    make_office(unit)
    response = as_user(admin).post(
        "/api/offices/",
        {
            "unit": unit.id,
            "name": "Second watch",
            "code": "OPSCTR2",
            "kind": "office",
            "function": "operations_center",
        },
        format="json",
    )
    assert response.status_code == 400, response.content
    assert "already has an active Operations Center" in str(response.json())


def test_office_function_matches_the_role_map():
    """The stored function value and the role map cannot drift apart."""
    from apps.core.roles import OFFICE_FUNCTION_ROLE_MAP

    assert OFFICE_FUNCTION_ROLE_MAP["operations_center"] == "operations_center"
    assert "operations_center" in dict(OfficeSection.FUNCTION_CHOICES)

"""Mission-to-MRM integration (spec §2 of the MRM enhancement).

The planning form no longer links an assessment by hand: the Operations Center
names the personnel involved in the mission, picks the MRM creator from that
list and an evaluator from the mission's own unit, and the assessment is linked
when it is started. These tests pin those rules and the notification behaviour.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.core.factories import make_user
from apps.legacy.models import MRMForm
from apps.ops.models import (
    Mission,
    MissionPersonnel,
    MissionRiskTeam,
    Notification,
    RiskAssessment,
)
from apps.ops.tests.test_missions import ground_payload

pytestmark = pytest.mark.django_db


def create_mission(client, unit, **extra):
    payload = ground_payload(unit, **extra)
    response = client.post("/api/missions/", payload, format="json")
    assert response.status_code == 201, response.content
    return response.json()


def involve(client, mission_id, people):
    """Record the personnel involved in a mission."""
    response = client.post(
        f"/api/missions/{mission_id}/assign-personnel/",
        {"personnel": [{"user_id": person.id, "role": "Team Leader"} for person in people]},
        format="json",
    )
    assert response.status_code == 200, response.content
    return response.json()


def assign_team(client, mission_id, entries):
    return client.patch(
        f"/api/missions/{mission_id}/", {"mrm_team": entries}, format="json"
    )


def mission_with_team(client, unit, lead, evaluator):
    """The planning workflow: create, record personnel, assign the MRM team."""
    data = create_mission(client, unit)
    involve(client, data["id"], [lead])
    teams = [{"user": lead.id, "role": "lead"}]
    if evaluator is not None:
        teams.append({"user": evaluator.id, "role": "reviewer"})
    response = assign_team(client, data["id"], teams)
    assert response.status_code == 200, response.content
    return response.json()


def test_assigning_the_mrm_team_notifies_the_members(as_user, planner, creator, evaluator, unit):
    data = mission_with_team(as_user(planner), unit, creator, evaluator)

    assert data["mrm"]["status"] == "pending"
    assert data["mrm"]["label"] == "Pending"
    roles = {(row["user"], row["role"]) for row in data["mrm_team"]}
    assert (creator.id, "lead") in roles
    assert (evaluator.id, "reviewer") in roles

    mission = Mission.objects.get(pk=data["id"])
    assert MissionRiskTeam.objects.filter(mission=mission, user=creator).exists()

    for person in (creator, evaluator):
        item = Notification.objects.get(
            recipient_id=person.id,
            related_type="mission_mrm",
            related_id=str(mission.id),
        )
        assert item.requires_action is True
        assert item.action_label == "Prepare the MRM assessment"
        assert item.action_url.endswith("#mrm")
        assert mission.mission_number in item.title


def test_the_mrm_creator_must_be_personnel_of_that_mission(as_user, planner, creator, unit):
    client = as_user(planner)
    data = create_mission(client, unit)

    response = assign_team(client, data["id"], [{"user": creator.id, "role": "lead"}])
    assert response.status_code == 400, response.content
    assert "personnel involved" in str(response.json()["errors"]["mrm_team"]).lower()


def test_the_evaluator_must_belong_to_the_mission_unit(
    as_user, planner, creator, evaluator_other_unit, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    involve(client, data["id"], [creator])

    response = assign_team(
        client,
        data["id"],
        [
            {"user": creator.id, "role": "lead"},
            {"user": evaluator_other_unit.id, "role": "reviewer"},
        ],
    )
    assert response.status_code == 400
    assert "cannot be its mrm evaluator" in str(response.json()["errors"]["mrm_team"]).lower()


def test_the_evaluator_must_hold_evaluation_authority(as_user, planner, creator, unit):
    client = as_user(planner)
    data = create_mission(client, unit)
    involve(client, data["id"], [creator])
    # A plain Creator/Driver account holds no evaluation authority.
    driver = make_user(unit=unit, authority="Creator")

    response = assign_team(
        client,
        data["id"],
        [
            {"user": creator.id, "role": "lead"},
            {"user": driver.id, "role": "reviewer"},
        ],
    )
    assert response.status_code == 400
    assert "evaluation authority" in str(response.json()["errors"]["mrm_team"]).lower()


def test_only_one_creator_and_one_evaluator(as_user, planner, creator, evaluator, unit):
    client = as_user(planner)
    second = make_user(unit=unit, authority="Evaluator")
    data = create_mission(client, unit)
    involve(client, data["id"], [creator, second])

    response = assign_team(
        client,
        data["id"],
        [
            {"user": creator.id, "role": "lead"},
            {"user": second.id, "role": "lead"},
        ],
    )
    assert response.status_code == 400
    assert "one mrm creator" in str(response.json()["errors"]["mrm_team"]).lower()

    response = assign_team(
        client,
        data["id"],
        [
            {"user": creator.id, "role": "lead"},
            {"user": evaluator.id, "role": "reviewer"},
            {"user": second.id, "role": "reviewer"},
        ],
    )
    assert response.status_code == 400
    assert "one mrm evaluator" in str(response.json()["errors"]["mrm_team"]).lower()


def test_inactive_personnel_cannot_join_the_mrm_team(as_user, planner, creator, evaluator, unit):
    evaluator.is_active = False
    evaluator.save(update_fields=["is_active"])
    client = as_user(planner)
    data = create_mission(client, unit)
    involve(client, data["id"], [creator])

    response = assign_team(
        client,
        data["id"],
        [
            {"user": creator.id, "role": "lead"},
            {"user": evaluator.id, "role": "reviewer"},
        ],
    )
    assert response.status_code == 400
    assert "inactive" in str(response.json()["errors"]["mrm_team"]).lower()


def test_personnel_from_an_unrelated_unit_cannot_join(
    as_user, planner, creator, evaluator_other_unit, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    involve(client, data["id"], [creator])

    response = assign_team(
        client,
        data["id"],
        [
            {"user": creator.id, "role": "lead"},
            {"user": evaluator_other_unit.id, "role": "assessor"},
        ],
    )
    assert response.status_code == 400
    assert "not authorised" in str(response.json()["errors"]["mrm_team"]).lower()


def test_duplicate_team_rows_are_rejected(as_user, planner, creator, unit):
    client = as_user(planner)
    data = create_mission(client, unit)
    involve(client, data["id"], [creator])

    response = assign_team(
        client,
        data["id"],
        [
            {"user": creator.id, "role": "lead"},
            {"user": creator.id, "role": "assessor"},
        ],
    )
    assert response.status_code == 400


def test_assignable_evaluators_are_limited_to_the_mission_unit(
    as_user, planner, evaluator, evaluator_other_unit, admin, creator, unit
):
    """The evaluator picker must not offer personnel from another unit."""
    response = as_user(planner).get(
        f"/api/personnel/assignable/?unit={unit.id}&purpose=evaluator"
    )
    assert response.status_code == 200, response.content
    ids = {row["id"] for row in response.json()["results"]}
    assert evaluator.id in ids
    assert evaluator_other_unit.id not in ids
    # Only people with evaluation authority are offered.
    assert creator.id not in ids

    # A unit outside the caller's scope is refused, not silently widened.
    other = as_user(evaluator_other_unit).get(
        f"/api/personnel/assignable/?unit={unit.id}&purpose=evaluator"
    )
    assert other.status_code == 403


def test_start_mrm_creates_a_linked_legacy_assessment(
    as_user, planner, creator, evaluator, unit
):
    client = as_user(planner)
    data = mission_with_team(client, unit, creator, evaluator)

    started = as_user(creator).post(
        f"/api/missions/{data['id']}/start-mrm/", {}, format="json"
    )
    assert started.status_code == 200, started.content
    assessment = RiskAssessment.objects.get(mission_id=data["id"])
    assert assessment.mrm_form_id is not None

    form = MRMForm.objects.get(pk=assessment.mrm_form_id)
    assert form.created_by_id == creator.id
    assert form.status == "draft"
    assert form.original_unit_id == unit.id
    assert form.mrm_number.startswith(dt.datetime.now().strftime("%Y%m%d"))
    # The Operations Center named the evaluator on the mission's MRM team, so the
    # assessment starts already evaluated by that person.
    assert form.reviewed_by_id == evaluator.id
    assert assessment.evaluator_id == evaluator.id

    mission = Mission.objects.get(pk=data["id"])
    assert mission.risk_form_id == form.id

    detail = client.get(f"/api/missions/{data['id']}/").json()
    assert detail["mrm"]["status"] == "in_progress"
    assert detail["mrm"]["mrm_form"] == form.id
    assert detail["mrm"]["mrm_number"] == form.mrm_number

    # The linked MRM points back at the mission.
    risk = client.get(f"/api/risk-register/{form.id}/").json()
    assert risk["mission"] == mission.id
    assert risk["mission_number"] == mission.mission_number
    assert risk["mission_evaluator"] == evaluator.id
    assert risk["mission_evaluator_name"] == evaluator.full_name

    duplicate = as_user(creator).post(
        f"/api/missions/{data['id']}/start-mrm/", {}, format="json"
    )
    assert duplicate.status_code == 400
    assert MRMForm.objects.filter(pk=form.id).count() == 1


def test_linking_an_existing_mrm_through_the_api_still_works(as_user, planner, unit, mrm_form):
    """The form no longer offers it, but the API contract is kept intact."""
    client = as_user(planner)
    data = create_mission(client, unit, risk_form=mrm_form.id)

    assessment = RiskAssessment.objects.get(mission_id=data["id"])
    assert assessment.mrm_form_id == mrm_form.id
    assert data["mrm"]["status"] == "in_progress"
    assert client.get(f"/api/risk-register/{mrm_form.id}/").json()["mission"] == data["id"]


def test_an_approved_mrm_cannot_be_replaced(as_user, planner, unit, mrm_form):
    client = as_user(planner)
    data = create_mission(client, unit, risk_form=mrm_form.id)
    mrm_form.authority_approved = True
    mrm_form.save(update_fields=["authority_approved"])

    other = MRMForm.objects.create(
        mrm_number="2026010199",
        activity_mission="Replacement",
        created_by=planner,
        safety_objectives="",
        original_unit_id=unit.id,
        status="draft",
        review_status="pending",
    )
    response = client.patch(
        f"/api/missions/{data['id']}/", {"risk_form": other.id}, format="json"
    )
    assert response.status_code == 400
    assert "approved" in str(response.json()["errors"]["risk_form"]).lower()


def test_start_mrm_requires_a_team(as_user, planner, unit):
    client = as_user(planner)
    data = create_mission(client, unit)
    response = client.post(f"/api/missions/{data['id']}/start-mrm/", {}, format="json")
    assert response.status_code == 400
    assert "team" in str(response.json()["errors"]["detail"]).lower()


def test_the_named_mrm_lead_can_start_without_planning_authority(
    as_user, planner, creator, evaluator, unit
):
    """The lead the Operations Center named creates the assessment."""
    data = mission_with_team(as_user(planner), unit, creator, evaluator)

    started = as_user(creator).post(f"/api/missions/{data['id']}/start-mrm/", {}, format="json")
    assert started.status_code == 200, started.content
    assert RiskAssessment.objects.get(mission_id=data["id"]).mrm_form_id is not None


def test_other_team_members_and_planners_cannot_start_the_mrm(
    as_user, planner, creator, evaluator, unit
):
    """A reviewer, another assessor and the planner all see no way in."""
    data = mission_with_team(as_user(planner), unit, creator, evaluator)
    assessor = make_user(unit=unit, authority="Creator")
    MissionRiskTeam.objects.create(
        mission_id=data["id"], user=assessor, role="assessor", is_active=True
    )

    for person in (evaluator, assessor, planner):
        response = as_user(person).post(
            f"/api/missions/{data['id']}/start-mrm/", {}, format="json"
        )
        assert response.status_code == 403, (person.username, response.content)
    assert not RiskAssessment.objects.filter(mission_id=data["id"]).exists()


def test_outsider_cannot_start_another_missions_mrm(as_user, planner, creator, evaluator, unit):
    data = mission_with_team(as_user(planner), unit, creator, evaluator)
    outsider = make_user(unit=unit, authority="Creator")

    # Missions outside the caller's visibility are reported as not found.
    response = as_user(outsider).post(f"/api/missions/{data['id']}/start-mrm/", {}, format="json")
    assert response.status_code == 404
    assert not RiskAssessment.objects.filter(mission_id=data["id"]).exists()


def test_starting_an_assessment_does_not_duplicate_the_mission_link(
    as_user, planner, creator, evaluator, unit
):
    client = as_user(planner)
    data = mission_with_team(client, unit, creator, evaluator)
    as_user(creator).post(f"/api/missions/{data['id']}/start-mrm/", {}, format="json")
    as_user(creator).post(f"/api/missions/{data['id']}/start-mrm/", {}, format="json")
    assert RiskAssessment.objects.filter(mission_id=data["id"]).count() == 1


def test_team_can_be_changed_later_and_new_members_are_notified(
    as_user, planner, creator, evaluator, unit
):
    client = as_user(planner)
    data = mission_with_team(client, unit, creator, evaluator)
    safety = make_user(unit=unit, authority="Unit Safety Officer")

    response = assign_team(
        client,
        data["id"],
        [
            {"user": creator.id, "role": "lead"},
            {"user": safety.id, "role": "reviewer"},
        ],
    )
    assert response.status_code == 200, response.content
    assert MissionRiskTeam.objects.filter(
        mission_id=data["id"], user=safety, is_active=True
    ).exists()
    assert Notification.objects.filter(
        recipient_id=safety.id, related_type="mission_mrm"
    ).exists()

    # Removing someone ends the assignment; history is retained.
    assign_team(client, data["id"], [{"user": creator.id, "role": "lead"}])
    row = MissionRiskTeam.objects.get(mission_id=data["id"], user=safety)
    assert row.is_active is False


def test_approved_assessment_freezes_the_team(as_user, planner, creator, evaluator, unit):
    client = as_user(planner)
    data = mission_with_team(client, unit, creator, evaluator)
    as_user(creator).post(f"/api/missions/{data['id']}/start-mrm/", {}, format="json")

    assessment = RiskAssessment.objects.get(mission_id=data["id"])
    form = assessment.mrm_form
    form.authority_approved = True
    form.save(update_fields=["authority_approved"])

    response = assign_team(client, data["id"], [{"user": creator.id, "role": "lead"}])
    assert response.status_code == 400, response.content
    assert "approved" in str(response.json()["errors"]["mrm_team"]).lower()


def test_an_mrm_cannot_be_linked_to_two_missions(as_user, planner, creator, evaluator, unit):
    client = as_user(planner)
    first = mission_with_team(client, unit, creator, evaluator)
    as_user(creator).post(f"/api/missions/{first['id']}/start-mrm/", {}, format="json")
    form = RiskAssessment.objects.get(mission_id=first["id"]).mrm_form

    second = create_mission(client, unit)
    response = client.post(
        f"/api/missions/{second['id']}/risk/", {"mrm_form_id": form.id}, format="json"
    )
    assert response.status_code == 400
    assert "already linked" in str(response.json()["errors"]["mrm_form_id"]).lower()


def test_submitting_the_mrm_completes_the_prepare_action(
    as_user, planner, creator, evaluator, unit
):
    client = as_user(planner)
    data = mission_with_team(client, unit, creator, evaluator)
    as_user(creator).post(f"/api/missions/{data['id']}/start-mrm/", {}, format="json")
    assessment = RiskAssessment.objects.get(mission_id=data["id"])
    form = assessment.mrm_form

    assigned = client.post(
        f"/api/missions/{data['id']}/risk/", {"evaluator_id": evaluator.id}, format="json"
    )
    assert assigned.status_code == 200, assigned.content

    submitted = as_user(creator).post(
        f"/api/risk-register/{form.id}/submit/", {}, format="json"
    )
    assert submitted.status_code == 200, submitted.content

    assessment.refresh_from_db()
    assert assessment.status == "submitted"

    action = Notification.objects.get(
        recipient_id=creator.id,
        related_type="mission_mrm",
        related_id=str(data["id"]),
    )
    assert action.action_status == Notification.ACTION_COMPLETED
    assert action.completed_at is not None

    detail = client.get(f"/api/missions/{data['id']}/").json()
    assert detail["mrm"]["status"] == "submitted"

    # Reviewing still holds the evaluator's own action item.
    reviewer_action = Notification.objects.get(
        recipient_id=evaluator.id,
        related_type="mrm_form",
        related_id=str(form.id),
        requires_action=True,
    )
    assert reviewer_action.requires_action is True
    assert reviewer_action.action_status == Notification.ACTION_OPEN


def test_one_assessment_cannot_be_shared_by_two_missions(as_user, planner, unit, mrm_form):
    client = as_user(planner)
    first = create_mission(client, unit, risk_form=mrm_form.id)

    response = client.post(
        "/api/missions/", ground_payload(unit, risk_form=mrm_form.id), format="json"
    )
    assert response.status_code == 400
    message = str(response.json()["errors"]["risk_form"])
    assert "already linked" in message.lower()
    assert Mission.objects.filter(risk_form=mrm_form).count() == 1

    # Re-saving the owning mission keeps its own assessment.
    detail = client.patch(
        f"/api/missions/{first['id']}/",
        {"title": "Route security patrol (revised)", "risk_form": mrm_form.id},
        format="json",
    )
    assert detail.status_code == 200, detail.content
    assert detail.json()["mrm"]["mrm_form"] == mrm_form.id


def test_overdue_flag_appears_after_the_scheduled_start(as_user, planner, unit):
    client = as_user(planner)
    past = (dt.datetime.now() - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    later = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    data = create_mission(
        client,
        unit,
        ground_detail={
            "estimated_start": past,
            "estimated_end": later,
            "upr_unit": unit.id,
            "osp_section": "S3",
            "operational_area": "North Ridge",
        },
    )
    assert data["mrm"]["status"] == "not_created"
    assert data["mrm"]["requires_mrm"] is True
    assert data["mrm"]["overdue"] is True


def test_a_team_cannot_be_assigned_before_the_mission_exists(
    as_user, planner, creator, unit
):
    """The creator has to be a listed person, so creation carries no team."""
    response = as_user(planner).post(
        "/api/missions/",
        ground_payload(unit, mrm_team=[{"user": creator.id, "role": "lead"}]),
        format="json",
    )
    assert response.status_code == 400
    assert "personnel first" in str(response.json()["errors"]["mrm_team"]).lower()
    assert Mission.objects.count() == 0


def test_personnel_involved_are_recorded_before_the_team_is_valid(
    as_user, planner, creator, unit
):
    """The team can only reference people the mission actually lists."""
    client = as_user(planner)
    data = create_mission(client, unit)
    assert MissionPersonnel.objects.filter(mission_id=data["id"]).count() == 0

    response = assign_team(client, data["id"], [{"user": creator.id, "role": "lead"}])
    assert response.status_code == 400, response.content

    involve(client, data["id"], [creator])
    accepted = assign_team(client, data["id"], [{"user": creator.id, "role": "lead"}])
    assert accepted.status_code == 200, accepted.content

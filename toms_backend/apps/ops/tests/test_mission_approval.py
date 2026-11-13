"""Approving authority, approval decisions and the execution gate (spec §2-§4).

These tests pin the rules the specification is most explicit about:

* the Operations Center names an eligible post *and* the person holding it;
* only that person decides, and the creator never approves their own mission;
* a mission cannot be dispatched until its MRM is approved and its approval
  covers the revision being executed;
* the monitoring payload carries the same state the gate uses.
"""

from __future__ import annotations

import pytest

from apps.core.factories import make_user
from apps.ops.models import AuditLog, Mission, Notification, RiskAssessment
from apps.ops.tests.test_missions import ground_payload

pytestmark = pytest.mark.django_db


def create_mission(client, unit, **extra):
    response = client.post("/api/missions/", ground_payload(unit, **extra), format="json")
    assert response.status_code == 201, response.content
    return response.json()


def assign_authority(client, mission_id, *, role, user_id, note=""):
    return client.post(
        f"/api/missions/{mission_id}/approving-authority/",
        {"role": role, "user_id": user_id, "note": note},
        format="json",
    )


def approve_mrm(mission_id):
    """Take the mission's MRM to its formally accepted final state."""
    assessment = RiskAssessment.objects.get(mission_id=mission_id)
    form = assessment.mrm_form
    form.authority_approved = True
    form.review_status = "approved"
    form.save(update_fields=["authority_approved", "review_status"])


def link_approved_mrm(mission_id, creator, unit):
    """Give a mission an already-approved assessment (approval paperwork tests)."""
    from apps.legacy.models import MRMForm

    form = MRMForm.objects.create(
        mrm_number=f"MRM-{mission_id:04d}",
        activity_mission="Mission",
        created_by=creator,
        safety_objectives="Safe",
        status="approved",
        review_status="approved",
        authority_approved=True,
        original_unit_id=unit.id,
        original_unit_name=unit.name,
    )
    RiskAssessment.objects.create(mission_id=mission_id, mrm_form=form, status="approved")
    Mission.objects.filter(pk=mission_id).update(risk_form=form)


def team_up(mission_id, member):
    """Give the mission an MRM team so its assessment can be started."""
    from apps.ops.models import MissionRiskTeam

    MissionRiskTeam.objects.create(
        mission_id=mission_id, user=member, role="lead", is_active=True
    )


def start_mrm(as_user, lead, mission_id):
    """Only the MRM lead the Operations Center named may create the assessment."""
    response = as_user(lead).post(
        f"/api/missions/{mission_id}/start-mrm/", {}, format="json"
    )
    assert response.status_code == 200, response.content


def approve_mission(client, mission_id, note=""):
    return client.post(
        f"/api/missions/{mission_id}/approval/",
        {"decision": "approve", "note": note},
        format="json",
    )


def approve_order(client, mission_id, note=""):
    return client.post(
        f"/api/missions/{mission_id}/mission-order/approve/",
        {"note": note},
        format="json",
    )


# ---------------------------------------------------------------------------
# Choosing the approving authority
# ---------------------------------------------------------------------------


def test_operations_center_assigns_an_eligible_commander(
    as_user, planner, commander, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)

    response = assign_authority(
        client, data["id"], role="commander", user_id=commander.id
    )
    assert response.status_code == 200, response.content

    mission = Mission.objects.get(pk=data["id"])
    assert mission.approving_authority_id == commander.id
    assert mission.approving_authority_role == "commander"
    assert mission.approval_status == Mission.APPROVAL_PENDING
    assert mission.approval_assigned_at is not None

    # The assignment is an action item for that person, not a silent edit.
    assert Notification.objects.filter(
        recipient=commander, related_type="mission", related_id=mission.id, requires_action=True
    ).exists()
    assert AuditLog.objects.filter(
        action="assign_approver", entity_type="mission", entity_id=mission.id
    ).exists()


def test_eligible_roster_lists_only_people_holding_a_post(as_user, planner, commander, unit):
    client = as_user(planner)
    data = create_mission(client, unit)

    response = client.get(f"/api/missions/{data['id']}/approving-authorities/")
    assert response.status_code == 200, response.content
    ids = [row["user_id"] for row in response.json()["results"]]
    assert commander.id in ids
    assert data["created_by"] not in ids  # planners do not hold a post here


def test_only_the_operations_center_may_choose_the_authority(
    as_user, planner, commander, creator, unit
):
    data = create_mission(as_user(planner), unit)

    response = assign_authority(
        as_user(creator), data["id"], role="commander", user_id=commander.id
    )
    assert response.status_code == 403
    assert Mission.objects.get(pk=data["id"]).approving_authority_id is None


def test_a_person_without_the_post_cannot_be_selected(as_user, planner, creator, unit):
    client = as_user(planner)
    data = create_mission(client, unit)

    response = assign_authority(
        client, data["id"], role="commander", user_id=creator.id
    )
    assert response.status_code == 400
    assert "does not hold" in str(response.json()["errors"]["detail"])


def test_inactive_authority_is_refused(as_user, planner, commander, unit):
    client = as_user(planner)
    data = create_mission(client, unit)
    commander.is_active = False
    commander.save(update_fields=["is_active"])

    response = assign_authority(
        client, data["id"], role="commander", user_id=commander.id
    )
    assert response.status_code == 400
    assert "no longer exists" in str(response.json()["errors"]["detail"]).lower() or (
        "inactive" in str(response.json()["errors"]["detail"]).lower()
    )


def test_reassignment_is_audited(as_user, planner, commander, ops_officer, unit):
    client = as_user(planner)
    data = create_mission(client, unit)
    assign_authority(client, data["id"], role="commander", user_id=commander.id)

    response = assign_authority(
        client, data["id"], role="operations_officer", user_id=ops_officer.id
    )
    assert response.status_code == 200, response.content

    mission = Mission.objects.get(pk=data["id"])
    assert mission.approving_authority_id == ops_officer.id
    assert AuditLog.objects.filter(
        action="reassign_approver", entity_id=mission.id
    ).exists()


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_only_the_assigned_approver_may_decide(
    as_user, planner, commander, ops_officer, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    assign_authority(client, data["id"], role="commander", user_id=commander.id)

    refused = approve_mission(as_user(ops_officer), data["id"])
    assert refused.status_code == 400
    assert "assigned approving authority" in str(refused.json()["errors"]["detail"])
    assert Mission.objects.get(pk=data["id"]).approval_status == Mission.APPROVAL_PENDING

    link_approved_mrm(data["id"], ops_officer, unit)
    accepted = approve_mission(as_user(commander), data["id"])
    assert accepted.status_code == 200, accepted.content
    mission = Mission.objects.get(pk=data["id"])
    assert mission.approval_status == Mission.APPROVAL_APPROVED
    assert mission.approved_by_id == commander.id
    assert mission.approved_revision == mission.revision


def test_returning_for_revision_requires_an_explanation(
    as_user, planner, commander, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    assign_authority(client, data["id"], role="commander", user_id=commander.id)

    approver = as_user(commander)
    refused = approver.post(
        f"/api/missions/{data['id']}/approval/",
        {"decision": "return", "note": ""},
        format="json",
    )
    assert refused.status_code == 400

    returned = approver.post(
        f"/api/missions/{data['id']}/approval/",
        {"decision": "return", "note": "Add the second radio operator."},
        format="json",
    )
    assert returned.status_code == 200, returned.content
    mission = Mission.objects.get(pk=data["id"])
    assert mission.approval_status == Mission.APPROVAL_RETURNED
    assert mission.approval_note == "Add the second radio operator."


def test_a_second_approval_is_refused(as_user, planner, commander, creator, unit):
    client = as_user(planner)
    data = create_mission(client, unit)
    assign_authority(client, data["id"], role="commander", user_id=commander.id)
    link_approved_mrm(data["id"], creator, unit)
    assert approve_mission(as_user(commander), data["id"]).status_code == 200

    again = approve_mission(as_user(commander), data["id"])
    assert again.status_code == 400
    assert "already been approved" in str(again.json()["errors"]["detail"])


# ---------------------------------------------------------------------------
# Execution gate
# ---------------------------------------------------------------------------


def test_mission_approval_is_refused_until_the_mrm_is_approved(
    as_user, planner, commander, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    assign_authority(client, data["id"], role="commander", user_id=commander.id)

    refused = approve_mission(as_user(commander), data["id"])
    assert refused.status_code == 400
    assert "MRM" in str(refused.json()["errors"]["detail"])
    mission = Mission.objects.get(pk=data["id"])
    assert mission.approval_status != Mission.APPROVAL_APPROVED

    response = as_user(planner).post(
        f"/api/missions/{data['id']}/dispatch/", {}, format="json"
    )
    assert response.status_code == 400
    assert "MRM" in str(response.json()["errors"]["detail"])
    assert Mission.objects.get(pk=data["id"]).status != "active"


def test_dispatch_blocked_without_approval(as_user, planner, creator, unit):
    client = as_user(planner)
    data = create_mission(client, unit)
    team_up(data["id"], creator)
    start_mrm(as_user, creator, data["id"])
    approve_mrm(data["id"])

    response = client.post(f"/api/missions/{data['id']}/dispatch/", {}, format="json")
    assert response.status_code == 400
    assert Mission.objects.get(pk=data["id"]).status != "active"


def test_dispatch_blocked_until_mission_order_is_signed(
    as_user, planner, commander, creator, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    team_up(data["id"], creator)
    start_mrm(as_user, creator, data["id"])
    approve_mrm(data["id"])
    assign_authority(client, data["id"], role="commander", user_id=commander.id)
    approved = approve_mission(as_user(commander), data["id"])
    assert approved.status_code == 200, approved.content

    mission = Mission.objects.get(pk=data["id"])
    assert mission.approval_status == Mission.APPROVAL_APPROVED
    assert mission.mission_order_status == Mission.MISSION_ORDER_AWAITING_SIGNATURE
    assert mission.mission_order_signatory_id == commander.id

    blocked = as_user(planner).post(
        f"/api/missions/{data['id']}/dispatch/", {}, format="json"
    )
    assert blocked.status_code == 400
    blockers = blocked.json()["errors"]["blockers"]
    assert any(row["code"] == "mission_order_not_approved" for row in blockers)

    signed = approve_order(as_user(commander), data["id"], "Signed for operations.")
    assert signed.status_code == 200, signed.content
    assert signed.json()["mission_order"]["status"] == "approved"

    response = as_user(planner).post(
        f"/api/missions/{data['id']}/dispatch/", {}, format="json"
    )
    assert response.status_code == 200, response.content


def test_designated_signatory_signs_after_mission_approval(
    as_user, planner, commander, ops_officer, creator, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    team_up(data["id"], creator)
    start_mrm(as_user, creator, data["id"])
    approve_mrm(data["id"])
    assign_authority(client, data["id"], role="commander", user_id=commander.id)

    designated = client.post(
        f"/api/missions/{data['id']}/mission-order/signatory/",
        {"user_id": ops_officer.id},
        format="json",
    )
    assert designated.status_code == 200, designated.content
    assert designated.json()["mission_order"]["signatory_id"] == ops_officer.id

    approved = approve_mission(as_user(commander), data["id"])
    assert approved.status_code == 200, approved.content
    assert approved.json()["mission_order"]["signatory_id"] == ops_officer.id
    assert (
        approved.json()["mission_order"]["status"]
        == Mission.MISSION_ORDER_AWAITING_SIGNATURE
    )

    refused = approve_order(as_user(commander), data["id"])
    assert refused.status_code == 400
    assert "designated Mission Order signatory" in str(
        refused.json()["errors"]["detail"]
    )

    accepted = approve_order(as_user(ops_officer), data["id"])
    assert accepted.status_code == 200, accepted.content
    mission = Mission.objects.get(pk=data["id"])
    assert mission.mission_order_status == Mission.MISSION_ORDER_APPROVED
    assert mission.mission_order_signed_by_id == ops_officer.id


def test_dispatch_allowed_when_mrm_and_approval_are_complete(
    as_user, planner, commander, creator, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    team_up(data["id"], creator)
    start_mrm(as_user, creator, data["id"])
    approve_mrm(data["id"])
    assign_authority(client, data["id"], role="commander", user_id=commander.id)
    assert approve_mission(as_user(commander), data["id"]).status_code == 200
    signed = approve_order(as_user(commander), data["id"])
    assert signed.status_code == 200, signed.content

    response = as_user(planner).post(
        f"/api/missions/{data['id']}/dispatch/", {}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "active"
    assert response.json()["execution"]["state"] == "in_progress"


def test_dispatch_opens_a_movement_run_and_notifies_the_driver(
    as_user, planner, commander, creator, unit
):
    """Dispatch is also the moment tracking begins, with no second action."""
    import datetime as dt

    from apps.ops.models import (
        Vehicle,
        VehicleRun,
        VehicleTrip,
        VehicleTripVehicle,
    )

    client = as_user(planner)
    data = create_mission(client, unit)
    team_up(data["id"], creator)
    start_mrm(as_user, creator, data["id"])
    approve_mrm(data["id"])
    assign_authority(client, data["id"], role="commander", user_id=commander.id)
    assert approve_mission(as_user(commander), data["id"]).status_code == 200
    signed = approve_order(as_user(commander), data["id"])
    assert signed.status_code == 200, signed.content

    departure = dt.datetime(2026, 11, 1, 6, 0)
    vehicle = Vehicle.objects.create(
        registration_number="KM450-DSP-1",
        vehicle_type="KM450",
        unit=unit,
        capacity=10,
    )
    trip = VehicleTrip.objects.create(
        trip_number="TRIP-DSP-1",
        title="Dispatch move",
        mission_id=data["id"],
        created_by=planner,
        status="planned",
        departure_at=departure,
        return_at=departure + dt.timedelta(hours=6),
    )
    VehicleTripVehicle.objects.create(trip=trip, vehicle=vehicle, driver=creator)

    response = as_user(planner).post(
        f"/api/missions/{data['id']}/dispatch/", {}, format="json"
    )
    assert response.status_code == 200, response.content

    run = VehicleRun.objects.get(mission_id=data["id"])
    mission = Mission.objects.get(pk=data["id"])
    assert run.driver_id == creator.id
    assert run.status == VehicleRun.STATUS_ACTIVE
    # The dispatch timestamp is held as an explicitly unconfirmed ATD.
    assert run.atd_source == VehicleRun.ATD_AUTO_DISPATCH
    assert run.atd == mission.dispatched_at
    assert run.etd == departure

    note = Notification.objects.filter(
        recipient=creator, category="movement", requires_action=True
    ).first()
    assert note is not None
    assert note.action_url == f"/missions/{data['id']}/track/driver"


def test_patching_status_to_active_cannot_bypass_the_gate(as_user, planner, unit):
    client = as_user(planner)
    data = create_mission(client, unit)

    response = client.patch(
        f"/api/missions/{data['id']}/", {"status": "active"}, format="json"
    )
    assert response.status_code == 400
    assert Mission.objects.get(pk=data["id"]).status != "active"


def test_a_material_change_after_approval_requires_reapproval(
    as_user, planner, commander, creator, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    team_up(data["id"], creator)
    start_mrm(as_user, creator, data["id"])
    approve_mrm(data["id"])
    assign_authority(client, data["id"], role="commander", user_id=commander.id)
    approve_mission(as_user(commander), data["id"])

    # Changing the plan after the fact must not ride on the old approval.
    patched = as_user(planner).patch(
        f"/api/missions/{data['id']}/", {"purpose": "Extended patrol"}, format="json"
    )
    assert patched.status_code == 200, patched.content
    assert patched.json()["execution"]["state"] == "blocked"
    assert any(
        blocker["code"] == "approval_stale"
        for blocker in patched.json()["execution"]["blockers"]
    )

    blocked = as_user(planner).post(
        f"/api/missions/{data['id']}/dispatch/", {}, format="json"
    )
    assert blocked.status_code == 400


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------


def test_monitoring_payload_reports_approval_and_readiness(
    as_user, planner, commander, unit
):
    client = as_user(planner)
    data = create_mission(client, unit)
    assign_authority(client, data["id"], role="commander", user_id=commander.id)

    row = client.get(f"/api/missions/{data['id']}/").json()
    assert row["approval"]["status"] == "pending"
    assert row["approval"]["authority_id"] == commander.id
    assert row["approval"]["authority_role_label"] == "Commander"
    assert row["execution"]["state"] == "blocked"

    listing = client.get("/api/missions/?approval_status=pending").json()
    assert data["id"] in [item["id"] for item in listing["results"]]

    ready = client.get("/api/missions/?readiness=ready").json()
    assert data["id"] not in [item["id"] for item in ready["results"]]


def test_authority_sees_the_mission_in_their_queue(as_user, planner, commander, unit):
    create_mission(as_user(planner), unit)

    mission = Mission.objects.first()
    assign_authority(as_user(planner), mission.id, role="commander", user_id=commander.id)

    queue = as_user(commander).get("/api/missions/?approving_authority=me").json()
    assert [item["id"] for item in queue["results"]] == [mission.id]


def test_creator_cannot_be_named_as_their_own_approver(as_user, planner, unit):
    client = as_user(planner)
    data = create_mission(client, unit)
    # Give the creator the Commander post: the ban is about the person, not the role.
    from apps.command.models import UnitAppointment

    creator = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=creator, role="commander")

    response = assign_authority(client, data["id"], role="commander", user_id=creator.id)
    assert response.status_code == 200  # creator did not create *this* mission

    own = create_mission(as_user(planner), unit)
    Mission.objects.filter(pk=own["id"]).update(created_by=creator)
    refusal = assign_authority(client, own["id"], role="commander", user_id=creator.id)
    assert refusal.status_code == 400
    assert "cannot approve their own mission" in str(
        refusal.json()["errors"]["detail"]
    )


@pytest.mark.django_db(transaction=True)
def test_route_decision_commits_in_autocommit(
    as_user, planner, commander, ops_officer, unit
):
    """The route decision locks the mission, so it needs its own transaction."""
    from django.db import connection

    assert connection.get_autocommit() is True

    from apps.command.models import UnitAppointment

    for role in ("director_operations", "deputy_commander"):
        UnitAppointment.objects.create(
            unit=unit, user=make_user(unit=unit, authority="Creator"), role=role
        )

    client = as_user(planner)
    data = create_mission(client, unit)
    assert (
        assign_authority(
            client, data["id"], role="commander", user_id=commander.id
        ).status_code
        == 200
    )
    started = client.post(
        f"/api/missions/{data['id']}/begin-routing/", {}, format="json"
    )
    assert started.status_code == 200, started.content
    ops_action = Notification.objects.get(
        recipient=ops_officer,
        related_type="mission",
        related_id=str(data["id"]),
        requires_action=True,
    )
    assert ops_action.action_status == Notification.ACTION_OPEN

    response = as_user(ops_officer).post(
        f"/api/missions/{data['id']}/routing-decision/",
        {"decision": "approve", "note": "Forwarded from operations."},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["approval"]["status"] == Mission.APPROVAL_PENDING

    ops_action.refresh_from_db()
    assert ops_action.action_status == Notification.ACTION_COMPLETED
    assert ops_action.completed_by_id == ops_officer.id

    director_action = Notification.objects.get(
        related_type="mission",
        related_id=str(data["id"]),
        title__icontains="Director for Operations",
        requires_action=True,
    )
    assert director_action.action_status == Notification.ACTION_OPEN

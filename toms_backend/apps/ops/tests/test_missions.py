"""Air vs Ground operations: separate validation, permissions, scoping."""

from __future__ import annotations

import pytest

from apps.core.factories import make_user
from apps.ops.models import AirAsset, Mission, MissionOperationalArea, MissionPersonnel

pytestmark = pytest.mark.django_db

GROUND_START = "2026-11-01T06:00:00"
GROUND_END = "2026-11-01T18:00:00"


def ground_payload(unit, **overrides):
    payload = {
        "category": "ground",
        "title": "Route security patrol",
        "purpose": "Presence patrol",
        "unit": unit.id,
        "office_section": "S3",
        "location_name": "North Ridge",
        "latitude": 14.5995,
        "longitude": 120.9842,
        "ground_detail": {
            "estimated_start": GROUND_START,
            "estimated_end": GROUND_END,
            "upr_unit": unit.id,
            "osp_section": "S3",
            "operational_area": "North Ridge",
        },
    }
    payload.update(overrides)
    return payload


def air_payload(unit, **overrides):
    payload = {
        "category": "air",
        "title": "Aerial reconnaissance",
        "purpose": "Imagery tasking",
        "unit": unit.id,
        "air_detail": {
            "aircraft_type": "C-208",
            "flight_identification": "PAF-101",
            "departure_location": "Villamor",
            "destination_location": "Cebu",
            "route": "VLLM-DCT-CEB",
            "scheduled_departure": GROUND_START,
            "scheduled_arrival": GROUND_END,
        },
    }
    payload.update(overrides)
    return payload


def make_air_asset(opcon_unit, tail="001", **overrides):
    # A formation rule needs a real location. Tests that care about separation
    # override this; the rest start co-located at HTOG 10.
    overrides.setdefault("current_location", "HTOG 10 (51PZK1060483777)")
    return AirAsset.objects.create(
        aircraft_type="fixed_wing",
        categories=["attack_cas"],
        model="FA-50PH",
        tail_number=tail,
        callsign="EAGLE 01",
        assigned_unit="15sw",
        opcon_unit=opcon_unit,
        operational_status="available",
        **overrides,
    )


def test_create_ground_mission(as_user, planner, unit):
    client = as_user(planner)
    response = client.post("/api/missions/", ground_payload(unit), format="json")
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["category"] == "ground"
    assert body["mission_number"].startswith("TOWEMS-GO-")
    assert body["ground_detail"]["operational_area"] == "North Ridge"
    assert body["air_detail"] is None
    assert body["mgrs"].startswith("51P")  # derived from the coordinates


def test_mission_times_are_derived_from_the_estimates(as_user, planner, unit):
    """The form collects estimated times only; the header follows them."""
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    assert created["start_at"].startswith("2026-11-01T06:00")
    assert created["end_at"].startswith("2026-11-01T18:00")


def test_additional_operational_areas_are_stored_in_order(as_user, planner, unit):
    """The primary area stays on the ground detail; extras get their own rows."""
    payload = ground_payload(
        unit,
        operational_areas=[
            {"name": "South Ridge", "latitude": 14.41, "longitude": 120.95, "mgrs": ""},
            {"name": "River Crossing", "latitude": 14.35, "longitude": 120.9},
        ],
    )
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 201, response.content
    body = response.json()

    assert body["ground_detail"]["operational_area"] == "North Ridge"
    assert [row["name"] for row in body["operational_areas"]] == [
        "South Ridge",
        "River Crossing",
    ]
    assert [row["sequence"] for row in body["operational_areas"]] == [1, 2]

    mission = Mission.objects.get(pk=body["id"])
    assert MissionOperationalArea.objects.filter(mission=mission).count() == 2

    # Editing replaces the list rather than appending duplicates.
    update = as_user(planner).patch(
        f"/api/missions/{mission.id}/",
        {"operational_areas": [{"name": "Only one"}]},
        format="json",
    )
    assert update.status_code == 200, update.content
    names = list(
        MissionOperationalArea.objects.filter(mission=mission).values_list("name", flat=True)
    )
    assert names == ["Only one"]


def test_no_additional_areas_are_created_by_default(as_user, planner, unit):
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    assert created["operational_areas"] == []


def test_additional_areas_are_refused_for_air_missions(as_user, planner, unit):
    payload = air_payload(unit, operational_areas=[{"name": "Somewhere"}])
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400
    assert "operational_areas" in response.json()["errors"]


def test_create_air_mission(as_user, planner, unit):
    client = as_user(planner)
    response = client.post("/api/missions/", air_payload(unit), format="json")
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["category"] == "air"
    assert body["mission_number"].startswith("TOWEMS-AO-")
    assert body["air_detail"]["flight_identification"] == "PAF-101"
    assert body["air_detail"]["air_asset"] is None
    assert body["air_detail"]["mission_configuration"] == "single"
    assert body["air_detail"]["aircraft_count"] == 1
    assert body["air_detail"]["assigned_aircraft"] == []
    assert body["ground_detail"] is None
    # Air times come from the scheduled departure/arrival.
    assert body["start_at"].startswith("2026-11-01T06:00")


def test_air_mission_does_not_require_mrm_order_or_approval(as_user, planner, unit):
    created = as_user(planner).post(
        "/api/missions/", air_payload(unit), format="json"
    ).json()
    mission = Mission.objects.get(pk=created["id"])

    from apps.ops import approvals, mission_order_workflow
    from apps.ops.serializers import mrm_state

    assert approvals.mrm_complete(mission) is True
    assert mrm_state(mission)["requires_mrm"] is False
    assert mission_order_workflow.execution_blocker(mission) is None
    assert approvals.execution_blockers(mission) == []
    assert approvals.readiness(mission)["state"] == "ready"


def test_air_mission_cannot_assign_an_approving_authority(as_user, planner, unit):
    created = as_user(planner).post(
        "/api/missions/", air_payload(unit), format="json"
    ).json()
    mission = Mission.objects.get(pk=created["id"])

    from apps.ops import approvals

    with pytest.raises(approvals.ApprovalError):
        approvals.assign_authority(
            mission,
            role="commander",
            user_id=planner.id,
            actor=planner,
        )


def test_air_mission_cannot_start_an_mrm(as_user, planner, unit):
    created = as_user(planner).post(
        "/api/missions/", air_payload(unit), format="json"
    ).json()
    response = as_user(planner).post(
        f"/api/missions/{created['id']}/start-mrm/", {}, format="json"
    )
    assert response.status_code == 400
    assert "do not carry an MRM" in response.json()["errors"]["detail"]


def test_air_mission_links_an_inventory_air_asset(as_user, planner, unit):
    asset = make_air_asset(unit)
    client = as_user(planner)

    # The form needs to read the same register that the mission links to.
    listed = client.get("/api/inventory/air-assets/")
    assert listed.status_code == 200, listed.content

    payload = air_payload(unit)
    payload["air_detail"].update(
        {
            "air_asset": asset.id,
            # A linked mission must use the register's identity, not a
            # caller-supplied disagreement with it.
            "aircraft_type": "manual-type",
            "aircraft_tail_number": "manual-tail",
            "flight_identification": "",
        }
    )
    response = client.post("/api/missions/", payload, format="json")
    assert response.status_code == 201, response.content
    detail = response.json()["air_detail"]

    assert detail["air_asset"] == asset.id
    assert detail["air_asset_label"] == "FA-50PH · 001 · EAGLE 01"
    assert detail["aircraft_type"] == "FA-50PH"
    assert detail["aircraft_tail_number"] == "001"
    assert detail["flight_identification"] == "EAGLE 01"
    assert len(detail["assigned_aircraft"]) == 1
    assert detail["assigned_aircraft"][0]["is_lead"] is True

    mission = Mission.objects.get(pk=response.json()["id"])
    assert mission.air_detail.air_asset_id == asset.id


def test_single_air_mission_infers_its_sole_aircraft_as_lead(as_user, planner, unit):
    asset = make_air_asset(unit, "009")
    payload = air_payload(unit)
    payload["air_detail"].update(
        {
            "mission_configuration": "single",
            "aircraft_count": 1,
            "air_asset_ids": [asset.id],
            # The form does not send a lead for a single-aircraft mission.
        }
    )

    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 201, response.content
    detail = response.json()["air_detail"]
    assert detail["air_asset"] == asset.id
    assert detail["assigned_aircraft"][0]["is_lead"] is True


def test_air_mission_stores_a_formation_and_its_lead(as_user, planner, unit):
    first = make_air_asset(unit, "001")
    second = make_air_asset(unit, "002")
    payload = air_payload(unit)
    payload["air_detail"].update(
        {
            "mission_configuration": "element",
            "aircraft_count": 2,
            "air_asset_ids": [first.id, second.id],
            "air_asset": second.id,
            "aircraft_crew": [
                {
                    "air_asset": first.id,
                    "pilot_in_command": "Capt First",
                    "co_pilot": "Lt First",
                },
                {
                    "air_asset": second.id,
                    "pilot_in_command": "Capt Second",
                    "co_pilot": "Lt Second",
                },
            ],
            "flight_identification": "EAGLE FLIGHT",
        }
    )

    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 201, response.content
    detail = response.json()["air_detail"]

    assert detail["mission_configuration"] == "element"
    assert detail["mission_configuration_label"] == "Element"
    assert detail["aircraft_count"] == 2
    assert detail["air_asset"] == second.id
    assert detail["aircraft_tail_number"] == "002"
    assert detail["flight_identification"] == "EAGLE FLIGHT"
    assert [row["tail_number"] for row in detail["assigned_aircraft"]] == [
        "001",
        "002",
    ]
    assert [row["is_lead"] for row in detail["assigned_aircraft"]] == [False, True]
    assert [row["element_role"] for row in detail["assigned_aircraft"]] == [
        "wing_man",
        "element_lead",
    ]
    assert [row["pilot_in_command"] for row in detail["assigned_aircraft"]] == [
        "Capt First",
        "Capt Second",
    ]
    assert [row["co_pilot"] for row in detail["assigned_aircraft"]] == [
        "Lt First",
        "Lt Second",
    ]

    mission = Mission.objects.get(pk=response.json()["id"])
    assert mission.air_detail.assigned_aircraft.count() == 2
    assert mission.air_detail.assigned_aircraft.get(is_lead=True).air_asset_id == second.id


def test_air_mission_rejects_an_incomplete_formation(as_user, planner, unit):
    first = make_air_asset(unit, "001")
    payload = air_payload(unit)
    payload["air_detail"].update(
        {
            "mission_configuration": "element",
            "aircraft_count": 2,
            "air_asset_ids": [first.id],
            "air_asset": first.id,
        }
    )

    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400
    assert "air_asset_ids" in response.json()["errors"]["air_detail"]
    assert Mission.objects.count() == 0


def test_air_mission_rejects_a_lead_outside_the_formation(as_user, planner, unit):
    first = make_air_asset(unit, "001")
    second = make_air_asset(unit, "002")
    third = make_air_asset(unit, "003")
    payload = air_payload(unit)
    payload["air_detail"].update(
        {
            "mission_configuration": "element",
            "aircraft_count": 2,
            "air_asset_ids": [first.id, second.id],
            "air_asset": third.id,
        }
    )

    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400
    assert "air_asset" in response.json()["errors"]["air_detail"]
    assert Mission.objects.count() == 0


def test_air_asset_conflicts_report_only_overlapping_live_missions(
    as_user, planner, unit
):
    first = make_air_asset(unit, "001")
    second = make_air_asset(unit, "002")
    payload = air_payload(unit)
    payload["air_detail"].update(
        {
            "mission_configuration": "single",
            "aircraft_count": 1,
            "air_asset_ids": [first.id],
            "air_asset": first.id,
            "scheduled_departure": "2026-11-01T06:00:00",
            "scheduled_arrival": "2026-11-01T10:00:00",
        }
    )
    client = as_user(planner)
    created = client.post("/api/missions/", payload, format="json")
    assert created.status_code == 201, created.content
    mission_id = created.json()["id"]

    response = client.get(
        "/api/inventory/air-assets/conflicts/",
        {
            "air_asset_ids": f"{first.id},{second.id}",
            "start": "2026-11-01T09:00:00",
            "end": "2026-11-01T11:00:00",
        },
    )
    assert response.status_code == 200, response.content
    conflicts = response.json()["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["air_asset"] == first.id
    assert conflicts[0]["mission_id"] == mission_id
    assert conflicts[0]["status_label"] == "Planned"
    assert conflicts[0]["tail_number"] == "001"

    # Touching windows do not overlap: the existing sortie ends at 10:00.
    touching = client.get(
        "/api/inventory/air-assets/conflicts/",
        {
            "air_asset_ids": str(first.id),
            "start": "2026-11-01T10:00:00",
            "end": "2026-11-01T12:00:00",
        },
    )
    assert touching.status_code == 200
    assert touching.json()["conflicts"] == []

    Mission.objects.filter(pk=mission_id).update(status="completed")
    completed = client.get(
        "/api/inventory/air-assets/conflicts/",
        {
            "air_asset_ids": str(first.id),
            "start": "2026-11-01T09:00:00",
            "end": "2026-11-01T11:00:00",
        },
    )
    assert completed.status_code == 200
    assert completed.json()["conflicts"] == []


def test_air_mission_rejects_an_asset_outside_the_planner_scope(
    as_user, planner, other_unit
):
    asset = make_air_asset(other_unit)
    payload = air_payload(planner.unit)
    payload["air_detail"]["air_asset"] = asset.id

    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400
    assert "air_asset" in response.json()["errors"]["air_detail"]
    assert Mission.objects.count() == 0


def test_only_the_operations_center_may_plan_a_mission(as_user, creator, commander, safety, unit):
    """Planning is the Operations Center's authority, not a general one."""
    for user in (creator, commander, safety):
        response = as_user(user).post("/api/missions/", ground_payload(unit), format="json")
        assert response.status_code == 403, (user.authority, response.content)
    assert Mission.objects.count() == 0


def test_mission_records_are_hidden_from_unrelated_users(as_user, planner, creator, unit):
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()

    # A Creator account is not attached to this mission: no list entry, no record.
    listing = as_user(creator).get("/api/missions/")
    assert listing.status_code == 200
    assert listing.json()["count"] == 0
    assert as_user(creator).get(f"/api/missions/{created['id']}/").status_code == 404


def test_assigned_personnel_can_see_their_own_mission(as_user, planner, creator, unit):
    client = as_user(planner)
    created = client.post("/api/missions/", ground_payload(unit), format="json").json()
    client.post(
        f"/api/missions/{created['id']}/assign-personnel/",
        {"personnel": [{"user_id": creator.id, "role": "Member"}]},
        format="json",
    )
    detail = as_user(creator).get(f"/api/missions/{created['id']}/")
    assert detail.status_code == 200, detail.content
    assert detail.json()["id"] == created["id"]


def test_command_officers_may_view_but_not_edit_missions(as_user, planner, commander, unit):
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()

    assert as_user(commander).get(f"/api/missions/{created['id']}/").status_code == 200
    update = as_user(commander).patch(
        f"/api/missions/{created['id']}/", {"title": "Changed by command"}, format="json"
    )
    assert update.status_code == 403
    assert Mission.objects.get(pk=created["id"]).title == "Route security patrol"


def test_ground_mission_requires_ground_detail(as_user, planner, unit):
    payload = ground_payload(unit)
    payload.pop("ground_detail")
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400
    assert "ground_detail" in response.json()["errors"]


def test_ground_mission_requires_estimated_times(as_user, planner, unit):
    payload = ground_payload(unit)
    payload["ground_detail"] = {"operational_area": "North Ridge"}
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400


def test_air_mission_requires_air_detail(as_user, planner, unit):
    payload = air_payload(unit)
    payload.pop("air_detail")
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400


def test_air_mission_rejects_ground_fields(as_user, planner, unit):
    payload = air_payload(unit)
    payload["ground_detail"] = ground_payload(unit)["ground_detail"]
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400


def test_ground_mission_rejects_air_fields(as_user, planner, unit):
    payload = ground_payload(unit)
    payload["air_detail"] = air_payload(unit)["air_detail"]
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400


def test_estimated_end_before_start_is_rejected(as_user, planner, unit):
    payload = ground_payload(unit)
    payload["ground_detail"]["estimated_start"] = GROUND_END
    payload["ground_detail"]["estimated_end"] = GROUND_START
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400


def test_invalid_coordinates_are_rejected(as_user, planner, unit):
    payload = ground_payload(unit, latitude=123.0, longitude=200.0)
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400


def test_invalid_mgrs_is_rejected(as_user, planner, unit):
    payload = ground_payload(unit, mgrs="NOT-AN-MGRS")
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 400


def test_mission_list_filters_by_category(as_user, planner, unit):
    client = as_user(planner)
    client.post("/api/missions/", ground_payload(unit), format="json")
    client.post("/api/missions/", air_payload(unit), format="json")

    ground = client.get("/api/missions/?category=ground")
    air = client.get("/api/missions/?category=air")
    assert ground.json()["count"] == 1
    assert air.json()["count"] == 1
    assert ground.json()["results"][0]["category"] == "ground"


def test_mission_list_is_unit_scoped(as_user, admin, planner, unit, other_unit):
    as_user(admin).post("/api/missions/", ground_payload(other_unit), format="json")
    response = as_user(planner).get("/api/missions/")
    assert response.json()["count"] == 0


def walk_approval_chain(
    as_user, planner, ops_officer, mission_id, authority=None, role="operations_officer"
):
    """Submit, route through the Operations Center and sign each intermediate post.

    Routing names the approving authority as part of the same decision. A route
    ending above the Operations Officer walks the configured intermediate posts,
    each with a comment, so the final signer cannot skip a review.
    """
    from apps.command.models import UnitAppointment

    client = as_user(planner)
    assert client.post(f"/api/missions/{mission_id}/submit/", {}, format="json").status_code == 200
    router = as_user(ops_officer)
    approver = authority or ops_officer

    route = [
        "operations_officer",
        "director_operations",
        "deputy_commander",
        "commander",
    ]
    final_index = route.index(role)
    intermediates = route[1:final_index]
    holders = {}
    for post in intermediates:
        appointment = (
            UnitAppointment.objects.filter(
                unit=approver.unit, role=post, is_active=True, user__is_active=True
            )
            .select_related("user")
            .first()
        )
        if appointment is None:
            person = make_user(unit=approver.unit, authority="Creator")
            UnitAppointment.objects.create(unit=approver.unit, user=person, role=post)
            holders[post] = person
        else:
            holders[post] = appointment.user

    assert (
        router.post(
            f"/api/missions/{mission_id}/route/",
            {
                "stage": "operations_center",
                "action": "routed",
                "approving_authority_role": role,
                "approving_authority": approver.id,
            },
            format="json",
        ).status_code
        == 200
    )
    assert (
        router.post(
            f"/api/missions/{mission_id}/route/",
            {"stage": "operations_officer", "action": "approved", "note": "Reviewed"},
            format="json",
        ).status_code
        == 200
    )
    for post in intermediates:
        response = as_user(holders[post]).post(
            f"/api/missions/{mission_id}/routing-decision/",
            {"decision": "approve", "note": f"Approved at {post.replace('_', ' ')}"},
            format="json",
        )
        assert response.status_code == 200, response.content


def link_approved_mrm(mission_id, user, unit):
    """Give a mission the authority-approved MRM the final approval requires."""
    import uuid

    from apps.legacy.models import MRMForm
    from apps.ops.models import Mission, RiskAssessment

    form = MRMForm.objects.create(
        mrm_number=f"MRM-{uuid.uuid4().hex[:6]}",
        activity_mission="Mission",
        created_by=user,
        safety_objectives="Safe",
        status="approved",
        review_status="approved",
        authority_approved=True,
        original_unit_id=unit.id,
        original_unit_name=unit.name,
    )
    RiskAssessment.objects.create(mission_id=mission_id, mrm_form=form, status="approved")
    Mission.objects.filter(pk=mission_id).update(risk_form=form)
    return form


def test_planner_cannot_approve_mission(as_user, planner, unit):
    client = as_user(planner)
    created = client.post("/api/missions/", ground_payload(unit), format="json").json()
    response = client.post(f"/api/missions/{created['id']}/approve/", {}, format="json")
    assert response.status_code == 403


def test_approval_without_routing_is_refused(as_user, planner, safety, unit):
    """The Operations Center and Operations Officer stages cannot be skipped."""
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    response = as_user(safety).post(
        f"/api/missions/{created['id']}/approve/", {}, format="json"
    )
    assert response.status_code == 400, response.content
    assert "approval chain" in response.json()["detail"]


def test_safety_officer_can_approve_mission_after_routing(
    as_user, planner, safety, ops_officer, unit
):
    from apps.command.models import UnitAppointment

    UnitAppointment.objects.create(unit=unit, user=safety, role="commander")
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    walk_approval_chain(
        as_user, planner, ops_officer, created["id"], authority=safety, role="commander"
    )

    # Final approval requires the mission's MRM to be completed.
    link_approved_mrm(created["id"], planner, unit)

    response = as_user(safety).post(
        f"/api/missions/{created['id']}/approve/", {}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "approved"
    assert response.json()["approved_by"] == safety.id
    assert response.json()["routing_stage"] == "approved"


def test_final_approval_is_refused_without_an_approved_mrm(
    as_user, planner, safety, ops_officer, unit
):
    from apps.command.models import UnitAppointment

    UnitAppointment.objects.create(unit=unit, user=safety, role="commander")
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    walk_approval_chain(
        as_user, planner, ops_officer, created["id"], authority=safety, role="commander"
    )

    refused = as_user(safety).post(
        f"/api/missions/{created['id']}/approve/", {}, format="json"
    )
    assert refused.status_code == 400, refused.content
    assert "MRM" in str(refused.json()["detail"])
    assert as_user(safety).get(f"/api/missions/{created['id']}/").json()["status"] != "approved"


def test_operations_officer_review_cannot_be_skipped(as_user, planner, ops_officer, unit):
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    as_user(planner).post(f"/api/missions/{created['id']}/submit/", {}, format="json")

    # Straight to the Operations Officer without the Operations Center step.
    response = as_user(ops_officer).post(
        f"/api/missions/{created['id']}/route/",
        {"stage": "operations_officer", "action": "approved"},
        format="json",
    )
    assert response.status_code == 400
    assert "Operations Center" in response.json()["detail"]


def test_assign_personnel(as_user, planner, unit):
    client = as_user(planner)
    driver = make_user(unit=unit, authority="Creator")
    created = client.post("/api/missions/", ground_payload(unit), format="json").json()

    response = client.post(
        f"/api/missions/{created['id']}/assign-personnel/",
        {"personnel": [{"user_id": driver.id, "role": "Driver"}]},
        format="json",
    )
    assert response.status_code == 200, response.content
    assignment = MissionPersonnel.objects.get(mission_id=created["id"], user_id=driver.id)
    assert assignment.role == "Driver"
    assert assignment.is_active is True


def test_assign_personnel_rejects_unknown_user(as_user, planner, unit):
    client = as_user(planner)
    created = client.post("/api/missions/", ground_payload(unit), format="json").json()
    response = client.post(
        f"/api/missions/{created['id']}/assign-personnel/",
        {"personnel": [{"user_id": 999999, "role": "Member"}]},
        format="json",
    )
    assert response.status_code == 400


def test_mission_links_to_existing_mrm(as_user, planner, unit, mrm_form):
    payload = ground_payload(unit, risk_form=mrm_form.id)
    response = as_user(planner).post("/api/missions/", payload, format="json")
    assert response.status_code == 201, response.content
    assert response.json()["risk_form_number"] == mrm_form.mrm_number


def test_destroy_is_super_administrator_only(as_user, planner, commander, seat_admin, unit):
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    bystander = make_user(unit=unit, authority="Administrator")
    # Oversight alone must not delete a unit's mission record.
    assert as_user(commander).delete(f"/api/missions/{created['id']}/").status_code == 403
    # Nor may the Operations Center that planned it: planning is not erasure.
    assert as_user(planner).delete(f"/api/missions/{created['id']}/").status_code == 403
    # Nor an administrator who does not hold the Super Administrator seat.
    assert as_user(bystander).delete(f"/api/missions/{created['id']}/").status_code == 403
    assert Mission.objects.filter(pk=created["id"]).exists()
    assert as_user(seat_admin).delete(f"/api/missions/{created['id']}/").status_code == 204
    assert not Mission.objects.filter(pk=created["id"]).exists()


def test_administrator_can_remove_a_draft(as_user, planner, seat_admin, unit):
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    assert as_user(seat_admin).delete(f"/api/missions/{created['id']}/").status_code == 204
    assert not Mission.objects.filter(pk=created["id"]).exists()


def test_the_seat_holder_deletes_a_running_mission(as_user, planner, seat_admin, unit):
    """The seat's erase is not limited by status: draft, completed or active."""
    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    for status in ("active", "completed"):
        Mission.objects.filter(pk=created["id"]).update(status=status)
        response = as_user(seat_admin).delete(f"/api/missions/{created['id']}/")
        assert response.status_code == 204, response.content
        if status == "active":
            created = as_user(planner).post(
                "/api/missions/", ground_payload(unit), format="json"
            ).json()


def test_personnel_custom_designation_is_saved_and_displayed(as_user, planner, unit):
    client = as_user(planner)
    handler = make_user(unit=unit, authority="Creator")
    created = client.post("/api/missions/", ground_payload(unit), format="json").json()

    response = client.post(
        f"/api/missions/{created['id']}/assign-personnel/",
        {
            "personnel": [
                {"user_id": handler.id, "role": "Specify", "custom_role": "K9 Handler"}
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    assignment = MissionPersonnel.objects.get(mission_id=created["id"], user_id=handler.id)
    assert assignment.role == "Specify"
    assert assignment.custom_role == "K9 Handler"

    row = next(
        item
        for item in client.get(f"/api/missions/{created['id']}/").json()["personnel"]
        if item["user"] == handler.id
    )
    assert row["display_role"] == "K9 Handler"


def test_personnel_specify_requires_a_custom_designation(as_user, planner, unit):
    client = as_user(planner)
    member = make_user(unit=unit, authority="Creator")
    created = client.post("/api/missions/", ground_payload(unit), format="json").json()
    response = client.post(
        f"/api/missions/{created['id']}/assign-personnel/",
        {"personnel": [{"user_id": member.id, "role": "Specify", "custom_role": "  "}]},
        format="json",
    )
    assert response.status_code == 400
    assert "custom designation" in str(response.json()).lower()


def test_director_route_stops_before_command_and_notifies_higher(
    as_user, planner, ops_officer, commander, unit
):
    from apps.command.models import UnitAppointment
    from apps.ops.models import Notification

    director = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=director, role="director_operations")
    deputy = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=deputy, role="deputy_commander")

    created = as_user(planner).post("/api/missions/", ground_payload(unit), format="json").json()
    walk_approval_chain(
        as_user,
        planner,
        ops_officer,
        created["id"],
        authority=director,
        role="director_operations",
    )

    body = as_user(planner).get(f"/api/missions/{created['id']}/").json()
    plan = body["route_plan"]
    assert [stage["role"] for stage in plan["stages"]] == [
        "operations_officer",
        "director_operations",
    ]
    assert plan["current_role"] == "director_operations"
    assert {row["role"] for row in plan["higher_authorities"]} == {
        "deputy_commander",
        "commander",
    }
    for post_holder in (deputy, commander):
        assert Notification.objects.filter(
            recipient=post_holder,
            category="mission_routing",
            related_id=str(created["id"]),
        ).exists()
    assert Mission.objects.filter(pk=created["id"]).exists()

"""Audit trail and notification behaviour."""

from __future__ import annotations

import pytest

from apps.ops.models import AuditLog, Notification

pytestmark = pytest.mark.django_db


def test_mission_creation_writes_audit_entry(as_user, planner, unit):
    from apps.ops.tests.test_missions import ground_payload

    client = as_user(planner)
    created = client.post("/api/missions/", ground_payload(unit), format="json").json()

    entry = AuditLog.objects.get(
        entity_type="mission", entity_id=str(created["id"]), action="create"
    )
    assert entry.actor_id == planner.id
    assert entry.after["mission_number"] == created["mission_number"]


def test_mission_approval_writes_before_and_after(
    as_user, planner, safety, ops_officer, unit
):
    from apps.ops.tests.test_missions import (
        ground_payload,
        link_approved_mrm,
        walk_approval_chain,
    )

    created = as_user(planner).post(
        "/api/missions/", ground_payload(unit), format="json"
    ).json()
    from apps.command.models import UnitAppointment

    UnitAppointment.objects.create(unit=unit, user=safety, role="commander")
    walk_approval_chain(
        as_user, planner, ops_officer, created["id"], authority=safety, role="commander"
    )
    link_approved_mrm(created["id"], planner, unit)
    as_user(safety).post(f"/api/missions/{created['id']}/approve/", {}, format="json")

    entry = AuditLog.objects.get(
        entity_type="mission", entity_id=str(created["id"]), action="approve"
    )
    # Submission moved the mission draft -> planned before the decision.
    assert entry.before["status"] == "planned"
    assert entry.after["status"] == "approved"


def test_mission_creation_notifies_the_planner(as_user, planner, unit):
    from apps.ops.tests.test_missions import ground_payload

    as_user(planner).post("/api/missions/", ground_payload(unit), format="json")
    assert Notification.objects.filter(
        recipient_id=planner.id, category="mission"
    ).exists()


def test_audit_endpoint_is_admin_only(as_user, creator, admin):
    assert as_user(creator).get("/api/audit/").status_code == 403
    response = as_user(admin).get("/api/audit/")
    assert response.status_code == 200
    assert "results" in response.json()


def test_evaluator_assignment_is_audited(as_user, admin, evaluator, mrm_form):
    as_user(admin).post(
        f"/api/risk-register/{mrm_form.id}/assign-evaluator/",
        {"evaluator_id": evaluator.id},
        format="json",
    )
    entry = AuditLog.objects.get(entity_type="mrm_form", action="assign_evaluator")
    assert entry.after["reviewed_by"] == evaluator.id


def test_notifications_are_per_user(as_user, planner, evaluator, unit):
    from apps.ops.tests.test_missions import ground_payload

    as_user(planner).post("/api/missions/", ground_payload(unit), format="json")
    response = as_user(evaluator).get("/api/notifications/")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_mark_notification_read(as_user, planner, unit):
    from apps.ops.tests.test_missions import ground_payload

    client = as_user(planner)
    client.post("/api/missions/", ground_payload(unit), format="json")
    notification = Notification.objects.filter(recipient_id=planner.id).first()
    response = client.post(f"/api/notifications/{notification.id}/mark-read/")
    assert response.status_code == 200
    notification.refresh_from_db()
    assert notification.is_read is True
    assert notification.read_at is not None


def test_mark_all_read(as_user, planner, unit):
    from apps.ops.tests.test_missions import ground_payload

    client = as_user(planner)
    client.post("/api/missions/", ground_payload(unit), format="json")
    client.post("/api/missions/", ground_payload(unit), format="json")
    response = client.post("/api/notifications/mark-all-read/")
    assert response.status_code == 200
    assert response.json()["updated"] >= 2
    assert Notification.objects.filter(recipient_id=planner.id, is_read=False).count() == 0


def test_response_carries_request_id(as_user, creator):
    response = as_user(creator).get("/api/dashboard/")
    assert response["X-Request-ID"]

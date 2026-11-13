"""Persistent action notifications (spec §4).

The core rule: reading a notification may clear the unread badge, but a task
stays pending until the owning workflow records completion.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.core.factories import make_user
from apps.ops.models import Notification
from apps.ops.notifications import notify

pytestmark = pytest.mark.django_db


def action_notification(user, **overrides):
    data = {
        "title": "Review this",
        "message": "Please review.",
        "category": "approval",
        "requires_action": True,
        "action_label": "Review",
        "action_url": "/missions/1",
        "priority": "high",
        "related": ("", ""),
    }
    data.update(overrides)
    related = data.pop("related")
    return notify(user, related=related, **data)


def test_reading_an_action_does_not_complete_it(as_user, creator):
    item = action_notification(creator)
    client = as_user(creator)

    response = client.post(f"/api/notifications/{item.id}/mark-read/")
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.is_read is True
    assert item.requires_action is True
    assert item.action_status == Notification.ACTION_OPEN
    assert item.completed_at is None


def test_acknowledgement_is_recorded_separately_from_completion(as_user, creator):
    item = action_notification(creator)
    client = as_user(creator)

    response = client.post(f"/api/notifications/{item.id}/acknowledge/", {}, format="json")
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.acknowledged_at is not None
    assert item.acknowledged_by_id == creator.id
    assert item.action_status == Notification.ACTION_IN_PROGRESS
    assert item.completed_at is None

    # Acknowledging twice is idempotent.
    again = client.post(f"/api/notifications/{item.id}/acknowledge/", {}, format="json")
    assert again.status_code == 200
    item.refresh_from_db()
    assert item.action_status == Notification.ACTION_IN_PROGRESS


def test_directly_completing_a_non_workflow_action_records_who_and_when(as_user, admin):
    item = action_notification(admin)
    response = as_user(admin).post(
        f"/api/notifications/{item.id}/complete/", {"note": "Handled"}, format="json"
    )
    assert response.status_code == 200, response.content
    item.refresh_from_db()
    assert item.action_status == Notification.ACTION_COMPLETED
    assert item.completed_by_id == admin.id
    assert item.completed_at is not None
    assert item.completion_note == "Handled"


def test_workflow_backed_actions_cannot_be_closed_from_the_bell(as_user, admin):
    item = action_notification(admin, related=("mission", 42))
    response = as_user(admin).post(f"/api/notifications/{item.id}/complete/", {}, format="json")
    assert response.status_code == 400
    assert "own workflow" in response.json()["detail"]

    item.refresh_from_db()
    assert item.action_status == Notification.ACTION_OPEN


def test_summary_counts_open_and_completed_actions(as_user, creator, admin):
    action_notification(creator)
    action_notification(creator)
    completed = action_notification(creator)
    completed.action_status = Notification.ACTION_COMPLETED
    completed.save(update_fields=["action_status"])
    action_notification(admin)  # another recipient's item is not counted

    response = as_user(creator).get("/api/notifications/summary/")
    assert response.status_code == 200
    body = response.json()
    assert body["pending_actions"] == 2
    assert body["completed"] == 1
    assert body["open"] == 2
    assert body["total"] == 3


def test_scope_filter_separates_pending_from_history(as_user, creator):
    pending = action_notification(creator)
    done = action_notification(creator)
    done.action_status = Notification.ACTION_COMPLETED
    done.save(update_fields=["action_status"])
    notify(creator, title="FYI", message="Read only.")

    client = as_user(creator)
    action_ids = {row["id"] for row in client.get("/api/notifications/?scope=action").json()["results"]}
    history_ids = {
        row["id"] for row in client.get("/api/notifications/?scope=history").json()["results"]
    }
    assert action_ids == {pending.id}
    assert done.id in history_ids
    assert pending.id not in history_ids


def test_pending_action_survives_a_new_session(as_user, creator):
    item = action_notification(creator)
    as_user(creator).post(f"/api/notifications/{item.id}/mark-read/")

    fresh = APIClient()
    fresh.force_authenticate(user=creator)
    response = fresh.get("/api/notifications/?scope=action")
    ids = {row["id"] for row in response.json()["results"]}
    assert item.id in ids


def test_only_the_recipient_sees_a_notification(as_user, creator, evaluator):
    action_notification(creator)
    response = as_user(evaluator).get("/api/notifications/")
    assert response.json()["count"] == 0
    assert (
        as_user(evaluator).post(
            f"/api/notifications/{Notification.objects.first().id}/mark-read/"
        ).status_code
        == 404
    )


def test_mission_submission_notifies_operations_officer_and_routing_completes_it(
    as_user, planner, ops_officer, unit
):
    from apps.ops.tests.test_missions import ground_payload

    created = as_user(planner).post(
        "/api/missions/", ground_payload(unit), format="json"
    ).json()
    assert as_user(planner).post(
        f"/api/missions/{created['id']}/submit/", {}, format="json"
    ).status_code == 200

    item = Notification.objects.get(
        recipient_id=ops_officer.id, related_type="mission", related_id=str(created["id"])
    )
    assert item.requires_action is True
    assert item.action_label
    assert item.action_url == f"/missions/{created['id']}"

    # Reading it does not close the task.
    as_user(ops_officer).post(f"/api/notifications/{item.id}/mark-read/")
    item.refresh_from_db()
    assert item.action_status == Notification.ACTION_OPEN

    # Completing the workflow does.
    as_user(ops_officer).post(
        f"/api/missions/{created['id']}/route/",
        {
            "stage": "operations_center",
            "action": "routed",
            "approving_authority_role": "operations_officer",
            "approving_authority": ops_officer.id,
        },
        format="json",
    )
    item.refresh_from_db()
    assert item.action_status == Notification.ACTION_COMPLETED
    assert item.completed_by_id == ops_officer.id

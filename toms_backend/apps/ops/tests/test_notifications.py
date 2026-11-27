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


def test_the_pulse_announces_a_new_urgent_item_once(as_user, creator):
    first = action_notification(creator, title="Old news")
    client = as_user(creator)

    # The first call only sets the mark: nothing already in the inbox is
    # announced as if it had just arrived.
    opening = client.get("/api/notifications/pulse/").json()
    assert opening["latest_id"] == first.id
    assert opening["urgent"] == []

    urgent = action_notification(
        creator, title="EMERGENCY: KM450-1234567", level="danger", priority="urgent"
    )
    body = client.get(f"/api/notifications/pulse/?since={first.id}").json()

    assert body["latest_id"] == urgent.id
    assert [row["id"] for row in body["urgent"]] == [urgent.id]
    assert body["urgent"][0]["title"] == "EMERGENCY: KM450-1234567"
    assert body["unread"] == 2


def test_the_bell_can_leave_private_messages_out(as_user, creator):
    """Message notices belong to the Messages counter, not the system bell."""
    system = action_notification(
        creator, title="System notice", requires_action=False
    )
    message = notify(
        creator,
        title="New message from Juan Dela Cruz",
        message="Open Messages to read it.",
        category="message",
    )
    client = as_user(creator)

    listed = client.get("/api/notifications/?exclude_category=message").json()
    ids = [row["id"] for row in listed["results"]]
    assert system.id in ids
    assert message.id not in ids

    summary = client.get(
        "/api/notifications/summary/?exclude_category=message"
    ).json()
    assert summary["unread"] == 1
    assert summary["total"] == 1


def test_the_system_inbox_hides_messages_unless_asked(as_user, creator):
    """Message notices are out of the list and the counts by default."""
    system = action_notification(
        creator, title="System notice", requires_action=False
    )
    message = notify(
        creator,
        title="New message from Juan Dela Cruz",
        message="Open Messages to read it.",
        category="message",
    )
    client = as_user(creator)

    listed = client.get("/api/notifications/?page_size=50").json()
    ids = {row["id"] for row in listed["results"]}
    assert system.id in ids
    assert message.id not in ids

    summary = client.get("/api/notifications/summary/").json()
    assert summary["unread"] == 1
    assert summary["total"] == 1

    opted = client.get("/api/notifications/?include_messages=true").json()
    assert message.id in {row["id"] for row in opted["results"]}
    opted_summary = client.get("/api/notifications/summary/?include_messages=true").json()
    assert opted_summary["total"] == 2


def test_mark_all_read_leaves_private_messages_unread(as_user, creator):
    """The system inbox's bulk action must not touch messages it cannot show."""
    system = action_notification(creator, title="System notice")
    message = notify(
        creator,
        title="New message from Juan Dela Cruz",
        message="Open Messages to read it.",
        category="message",
    )
    client = as_user(creator)

    # No include_messages: the default inbox is what this action covers.
    response = client.post("/api/notifications/mark-all-read/", {}, format="json")
    assert response.status_code == 200
    system.refresh_from_db()
    message.refresh_from_db()
    assert system.is_read is True
    assert message.is_read is False


def test_the_pulse_repeats_an_unread_emergency_on_a_fresh_page(as_user, creator):
    """A console that opens after the alarm still has to hear it."""
    urgent = action_notification(
        creator, title="EMERGENCY: KM450-1234567", level="danger", priority="urgent"
    )
    quiet = action_notification(creator, title="Routine", priority="high")

    body = as_user(creator).get("/api/notifications/pulse/").json()

    assert [row["id"] for row in body["urgent"]] == [urgent.id]
    assert quiet.id not in {row["id"] for row in body["urgent"]}


def test_the_pulse_lists_a_new_message_although_it_is_not_urgent(as_user, creator):
    """A phone in a pocket must hear about a message, not only an alarm.

    ``urgent`` stays as it was for the desk; ``items`` is what the Android
    watcher raises notifications from, and it cannot be filtered to the loud
    priorities or messages and routine instructions never reach the phone.
    """
    first = action_notification(creator, title="Old news")
    message = notify(
        creator,
        title="New message from Juan Dela Cruz",
        message="Open Messages to read it.",
        category="message",
        priority="normal",
        action_url="/messages?conversation=7",
    )
    routine = notify(creator, title="Trip sheet approved", priority="normal")

    body = as_user(creator).get(f"/api/notifications/pulse/?since={first.id}").json()

    assert body["urgent"] == []
    assert [row["id"] for row in body["items"]] == [message.id, routine.id]
    assert body["items"][0]["category"] == "message"
    # The chat text is never part of the alert; the server sends a pointer.
    assert "Open Messages" in body["items"][0]["message"]
    assert routine.id not in {row["id"] for row in body["urgent"]}


def test_the_pulse_does_not_repeat_an_item_the_phone_already_saw(as_user, creator):
    first = action_notification(creator, title="Old news")
    item = notify(creator, title="New message from Juan", category="message")

    # The phone marks the watermark as it notifies, so the next poll is silent.
    assert [row["id"] for row in as_user(creator).get(
        f"/api/notifications/pulse/?since={first.id}"
    ).json()["items"]] == [item.id]
    body = as_user(creator).get(f"/api/notifications/pulse/?since={item.id}").json()
    assert body["items"] == []


def test_the_pulse_stays_quiet_about_items_already_read(as_user, creator):
    first = action_notification(creator)
    item = action_notification(creator, priority="urgent")
    as_user(creator).post(f"/api/notifications/{item.id}/mark-read/")

    body = as_user(creator).get(f"/api/notifications/pulse/?since={first.id}").json()

    assert body["latest_id"] == item.id
    assert body["urgent"] == []


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

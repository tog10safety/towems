"""Group chat extras: quoting a message, @mentions, and unsending with a trace."""

from __future__ import annotations

import pytest

from apps.core.factories import make_user
from apps.messaging import services
from apps.messaging.models import Conversation, ConversationMember, Message
from apps.ops.models import AuditLog, Notification

pytestmark = pytest.mark.django_db


def unit_group(as_user, user) -> dict:
    inbox = as_user(user).get("/api/messaging/conversations/")
    assert inbox.status_code == 200, inbox.content
    group = inbox.json()["unit_group"]
    assert group is not None
    return group


def send(client, conversation, body, **extra):
    return client.post(
        f"/api/messaging/conversations/{conversation}/messages/",
        {"body": body, **extra},
        format="json",
    )


def messages_url(conversation) -> str:
    return f"/api/messaging/conversations/{conversation}/messages/"


def unsend_url(conversation, message_id) -> str:
    return f"/api/messaging/conversations/{conversation}/messages/{message_id}/unsend/"


def test_a_group_message_can_quote_an_earlier_one(unit, as_user, planner, operations_center):
    group = unit_group(as_user, planner)
    client = as_user(planner)

    original = send(client, group["id"], "Move at 0600.")
    assert original.status_code == 201, original.content

    answer = send(
        client,
        group["id"],
        "Confirmed.",
        reply_to=original.json()["id"],
    )
    assert answer.status_code == 201, answer.content
    card = answer.json()["reply_to"]
    assert card["id"] == original.json()["id"]
    assert card["sender"]["id"] == planner.id
    assert card["body"] == "Move at 0600."
    assert card["is_unsent"] is False

    # The quoted card survives a reload of the window.
    window = client.get(messages_url(group["id"])).json()["messages"]
    assert window[-1]["reply_to"]["id"] == original.json()["id"]


def test_a_reply_cannot_point_at_another_thread(unit, as_user, planner, operations_center):
    other = make_user(unit=unit)
    group = unit_group(as_user, planner)
    planner_client = as_user(planner)
    private = planner_client.post(
        "/api/messaging/conversations/", {"user_id": other.id}, format="json"
    ).json()["id"]
    private_message = send(planner_client, private, "private note").json()["id"]

    refused = send(
        planner_client,
        group["id"],
        "quoting the wrong thread",
        reply_to=private_message,
    )
    assert refused.status_code == 400
    assert "not part of this conversation" in refused.json()["detail"]


def test_mentions_are_stored_and_notify_without_the_text(
    unit, as_user, planner, operations_center
):
    member = make_user(unit=unit)
    group = unit_group(as_user, planner)
    body = "Radio check at 0800, please confirm."

    sent = send(
        as_user(planner),
        group["id"],
        f"@{member.full_name} {body}",
        mention_ids=[member.id],
    )
    assert sent.status_code == 201, sent.content
    assert [row["id"] for row in sent.json()["mentions"]] == [member.id]

    notice = Notification.objects.get(recipient=member, category="message")
    assert "mentioned you" in notice.title
    # The notice says who and where, never what was written.
    assert body not in notice.message
    assert body not in notice.title
    assert notice.related_id == str(group["id"])


def test_only_people_who_can_see_the_thread_can_be_mentioned(
    unit, other_unit, as_user, planner, operations_center
):
    outsider = make_user(unit=other_unit)
    group = unit_group(as_user, planner)

    refused = send(
        as_user(planner),
        group["id"],
        f"@{outsider.full_name} are you there?",
        mention_ids=[outsider.id],
    )
    assert refused.status_code == 400
    assert "can see this conversation" in refused.json()["detail"]
    assert not Notification.objects.filter(recipient=outsider).exists()


def test_unsending_keeps_a_trace_and_clears_the_text(
    unit, as_user, planner, operations_center
):
    member = make_user(unit=unit)
    group = unit_group(as_user, planner)
    client = as_user(member)

    sent = send(client, group["id"], "Secret movement order")
    message_id = sent.json()["id"]
    assert sent.json()["can_unsend"] is True

    removed = client.post(unsend_url(group["id"], message_id), {}, format="json")
    assert removed.status_code == 200, removed.content
    payload = removed.json()
    assert payload["is_unsent"] is True
    assert payload["body"] == ""
    assert payload["unsent_by"]["id"] == member.id
    assert payload["unsent_at"]
    assert payload["can_unsend"] is False

    # The row is still in the thread, as a trace rather than a deleted gap.
    window = client.get(messages_url(group["id"])).json()["messages"]
    row = next(item for item in window if item["id"] == message_id)
    assert row["is_unsent"] is True
    assert row["body"] == ""

    audit = AuditLog.objects.get(action="message_unsent", entity_id=message_id)
    assert "Secret movement order" not in (audit.note or "")
    assert audit.after["unsent"] is True

    # Neither search nor the inbox preview repeats the withdrawn text.
    assert (
        client.get("/api/messaging/search/?q=Secret").json()["results"] == []
    )
    inbox = as_user(planner).get("/api/messaging/conversations/").json()
    assert inbox["unit_group"]["last_message"]["body"] == "Message unsent"


def test_only_the_sender_can_unsend_and_only_once(
    unit, as_user, planner, operations_center
):
    member = make_user(unit=unit)
    group = unit_group(as_user, planner)
    member_client = as_user(member)
    planner_client = as_user(planner)

    message_id = send(member_client, group["id"], "mine to withdraw").json()["id"]

    refused = planner_client.post(unsend_url(group["id"], message_id), {}, format="json")
    assert refused.status_code == 400
    assert "Only the sender" in refused.json()["detail"]
    assert Message.objects.get(pk=message_id).body == "mine to withdraw"

    first = member_client.post(unsend_url(group["id"], message_id), {}, format="json")
    assert first.status_code == 200
    trace = Message.objects.get(pk=message_id).unsent_at
    second = member_client.post(unsend_url(group["id"], message_id), {}, format="json")
    assert second.status_code == 200
    assert second.json()["is_unsent"] is True
    # Unsend is idempotent, so a double tap cannot rewrite the trace.
    assert Message.objects.get(pk=message_id).unsent_at == trace


def test_asking_a_withdrawn_message_cannot_be_quoted(
    unit, as_user, planner, operations_center
):
    member = make_user(unit=unit)
    group = unit_group(as_user, planner)
    client = as_user(member)
    message_id = send(client, group["id"], "withdraw me").json()["id"]
    assert client.post(unsend_url(group["id"], message_id), {}, format="json").status_code == 200

    refused = send(client, group["id"], "quoting it", reply_to=message_id)
    assert refused.status_code == 400
    assert "cannot be quoted" in refused.json()["detail"]


def test_decisions_in_a_group_chat_request_cannot_be_unsent(
    unit, as_user, planner
):
    thread = Conversation.objects.create(
        kind=Conversation.KIND_GROUP_REQUEST,
        unit=unit,
        name="Group chat request: Test",
        created_by=planner,
        chat_enabled=False,
    )
    ConversationMember.objects.create(conversation=thread, user=planner)
    message = services.send_message(thread, planner, "Proposed for 3 personnel.")

    refused = as_user(planner).post(
        unsend_url(thread.id, message.id), {}, format="json"
    )
    assert refused.status_code == 400
    assert "cannot be unsent" in refused.json()["detail"]
    assert Message.objects.get(pk=message.id).is_unsent is False

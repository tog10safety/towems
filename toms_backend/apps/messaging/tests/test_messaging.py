"""Private messaging: access control, receipts, search, presence, retention."""

from __future__ import annotations

import datetime as dt
from io import StringIO

import pytest
from django.core.management import call_command

from apps.accounts.models import UserProfile
from apps.core import timeutils
from apps.core.factories import make_user
from apps.messaging.models import Conversation, Message, TypingState
from apps.ops.models import AuditLog, Notification

pytestmark = pytest.mark.django_db


def open_thread(as_user, actor, other):
    return as_user(actor).post(
        "/api/messaging/conversations/", {"user_id": other.id}, format="json"
    )


def conversation_id(as_user, actor, other) -> int:
    response = open_thread(as_user, actor, other)
    assert response.status_code in (200, 201), response.content
    return response.json()["id"]


def send(client, conversation, body):
    return client.post(
        f"/api/messaging/conversations/{conversation}/messages/",
        {"body": body},
        format="json",
    )


def messages_url(conversation) -> str:
    return f"/api/messaging/conversations/{conversation}/messages/"


def test_a_pair_has_one_conversation_whichever_side_opens_it(unit, as_user):
    first = make_user(unit=unit)
    second = make_user(unit=unit)

    created = open_thread(as_user, first, second)
    assert created.status_code == 201, created.content
    reopened = open_thread(as_user, second, first)
    assert reopened.status_code == 200, reopened.content

    assert created.json()["id"] == reopened.json()["id"]
    conversation = Conversation.objects.get()
    # The pair is stored in ascending id order, which the table enforces.
    assert conversation.low_user_id < conversation.high_user_id
    assert Conversation.objects.count() == 1


def test_a_conversation_needs_two_different_people(unit, as_user):
    person = make_user(unit=unit)

    response = open_thread(as_user, person, person)

    assert response.status_code == 400
    assert "another person" in str(response.json()["detail"])


def test_sending_reaches_the_peer_with_an_unread_counter(unit, as_user):
    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    conversation = conversation_id(as_user, sender, recipient)

    sent = send(as_user(sender), conversation, "  Hello there  ")
    assert sent.status_code == 201, sent.content
    assert sent.json()["body"] == "Hello there"

    inbox = as_user(recipient).get("/api/messaging/conversations/").json()
    assert inbox["unread_total"] == 1
    row = inbox["conversations"][0]
    assert row["unread_count"] == 1
    assert row["last_message"]["body"] == "Hello there"
    assert row["peer"]["id"] == sender.id

    # The sender's own copy is never unread.
    own = as_user(sender).get("/api/messaging/conversations/").json()
    assert own["unread_total"] == 0


def test_an_empty_or_oversized_message_is_refused(unit, as_user):
    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    conversation = conversation_id(as_user, sender, recipient)
    client = as_user(sender)

    assert send(client, conversation, "   ").status_code == 400
    assert send(client, conversation, "x" * 4001).status_code == 400


def test_delivery_and_read_receipts_move_forward(unit, as_user):
    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    conversation = conversation_id(as_user, sender, recipient)
    sender_client = as_user(sender)
    recipient_client = as_user(recipient)

    for body in ("first", "second"):
        assert send(sender_client, conversation, body).status_code == 201

    # The recipient's client fetching the window is delivery.
    fetched = recipient_client.get(messages_url(conversation)).json()
    assert [row["body"] for row in fetched["messages"]] == ["first", "second"]
    assert all(row["delivered_at"] for row in fetched["messages"])
    assert fetched["conversation"]["peer_typing"] is False

    seen = sender_client.get(messages_url(conversation)).json()
    assert {row["status"] for row in seen["messages"]} == {"delivered"}

    read = recipient_client.post(
        f"/api/messaging/conversations/{conversation}/read/", {}, format="json"
    )
    assert read.status_code == 200, read.content
    assert read.json()["unread_total"] == 0

    after = sender_client.get(messages_url(conversation)).json()
    assert {row["status"] for row in after["messages"]} == {"read"}
    assert all(row["read_at"] for row in after["messages"])

    summary = recipient_client.get("/api/messaging/summary/").json()
    assert summary["unread_total"] == 0


def test_one_notification_per_thread_and_reading_clears_it(unit, as_user):
    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    conversation = conversation_id(as_user, sender, recipient)
    sender_client = as_user(sender)

    send(sender_client, conversation, "one")
    send(sender_client, conversation, "two")

    notices = Notification.objects.filter(recipient=recipient, category="message")
    assert notices.count() == 1
    notice = notices.get()
    assert notice.related_type == "conversation"
    assert notice.related_id == str(conversation)
    # The body must not be copied into the notification.
    assert "one" not in notice.message and "two" not in notice.message

    as_user(recipient).post(
        f"/api/messaging/conversations/{conversation}/read/", {}, format="json"
    )
    notice.refresh_from_db()
    assert notice.is_read is True


def test_a_third_person_cannot_reach_someone_elses_thread(unit, as_user):
    first = make_user(unit=unit)
    second = make_user(unit=unit)
    outsider = make_user(unit=unit)
    conversation = conversation_id(as_user, first, second)
    send(as_user(first), conversation, "private")
    client = as_user(outsider)

    assert client.get(messages_url(conversation)).status_code == 404
    assert send(client, conversation, "intruding").status_code == 404
    assert (
        client.post(
            f"/api/messaging/conversations/{conversation}/read/", {}, format="json"
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/messaging/conversations/{conversation}/typing/",
            {"typing": True},
            format="json",
        ).status_code
        == 404
    )
    # Their own search cannot see it either.
    found = client.get("/api/messaging/search/?q=private").json()["results"]
    assert found == []


def test_message_search_stays_inside_the_callers_threads(unit, as_user):
    first = make_user(unit=unit)
    second = make_user(unit=unit)
    third = make_user(unit=unit)
    with_second = conversation_id(as_user, first, second)
    with_third = conversation_id(as_user, first, third)
    send(as_user(first), with_second, "alpha bravo")
    send(as_user(first), with_third, "alpha charlie")

    mine = as_user(second).get("/api/messaging/search/?q=alpha").json()["results"]
    assert len(mine) == 1
    assert mine[0]["conversation_id"] == with_second
    assert mine[0]["peer"]["id"] == first.id

    theirs = as_user(third).get("/api/messaging/search/?q=alpha").json()["results"]
    assert [row["conversation_id"] for row in theirs] == [with_third]

    too_short = as_user(second).get("/api/messaging/search/?q=a")
    assert too_short.status_code == 400


def test_people_search_finds_a_person_but_never_yourself(unit, as_user):
    me = make_user(unit=unit, first_name="Self", last_name="Searchable")
    other = make_user(unit=unit, first_name="Target", last_name="Person")

    rows = as_user(me).get(f"/api/messaging/people/?q={other.serial_number}").json()[
        "people"
    ]
    assert [row["id"] for row in rows] == [other.id]
    assert rows[0]["serial_number"] == other.serial_number

    everything = as_user(me).get("/api/messaging/people/").json()["people"]
    assert all(row["id"] != me.id for row in everything)


def test_history_pages_backwards(unit, as_user):
    first = make_user(unit=unit)
    second = make_user(unit=unit)
    conversation = conversation_id(as_user, first, second)
    client = as_user(first)
    for index in range(5):
        assert send(client, conversation, f"message {index}").status_code == 201

    latest = client.get(f"{messages_url(conversation)}?page_size=2").json()
    assert [row["body"] for row in latest["messages"]] == ["message 3", "message 4"]
    assert latest["has_more"] is True

    older = client.get(
        f"{messages_url(conversation)}?page_size=2&before_id={latest['messages'][0]['id']}"
    ).json()
    assert [row["body"] for row in older["messages"]] == ["message 1", "message 2"]

    matches = client.get(f"{messages_url(conversation)}?q=message 3").json()["messages"]
    assert [row["body"] for row in matches] == ["message 3"]


def test_typing_shows_to_the_peer_and_expires(unit, as_user):
    first = make_user(unit=unit)
    second = make_user(unit=unit)
    conversation = conversation_id(as_user, first, second)

    told = as_user(first).post(
        f"/api/messaging/conversations/{conversation}/typing/",
        {"typing": True},
        format="json",
    )
    assert told.status_code == 204

    seen = as_user(second).get(messages_url(conversation)).json()
    assert seen["conversation"]["peer_typing"] is True
    assert seen["conversation"]["peer"]["id"] == first.id

    TypingState.objects.filter(user=first).update(
        updated_at=timeutils.now() - dt.timedelta(seconds=60)
    )
    expired = as_user(second).get(messages_url(conversation)).json()
    assert expired["conversation"]["peer_typing"] is False

    # Sending ends typing immediately.
    as_user(first).post(
        f"/api/messaging/conversations/{conversation}/typing/",
        {"typing": True},
        format="json",
    )
    send(as_user(first), conversation, "done")
    assert not TypingState.objects.filter(user=first).exists()


def test_online_flag_follows_the_existing_presence_stamp(unit, as_user):
    viewer = make_user(unit=unit)
    peer = make_user(unit=unit)
    conversation = conversation_id(as_user, viewer, peer)
    UserProfile.objects.update_or_create(
        user_id=peer.id, defaults={"last_seen": timeutils.now()}
    )

    inbox = as_user(viewer).get("/api/messaging/conversations/").json()
    assert inbox["conversations"][0]["peer"]["online"] is True

    UserProfile.objects.filter(user_id=peer.id).update(
        last_seen=timeutils.now() - dt.timedelta(minutes=30)
    )
    stale = as_user(viewer).get("/api/messaging/conversations/").json()
    assert stale["conversations"][0]["peer"]["online"] is False
    assert stale["conversations"][0]["id"] == conversation


def test_retention_deletes_only_messages_past_the_window(unit, as_user):
    first = make_user(unit=unit)
    second = make_user(unit=unit)
    conversation = conversation_id(as_user, first, second)
    client = as_user(first)
    old = send(client, conversation, "retire me").json()["id"]
    fresh = send(client, conversation, "keep me").json()["id"]
    Message.objects.filter(pk=old).update(
        created_at=timeutils.now() - dt.timedelta(days=90)
    )

    output = StringIO()
    call_command("purge_messages", "--days", "30", stdout=output)

    assert not Message.objects.filter(pk=old).exists()
    assert Message.objects.filter(pk=fresh).exists()
    audit = AuditLog.objects.filter(action="messaging_retention_purge").latest("id")
    assert audit.after["deleted"] == 1
    # The audit records the count, never the text.
    assert "retire me" not in audit.note


def test_pulse_counts_every_unread_message_not_just_one_per_thread(unit, as_user):
    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    conversation = conversation_id(as_user, sender, recipient)
    sender_client = as_user(sender)

    send(sender_client, conversation, "one")
    first = as_user(recipient).get("/api/notifications/pulse/?since=1").json()
    assert first["unread_messages"] == 1

    send(sender_client, conversation, "two")
    second = as_user(recipient).get("/api/notifications/pulse/?since=1").json()
    # The bell keeps a single notice for the thread, so ``items`` shows nothing
    # new; the message count is what tells the phone a second one arrived.
    assert second["unread"] == 1
    assert second["unread_messages"] == 2


def test_a_group_message_notifies_every_member_once(unit, as_user):
    from apps.messaging import services

    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    group = services.ensure_unit_group(sender, create=True)
    sender_client = as_user(sender)

    assert send(sender_client, group.id, "morning all").status_code in (200, 201)
    notice = Notification.objects.get(recipient=recipient, category="message")
    assert notice.related_type == "conversation"
    assert notice.related_id == str(group.id)
    assert "morning" not in notice.message

    # A second message in the same chat adds no row, but it is still counted.
    assert send(sender_client, group.id, "second").status_code in (200, 201)
    assert (
        Notification.objects.filter(recipient=recipient, category="message").count()
        == 1
    )
    pulse = as_user(recipient).get("/api/notifications/pulse/?since=1").json()
    assert pulse["unread_messages"] == 2

    # Reading the chat clears the notice, so the phone's count can reach zero.
    as_user(recipient).post(
        f"/api/messaging/conversations/{group.id}/read/", {}, format="json"
    )
    notice.refresh_from_db()
    assert notice.is_read is True


def test_a_mentioned_member_is_told_once_not_twice(unit, as_user):
    from apps.messaging import services

    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    group = services.ensure_unit_group(sender, create=True)

    response = as_user(sender).post(
        f"/api/messaging/conversations/{group.id}/messages/",
        {"body": "ping", "mention_ids": [recipient.id]},
        format="json",
    )
    assert response.status_code in (200, 201), response.content
    titles = list(
        Notification.objects.filter(recipient=recipient, category="message")
        .values_list("title", flat=True)
    )
    assert len(titles) == 1
    assert "mentioned" in titles[0]

"""Unit chat controls and the Super Administrator's private-messaging switch."""

from __future__ import annotations

import pytest

from apps.core.factories import make_user
from apps.messaging import services
from apps.messaging.models import ConversationMember, MessagingSettings

pytestmark = pytest.mark.django_db


def conversation_id(as_user, actor, other) -> int:
    response = as_user(actor).post(
        "/api/messaging/conversations/", {"user_id": other.id}, format="json"
    )
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


def unit_group(as_user, user):
    inbox = as_user(user).get("/api/messaging/conversations/")
    assert inbox.status_code == 200, inbox.content
    body = inbox.json()
    assert body["unit_group"] is not None, body
    return body["unit_group"]


def test_private_switch_fails_closed_when_settings_cannot_be_loaded(monkeypatch):
    def broken_load(cls):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(MessagingSettings, "load", classmethod(broken_load))

    assert services.private_messaging_enabled() is False


def test_private_switch_hides_and_blocks_dms_but_keeps_unit_chat(unit, as_user):
    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    conversation = conversation_id(as_user, sender, recipient)
    assert send(as_user(sender), conversation, "private hello").status_code == 201

    MessagingSettings.objects.update_or_create(
        pk=1, defaults={"private_messaging_enabled": False}
    )

    inbox = as_user(recipient).get("/api/messaging/conversations/").json()
    assert inbox["private_messaging_enabled"] is False
    assert inbox["conversations"] == []
    group = inbox["unit_group"]
    assert group is not None
    assert group["kind"] == "unit_group"

    recipient_client = as_user(recipient)
    assert recipient_client.get(messages_url(conversation)).status_code == 403
    assert send(recipient_client, conversation, "still private").status_code == 403
    assert (
        recipient_client.post(
            "/api/messaging/conversations/", {"user_id": sender.id}, format="json"
        ).status_code
        == 403
    )
    assert recipient_client.get("/api/messaging/people/").status_code == 403
    assert (
        recipient_client.get("/api/messaging/search/?q=private").json()["results"]
        == []
    )

    # The unit's own channel is not governed by the private-messaging switch.
    assert send(recipient_client, group["id"], "unit hello").status_code == 201
    seen = recipient_client.get(messages_url(group["id"]))
    assert seen.status_code == 200
    assert seen.json()["conversation"]["kind"] == "unit_group"
    assert [row["body"] for row in seen.json()["messages"]] == ["unit hello"]


def test_hiding_a_member_removes_group_from_inbox_and_search_and_blocks_open(
    unit, as_user, planner, operations_center
):
    member = make_user(unit=unit)
    planner_client = as_user(planner)
    group = unit_group(as_user, planner)
    assert send(planner_client, group["id"], "unit visible message").status_code == 201

    member_client = as_user(member)
    inbox = member_client.get("/api/messaging/conversations/").json()
    assert inbox["unit_group"]["id"] == group["id"]
    found = member_client.get("/api/messaging/search/?q=unit visible").json()["results"]
    assert [row["conversation_id"] for row in found] == [group["id"]]

    hidden = planner_client.patch(
        f"/api/messaging/unit-group/{group['id']}/members/{member.id}/",
        {"can_view": False},
        format="json",
    )
    assert hidden.status_code == 200, hidden.content
    assert hidden.json()["can_view"] is False
    # Hiding somebody necessarily also stops them posting.
    assert hidden.json()["can_post"] is False

    inbox = member_client.get("/api/messaging/conversations/").json()
    assert inbox["unit_group"] is None
    assert inbox["conversations"] == []
    assert (
        member_client.get("/api/messaging/search/?q=unit visible").json()["results"]
        == []
    )
    assert member_client.get(messages_url(group["id"])).status_code == 404
    assert send(member_client, group["id"], "hidden").status_code == 404


def test_reenabling_a_hidden_member_restores_group_and_operations_center_is_protected(
    unit, as_user, planner, operations_center, admin
):
    member = make_user(unit=unit)
    planner_client = as_user(planner)
    group = unit_group(as_user, planner)
    assert send(planner_client, group["id"], "welcome back").status_code == 201
    member_client = as_user(member)

    hidden = planner_client.patch(
        f"/api/messaging/unit-group/{group['id']}/members/{member.id}/",
        {"can_view": False},
        format="json",
    )
    assert hidden.status_code == 200, hidden.content
    assert member_client.get("/api/messaging/conversations/").json()["unit_group"] is None
    assert (
        member_client.get("/api/messaging/search/?q=welcome back").json()["results"]
        == []
    )

    restored = planner_client.patch(
        f"/api/messaging/unit-group/{group['id']}/members/{member.id}/",
        {"can_view": True, "can_post": True},
        format="json",
    )
    assert restored.status_code == 200, restored.content
    assert restored.json()["can_view"] is True
    assert restored.json()["can_post"] is True

    inbox = member_client.get("/api/messaging/conversations/").json()
    assert inbox["unit_group"]["id"] == group["id"]
    found = member_client.get("/api/messaging/search/?q=welcome back").json()["results"]
    assert [row["conversation_id"] for row in found] == [group["id"]]
    assert member_client.get(messages_url(group["id"])).status_code == 200

    # A different Administrator cannot lock out the Operations Center either;
    # the refusal must not create a restriction row. This proves the API
    # protects any Operations Center target, not only self-restriction.
    refused = as_user(admin).patch(
        f"/api/messaging/unit-group/{group['id']}/members/{planner.id}/",
        {"can_view": False},
        format="json",
    )
    assert refused.status_code == 400
    assert "keep their unit chat access" in refused.json()["detail"]
    assert not ConversationMember.objects.filter(
        conversation_id=group["id"], user_id=planner.id, can_view=False
    ).exists()
    # The Operations Center member kept both rights.
    protected = planner_client.get(f"/api/messaging/conversations/").json()["unit_group"]
    assert protected["id"] == group["id"]
    assert protected["can_post"] is True


def test_read_only_member_can_read_but_not_send(
    unit, as_user, planner, operations_center
):
    member = make_user(unit=unit)
    planner_client = as_user(planner)
    group = unit_group(as_user, planner)
    assert send(planner_client, group["id"], "briefing").status_code == 201

    changed = planner_client.patch(
        f"/api/messaging/unit-group/{group['id']}/members/{member.id}/",
        {"can_view": True, "can_post": False},
        format="json",
    )
    assert changed.status_code == 200, changed.content

    member_client = as_user(member)
    seen = member_client.get(messages_url(group["id"]))
    assert seen.status_code == 200
    assert seen.json()["conversation"]["can_post"] is False
    assert [row["body"] for row in seen.json()["messages"]] == ["briefing"]

    refused = send(member_client, group["id"], "not allowed")
    assert refused.status_code == 403
    assert "permission to post" in str(refused.json()["detail"])

    # The Operations Center keeps its own posting right.
    assert send(planner_client, group["id"], "acknowledged").status_code == 201


def test_only_the_super_administrator_can_change_the_private_switch(
    unit, as_user, seat_admin, creator
):
    ordinary_admin = make_user(unit=unit, authority="Administrator")
    payload = {"private_messaging_enabled": False}

    for user in (ordinary_admin, creator):
        refused = as_user(user).patch(
            "/api/messaging/settings/", payload, format="json"
        )
        assert refused.status_code == 403
    # A refused request must not have changed the stored switch either.
    assert MessagingSettings.load().private_messaging_enabled is True

    changed = as_user(seat_admin).patch(
        "/api/messaging/settings/", payload, format="json"
    )
    assert changed.status_code == 200, changed.content
    assert changed.json()["private_messaging_enabled"] is False
    assert MessagingSettings.load().private_messaging_enabled is False


def test_an_account_from_another_unit_cannot_open_or_read_the_unit_group(
    as_user, creator, other_unit
):
    # Even an Administrator of another unit is still an outsider here.
    outsider = make_user(unit=other_unit, authority="Administrator")
    creator_client = as_user(creator)
    group = unit_group(as_user, creator)
    assert send(creator_client, group["id"], "ALPHA only").status_code == 201

    outsider_client = as_user(outsider)
    assert outsider_client.get(messages_url(group["id"])).status_code == 404
    assert send(outsider_client, group["id"], "intruding").status_code == 404

    # The outsider may only ever see their own unit's channel, never this one.
    outsider_group = outsider_client.get("/api/messaging/conversations/").json()[
        "unit_group"
    ]
    assert outsider_group is not None
    assert outsider_group["id"] != group["id"]
    assert outsider_group["unit_acronym"] == other_unit.acronym
    assert (
        outsider_client.get(
            "/api/messaging/search/?q=ALPHA only"
        ).json()["results"]
        == []
    )


def test_non_admins_cannot_read_the_unit_roster(
    unit, as_user, planner, operations_center
):
    member = make_user(unit=unit)

    assert (
        as_user(member).get("/api/messaging/unit-group/members/").status_code == 403
    )

    allowed = as_user(planner).get("/api/messaging/unit-group/members/")
    assert allowed.status_code == 200, allowed.content
    rows = allowed.json()["members"]
    assert any(row["id"] == member.id for row in rows)
    assert any(row["is_admin"] for row in rows)
    assert any(not row["is_admin"] for row in rows)

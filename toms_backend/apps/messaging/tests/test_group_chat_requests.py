"""Approval routing for additional unit group chats."""

import pytest

from apps.messaging.models import (
    Conversation,
    GroupChatRequest,
    GroupChatRequestEvent,
    GroupChatRequestMember,
)

pytestmark = pytest.mark.django_db

REQUESTS = "/api/messaging/group-chat-requests/"


def create_request(client, name="Watch handover", members=()):
    return client.post(
        REQUESTS,
        {"name": name, "member_ids": [person.id for person in members]},
        format="json",
    )


def test_oc_routes_to_commander_then_super_administrator_and_creates_group(
    as_user, planner, commander, seat_admin, creator, evaluator, unit
):
    response = create_request(as_user(planner), members=[creator])
    assert response.status_code == 201, response.content
    request_id = response.json()["id"]
    assert response.json()["status"] == "pending_commander"
    assert response.json()["can_approve"] is False

    commander_client = as_user(commander)
    response = commander_client.post(f"{REQUESTS}{request_id}/approve/", {}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "pending_super_admin"

    # The Commander cannot give the Super Administrator's final approval.
    blocked = commander_client.post(f"{REQUESTS}{request_id}/approve/", {}, format="json")
    assert blocked.status_code == 400
    assert GroupChatRequest.objects.get(pk=request_id).status == "pending_super_admin"

    response = as_user(seat_admin).post(
        f"{REQUESTS}{request_id}/approve/", {}, format="json"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "approved"
    assert body["conversation_id"]

    conversation = Conversation.objects.get(pk=body["conversation_id"])
    assert conversation.kind == Conversation.KIND_CUSTOM_GROUP
    assert conversation.name == "Watch handover"
    assert conversation.unit_id == unit.id
    assert list(conversation.members.values_list("user_id", flat=True)) == [creator.id]

    inbox = as_user(creator).get("/api/messaging/conversations/").json()
    assert [row["id"] for row in inbox["custom_groups"]] == [conversation.id]
    assert (
        as_user(creator)
        .get(f"/api/messaging/conversations/{conversation.id}/messages/")
        .status_code
        == 200
    )
    assert (
        as_user(creator)
        .post(
            f"/api/messaging/conversations/{conversation.id}/messages/",
            {"body": "Selected personnel can post here."},
            format="json",
        )
        .status_code
        == 201
    )
    assert (
        as_user(evaluator)
        .post(
            f"/api/messaging/conversations/{conversation.id}/messages/",
            {"body": "Not selected."},
            format="json",
        )
        .status_code
        == 404
    )


def test_super_administrator_can_disable_group_or_only_its_chat(
    as_user, planner, commander, seat_admin, creator, evaluator
):
    request_id = create_request(as_user(planner), members=[creator]).json()["id"]
    as_user(commander).post(f"{REQUESTS}{request_id}/approve/", {}, format="json")
    approved = as_user(seat_admin).post(
        f"{REQUESTS}{request_id}/approve/", {}, format="json"
    ).json()
    conversation_id = approved["conversation_id"]

    oversight = as_user(seat_admin).get("/api/messaging/admin/group-chats/").json()
    assert [row["id"] for row in oversight["groups"]] == [conversation_id]

    disabled = as_user(seat_admin).patch(
        f"/api/messaging/admin/group-chats/{conversation_id}/",
        {"is_enabled": False},
        format="json",
    )
    assert disabled.status_code == 200
    inbox = as_user(creator).get("/api/messaging/conversations/").json()
    assert conversation_id not in [row["id"] for row in inbox["custom_groups"]]
    assert (
        as_user(creator)
        .get(f"/api/messaging/conversations/{conversation_id}/messages/")
        .status_code
        == 404
    )

    # Re-enable the group but leave chat off: readable, not writable.
    as_user(seat_admin).patch(
        f"/api/messaging/admin/group-chats/{conversation_id}/",
        {"is_enabled": True, "chat_enabled": False},
        format="json",
    )
    inbox = as_user(creator).get("/api/messaging/conversations/").json()
    card = next(row for row in inbox["custom_groups"] if row["id"] == conversation_id)
    assert card["can_post"] is False
    assert (
        as_user(creator)
        .post(
            f"/api/messaging/conversations/{conversation_id}/messages/",
            {"body": "blocked"},
            format="json",
        )
        .status_code
        == 403
    )
    assert (
        as_user(evaluator)
        .get(f"/api/messaging/conversations/{conversation_id}/messages/")
        .status_code
        == 404
    )


def test_only_the_operations_center_can_request(as_user, creator, commander):
    assert create_request(as_user(creator), members=[commander]).status_code == 400
    assert create_request(as_user(commander), members=[creator]).status_code == 400


def test_routing_is_a_messages_thread_with_every_decision(
    as_user, planner, commander, seat_admin, creator
):
    response = create_request(as_user(planner), members=[creator])
    assert response.status_code == 201, response.content
    request_id = response.json()["id"]
    thread_id = response.json()["thread_id"]
    assert thread_id

    thread = Conversation.objects.get(pk=thread_id)
    assert thread.kind == Conversation.KIND_GROUP_REQUEST
    # The proposer and both authorities follow the routing; selected personnel
    # are what they are deciding about, so they are not participants.
    assert set(thread.members.values_list("user_id", flat=True)) == {
        planner.id,
        commander.id,
        seat_admin.id,
    }

    def bodies(client):
        data = client.get(
            f"/api/messaging/conversations/{thread_id}/messages/"
        ).json()
        return [row["body"] for row in data["messages"]]

    assert "Proposed group chat" in bodies(as_user(planner))[0]
    assert (
        as_user(creator)
        .get(f"/api/messaging/conversations/{thread_id}/messages/")
        .status_code
        == 404
    )

    # Opening the conversation by id returns the routing card, not a private
    # thread shape, so the Messages view can render it read-only.
    opened = (
        as_user(commander)
        .get(f"/api/messaging/conversations/{thread_id}/messages/")
        .json()["conversation"]
    )
    assert opened["kind"] == "group_request"
    assert opened["request_id"] == request_id
    assert opened["status"] == "pending_commander"
    assert opened["can_approve"] is True
    assert opened["can_post"] is False

    inbox = as_user(commander).get("/api/messaging/conversations/").json()
    card = next(row for row in inbox["routing_threads"] if row["id"] == thread_id)
    assert card["kind"] == "group_request"
    assert card["request_id"] == request_id
    assert card["can_approve"] is True
    assert card["can_post"] is False

    # The thread is a routing record, not a chat: a free-text post is refused.
    assert (
        as_user(commander)
        .post(
            f"/api/messaging/conversations/{thread_id}/messages/",
            {"body": "approved verbally"},
            format="json",
        )
        .status_code
        == 403
    )

    assert (
        as_user(commander)
        .post(f"{REQUESTS}{request_id}/approve/", {}, format="json")
        .status_code
        == 200
    )
    commander_stage = bodies(as_user(commander))
    assert any(
        "Approved by the Unit Commander" in body and "Administrator" in body
        for body in commander_stage
    )

    assert (
        as_user(seat_admin)
        .post(f"{REQUESTS}{request_id}/approve/", {}, format="json")
        .status_code
        == 200
    )
    final_stage = bodies(as_user(seat_admin))
    assert any("Unit Commander" in body for body in final_stage)
    assert any("Final approval given" in body for body in final_stage)
    assert "Rejected" not in " ".join(final_stage)


def test_approved_group_is_found_by_search_only_for_selected_members(
    as_user, planner, commander, seat_admin, creator, evaluator
):
    request_id = create_request(as_user(planner), members=[creator]).json()["id"]
    as_user(commander).post(f"{REQUESTS}{request_id}/approve/", {}, format="json")
    approved = as_user(seat_admin).post(
        f"{REQUESTS}{request_id}/approve/", {}, format="json"
    ).json()
    conversation_id = approved["conversation_id"]

    assert (
        as_user(creator)
        .post(
            f"/api/messaging/conversations/{conversation_id}/messages/",
            {"body": "zebracheck handover note"},
            format="json",
        )
        .status_code
        == 201
    )

    found = as_user(creator).get("/api/messaging/search/?q=zebracheck").json()
    assert [row["conversation_id"] for row in found["results"]] == [conversation_id]

    # A same-unit person who was not selected cannot find it, even though the
    # unit channel itself is searchable for them.
    not_found = as_user(evaluator).get("/api/messaging/search/?q=zebracheck").json()
    assert not_found["results"] == []
    assert (
        as_user(evaluator)
        .get(f"/api/messaging/conversations/{conversation_id}/messages/")
        .status_code
        == 404
    )


def test_rejection_at_each_stage_leaves_no_group_behind(
    as_user, planner, commander, seat_admin, creator
):
    # Stage 1: the Unit Commander rejects, nothing is created.
    first = create_request(
        as_user(planner), name="Commander rejected", members=[creator]
    ).json()
    rejected = as_user(commander).post(
        f"{REQUESTS}{first['id']}/reject/", {}, format="json"
    )
    assert rejected.status_code == 200, rejected.content
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["conversation_id"] is None
    assert not Conversation.objects.filter(
        kind=Conversation.KIND_CUSTOM_GROUP, name="Commander rejected"
    ).exists()

    # Stage 2: the Commander approves, the Super Administrator rejects.
    second = create_request(
        as_user(planner), name="Administrator rejected", members=[creator]
    ).json()
    assert (
        as_user(commander)
        .post(f"{REQUESTS}{second['id']}/approve/", {}, format="json")
        .status_code
        == 200
    )
    rejected = as_user(seat_admin).post(
        f"{REQUESTS}{second['id']}/reject/", {}, format="json"
    )
    assert rejected.status_code == 200, rejected.content
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["conversation_id"] is None
    assert not Conversation.objects.filter(
        kind=Conversation.KIND_CUSTOM_GROUP, name="Administrator rejected"
    ).exists()

    # Neither rejection left a custom group for the selected person.
    assert not Conversation.objects.filter(kind=Conversation.KIND_CUSTOM_GROUP).exists()
    inbox = as_user(creator).get("/api/messaging/conversations/").json()
    assert inbox["custom_groups"] == []


def run_thread_backfill():
    """Apply the data migration that threads requests created before it."""
    import importlib

    from django.apps import apps as django_apps

    module = importlib.import_module(
        "apps.messaging.migrations.0007_backfill_group_chat_routing_threads"
    )
    module.backfill_routing_threads(django_apps, None)


def test_earlier_requests_get_a_routing_thread_with_their_history(
    as_user, planner, commander, seat_admin, creator, unit
):
    # A request as the side-panel workflow stored it: events, no thread.
    legacy = GroupChatRequest.objects.create(
        unit=unit,
        name="Old watch handover",
        created_by=planner,
        status=GroupChatRequest.STATUS_PENDING_SUPER_ADMIN,
        commander_acted_by=commander,
    )
    GroupChatRequestMember.objects.create(request=legacy, user=creator)
    GroupChatRequestEvent.objects.create(
        request=legacy,
        actor=planner,
        action=GroupChatRequestEvent.ACTION_SUBMITTED,
        note="Proposed for 1 selected personnel.",
    )
    GroupChatRequestEvent.objects.create(
        request=legacy,
        actor=commander,
        action=GroupChatRequestEvent.ACTION_COMMANDER_APPROVED,
    )

    run_thread_backfill()

    legacy.refresh_from_db()
    assert legacy.thread_id
    thread = legacy.thread
    assert thread.kind == Conversation.KIND_GROUP_REQUEST
    assert thread.name == "Group chat request: Old watch handover"
    assert thread.chat_enabled is False
    assert thread.unit_id == unit.id

    members = set(thread.members.values_list("user_id", flat=True))
    assert members == {planner.id, commander.id, seat_admin.id}
    assert creator.id not in members

    # The stored submission and approval are now the thread's messages.
    history = as_user(commander).get(
        f"/api/messaging/conversations/{thread.id}/messages/"
    )
    assert history.status_code == 200, history.content
    bodies = [row["body"] for row in history.json()["messages"]]
    assert len(bodies) == 2
    assert bodies[0].startswith("Proposed group chat “Old watch handover”")
    assert "Approved by the Unit Commander" in bodies[1]

    # It is an inbox thread, with the pending decision still actionable.
    inbox = as_user(seat_admin).get("/api/messaging/conversations/").json()
    card = next(
        row for row in inbox["routing_threads"] if row["id"] == thread.id
    )
    assert card["request_id"] == legacy.id
    assert card["can_approve"] is True
    assert (
        thread.id
        in [
            row["id"]
            for row in as_user(planner)
            .get("/api/messaging/conversations/")
            .json()["routing_threads"]
        ]
    )

    # The selection is what the thread decides about, not a participant.
    assert (
        as_user(creator)
        .get(f"/api/messaging/conversations/{thread.id}/messages/")
        .status_code
        == 404
    )

    # Running it again does not duplicate the thread or its messages.
    run_thread_backfill()
    assert Conversation.objects.filter(kind=Conversation.KIND_GROUP_REQUEST).count() == 1
    assert legacy.thread.messages.count() == 2

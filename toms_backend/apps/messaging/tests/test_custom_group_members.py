"""The Members panel for an approved group chat."""

from __future__ import annotations

import pytest

from apps.core.factories import make_user
from apps.messaging.models import Conversation, ConversationMember

pytestmark = pytest.mark.django_db


def approved_group(unit, owner, *members) -> Conversation:
    conversation = Conversation.objects.create(
        kind=Conversation.KIND_CUSTOM_GROUP,
        unit=unit,
        name="TOG10 Officers GC",
        created_by=owner,
        chat_enabled=True,
        is_enabled=True,
    )
    for person in (owner, *members):
        ConversationMember.objects.create(
            conversation=conversation, user=person, can_view=True, can_post=True
        )
    return conversation


def members_url(conversation) -> str:
    return f"/api/messaging/conversations/{conversation.id}/members/"


def test_a_member_of_an_approved_group_sees_the_roster(unit, as_user):
    owner = make_user(unit=unit)
    mate = make_user(unit=unit)
    group = approved_group(unit, owner, mate)

    response = as_user(mate).get(members_url(group))

    assert response.status_code == 200, response.content
    rows = response.json()["members"]
    assert {row["id"] for row in rows} == {owner.id, mate.id}
    by_id = {row["id"]: row for row in rows}
    assert by_id[mate.id]["is_self"] is True
    assert by_id[owner.id]["is_self"] is False
    assert by_id[owner.id]["full_name"] == f"{owner.rank} {owner.last_name}"


def test_an_inactive_member_is_left_out_of_the_roster(unit, as_user):
    owner = make_user(unit=unit)
    mate = make_user(unit=unit)
    gone = make_user(unit=unit)
    group = approved_group(unit, owner, mate, gone)
    gone.is_active = False
    gone.save(update_fields=["is_active"])

    rows = as_user(mate).get(members_url(group)).json()["members"]

    assert {row["id"] for row in rows} == {owner.id, mate.id}


def test_a_non_member_cannot_read_the_group_roster(unit, as_user):
    owner = make_user(unit=unit)
    mate = make_user(unit=unit)
    outsider = make_user(unit=unit)
    group = approved_group(unit, owner, mate)

    response = as_user(outsider).get(members_url(group))

    assert response.status_code == 404


def test_a_unit_member_can_read_the_channel_roster_without_controls(
    unit, as_user, planner
):
    # The unit channel keeps its admin endpoint for permissions, but every
    # member who can see the channel may read who is in it.
    group = as_user(planner).get("/api/messaging/conversations/").json()["unit_group"]
    member = make_user(unit=unit, rank="AM")

    response = as_user(member).get(
        f"/api/messaging/conversations/{group['id']}/members/"
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["can_manage"] is False
    assert any(row["id"] == member.id for row in body["members"])
    assert all(row["can_view"] for row in body["members"])

    managed = as_user(planner).get(
        f"/api/messaging/conversations/{group['id']}/members/"
    )
    assert managed.status_code == 200, managed.content
    assert managed.json()["can_manage"] is True


def test_unit_rosters_list_the_highest_rank_first(unit, as_user, planner):
    senior = make_user(unit=unit, rank="COL")
    junior = make_user(unit=unit, rank="AM")
    group = as_user(planner).get("/api/messaging/conversations/").json()["unit_group"]

    # The Members panel and the New group chat picker share this ordering.
    for url in (
        f"/api/messaging/conversations/{group['id']}/members/",
        "/api/messaging/unit-group/members/",
    ):
        response = as_user(planner).get(url)
        assert response.status_code == 200, response.content
        ids = [row["id"] for row in response.json()["members"]]
        assert ids.index(senior.id) < ids.index(junior.id)


def test_an_approved_group_roster_lists_the_highest_rank_first(unit, as_user):
    junior = make_user(unit=unit, rank="A2C")
    senior = make_user(unit=unit, rank="MGEN")
    group = approved_group(unit, junior, senior)

    rows = as_user(junior).get(members_url(group)).json()["members"]

    assert [row["id"] for row in rows] == [senior.id, junior.id]

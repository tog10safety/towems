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
    assert by_id[owner.id]["full_name"] == owner.full_name


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


def test_the_unit_channel_keeps_its_admin_roster(unit, as_user, planner):
    # The unit roster has its own endpoint because it carries permissions; the
    # read-only conversation roster must not replace it.
    group = as_user(planner).get("/api/messaging/conversations/").json()["unit_group"]

    response = as_user(planner).get(
        f"/api/messaging/conversations/{group['id']}/members/"
    )

    assert response.status_code == 400

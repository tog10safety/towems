"""Approval routing for additional unit group chats.

The Operations Center proposes a named group and selects unit personnel. The
request is routed to the active Unit Commander, then to the Super Administrator.
Only the final approval creates the conversation, so no group exists until both
authorities have decided. The request's routing thread in Messages is the single
record: the proposal and every decision are posted there.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from apps.accounts import ownership
from apps.accounts.models import User, UserProfile
from apps.command.models import UnitAppointment
from apps.core import timeutils
from apps.messaging.models import (
    Conversation,
    ConversationMember,
    GroupChatRequest,
    GroupChatRequestEvent,
    GroupChatRequestMember,
)
from apps.messaging import services


class GroupChatError(services.MessagingError):
    """A refusal written for the person who tried the action."""


def _super_administrator():
    profile = (
        UserProfile.objects.filter(is_system_owner=True)
        .select_related("user")
        .order_by("user_id")
        .first()
    )
    return profile.user if profile and profile.user.is_active else None


def _notify(
    user, *, title, message, actor, request_row, requires_action=True
):
    from apps.ops.notifications import notify

    return notify(
        user,
        title=title,
        message=message,
        category="group_chat",
        related=("group_chat_request", request_row.id),
        actor=actor,
        requires_action=requires_action,
        action_label="Review group chat request" if requires_action else "",
        action_url=(
            f"/messages?conversation={request_row.thread_id}"
            if request_row.thread_id
            else "/messages"
        ),
    )


def _post_decision(request_row, actor, summary, note=""):
    """Append one decision to the request's Messages thread."""
    thread = request_row.thread
    if thread is None:
        return
    text = summary
    clean_note = (note or "").strip()
    if clean_note:
        text = f"{text}\n\nNote: {clean_note}"
    services.send_message(thread, actor, text)


def _close_action(request_row, actor, note=""):
    from apps.ops.notifications import complete_action

    complete_action(
        related_type="group_chat_request",
        related_ids=request_row.id,
        note=note,
        actor=actor,
    )


def _selected_people(request_row):
    return User.objects.filter(
        messaging_group_chat_selections__request=request_row
    ).select_related("unit", "profile")


@transaction.atomic
def create_request(actor, *, name, member_ids, request=None):
    if not services.is_operations_center_member(actor):
        raise GroupChatError(
            "Only this unit's Operations Center can propose another group chat."
        )
    clean_name = (name or "").strip()
    if len(clean_name) < 3:
        raise GroupChatError("Give the group chat a name of at least three characters.")
    if len(clean_name) > 120:
        raise GroupChatError("A group chat name may be at most 120 characters.")

    commander = services.unit_commander(actor.unit_id)
    if commander is None:
        raise GroupChatError(
            "This unit has no active Unit Commander, so the request cannot be routed."
        )
    owner = _super_administrator()
    if owner is None:
        raise GroupChatError(
            "There is no active Administrator to receive the final approval."
        )

    wanted = {int(pk) for pk in (member_ids or []) if str(pk).isdigit()}
    if not wanted:
        raise GroupChatError("Select at least one person for the group chat.")
    people = list(
        User.objects.filter(
            id__in=wanted, is_active=True, unit_id=actor.unit_id
        ).select_related("unit", "profile")
    )
    if len(people) != len(wanted):
        raise GroupChatError(
            "Every selected person must be an active member of this unit."
        )

    row = GroupChatRequest.objects.create(
        unit_id=actor.unit_id,
        name=clean_name,
        created_by=actor,
        status=GroupChatRequest.STATUS_PENDING_COMMANDER,
    )
    GroupChatRequestMember.objects.bulk_create(
        [GroupChatRequestMember(request=row, user=person) for person in people]
    )
    GroupChatRequestEvent.objects.create(
        request=row,
        actor=actor,
        action=GroupChatRequestEvent.ACTION_SUBMITTED,
        note=f"Proposed for {len(people)} selected personnel.",
    )

    # The routing record is a real Messages thread. The proposal and every
    # decision are messages in it, so the authorities can follow and decide the
    # request where they already read their messages.
    thread = Conversation.objects.create(
        kind=Conversation.KIND_GROUP_REQUEST,
        unit_id=actor.unit_id,
        name=f"Group chat request: {clean_name}",
        created_by=actor,
        chat_enabled=False,
    )
    participants = {person.id: person for person in (actor, commander.user, owner)}
    ConversationMember.objects.bulk_create(
        [
            ConversationMember(conversation=thread, user=person)
            for person in participants.values()
        ],
        ignore_conflicts=True,
    )
    row.thread = thread
    row.save(update_fields=["thread"])
    services.send_message(
        thread,
        actor,
        (
            f"Proposed group chat “{clean_name}” for {len(people)} personnel: "
            f"{', '.join(services._display_name(person) for person in people)}.\n\n"
            "Routed to the Unit Commander, then an Administrator."
        ),
    )
    _notify(
        commander.user,
        title=f"Group chat request: {row.name}",
        message=(
            f"{services._display_name(actor)} proposed a group chat for "
            f"{row.unit.acronym or row.unit.name if row.unit else 'the unit'}. "
            "Approve it to route it to an Administrator."
        ),
        actor=actor,
        request_row=row,
    )
    return row


def _commander_for_request(request_row):
    return services.unit_commander(request_row.unit_id)


@transaction.atomic
def approve(request_row, actor, *, note="", request=None):
    if request_row.status == GroupChatRequest.STATUS_PENDING_COMMANDER:
        commander = _commander_for_request(request_row)
        if commander is None or commander.user_id != actor.id:
            raise GroupChatError(
                "Only this unit's Unit Commander can approve this request."
            )
        request_row.status = GroupChatRequest.STATUS_PENDING_SUPER_ADMIN
        request_row.commander_acted_by = actor
        request_row.commander_acted_at = timeutils.now()
        request_row.save(
            update_fields=[
                "status",
                "commander_acted_by",
                "commander_acted_at",
                "updated_at",
            ]
        )
        GroupChatRequestEvent.objects.create(
            request=request_row,
            actor=actor,
            action=GroupChatRequestEvent.ACTION_COMMANDER_APPROVED,
            note=(note or "").strip(),
        )
        _post_decision(
            request_row,
            actor,
            "Approved by the Unit Commander — routed to an Administrator "
            "for final approval.",
            note,
        )
        _close_action(request_row, actor, note=note)
        owner = _super_administrator()
        _notify(
            owner,
            title=f"Group chat request: {request_row.name}",
            message=(
                "The Unit Commander approved this request. It now needs an "
                "Administrator's final approval."
            ),
            actor=actor,
            request_row=request_row,
        )
        return request_row

    if request_row.status == GroupChatRequest.STATUS_PENDING_SUPER_ADMIN:
        if not ownership.is_system_owner(actor):
            raise GroupChatError(
                "Only an Administrator can give final approval."
            )
        selected = [
            person
            for person in _selected_people(request_row)
            if person.is_active and person.unit_id == request_row.unit_id
        ]
        if not selected:
            raise GroupChatError(
                "None of the selected personnel are still active in this unit."
            )
        conversation = Conversation.objects.create(
            kind=Conversation.KIND_CUSTOM_GROUP,
            unit_id=request_row.unit_id,
            name=request_row.name,
            created_by=request_row.created_by,
            is_enabled=True,
            chat_enabled=True,
        )
        ConversationMember.objects.bulk_create(
            [
                ConversationMember(conversation=conversation, user=person)
                for person in selected
            ],
            ignore_conflicts=True,
        )
        request_row.conversation = conversation
        request_row.status = GroupChatRequest.STATUS_APPROVED
        request_row.super_admin_acted_by = actor
        request_row.super_admin_acted_at = timeutils.now()
        request_row.decision_note = (note or "").strip()
        request_row.save(
            update_fields=[
                "conversation",
                "status",
                "super_admin_acted_by",
                "super_admin_acted_at",
                "decision_note",
                "updated_at",
            ]
        )
        GroupChatRequestEvent.objects.create(
            request=request_row,
            actor=actor,
            action=GroupChatRequestEvent.ACTION_SUPER_ADMIN_APPROVED,
            note=(note or "").strip(),
        )
        _post_decision(
            request_row,
            actor,
            f"Final approval given — group chat “{request_row.name}” created "
            f"for {len(selected)} personnel.",
            note,
        )
        _close_action(request_row, actor, note=note)
        from apps.ops.notifications import notify_many

        # Only selected personnel can open the new group; the requester sees the
        # approved status on the routing record even if not selected.
        recipients = list(selected)
        notify_many(
            recipients,
            title=f"Group chat approved: {request_row.name}",
            message="The group chat is now available in Messages.",
            category="group_chat",
            related=("group_chat_request", request_row.id),
            actor=actor,
            action_url=f"/messages?conversation={conversation.id}",
        )
        return request_row

    raise GroupChatError("This request has already been decided.")


@transaction.atomic
def reject(request_row, actor, *, note="", request=None):
    commander = _commander_for_request(request_row)
    if request_row.status == GroupChatRequest.STATUS_PENDING_COMMANDER:
        if commander is None or commander.user_id != actor.id:
            raise GroupChatError(
                "Only this unit's Unit Commander can reject this request."
            )
        request_row.commander_acted_by = actor
        request_row.commander_acted_at = timeutils.now()
    elif request_row.status == GroupChatRequest.STATUS_PENDING_SUPER_ADMIN:
        if not ownership.is_system_owner(actor):
            raise GroupChatError(
                "Only an Administrator can reject this request."
            )
        request_row.super_admin_acted_by = actor
        request_row.super_admin_acted_at = timeutils.now()
    else:
        raise GroupChatError("This request has already been decided.")

    request_row.status = GroupChatRequest.STATUS_REJECTED
    request_row.decision_note = (note or "").strip()
    request_row.save(
        update_fields=[
            "status",
            "commander_acted_by",
            "commander_acted_at",
            "super_admin_acted_by",
            "super_admin_acted_at",
            "decision_note",
            "updated_at",
        ]
    )
    GroupChatRequestEvent.objects.create(
        request=request_row,
        actor=actor,
        action=GroupChatRequestEvent.ACTION_REJECTED,
        note=(note or "").strip(),
    )
    rejected_by = (
        "Administrator"
        if request_row.super_admin_acted_by_id is not None
        else "Unit Commander"
    )
    _post_decision(request_row, actor, f"Rejected by the {rejected_by}.", note)
    _close_action(request_row, actor, note=note)
    _notify(
        request_row.created_by,
        title=f"Group chat request rejected: {request_row.name}",
        message=(note or "").strip() or "The request was rejected.",
        actor=actor,
        request_row=request_row,
        requires_action=False,
    )
    return request_row


def requests_for(user):
    queryset = GroupChatRequest.objects.select_related(
        "unit",
        "created_by",
        "commander_acted_by",
        "super_admin_acted_by",
        "conversation",
        "thread",
    ).prefetch_related(
        "selected_members__user",
    )
    if ownership.is_system_owner(user):
        return queryset
    scope = Q(created_by=user)
    if getattr(user, "unit_id", None) is not None:
        if services.is_operations_center_member(user):
            scope |= Q(unit_id=user.unit_id)
        command_units = UnitAppointment.objects.filter(
            user=user, role="commander", is_active=True
        ).values_list("unit_id", flat=True)
        scope |= Q(unit_id__in=command_units)
    return queryset.filter(scope).distinct()


def can_approve(request_row, user) -> bool:
    if request_row.status == GroupChatRequest.STATUS_PENDING_COMMANDER:
        return services.is_unit_commander(user, request_row.unit_id)
    if request_row.status == GroupChatRequest.STATUS_PENDING_SUPER_ADMIN:
        return ownership.is_system_owner(user)
    return False


def can_create(user) -> bool:
    return services.is_operations_center_member(user)


# ---------------------------------------------------------------------------
# Administrator oversight
# ---------------------------------------------------------------------------


def custom_groups_for_settings():
    return (
        Conversation.objects.filter(kind=Conversation.KIND_CUSTOM_GROUP)
        .select_related("unit")
        .order_by("unit__acronym", "name", "id")
    )


@transaction.atomic
def update_custom_group(conversation, actor, *, is_enabled=None, chat_enabled=None):
    from apps.core.audit import log_action

    if not ownership.is_system_owner(actor):
        raise GroupChatError(
            "Only an Administrator can change a group chat."
        )
    if conversation is None or not conversation.is_custom_group():
        raise GroupChatError("That is not an approved group chat.")
    before = {
        "is_enabled": conversation.is_enabled,
        "chat_enabled": conversation.chat_enabled,
    }
    if is_enabled is not None:
        conversation.is_enabled = bool(is_enabled)
    if chat_enabled is not None:
        conversation.chat_enabled = bool(chat_enabled)
    conversation.save(update_fields=["is_enabled", "chat_enabled", "updated_at"])
    log_action(
        actor=actor,
        action="update_custom_group",
        entity_type="conversation",
        entity_id=conversation.id,
        before=before,
        after={
            "is_enabled": conversation.is_enabled,
            "chat_enabled": conversation.chat_enabled,
        },
    )
    return conversation

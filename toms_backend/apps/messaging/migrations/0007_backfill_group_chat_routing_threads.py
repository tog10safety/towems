"""Backfill Messages routing threads for group chat requests made earlier.

The routing thread became the record of a request after the workflow first
shipped as a side panel. Requests created before that have no thread, so their
submission and decisions exist only as event rows. This migration gives each of
them the same thread a new request gets: the proposer, the reviewing Unit
Commander and the Super Administrator as participants, with the stored events posted
as the thread's messages so they appear in the inbox.
"""

from __future__ import annotations

from django.db import migrations

SUBMITTED = "submitted"
COMMANDER_APPROVED = "commander_approved"
SUPER_ADMIN_APPROVED = "super_admin_approved"
REJECTED = "rejected"


def _display_name(user) -> str:
    """The name the live workflow shows, without relying on model properties."""
    full = " ".join(
        part
        for part in (getattr(user, "first_name", ""), getattr(user, "last_name", ""))
        if part
    ).strip()
    return full or getattr(user, "username", "") or "TOWEMS"


def _submission_body(row, names) -> str:
    listed = ", ".join(names) if names else "no selected personnel"
    return (
        f"Proposed group chat “{row.name}” for {len(names)} personnel: {listed}.\n\n"
        "Routed to the Unit Commander, then an Administrator."
    )


def _event_body(row, event, names) -> str:
    if event.action == SUBMITTED:
        body = _submission_body(row, names)
    elif event.action == COMMANDER_APPROVED:
        body = (
            "Approved by the Unit Commander — routed to an Administrator "
            "for final approval."
        )
    elif event.action == SUPER_ADMIN_APPROVED:
        body = (
            f"Final approval given — group chat “{row.name}” created for "
            f"{len(names)} personnel."
        )
    elif event.action == REJECTED:
        rejected_by = (
            "Administrator" if row.super_admin_acted_by_id else "Unit Commander"
        )
        body = f"Rejected by the {rejected_by}."
    else:
        body = f"Routing update: {str(event.action).replace('_', ' ')}."
    note = (event.note or "").strip()
    if note:
        body = f"{body}\n\nNote: {note}"
    return body


def _current_commander_id(UnitAppointment, unit_id):
    if unit_id is None:
        return None
    return (
        UnitAppointment.objects.filter(
            unit_id=unit_id, role="commander", is_active=True, user__is_active=True
        )
        .order_by("id")
        .values_list("user_id", flat=True)
        .first()
    )


def _current_owner_id(UserProfile):
    return (
        UserProfile.objects.filter(is_system_owner=True, user__is_active=True)
        .order_by("user_id")
        .values_list("user_id", flat=True)
        .first()
    )


def backfill_routing_threads(apps, schema_editor):
    Conversation = apps.get_model("messaging", "Conversation")
    ConversationMember = apps.get_model("messaging", "ConversationMember")
    GroupChatRequest = apps.get_model("messaging", "GroupChatRequest")
    GroupChatRequestEvent = apps.get_model("messaging", "GroupChatRequestEvent")
    GroupChatRequestMember = apps.get_model("messaging", "GroupChatRequestMember")
    Message = apps.get_model("messaging", "Message")
    User = apps.get_model("accounts", "User")
    UserProfile = apps.get_model("accounts", "UserProfile")
    UnitAppointment = apps.get_model("command", "UnitAppointment")

    rows = GroupChatRequest.objects.filter(thread__isnull=True).order_by(
        "created_at", "id"
    )
    for row in rows:
        events = list(
            GroupChatRequestEvent.objects.filter(request_id=row.id).order_by(
                "created_at", "id"
            )
        )
        selection_ids = list(
            GroupChatRequestMember.objects.filter(request_id=row.id)
            .order_by("id")
            .values_list("user_id", flat=True)
        )
        people = User.objects.in_bulk(selection_ids)
        names = [_display_name(people[pk]) for pk in selection_ids if pk in people]

        first_at = events[0].created_at if events else row.created_at
        thread = Conversation.objects.create(
            kind="group_request",
            unit_id=row.unit_id,
            name=f"Group chat request: {row.name}"[:120],
            created_by_id=row.created_by_id,
            chat_enabled=False,
            created_at=first_at,
        )

        # The same participants a new request gets: the proposer and the two
        # authorities. An authority who already acted is taken from the request
        # so an old decision is not reposted to whoever holds the post today.
        participant_ids = []

        def add(user_id):
            if user_id and user_id not in participant_ids:
                participant_ids.append(user_id)

        add(row.created_by_id)
        for event in events:
            add(event.actor_id)
        add(
            row.commander_acted_by_id
            or _current_commander_id(UnitAppointment, row.unit_id)
        )
        add(row.super_admin_acted_by_id or _current_owner_id(UserProfile))

        ConversationMember.objects.bulk_create(
            [
                ConversationMember(conversation_id=thread.id, user_id=user_id)
                for user_id in participant_ids
            ],
            ignore_conflicts=True,
        )

        posts = []
        if not any(event.action == SUBMITTED for event in events):
            posts.append((row.created_at, row.created_by_id, _submission_body(row, names)))
        for event in events:
            posts.append((event.created_at, event.actor_id, _event_body(row, event, names)))
        posts.sort(key=lambda post: post[0])

        sent = {}
        last_at = None
        for created_at, sender_id, body in posts:
            message = Message.objects.create(
                conversation_id=thread.id,
                sender_id=sender_id,
                body=body,
                created_at=created_at,
            )
            sent[sender_id] = message.id
            last_at = created_at

        # A decision maker has already read what they posted; everyone else
        # keeps a zero cursor, so the backfilled history shows as unread.
        for user_id, message_id in sent.items():
            ConversationMember.objects.filter(
                conversation_id=thread.id, user_id=user_id
            ).update(last_read_message_id=message_id)

        thread.last_message_at = last_at
        thread.save(update_fields=["last_message_at"])
        row.thread_id = thread.id
        row.save(update_fields=["thread"])


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0006_alter_groupchatrequest_status_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_routing_threads, migrations.RunPython.noop),
    ]

"""Read and write rules for private messaging.

Everything that changes messaging state lives here so the views stay thin and
the same operations can be reused by a future Android client (the API is the
only entry point; nothing in this module depends on a browser session).
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from apps.core import timeutils
from apps.messaging.models import (
    Conversation,
    ConversationMember,
    Message,
    TypingState,
)


class MessagingError(Exception):
    """A refusal written for the person who tried the action."""


class MessagingDisabled(MessagingError):
    """Private messaging was switched off by the Super Administrator."""


def _setting(name: str, default):
    return getattr(settings, name, default)


def typing_seconds() -> int:
    return int(_setting("MESSAGING_TYPING_SECONDS", 6))


def online_minutes() -> int:
    return int(_setting("MESSAGING_ONLINE_MINUTES", 5))


def message_page_size() -> int:
    return int(_setting("MESSAGING_PAGE_SIZE", 40))


def max_message_length() -> int:
    return int(_setting("MESSAGING_MAX_MESSAGE_LENGTH", 4000))


def private_messaging_enabled() -> bool:
    """The Super Administrator's system-wide switch for one-to-one messages.

    The unit group channel is deliberately not governed by this flag: it is the
    unit's operational line, and its own per-person permissions are managed by
    the Operations Center.
    """
    from apps.messaging.models import MessagingSettings

    try:
        return bool(MessagingSettings.load().private_messaging_enabled)
    except Exception:
        # Fail closed: a missing table, a locked database or any other read
        # failure must not silently turn the security switch back on. Unit
        # chat is unaffected because it does not consult this flag.
        return False


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def ordered_pair(first, second):
    """Return the two users in ascending id order.

    The order is what makes a pair unique in the table; callers never have to
    think about who started the conversation.
    """
    return (first, second) if first.id < second.id else (second, first)


def get_or_create_conversation(actor, other, *, request=None):
    """Return ``(conversation, created)`` for the pair, creating it once."""
    from django.db import IntegrityError

    from apps.core.audit import log_action

    if not private_messaging_enabled():
        raise MessagingDisabled(
            "Private one-to-one messaging is turned off by an Administrator."
        )
    if other is None or other.id == actor.id:
        raise MessagingError("Choose another person to message.")
    if not getattr(other, "is_active", True):
        raise MessagingError("That account is not active.")

    low, high = ordered_pair(actor, other)
    try:
        conversation, created = Conversation.objects.get_or_create(
            low_user=low,
            high_user=high,
            defaults={"created_by": actor},
        )
    except IntegrityError:
        # Two devices created the pair at the same moment; the unique constraint
        # decided which row survives.
        conversation = Conversation.objects.get(low_user=low, high_user=high)
        created = False

    if created:
        ConversationMember.objects.bulk_create(
            [
                ConversationMember(conversation=conversation, user=low),
                ConversationMember(conversation=conversation, user=high),
            ]
        )
        log_action(
            actor=actor,
            action="messaging_conversation_created",
            entity_type="conversation",
            entity_id=conversation.pk,
            after={"participants": sorted([low.id, high.id])},
            request=request,
        )
    return conversation, created


def conversation_for_user(user, conversation_id):
    """The private thread, but only if ``user`` is one of its two participants."""
    return (
        Conversation.objects.filter(pk=conversation_id, kind=Conversation.KIND_PRIVATE)
        .filter(Q(low_user_id=user.id) | Q(high_user_id=user.id))
        .select_related(
            "low_user__profile",
            "low_user__unit",
            "high_user__profile",
            "high_user__unit",
        )
        .first()
    )


# ---------------------------------------------------------------------------
# Unit group chat
# ---------------------------------------------------------------------------


def unit_group_admin_ids(unit_id) -> set[int]:
    """The unit's Operations Center members plus Administrator accounts.

    One query for the office roster and one for the account role; the member
    list uses this instead of evaluating every person's roles separately.
    """
    if unit_id is None:
        return set()
    from apps.accounts.models import User
    from apps.command.models import OfficeSection, SectionAssignment
    from apps.core import roles

    office_members = set(
        SectionAssignment.objects.filter(
            is_active=True,
            section__is_active=True,
            section__unit_id=unit_id,
            section__function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
        ).values_list("user_id", flat=True)
    )
    administrators = set(
        User.objects.filter(is_active=True, authority=roles.BASE_ADMIN).values_list(
            "id", flat=True
        )
    )
    return office_members | administrators


def is_unit_group_admin(user, unit_id=None) -> bool:
    """The Operations Center of the unit, or a system Administrator."""
    if user is None or not getattr(user, "id", None):
        return False
    unit_id = unit_id if unit_id is not None else getattr(user, "unit_id", None)
    if unit_id is None:
        return False
    return user.id in unit_group_admin_ids(unit_id)


def is_operations_center_member(user, unit_id=None) -> bool:
    """Only an active Operations Center office member may propose a group."""
    if user is None or not getattr(user, "id", None):
        return False
    unit_id = unit_id if unit_id is not None else getattr(user, "unit_id", None)
    if unit_id is None:
        return False
    from apps.command.models import OfficeSection, SectionAssignment

    return SectionAssignment.objects.filter(
        user_id=user.id,
        is_active=True,
        section__is_active=True,
        section__unit_id=unit_id,
        section__function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    ).exists()


def unit_commander(unit_id):
    """The active Unit Commander appointment, or ``None``."""
    if unit_id is None:
        return None
    from apps.command.models import UnitAppointment
    from apps.core.roles import ROLE_COMMANDER

    return (
        UnitAppointment.objects.filter(
            unit_id=unit_id,
            role=ROLE_COMMANDER,
            is_active=True,
            user__is_active=True,
        )
        .select_related("user")
        .first()
    )


def is_unit_commander(user, unit_id) -> bool:
    appointment = unit_commander(unit_id)
    return bool(appointment and appointment.user_id == getattr(user, "id", None))


def ensure_unit_group(user, *, create=True):
    """The channel for the user's own unit, created the first time it is needed."""
    from django.db import IntegrityError

    unit_id = getattr(user, "unit_id", None)
    if unit_id is None:
        return None
    conversation = Conversation.objects.filter(
        kind=Conversation.KIND_UNIT_GROUP, unit_id=unit_id
    ).first()
    if conversation is not None or not create:
        return conversation
    try:
        conversation, _created = Conversation.objects.get_or_create(
            kind=Conversation.KIND_UNIT_GROUP,
            unit_id=unit_id,
            defaults={"created_by": user},
        )
    except IntegrityError:
        # Another request created the unit's single channel at the same time.
        conversation = Conversation.objects.get(
            kind=Conversation.KIND_UNIT_GROUP, unit_id=unit_id
        )
    return conversation


def unit_group_permissions(conversation, user, *, is_admin=None) -> tuple[bool, bool]:
    """``(can_view, can_post)`` for a unit channel.

    A unit member with no policy row keeps both defaults. The Operations Center
    and Administrators always retain access, so a policy mistake cannot lock
    the unit's watch out of its own channel. ``is_admin`` is passed by the
    roster, which already resolved the admin set for the whole unit.
    """
    if conversation is None or not conversation.is_unit_group():
        return (False, False)
    if getattr(user, "unit_id", None) != conversation.unit_id:
        return (False, False)
    if is_admin is None:
        is_admin = is_unit_group_admin(user, conversation.unit_id)
    if is_admin:
        return (True, True)
    row = ConversationMember.objects.filter(
        conversation=conversation, user_id=user.id
    ).first()
    if row is None:
        return (True, True)
    if not row.can_view:
        return (False, False)
    return (True, bool(row.can_post))


def custom_group_permissions(conversation, user) -> tuple[bool, bool]:
    """``(can_view, can_post)`` for an approved custom group.

    The Super Administrator's ``is_enabled`` switch hides the group completely;
    ``chat_enabled`` leaves it readable but read-only. The unit's Operations
    Center and Administrators can inspect every approved group in their unit,
    while selected personnel see only groups they were chosen for.
    """
    if conversation is None or not conversation.is_custom_group():
        return (False, False)
    if not conversation.is_enabled:
        return (False, False)
    if getattr(user, "unit_id", None) != conversation.unit_id:
        return (False, False)
    # Membership is the whole rule: only the personnel the Operations Center
    # selected (and both approving authorities chose to admit) may read it.
    is_member = ConversationMember.objects.filter(
        conversation=conversation, user_id=user.id
    ).exists()
    if not is_member:
        return (False, False)
    return (True, bool(conversation.chat_enabled))


def custom_groups_for(user):
    """Approved groups the caller may see in the inbox."""
    if getattr(user, "unit_id", None) is None:
        return Conversation.objects.none()
    queryset = Conversation.objects.filter(
        kind=Conversation.KIND_CUSTOM_GROUP,
        unit_id=user.unit_id,
        is_enabled=True,
    ).select_related("unit")
    return queryset.filter(members__user_id=user.id).distinct().order_by(
        "-last_message_at", "-id"
    )


def routing_threads_for(user):
    """Requests whose Messages thread includes the caller.

    The Operations Center that proposed it, the Unit Commander and the Super
    Administrator are the thread's participants; selected personnel are not,
    because the selection is what the authorities are reviewing.
    """
    return (
        Conversation.objects.filter(
            kind=Conversation.KIND_GROUP_REQUEST,
            members__user_id=user.id,
        )
        .select_related("unit", "group_chat_request_thread")
        .distinct()
        .order_by("-last_message_at", "-id")
    )


def routing_thread_unread_total(user) -> int:
    rows = ConversationMember.objects.filter(
        user_id=user.id, conversation__kind=Conversation.KIND_GROUP_REQUEST
    )
    total = 0
    for member in rows:
        total += (
            Message.objects.filter(conversation_id=member.conversation_id)
            .exclude(sender_id=user.id)
            .filter(id__gt=member.last_read_message_id)
            .count()
        )
    return total


def custom_group_unread(conversation, user_id) -> int:
    """Unread custom-group messages for a selected member (admins may not be
    members and therefore have no personal cursor to advance)."""
    if conversation is None:
        return 0
    member = ConversationMember.objects.filter(
        conversation=conversation, user_id=user_id
    ).first()
    if member is None:
        return 0
    return (
        conversation.messages.exclude(sender_id=user_id)
        .filter(id__gt=member.last_read_message_id)
        .count()
    )


def custom_group_unread_total(user) -> int:
    if getattr(user, "unit_id", None) is None:
        return 0
    rows = ConversationMember.objects.filter(
        user_id=user.id,
        conversation__kind=Conversation.KIND_CUSTOM_GROUP,
        conversation__unit_id=user.unit_id,
        conversation__is_enabled=True,
    )
    total = 0
    for member in rows:
        total += (
            Message.objects.filter(conversation_id=member.conversation_id)
            .exclude(sender_id=user.id)
            .filter(id__gt=member.last_read_message_id)
            .count()
        )
    return total


def conversation_for_viewer(user, conversation_id):
    """A thread the caller may open: own private pair, unit or approved group."""
    conversation = (
        Conversation.objects.filter(pk=conversation_id)
        .select_related(
            "unit",
            "low_user__profile",
            "low_user__unit",
            "high_user__profile",
            "high_user__unit",
        )
        .first()
    )
    if conversation is None:
        return None
    if conversation.is_unit_group():
        can_view, _can_post = unit_group_permissions(conversation, user)
        return conversation if can_view else None
    if conversation.is_custom_group():
        can_view, _can_post = custom_group_permissions(conversation, user)
        return conversation if can_view else None
    if conversation.is_group_request():
        is_member = ConversationMember.objects.filter(
            conversation=conversation, user_id=user.id
        ).exists()
        return conversation if is_member else None
    # Check participation before the global switch, so a non-participant gets
    # the same 404 whether private messaging is on or off.
    if user.id not in conversation.participant_ids():
        return None
    if not private_messaging_enabled():
        raise MessagingDisabled(
            "Private one-to-one messaging is turned off by an Administrator."
        )
    return conversation


def group_member_payload(
    conversation, person, viewer, *, is_admin=None, member=None
) -> dict:
    if is_admin is None:
        is_admin = is_unit_group_admin(person, conversation.unit_id)
    can_view, can_post = unit_group_permissions(
        conversation, person, is_admin=is_admin
    )
    if member is None:
        member = ConversationMember.objects.filter(
            conversation=conversation, user_id=person.id
        ).first()
    return {
        "id": person.id,
        "full_name": getattr(person, "full_name", "") or person.username,
        "rank": person.rank or "",
        "serial_number": person.serial_number,
        "can_view": can_view,
        "can_post": can_post,
        "is_admin": bool(is_admin),
        "is_self": person.id == viewer.id,
        "has_policy": member is not None,
    }


def unit_group_members(conversation, actor):
    """The unit roster with its current channel permissions (admins only)."""
    from apps.accounts.models import User

    if not is_unit_group_admin(actor, conversation.unit_id):
        raise MessagingError(
            "Only the unit's Operations Center can manage the unit chat."
        )
    people = (
        User.objects.filter(is_active=True, unit_id=conversation.unit_id)
        .select_related("profile", "unit")
        .order_by("last_name", "first_name", "id")
    )
    admin_ids = unit_group_admin_ids(conversation.unit_id)
    member_rows = {
        row.user_id: row
        for row in ConversationMember.objects.filter(conversation=conversation)
    }
    return [
        group_member_payload(
            conversation,
            person,
            actor,
            is_admin=person.id in admin_ids,
            member=member_rows.get(person.id),
        )
        for person in people
    ]


def update_group_member(conversation, actor, target, *, can_view=None, can_post=None):
    """Set one unit member's view/post permission on the channel."""
    from apps.core.audit import log_action

    if not is_unit_group_admin(actor, conversation.unit_id):
        raise MessagingError(
            "Only the unit's Operations Center can manage the unit chat."
        )
    if target is None or target.unit_id != conversation.unit_id:
        raise MessagingError("That person is not in this unit.")
    if not target.is_active:
        raise MessagingError("That account is not active.")
    if is_unit_group_admin(target, conversation.unit_id):
        raise MessagingError(
            "Operations Center members and Administrators keep their unit chat access."
        )

    member, _created = ConversationMember.objects.get_or_create(
        conversation=conversation, user=target
    )
    before = {"can_view": member.can_view, "can_post": member.can_post}
    if can_view is not None:
        member.can_view = bool(can_view)
    if can_post is not None:
        member.can_post = bool(can_post)
    # Posting requires seeing the channel; hiding it necessarily stops posting.
    if not member.can_view:
        member.can_post = False
    elif member.can_post:
        member.can_view = True
    member.save(update_fields=["can_view", "can_post"])

    log_action(
        actor=actor,
        action="update_unit_group_member",
        entity_type="conversation",
        entity_id=conversation.id,
        before=before,
        after={
            "user_id": target.id,
            "can_view": member.can_view,
            "can_post": member.can_post,
        },
    )
    return group_member_payload(conversation, target, actor)


def member_for(conversation, user) -> ConversationMember:
    member, _ = ConversationMember.objects.get_or_create(
        conversation=conversation, user=user
    )
    return member


def peer_of(conversation, user):
    peer_id = conversation.other_user_id(user.id)
    return (
        conversation.low_user
        if conversation.low_user_id == peer_id
        else conversation.high_user
    )


def unread_count(conversation, user_id) -> int:
    member = ConversationMember.objects.filter(
        conversation=conversation, user_id=user_id
    ).first()
    cursor = member.last_read_message_id if member else 0
    return (
        conversation.messages.exclude(sender_id=user_id)
        .filter(id__gt=cursor)
        .count()
    )


def unread_total(user_id) -> int:
    """Unread private messages across every thread the person is part of."""
    rows = (
        ConversationMember.objects.filter(user_id=user_id)
        .select_related("conversation")
    )
    total = 0
    for member in rows:
        if member.conversation.is_group():
            continue
        total += (
            Message.objects.filter(conversation_id=member.conversation_id)
            .exclude(sender_id=user_id)
            .filter(id__gt=member.last_read_message_id)
            .count()
        )
    return total


def unit_group_unread(conversation, user_id) -> int:
    """Unread messages in one unit channel for ``user_id``."""
    if conversation is None:
        return 0
    return unread_count(conversation, user_id)


def inbox_unread_total(user) -> int:
    """Everything the inbox badge should count: private, unit and approved groups."""
    total = unread_total(user.id) if private_messaging_enabled() else 0
    group = ensure_unit_group(user, create=False)
    return (
        total
        + unit_group_unread(group, user.id)
        + custom_group_unread_total(user)
        + routing_thread_unread_total(user)
    )


# ---------------------------------------------------------------------------
# Sending, delivery and reading
# ---------------------------------------------------------------------------


def _notify_recipient(conversation, sender, recipient):
    """One unread "new message" notice per thread, never one per message."""
    from apps.ops.models import Notification
    from apps.ops.notifications import notify

    existing = Notification.objects.filter(
        recipient_id=recipient.id,
        category="message",
        related_type="conversation",
        related_id=str(conversation.id),
        is_read=False,
    ).exists()
    if existing:
        return None
    sender_name = (
        getattr(sender, "full_name", "") or getattr(sender, "username", "") or "TOWEMS"
    )
    return notify(
        recipient,
        title=f"New message from {sender_name}"[:200],
        # The body is deliberately absent: a notification is not a copy of the
        # message and must not leak its text on a lock screen.
        message="Open Messages to read it.",
        level="info",
        category="message",
        related=("conversation", conversation.id),
        actor=sender,
        priority="normal",
        action_url=f"/messages?conversation={conversation.id}",
    )


def _display_name(user) -> str:
    return (
        getattr(user, "full_name", "")
        or getattr(user, "get_full_name", lambda: "")()
        or getattr(user, "username", "")
        or "TOWEMS"
    )


def conversation_people(conversation, viewer):
    """Who the viewer may name with @ in this thread.

    A private thread offers the peer; the unit channel offers every active
    member who can still see it (the Operations Center's hidden members are
    excluded); an approved group offers its selected members. The sender is
    never offered, because mentioning yourself notifies nobody.
    """
    from apps.accounts.models import User
    from apps.messaging.models import ConversationMember

    if conversation is None:
        return User.objects.none()
    if conversation.kind == Conversation.KIND_PRIVATE:
        peer = peer_of(conversation, viewer)
        if peer is None:
            return User.objects.none()
        return User.objects.filter(pk=peer.pk, is_active=True)
    if conversation.is_unit_group():
        hidden = ConversationMember.objects.filter(
            conversation=conversation, can_view=False
        ).values_list("user_id", flat=True)
        return (
            User.objects.filter(is_active=True, unit_id=conversation.unit_id)
            .exclude(pk=viewer.id)
            .exclude(pk__in=hidden)
            .order_by("last_name", "first_name", "id")
        )
    return (
        User.objects.filter(
            is_active=True, messaging_memberships__conversation=conversation
        )
        .exclude(pk=viewer.id)
        .order_by("last_name", "first_name", "id")
        .distinct()
    )


def conversation_roster(conversation):
    """The active people in a thread, in the order a roster shows them."""
    from apps.messaging.models import ConversationMember

    if conversation is None:
        return ConversationMember.objects.none()
    return (
        ConversationMember.objects.filter(
            conversation=conversation, user__is_active=True
        )
        .select_related("user")
        .order_by("user__last_name", "user__first_name", "user__id")
    )


def _notify_mentions(conversation, sender, mentioned) -> None:
    """Tell each named person once, without repeating what was written."""
    from apps.ops.notifications import notify_many

    title = f"{_display_name(sender)} mentioned you"
    if conversation.is_custom_group():
        subject = conversation.name or "a group chat"
    else:
        unit = getattr(conversation, "unit", None)
        subject = (
            f"the {unit.acronym or unit.name} unit chat" if unit else "the unit chat"
        )
    notify_many(
        mentioned,
        title=title,
        message=f"You were mentioned in {subject}. Open Messages to read it.",
        level="info",
        category="message",
        related=("conversation", conversation.id),
        actor=sender,
        action_url=f"/messages?conversation={conversation.id}",
    )


def _queue_closed_tab_push(conversation, sender, recipient, message) -> None:
    """Wake the recipient's closed browsers, but not a tab they already have.

    Presence is the existing five-minute stamp: somebody polling the app is
    online and gets the bell instead. The push itself runs after the message
    commits, in a worker thread, so neither the push service nor a dead
    endpoint can delay the send response.
    """
    from apps.messaging import push

    if not push.configured() or user_is_online(recipient):
        return
    recipient_id = recipient.id
    conversation_id = conversation.id
    message_id = message.id
    sender_name = _display_name(sender)
    transaction.on_commit(
        lambda: push.queue_message_push(
            recipient_id, sender_name, conversation_id, message_id
        )
    )


@transaction.atomic
def send_message(
    conversation,
    sender,
    body,
    *,
    request=None,
    reply_to_id=None,
    mention_ids=None,
):
    """Store one message and tell the recipient something arrived.

    A reply points at another message in the same thread, and mentions are
    resolved against the people who can currently see that thread, so neither
    an id from somewhere else nor a hidden member can be named.
    """
    from apps.accounts.models import User
    from apps.core.audit import log_action

    text = (body or "").strip()
    if not text:
        raise MessagingError("Write a message first.")
    if len(text) > max_message_length():
        raise MessagingError(
            f"A message may be at most {max_message_length()} characters."
        )

    quoted = None
    if reply_to_id:
        quoted = Message.objects.filter(
            pk=reply_to_id, conversation=conversation
        ).first()
        if quoted is None:
            raise MessagingError(
                "That message is not part of this conversation."
            )
        if quoted.unsent_at is not None:
            raise MessagingError("That message was unsent, so it cannot be quoted.")

    mentioned = []
    wanted = {int(pk) for pk in (mention_ids or []) if str(pk).isdigit()}
    if wanted:
        allowed = set(
            conversation_people(conversation, sender).values_list("id", flat=True)
        )
        if not wanted <= allowed:
            raise MessagingError(
                "You can only mention people who can see this conversation."
            )
        mentioned = list(User.objects.filter(pk__in=wanted))

    message = Message.objects.create(
        conversation=conversation,
        sender=sender,
        body=text,
        reply_to=quoted,
    )
    if mentioned:
        message.mentions.set(mentioned)
    conversation.last_message_at = message.created_at
    conversation.save(update_fields=["last_message_at", "updated_at"])

    sender_member = member_for(conversation, sender)
    if message.id > sender_member.last_read_message_id:
        sender_member.last_read_message_id = message.id
        sender_member.save(update_fields=["last_read_message_id"])

    # Sending ends "typing…" immediately, without waiting for the TTL.
    TypingState.objects.filter(user_id=sender.id).delete()

    # A unit channel has no single recipient: the bell would either notify every
    # member for every message or silently pick one. Its members see the unread
    # badge instead, like any other thread.
    recipient = None if conversation.is_group() else peer_of(conversation, sender)
    if recipient is not None:
        _notify_recipient(conversation, sender, recipient)
        _queue_closed_tab_push(conversation, sender, recipient, message)
    elif mentioned and not conversation.is_group_request():
        _notify_mentions(conversation, sender, mentioned)

    log_action(
        actor=sender,
        action="message_sent",
        entity_type="message",
        entity_id=message.id,
        # Metadata only: the audit trail must not become a second copy of the
        # private conversation.
        after={"conversation": conversation.id, "recipient": recipient.id if recipient else None},
        request=request,
    )
    return message


@transaction.atomic
def unsend_message(conversation, message, actor, *, request=None):
    """Withdraw the sender's own message, leaving the fact that it existed.

    The row stays in the thread as a trace: the text is cleared, the time and
    person who withdrew it are kept, and the audit trail records the action
    without ever storing a second copy of what was written.
    """
    from apps.core.audit import log_action

    if message.conversation_id != conversation.id:
        raise MessagingError("That message is not part of this conversation.")
    if conversation.is_group_request():
        raise MessagingError(
            "Decisions in a group chat request cannot be unsent."
        )
    if message.sender_id != actor.id:
        raise MessagingError("Only the sender can unsend this message.")
    if message.unsent_at is not None:
        return message

    message.unsent_at = timeutils.now()
    message.unsent_by = actor
    message.body = ""
    message.save(update_fields=["unsent_at", "unsent_by", "body"])

    log_action(
        actor=actor,
        action="message_unsent",
        entity_type="message",
        entity_id=message.id,
        before={"conversation": conversation.id},
        after={"unsent": True},
        request=request,
    )
    return message


def mark_delivered(conversation, viewer, up_to_id=None) -> int:
    """Record that the viewer's client received messages up to ``up_to``."""
    now = timeutils.now()
    member = member_for(conversation, viewer)
    latest = (
        conversation.messages.order_by("-id").values_list("id", flat=True).first() or 0
    )
    try:
        up_to = min(int(up_to_id), latest) if up_to_id else latest
    except (TypeError, ValueError):
        up_to = latest
    if up_to <= 0:
        return 0

    # Group delivery is a cursor per member, not a stamp on the message: one
    # reader must not make the whole unit look like it received the message.
    updated = 0
    if not conversation.is_group():
        updated = (
            conversation.messages.exclude(sender_id=viewer.id)
            .filter(id__lte=up_to, delivered_at__isnull=True)
            .update(delivered_at=now)
        )
    if up_to > member.last_delivered_message_id:
        member.last_delivered_message_id = up_to
        member.save(update_fields=["last_delivered_message_id"])
    return updated


def mark_read(conversation, viewer, up_to_id=None) -> int:
    """Move the viewer's read cursor forward and stamp the messages it covers."""
    from apps.ops.models import Notification

    now = timeutils.now()
    member = member_for(conversation, viewer)
    latest = (
        conversation.messages.order_by("-id").values_list("id", flat=True).first() or 0
    )
    try:
        up_to = min(int(up_to_id), latest) if up_to_id else latest
    except (TypeError, ValueError):
        up_to = latest
    if up_to <= 0:
        return 0

    updated = 0
    if not conversation.is_group():
        conversation.messages.exclude(sender_id=viewer.id).filter(
            id__lte=up_to, delivered_at__isnull=True
        ).update(delivered_at=now)
        updated = (
            conversation.messages.exclude(sender_id=viewer.id)
            .filter(id__lte=up_to, read_at__isnull=True)
            .update(read_at=now)
        )
    if up_to > member.last_read_message_id:
        member.last_read_message_id = up_to
        member.last_read_at = now
        member.save(update_fields=["last_read_message_id", "last_read_at"])

    if not conversation.is_group():
        # The bell's notice for this thread has now been acted on.
        Notification.objects.filter(
            recipient_id=viewer.id,
            category="message",
            related_type="conversation",
            related_id=str(conversation.id),
            is_read=False,
        ).update(is_read=True, read_at=now)
    return updated


# ---------------------------------------------------------------------------
# Typing and presence
# ---------------------------------------------------------------------------


def set_typing(conversation, user, typing: bool) -> None:
    if not typing:
        TypingState.objects.filter(user_id=user.id).delete()
        return
    TypingState.objects.update_or_create(
        user_id=user.id,
        defaults={"conversation": conversation, "updated_at": timeutils.now()},
    )


def peer_is_typing(conversation, viewer) -> bool:
    cutoff = timeutils.now() - timedelta(seconds=typing_seconds())
    return (
        TypingState.objects.filter(
            conversation_id=conversation.id, updated_at__gte=cutoff
        )
        .exclude(user_id=viewer.id)
        .exists()
    )


def user_is_online(user) -> bool:
    """Seen in the last few minutes, from the existing presence stamps."""
    profile = getattr(user, "profile", None)
    last_seen = getattr(profile, "last_seen", None)
    if last_seen is None:
        return False
    return last_seen >= timeutils.now() - timedelta(minutes=online_minutes())


def avatar_url(user) -> str:
    profile = getattr(user, "profile", None)
    filename = getattr(profile, "avatar_filename", "") or ""
    if not filename:
        return ""
    return f"/api/personnel/{user.id}/avatar/?v={filename}"


# ---------------------------------------------------------------------------
# Queries used by the API
# ---------------------------------------------------------------------------


def conversations_for(user):
    """The user's threads with their inbox annotations.

    One query carries the peer, presence, last message and unread count: the
    inbox is polled while a screen is open, so it must not become N+1.
    """
    from django.db.models import Count, Exists, OuterRef, Subquery

    from apps.messaging.models import ConversationMember as Member

    last = Message.objects.filter(conversation_id=OuterRef("pk")).order_by("-id")
    cursor = Member.objects.filter(
        conversation_id=OuterRef("pk"), user_id=user.id
    ).values("last_read_message_id")[:1]
    typing = TypingState.objects.filter(
        conversation_id=OuterRef("pk"),
        updated_at__gte=timeutils.now() - timedelta(seconds=typing_seconds()),
    ).exclude(user_id=user.id)

    return (
        Conversation.objects.filter(
            Q(low_user_id=user.id) | Q(high_user_id=user.id)
        )
        .select_related(
            "low_user__profile",
            "low_user__unit",
            "high_user__profile",
            "high_user__unit",
        )
        .annotate(
            unread_count=Count(
                "messages",
                filter=Q(messages__id__gt=Subquery(cursor))
                & ~Q(messages__sender_id=user.id),
                distinct=True,
            ),
            # Annotation names must not shadow model fields (``last_message_at``
            # is a real column on Conversation).
            preview_id=Subquery(last.values("id")[:1]),
            preview_body=Subquery(last.values("body")[:1]),
            preview_sender_id=Subquery(last.values("sender_id")[:1]),
            preview_at=Subquery(last.values("created_at")[:1]),
            peer_typing=Exists(typing),
        )
        .order_by("-last_message_at", "-id")
    )


def messages_query(conversation):
    return (
        conversation.messages.select_related(
            "sender",
            "conversation",
            "reply_to",
            "reply_to__sender",
            "unsent_by",
        )
        .prefetch_related("mentions")
        .order_by("id")
    )


def search_messages(user, term: str, limit: int = 50):
    """Search the caller's own private threads and own unit channel."""
    term = (term or "").strip()
    if len(term) < 2:
        return Message.objects.none()

    scope = Q(pk__in=[])
    if private_messaging_enabled():
        scope |= Q(conversation__low_user_id=user.id) | Q(
            conversation__high_user_id=user.id
        )
    if getattr(user, "unit_id", None) is not None:
        scope |= Q(
            conversation__kind=Conversation.KIND_UNIT_GROUP,
            conversation__unit_id=user.unit_id,
        )
        # Approved groups are searchable only by the people selected for them.
        scope |= Q(
            conversation__kind=Conversation.KIND_CUSTOM_GROUP,
            conversation__unit_id=user.unit_id,
            conversation__is_enabled=True,
            conversation__members__user_id=user.id,
        )
    # Routing threads are searchable by their participants only.
    scope |= Q(
        conversation__kind=Conversation.KIND_GROUP_REQUEST,
        conversation__members__user_id=user.id,
    )

    queryset = Message.objects.filter(
        scope, body__icontains=term, unsent_at__isnull=True
    )
    if not is_unit_group_admin(user, getattr(user, "unit_id", None)):
        # A restricted member must not find the channel through search either.
        hidden = ConversationMember.objects.filter(
            user_id=user.id, can_view=False
        ).values_list("conversation_id", flat=True)
        queryset = queryset.exclude(conversation_id__in=hidden)
    return (
        queryset.select_related("sender", "conversation", "conversation__unit")
        .distinct()
        .order_by("-id")[:limit]
    )

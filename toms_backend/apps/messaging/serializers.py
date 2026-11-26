"""Input validation and the JSON shapes the messaging API returns.

Payloads are built here rather than with ModelSerializers because they are
deliberately narrower than the models: a participant sees the other person's
directory card, never their account row, and a message carries the sender's
name and rank but not their profile.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings
from rest_framework import serializers

from apps.messaging import services
from apps.messaging.models import ConversationMember, Message


class StartConversationSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)


class SendMessageSerializer(serializers.Serializer):
    body = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
        max_length=services.max_message_length(),
    )
    #: The message being answered, in this same thread.
    reply_to = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    #: People named with @; the server checks they can see the thread.
    mention_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
    )

    def validate_mention_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Mention each person once.")
        if len(value) > 50:
            raise serializers.ValidationError("Mention at most 50 people at once.")
        return value


class TypingSerializer(serializers.Serializer):
    typing = serializers.BooleanField(default=True)


class MessagingSettingsSerializer(serializers.Serializer):
    private_messaging_enabled = serializers.BooleanField()


class UnitGroupMemberSerializer(serializers.Serializer):
    can_view = serializers.BooleanField(required=False)
    can_post = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Set can_view, can_post, or both."
            )
        return attrs


class GroupChatRequestCreateSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=3, max_length=120, trim_whitespace=True)
    member_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False
    )

    def validate_member_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Select each person once.")
        return value


class GroupChatRequestDecisionSerializer(serializers.Serializer):
    note = serializers.CharField(
        required=False, allow_blank=True, max_length=2000, trim_whitespace=True
    )


class AdminGroupChatSerializer(serializers.Serializer):
    is_enabled = serializers.BooleanField(required=False)
    chat_enabled = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Set the group switch, the chat switch, or both."
            )
        return attrs


class ReadSerializer(serializers.Serializer):
    up_to_id = serializers.IntegerField(required=False, min_value=0)


class PushSubscriptionSerializer(serializers.Serializer):
    """The object ``PushSubscription.toJSON()`` sends from the browser."""

    endpoint = serializers.URLField(max_length=500)
    keys = serializers.DictField(child=serializers.CharField(max_length=255))

    def validate_endpoint(self, value):
        """Only the real browser push services, over HTTPS.

        Without this, any signed-in account could register an arbitrary URL and
        make the server POST to it whenever a message is sent - a blind SSRF.
        Browsers issue these endpoints themselves, so a genuine client never
        needs another host.
        """
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            raise serializers.ValidationError("Push endpoints must use HTTPS.")
        allowed = getattr(settings, "MESSAGING_PUSH_ALLOWED_HOSTS", ())
        if allowed and not any(
            host == candidate or host.endswith(f".{candidate}")
            for candidate in allowed
        ):
            raise serializers.ValidationError(
                "That push service is not allowed by this deployment."
            )
        return value

    def validate_keys(self, value):
        if not value.get("p256dh") or not value.get("auth"):
            raise serializers.ValidationError(
                "Both the p256dh and auth encryption keys are required."
            )
        return value


class PushEndpointSerializer(serializers.Serializer):
    endpoint = serializers.URLField(max_length=500)


def _iso(value):
    return value.isoformat() if value else None


def _name(user) -> str:
    return (
        getattr(user, "full_name", "")
        or getattr(user, "get_full_name", lambda: "")()
        or getattr(user, "username", "")
    )


def conversation_member_payload(membership, viewer_id) -> dict:
    """One person on a group's roster. Approved groups keep this read-only."""
    user = membership.user
    return {
        "id": user.id,
        "full_name": _name(user),
        "rank": user.rank or "",
        "serial_number": user.serial_number,
        "can_view": membership.can_view,
        "can_post": membership.can_post,
        "is_admin": False,
        "is_self": user.id == viewer_id,
    }


def person_payload(user, *, viewer_id=None) -> dict:
    """A directory card: enough to pick the right person, nothing more."""
    return {
        "id": user.id,
        "serial_number": user.serial_number,
        "rank": user.rank or "",
        "full_name": _name(user),
        "designation": user.designation or "",
        "unit_name": user.unit.name if user.unit_id else "",
        "unit_acronym": user.unit.acronym if user.unit_id else "",
        "avatar_url": services.avatar_url(user),
        "online": services.user_is_online(user),
        "is_self": user.id == viewer_id,
    }


def _reply_card(message: Message) -> dict | None:
    """What the thread shows above a reply: who said it, and just enough text."""
    target = message.reply_to
    if target is None:
        return None
    unsent = target.unsent_at is not None
    sender = getattr(target, "sender", None)
    return {
        "id": target.id,
        "is_unsent": unsent,
        "body": "" if unsent else target.body.strip()[:140],
        "sender": (
            {"id": sender.id, "full_name": _name(sender)} if sender is not None else None
        ),
    }


def message_payload(message: Message, viewer_id) -> dict:
    conversation = getattr(message, "conversation", None)
    is_group = bool(
        getattr(conversation, "is_group", lambda: False)()
    )
    is_request = bool(
        getattr(conversation, "is_group_request", lambda: False)()
    )
    is_unsent = message.unsent_at is not None
    unsent_by = getattr(message, "unsent_by", None)
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        # An unsent message keeps its row but not its text.
        "body": "" if is_unsent else message.body,
        "is_unsent": is_unsent,
        "unsent_at": _iso(message.unsent_at),
        "unsent_by": (
            {"id": message.unsent_by_id, "full_name": _name(unsent_by)}
            if unsent_by is not None
            else None
        ),
        "can_unsend": (
            message.sender_id == viewer_id and not is_unsent and not is_request
        ),
        "reply_to": _reply_card(message) if message.reply_to_id else None,
        "mentions": [
            {"id": person.id, "full_name": _name(person)}
            for person in message.mentions.all()
        ],
        "created_at": _iso(message.created_at),
        "is_mine": message.sender_id == viewer_id,
        "sender": {
            "id": message.sender_id,
            "serial_number": message.sender.serial_number,
            "rank": message.sender.rank or "",
            "full_name": _name(message.sender),
        },
        # A unit channel has many readers; one member's fetch must not claim
        # that everyone received or read the message, so it carries no receipt.
        "delivered_at": None if is_group else _iso(message.delivered_at),
        "read_at": None if is_group else _iso(message.read_at),
        # Only the sender is shown a receipt; "sent" on the other side would be
        # meaningless to the person who wrote the message.
        "status": None if is_group else message.status_for(viewer_id),
    }


def _last_message(conversation, viewer_id):
    if hasattr(conversation, "preview_id"):
        if not conversation.preview_id:
            return None
        body = (conversation.preview_body or "").strip()
        return {
            "id": conversation.preview_id,
            "body": (
                body[:140] + ("…" if len(body) > 140 else "")
                if body
                else "Message unsent"
            ),
            "created_at": _iso(conversation.preview_at),
            "is_mine": conversation.preview_sender_id == viewer_id,
        }
    message = conversation.messages.order_by("-id").first()
    if message is None:
        return None
    body = message.body.strip()
    return {
        "id": message.id,
        "body": (
            body[:140] + ("…" if len(body) > 140 else "")
            if body
            else "Message unsent"
        ),
        "created_at": _iso(message.created_at),
        "is_mine": message.sender_id == viewer_id,
    }


def conversation_payload(conversation, viewer) -> dict:
    peer = services.peer_of(conversation, viewer)
    unread = getattr(conversation, "unread_count", None)
    if unread is None:
        unread = services.unread_count(conversation, viewer.id)
    typing = getattr(conversation, "peer_typing", None)
    if typing is None:
        typing = services.peer_is_typing(conversation, viewer)
    member = ConversationMember.objects.filter(
        conversation=conversation, user_id=viewer.id
    ).first()
    return {
        "id": conversation.id,
        "kind": conversation.kind,
        "peer": person_payload(peer, viewer_id=viewer.id),
        "last_message": _last_message(conversation, viewer.id),
        "unread_count": unread,
        "peer_typing": bool(typing),
        "last_read_message_id": member.last_read_message_id if member else 0,
        "created_at": _iso(conversation.created_at),
        "last_message_at": _iso(conversation.last_message_at),
    }


def unit_group_payload(conversation, viewer) -> dict | None:
    """The unit channel card: no peer, a unit title, and this member's rights."""
    if conversation is None:
        return None
    can_view, can_post = services.unit_group_permissions(conversation, viewer)
    if not can_view:
        return None
    from apps.accounts.models import User

    unit = conversation.unit
    member = ConversationMember.objects.filter(
        conversation=conversation, user_id=viewer.id
    ).first()
    title = (
        f"{unit.acronym or unit.name} unit chat" if unit else "Unit chat"
    )
    return {
        "id": conversation.id,
        "kind": "unit_group",
        "title": title,
        "unit_name": unit.name if unit else "",
        "unit_acronym": unit.acronym if unit else "",
        "peer": None,
        "last_message": _last_message(conversation, viewer.id),
        "unread_count": services.unread_count(conversation, viewer.id),
        "peer_typing": False,
        "last_read_message_id": member.last_read_message_id if member else 0,
        "member_count": User.objects.filter(
            is_active=True, unit_id=conversation.unit_id
        ).count(),
        "can_post": can_post,
        "is_admin": services.is_unit_group_admin(viewer, conversation.unit_id),
        "created_at": _iso(conversation.created_at),
        "last_message_at": _iso(conversation.last_message_at),
    }


def custom_group_payload(conversation, viewer) -> dict | None:
    """An approved group's card. The title is the name both authorities saw."""
    if conversation is None:
        return None
    can_view, can_post = services.custom_group_permissions(conversation, viewer)
    if not can_view:
        return None
    member = ConversationMember.objects.filter(
        conversation=conversation, user_id=viewer.id
    ).first()
    unit = conversation.unit
    return {
        "id": conversation.id,
        "kind": "custom_group",
        "title": conversation.name,
        "unit_name": unit.name if unit else "",
        "unit_acronym": unit.acronym if unit else "",
        "peer": None,
        "last_message": _last_message(conversation, viewer.id),
        "unread_count": services.custom_group_unread(conversation, viewer.id),
        "peer_typing": False,
        "last_read_message_id": member.last_read_message_id if member else 0,
        "member_count": conversation.members.count(),
        "can_post": can_post,
        "is_admin": False,
        "is_enabled": conversation.is_enabled,
        "chat_enabled": conversation.chat_enabled,
        "created_at": _iso(conversation.created_at),
        "last_message_at": _iso(conversation.last_message_at),
    }


def routing_thread_payload(conversation, viewer) -> dict | None:
    """A group chat request's Messages thread card.

    It behaves like a group card (title, members, last message) and carries the
    request's decision state so the thread itself can offer Approve/Reject to
    the authority whose turn it is.
    """
    if conversation is None or not conversation.is_group_request():
        return None
    from apps.messaging import group_chats

    member = ConversationMember.objects.filter(
        conversation=conversation, user_id=viewer.id
    ).first()
    if member is None:
        return None
    request_row = getattr(conversation, "group_chat_request_thread", None)
    unit = conversation.unit
    can_open = bool(
        request_row
        and request_row.conversation_id
        and services.custom_group_permissions(request_row.conversation, viewer)[0]
    )
    return {
        "id": conversation.id,
        "kind": "group_request",
        "title": conversation.name,
        "unit_name": unit.name if unit else "",
        "unit_acronym": unit.acronym if unit else "",
        "peer": None,
        "last_message": _last_message(conversation, viewer.id),
        "unread_count": services.unread_count(conversation, viewer.id),
        "peer_typing": False,
        "last_read_message_id": member.last_read_message_id,
        "member_count": conversation.members.count(),
        "can_post": False,
        "is_admin": False,
        "request_id": request_row.id if request_row else None,
        "status": request_row.status if request_row else "",
        "status_label": request_row.get_status_display() if request_row else "",
        "can_approve": (
            group_chats.can_approve(request_row, viewer) if request_row else False
        ),
        "conversation_id": request_row.conversation_id if request_row else None,
        "can_open": can_open,
        "decision_note": request_row.decision_note if request_row else "",
        "created_at": _iso(conversation.created_at),
        "last_message_at": _iso(conversation.last_message_at),
    }


def group_chat_request_payload(request_row, viewer) -> dict:
    """The routing thread shown in Messages. No separate approval form."""
    from apps.messaging import group_chats

    selected = [
        person_payload(selection.user, viewer_id=viewer.id)
        for selection in request_row.selected_members.all()
        if getattr(selection, "user", None) is not None
    ]
    unit = request_row.unit
    can_open = False
    if request_row.conversation_id:
        can_open = services.custom_group_permissions(
            request_row.conversation, viewer
        )[0]
    return {
        "id": request_row.id,
        "name": request_row.name,
        "status": request_row.status,
        "status_label": request_row.get_status_display(),
        "unit_id": request_row.unit_id,
        "unit_name": unit.name if unit else "",
        "unit_acronym": unit.acronym if unit else "",
        "created_by": person_payload(request_row.created_by, viewer_id=viewer.id),
        "created_at": _iso(request_row.created_at),
        "selected_members": selected,
        "member_count": len(selected),
        "can_approve": group_chats.can_approve(request_row, viewer),
        "conversation_id": request_row.conversation_id,
        "thread_id": request_row.thread_id,
        "can_open": can_open,
        "decision_note": request_row.decision_note,
    }


def admin_custom_group_payload(conversation) -> dict:
    """Super Administrator oversight row, including a disabled group."""
    unit = conversation.unit
    return {
        "id": conversation.id,
        "title": conversation.name,
        "unit_id": conversation.unit_id,
        "unit_name": unit.name if unit else "",
        "unit_acronym": unit.acronym if unit else "",
        "is_enabled": conversation.is_enabled,
        "chat_enabled": conversation.chat_enabled,
        "member_count": conversation.members.count(),
        "created_at": _iso(conversation.created_at),
        "last_message_at": _iso(conversation.last_message_at),
    }


def search_result_payload(message: Message, viewer) -> dict:
    conversation = message.conversation
    body = message.body.strip()
    is_group = conversation.is_group()
    peer = None if is_group else services.peer_of(conversation, viewer)
    unit = conversation.unit
    title = ""
    if conversation.is_custom_group() or conversation.is_group_request():
        title = conversation.name
    elif is_group and unit:
        title = f"{unit.acronym or unit.name} unit chat"
    return {
        "id": message.id,
        "conversation_id": conversation.id,
        "kind": conversation.kind,
        "title": title,
        "peer": person_payload(peer, viewer_id=viewer.id) if peer else None,
        "is_mine": message.sender_id == viewer.id,
        "body": body[:200] + ("…" if len(body) > 200 else ""),
        "created_at": _iso(message.created_at),
    }

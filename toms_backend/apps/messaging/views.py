"""Private messaging API.

Every endpoint is scoped to the signed-in account: the only way to reach a
thread is to be one of its two participants, which
:func:`services.conversation_for_user` enforces (a non-participant gets 404, not
a list of somebody else's messages).

The endpoints are plain request/response on purpose. TOWEMS is deployed as a
WSGI application behind gunicorn, so the client keeps a thread live by polling
the message window and the inbox at short intervals; the payloads are shaped so
a future WebSocket gateway (or the Android client) can push exactly the same
objects without changing the API contracts.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import ownership
from apps.accounts.device_tokens import ALERT_AUTHENTICATION, DeviceTokenReadOnly
from apps.accounts.models import User
from apps.core.audit import log_action
from apps.core.csrf import enforce_csrf
from apps.messaging import group_chats, push, serializers, services
from apps.messaging.models import (
    Conversation,
    GroupChatRequest,
    Message,
    MessagingSettings,
    PushSubscription,
)


def _thread_payload(conversation, viewer):
    """The card shape for a private thread, the unit channel or a group."""
    if conversation is not None and conversation.is_unit_group():
        return serializers.unit_group_payload(conversation, viewer)
    if conversation is not None and conversation.is_custom_group():
        return serializers.custom_group_payload(conversation, viewer)
    if conversation is not None and conversation.is_group_request():
        return serializers.routing_thread_payload(conversation, viewer)
    return serializers.conversation_payload(conversation, viewer)


class MessagingBaseView(APIView):
    permission_classes = [IsAuthenticated]

    def conversation(self, request, pk):
        try:
            conversation = services.conversation_for_viewer(request.user, pk)
        except services.MessagingDisabled as exc:
            raise PermissionDenied(str(exc))
        if conversation is None:
            # 404 rather than 403: another person's thread must not even be
            # confirmed to exist.
            raise Http404("Conversation not found.")
        return conversation


class PeopleSearchView(MessagingBaseView):
    """Find a registered TOWEMS user to start a private thread with."""

    def get(self, request):
        if not services.private_messaging_enabled():
            raise PermissionDenied(
                "Private one-to-one messaging is turned off by an Administrator."
            )
        term = (request.query_params.get("q") or "").strip()[:80]
        queryset = (
            User.objects.filter(is_active=True)
            .exclude(pk=request.user.id)
            .select_related("profile", "unit")
        )
        if term:
            queryset = queryset.filter(
                Q(first_name__icontains=term)
                | Q(middle_name__icontains=term)
                | Q(last_name__icontains=term)
                | Q(serial_number__icontains=term)
                | Q(designation__icontains=term)
                | Q(unit__name__icontains=term)
                | Q(unit__acronym__icontains=term)
            ).order_by("last_name", "first_name", "id")
            limit = 25
        else:
            # An empty search is the "new message" shortlist: whoever has used
            # TOWEMS most recently.
            queryset = queryset.order_by("-profile__last_seen", "last_name", "id")
            limit = 15
        rows = list(queryset[:limit])
        return Response(
            {
                "people": [
                    serializers.person_payload(row, viewer_id=request.user.id)
                    for row in rows
                ],
                "term": term,
            }
        )


class ConversationListCreateView(MessagingBaseView):
    """The caller's inbox, and the way a thread is opened for the first time."""

    def get(self, request):
        enabled = services.private_messaging_enabled()
        rows = (
            list(services.conversations_for(request.user)[:100]) if enabled else []
        )
        payload = [serializers.conversation_payload(row, request.user) for row in rows]
        group = serializers.unit_group_payload(
            services.ensure_unit_group(request.user), request.user
        )
        custom = [
            serializers.custom_group_payload(row, request.user)
            for row in services.custom_groups_for(request.user)
        ]
        custom = [row for row in custom if row is not None]
        routing = [
            serializers.routing_thread_payload(row, request.user)
            for row in services.routing_threads_for(request.user)
        ]
        routing = [row for row in routing if row is not None]
        unread = sum(row["unread_count"] for row in payload)
        if group is not None:
            unread += group["unread_count"]
        unread += sum(row["unread_count"] for row in custom)
        unread += sum(row["unread_count"] for row in routing)
        return Response(
            {
                "conversations": payload,
                "unit_group": group,
                "custom_groups": custom,
                "routing_threads": routing,
                "private_messaging_enabled": enabled,
                "unread_total": unread,
                "count": len(payload)
                + len(custom)
                + len(routing)
                + (1 if group is not None else 0),
            }
        )

    def post(self, request):
        if not services.private_messaging_enabled():
            raise PermissionDenied(
                "Private one-to-one messaging is turned off by an Administrator."
            )
        serializer = serializers.StartConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        other = User.objects.filter(pk=serializer.validated_data["user_id"]).first()
        if other is None:
            return Response(
                {"detail": "That person is not a TOWEMS user."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            conversation, created = services.get_or_create_conversation(
                request.user, other, request=request
            )
        except services.MessagingError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        conversation = services.conversation_for_user(request.user, conversation.id)
        return Response(
            serializers.conversation_payload(conversation, request.user),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MessageListCreateView(MessagingBaseView):
    """The thread window (newest page, older pages, or a term) and sending."""

    def get(self, request, pk):
        conversation = self.conversation(request, pk)
        term = (request.query_params.get("q") or "").strip()[:100]
        try:
            before_id = int(request.query_params.get("before_id") or 0)
        except (TypeError, ValueError):
            before_id = 0
        try:
            page_size = int(request.query_params.get("page_size") or 0)
        except (TypeError, ValueError):
            page_size = 0
        page_size = min(max(page_size or services.message_page_size(), 1), 100)

        latest_id = (
            conversation.messages.order_by("-id")
            .values_list("id", flat=True)
            .first()
            or 0
        )
        # Fetching a window is delivery: the recipient's client now has it.
        # Mark first, then read, so the rows returned already carry the stamp.
        services.mark_delivered(conversation, request.user, up_to_id=latest_id)

        queryset = services.messages_query(conversation)
        if term:
            queryset = queryset.filter(body__icontains=term)
        if before_id:
            queryset = queryset.filter(id__lt=before_id)
        # Newest page first (cheap with the (conversation, id) index), then back
        # into reading order for the client.
        rows = list(queryset.order_by("-id")[:page_size])
        has_more = len(rows) == page_size
        rows.reverse()

        return Response(
            {
                "conversation": _thread_payload(conversation, request.user),
                "messages": [
                    serializers.message_payload(row, request.user.id) for row in rows
                ],
                "has_more": has_more,
                "term": term,
            }
        )

    def post(self, request, pk):
        conversation = self.conversation(request, pk)
        if conversation.is_group_request():
            # The thread is the routing record; free text would blur whose
            # decision stands. Proposals and decisions use the request actions.
            raise PermissionDenied(
                "Use the group chat request actions to approve or reject this request."
            )
        if conversation.is_unit_group():
            _can_view, can_post = services.unit_group_permissions(
                conversation, request.user
            )
            if not can_post:
                raise PermissionDenied(
                    "The Operations Center has turned off your permission to post in the unit chat."
                )
        elif conversation.is_custom_group():
            _can_view, can_post = services.custom_group_permissions(
                conversation, request.user
            )
            if not can_post:
                raise PermissionDenied(
                    "The Administrator has turned off chat in this group."
                )
        serializer = serializers.SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            message = services.send_message(
                conversation,
                request.user,
                serializer.validated_data["body"],
                request=request,
                reply_to_id=serializer.validated_data.get("reply_to"),
                mention_ids=serializer.validated_data.get("mention_ids"),
            )
        except services.MessagingError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        message = services.messages_query(conversation).get(pk=message.pk)
        return Response(
            serializers.message_payload(message, request.user.id),
            status=status.HTTP_201_CREATED,
        )


class MessageUnsendView(MessagingBaseView):
    """Withdraw one of the caller's own messages, leaving a trace in the thread."""

    def post(self, request, pk, message_id):
        conversation = self.conversation(request, pk)
        message = services.messages_query(conversation).filter(pk=message_id).first()
        if message is None:
            raise Http404("Message not found.")
        try:
            services.unsend_message(
                conversation, message, request.user, request=request
            )
        except services.MessagingError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        message = services.messages_query(conversation).get(pk=message.pk)
        return Response(serializers.message_payload(message, request.user.id))


class ConversationMembersView(MessagingBaseView):
    """Roster of a group chat for everyone who can open it.

    Rank order is the same everywhere personnel are listed (GEN first, AM
    last). ``can_manage`` says whether this viewer may edit the unit channel's
    View/Post permissions, which the roster renders only for them.
    """

    def get(self, request, pk):
        conversation = self.conversation(request, pk)
        if conversation.is_group_request():
            return Response(
                {"detail": "A group chat request's participants are its thread."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if conversation.is_unit_group():
            members = services.unit_group_view_roster(conversation, request.user)
            can_manage = services.is_unit_group_admin(
                request.user, conversation.unit_id
            )
        else:
            rows = services.conversation_roster(conversation)
            members = [
                serializers.conversation_member_payload(row, request.user.id)
                for row in rows
            ]
            can_manage = False
        return Response({"members": members, "can_manage": can_manage})


class ConversationPeopleView(MessagingBaseView):
    """People the caller may name with @ in this thread."""

    def get(self, request, pk):
        conversation = self.conversation(request, pk)
        rows = list(services.conversation_people(conversation, request.user)[:200])
        return Response(
            {
                "people": [
                    serializers.person_payload(row, viewer_id=request.user.id)
                    for row in rows
                ],
                "can_mention": not conversation.is_group_request(),
            }
        )


class ConversationReadView(MessagingBaseView):
    """Advance the caller's read cursor and clear the thread's notice."""

    def post(self, request, pk):
        conversation = self.conversation(request, pk)
        serializer = serializers.ReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.mark_read(
            conversation,
            request.user,
            up_to_id=serializer.validated_data.get("up_to_id"),
        )
        refreshed = services.conversation_for_viewer(request.user, conversation.id)
        return Response(
            {
                "conversation": _thread_payload(refreshed, request.user),
                "unread_total": services.inbox_unread_total(request.user),
            }
        )


class ConversationStateView(MessagingBaseView):
    """Per-person inbox actions: pin, mute, archive, unread and delete.

    These flags belong to one participant's view. They never change what the
    other participants see, and a new message restores a deleted thread.
    """

    def post(self, request, pk):
        enforce_csrf(request)
        conversation = self.conversation(request, pk)
        action = str(request.data.get("action") or "")
        try:
            services.set_conversation_state(conversation, request.user, action)
        except services.MessagingError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            {
                "action": action,
                "unread_total": services.inbox_unread_total(request.user),
            }
        )


class TypingView(MessagingBaseView):
    """Tell the other participant that this one is composing."""

    def post(self, request, pk):
        conversation = self.conversation(request, pk)
        serializer = serializers.TypingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.set_typing(
            conversation, request.user, serializer.validated_data["typing"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessageSearchView(MessagingBaseView):
    """Search the text of the caller's own conversations."""

    def get(self, request):
        term = (request.query_params.get("q") or "").strip()[:100]
        if len(term) < 2:
            return Response(
                {"detail": "Type at least two characters to search."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = services.search_messages(request.user, term)
        return Response(
            {
                "results": [
                    serializers.search_result_payload(row, request.user)
                    for row in rows
                ],
                "term": term,
            }
        )


class PushConfigView(MessagingBaseView):
    """Whether this deployment can push, and the key a browser subscribes with."""

    def get(self, request):
        enabled = push.configured()
        return Response(
            {
                "enabled": enabled,
                # The VAPID public key is public by design; the private half
                # never leaves the server environment.
                "public_key": push.public_key() if enabled else "",
            }
        )


class PushSubscribeView(MessagingBaseView):
    """Store (or refresh) one browser's push endpoint for the caller."""

    def post(self, request):
        if not push.configured():
            return Response(
                {"detail": "Push notifications are not configured on this server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        serializer = serializers.PushSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        keys = data["keys"]
        subscription, created = PushSubscription.objects.update_or_create(
            endpoint=data["endpoint"],
            defaults={
                # An endpoint belongs to the browser install, so the last person
                # signed in there owns it; signing out never leaves the previous
                # account receiving alerts on this device.
                "user": request.user,
                "p256dh": keys["p256dh"],
                "auth": keys["auth"],
                "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:255],
                "failure_count": 0,
            },
        )
        return Response(
            {"subscribed": True, "id": subscription.id},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PushUnsubscribeView(MessagingBaseView):
    """Forget this browser's endpoint for the caller (never somebody else's)."""

    def post(self, request):
        serializer = serializers.PushEndpointSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        PushSubscription.objects.filter(
            user=request.user, endpoint=serializer.validated_data["endpoint"]
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessagingSummaryView(MessagingBaseView):
    """The nav badge: how many messages are waiting, and in how many threads.

    Also the Android watcher's only messaging call: it reads the count, never a
    message, so a phone alert says only that something is waiting.
    """

    authentication_classes = ALERT_AUTHENTICATION
    permission_classes = [IsAuthenticated, DeviceTokenReadOnly]

    def get(self, request):
        enabled = services.private_messaging_enabled()
        unread = 0
        threads = 0
        if enabled:
            for row in services.conversations_for(request.user)[:100]:
                unread += row.unread_count
                if row.unread_count:
                    threads += 1
        group = services.ensure_unit_group(request.user, create=False)
        if group is not None:
            group_unread = services.unread_count(group, request.user.id)
            unread += group_unread
            if group_unread:
                threads += 1
        custom_unread = services.custom_group_unread_total(request.user)
        if custom_unread:
            unread += custom_unread
            threads += 1
        routing_unread = services.routing_thread_unread_total(request.user)
        if routing_unread:
            unread += routing_unread
            threads += 1
        return Response(
            {
                "unread_total": unread,
                "unread_threads": threads,
                "private_messaging_enabled": enabled,
                "retention_days": int(
                    getattr(settings, "MESSAGING_RETENTION_DAYS", 365)
                ),
            }
        )


class MessagingSettingsView(APIView):
    """The Super Administrator's private-messaging security switch."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        row = MessagingSettings.load()
        return Response(
            {
                "private_messaging_enabled": row.private_messaging_enabled,
                "can_manage": ownership.is_system_owner(request.user),
            }
        )

    def patch(self, request):
        if not ownership.is_system_owner(request.user):
            raise PermissionDenied(
                "Only an Administrator can change this setting."
            )
        enforce_csrf(request)
        serializer = serializers.MessagingSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = MessagingSettings.load()
        before = row.private_messaging_enabled
        row.private_messaging_enabled = serializer.validated_data[
            "private_messaging_enabled"
        ]
        row.updated_by = request.user
        row.save(update_fields=["private_messaging_enabled", "updated_by", "updated_at"])
        log_action(
            actor=request.user,
            action="update_messaging_settings",
            entity_type="settings",
            entity_id=row.pk,
            before={"private_messaging_enabled": before},
            after={"private_messaging_enabled": row.private_messaging_enabled},
            request=request,
        )
        return Response(
            {
                "private_messaging_enabled": row.private_messaging_enabled,
                "can_manage": True,
            }
        )


class GroupChatRequestListCreateView(MessagingBaseView):
    """The Operations Center's proposals, and every authority's routing list."""

    def get(self, request):
        rows = group_chats.requests_for(request.user)
        return Response(
            {
                "requests": [
                    serializers.group_chat_request_payload(row, request.user)
                    for row in rows
                ],
                "can_create": group_chats.can_create(request.user),
            }
        )

    def post(self, request):
        serializer = serializers.GroupChatRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            row = group_chats.create_request(
                request.user, request=request, **serializer.validated_data
            )
        except services.MessagingError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        row = group_chats.requests_for(request.user).get(pk=row.pk)
        return Response(
            serializers.group_chat_request_payload(row, request.user),
            status=status.HTTP_201_CREATED,
        )


class GroupChatRequestDecisionView(MessagingBaseView):
    """Approve or reject at the request's current stage."""

    def post(self, request, pk, decision):
        row = group_chats.requests_for(request.user).filter(pk=pk).first()
        if row is None:
            raise Http404("Group chat request not found.")
        serializer = serializers.GroupChatRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get("note", "")
        try:
            if decision == "approve":
                group_chats.approve(row, request.user, note=note, request=request)
            elif decision == "reject":
                group_chats.reject(row, request.user, note=note, request=request)
            else:
                raise Http404("Unknown decision.")
        except services.MessagingError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        row = group_chats.requests_for(request.user).get(pk=row.pk)
        return Response(serializers.group_chat_request_payload(row, request.user))


class AdminGroupChatListView(MessagingBaseView):
    """Super Administrator oversight: approved groups, grouped by unit."""

    def get(self, request):
        if not ownership.is_system_owner(request.user):
            raise PermissionDenied(
                "Only an Administrator can review group chat settings."
            )
        rows = list(group_chats.custom_groups_for_settings())
        units = {}
        for row in rows:
            if row.unit_id not in units:
                units[row.unit_id] = {
                    "id": row.unit_id,
                    "name": row.unit.name if row.unit else "",
                    "acronym": row.unit.acronym if row.unit else "",
                }
        return Response(
            {
                "groups": [serializers.admin_custom_group_payload(row) for row in rows],
                "units": list(units.values()),
            }
        )


class AdminGroupChatView(MessagingBaseView):
    """Enable/disable one approved group and its chat, for the whole unit."""

    def patch(self, request, pk):
        if not ownership.is_system_owner(request.user):
            raise PermissionDenied(
                "Only an Administrator can change a group chat."
            )
        row = Conversation.objects.filter(
            pk=pk, kind=Conversation.KIND_CUSTOM_GROUP
        ).first()
        if row is None:
            raise Http404("Group chat not found.")
        serializer = serializers.AdminGroupChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            row = group_chats.update_custom_group(
                row, request.user, **serializer.validated_data
            )
        except services.MessagingError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(serializers.admin_custom_group_payload(row))


class UnitGroupMembersView(MessagingBaseView):
    """The unit roster with channel permissions, for the group's admins."""

    def get(self, request):
        group = services.ensure_unit_group(request.user)
        if group is None:
            raise Http404("No unit chat is available for this account.")
        try:
            members = services.unit_group_members(group, request.user)
        except services.MessagingError as exc:
            raise PermissionDenied(str(exc))
        return Response(
            {
                "conversation_id": group.id,
                "members": members,
                "can_manage": True,
            }
        )


class UnitGroupMemberView(MessagingBaseView):
    """Enable or disable one unit member's view/post rights."""

    def patch(self, request, pk, user_id):
        group = services.ensure_unit_group(request.user)
        if group is None or group.id != pk:
            raise Http404("No unit chat is available for this account.")
        target = User.objects.filter(pk=user_id).first()
        if target is None:
            raise Http404("Unknown person.")
        serializer = serializers.UnitGroupMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            row = services.update_group_member(
                group, request.user, target, **serializer.validated_data
            )
        except services.MessagingError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(row)

"""Private one-to-one messaging inside TOWEMS.

Why the shape is what it is
===========================
* **One row per pair.** ``Conversation`` stores the two participants in
  ascending id order and the database itself enforces that order and their
  uniqueness. "The conversation between A and B" is therefore one row even when
  two tabs, two devices (or the Android client later) open it at the same time.
* **No public rooms.** A private thread has exactly two participant columns.
  The only group rows are the unit's one default channel and approved custom
  groups whose membership is chosen through the Commander/SA workflow.
* **Read state is a cursor, not a per-message flag.** Each participant's
  ``last_read_message_id`` answers "how much of this thread has been seen".
  Polling clients can fetch the same window repeatedly without inflating an
  unread count, and read receipts fall out of the same integer.
* **Bodies stay in the database, not in notifications or the audit trail.**
  A notification says who wrote; it never repeats what was written. The audit
  entry records that a message was sent, to whom, and when - not its text.

Timestamps follow the application convention (naive Asia/Manila wall clock,
see ``apps.core.timeutils``), so they sort and display exactly like the rest of
TOWEMS.
"""

from django.db import models
from django.db.models import F, Q

from apps.core import timeutils


class Conversation(models.Model):
    """A private pair thread, a unit channel or an approved custom group.

    Private threads keep the two participants in ordered, nullable columns. A
    unit group has no pair: its ``kind`` is ``unit_group`` and its ``unit`` is
    the only membership rule, so a unit has exactly one default channel. An
    approved custom group also belongs to a unit but is visible only to the
    personnel selected in its approved request.
    """

    KIND_PRIVATE = "private"
    KIND_UNIT_GROUP = "unit_group"
    KIND_CUSTOM_GROUP = "custom_group"
    #: One group chat request's routing record: the Operations Center, the Unit
    #: Commander and the Super Administrator follow and decide it as a Messages
    #: thread, so the proposal and every decision are ordinary messages.
    KIND_GROUP_REQUEST = "group_request"
    KIND_CHOICES = [
        (KIND_PRIVATE, "Private conversation"),
        (KIND_UNIT_GROUP, "Unit group chat"),
        (KIND_CUSTOM_GROUP, "Approved group chat"),
        (KIND_GROUP_REQUEST, "Group chat request"),
    ]

    kind = models.CharField(
        max_length=20, choices=KIND_CHOICES, default=KIND_PRIVATE, db_index=True
    )
    #: Name is used by an approved custom group; the default unit channel
    #: derives its title from the unit instead.
    name = models.CharField(max_length=120, blank=True, default="")
    #: Administrator controls for custom groups. The default unit channel
    #: is governed by the Operations Center's member permissions instead.
    is_enabled = models.BooleanField(default=True)
    chat_enabled = models.BooleanField(default=True)
    #: Set only on a unit group. The channel is the unit, not a joinable room.
    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="messaging_groups",
    )
    low_user = models.ForeignKey(
        "accounts.User",
        db_column="low_user_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="messaging_conversations_as_low",
    )
    high_user = models.ForeignKey(
        "accounts.User",
        db_column="high_user_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="messaging_conversations_as_high",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    created_at = models.DateTimeField(default=timeutils.now)
    updated_at = models.DateTimeField(auto_now=True)
    #: Denormalised so the inbox can be ordered without touching every message.
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "messaging_conversation"
        ordering = ["-last_message_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["low_user", "high_user"], name="messaging_conversation_pair_unique"
            ),
            models.CheckConstraint(
                condition=Q(kind__in=["unit_group", "custom_group", "group_request"])
                | Q(low_user_id__lt=F("high_user_id")),
                name="messaging_conversation_pair_ordered",
            ),
            # One channel per unit. A private row has no unit, so the partial
            # index never collides with the pair rows.
            models.UniqueConstraint(
                fields=["unit"],
                condition=Q(kind="unit_group"),
                name="messaging_unit_group_unique",
            ),
        ]

    def is_unit_group(self) -> bool:
        return self.kind == self.KIND_UNIT_GROUP

    def is_custom_group(self) -> bool:
        return self.kind == self.KIND_CUSTOM_GROUP

    def is_group_request(self) -> bool:
        return self.kind == self.KIND_GROUP_REQUEST

    def is_group(self) -> bool:
        return self.kind in {
            self.KIND_UNIT_GROUP,
            self.KIND_CUSTOM_GROUP,
            self.KIND_GROUP_REQUEST,
        }

    def participant_ids(self) -> set[int]:
        return {self.low_user_id, self.high_user_id}

    def other_user_id(self, user_id) -> int:
        return self.high_user_id if self.low_user_id == user_id else self.low_user_id

    def __str__(self) -> str:
        return f"Conversation {self.pk} ({self.low_user_id} / {self.high_user_id})"


class ConversationMember(models.Model):
    """One participant's position in a thread: how far they have read."""

    conversation = models.ForeignKey(
        Conversation,
        db_column="conversation_id",
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="messaging_memberships",
    )
    #: Highest message id this participant has read. Zero means nothing yet.
    last_read_message_id = models.PositiveBigIntegerField(default=0)
    last_read_at = models.DateTimeField(null=True, blank=True)
    #: Highest message id handed to this participant's client.
    last_delivered_message_id = models.PositiveBigIntegerField(default=0)
    #: Unit-group policy. A missing row means the unit member keeps the default
    #: (both allowed); the Operations Center creates the row when it restricts.
    can_view = models.BooleanField(default=True)
    can_post = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timeutils.now)

    class Meta:
        db_table = "messaging_conversation_member"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "user"],
                name="messaging_conversation_member_unique",
            )
        ]
        indexes = [models.Index(fields=["user"], name="msg_member_user_idx")]

    def __str__(self) -> str:
        return f"Conversation {self.conversation_id} member {self.user_id}"


class Message(models.Model):
    """One message in a private thread.

    A message is never edited. Retention deletes the oldest rows (and with them
    the conversation history); the audit trail keeps the fact that a message
    was sent, without its text.
    """

    conversation = models.ForeignKey(
        Conversation,
        db_column="conversation_id",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        "accounts.User",
        db_column="sender_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="messaging_messages_sent",
    )
    body = models.TextField()
    created_at = models.DateTimeField(default=timeutils.now)
    #: Set when the recipient's client fetched it.
    delivered_at = models.DateTimeField(null=True, blank=True)
    #: Set when the recipient opened the thread past it.
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "messaging_message"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["conversation", "id"], name="msg_conv_id_idx"),
            models.Index(fields=["sender", "created_at"], name="msg_sender_created_idx"),
        ]

    def status_for(self, viewer_id) -> str | None:
        """A sender sees sent/delivered/read; a recipient sees nothing."""
        if self.sender_id != viewer_id:
            return None
        if self.read_at:
            return "read"
        if self.delivered_at:
            return "delivered"
        return "sent"

    def __str__(self) -> str:
        return f"Message {self.pk} in conversation {self.conversation_id}"


class TypingState(models.Model):
    """The last time somebody told a thread they are typing.

    Transient by design: rows are cleared when a message is sent and ignored
    once they age past ``MESSAGING_TYPING_SECONDS``, so an abandoned composing
    window cannot leave a permanent "typing…" marker.
    """

    user = models.OneToOneField(
        "accounts.User",
        db_column="user_id",
        on_delete=models.CASCADE,
        related_name="messaging_typing",
    )
    conversation = models.ForeignKey(
        Conversation,
        db_column="conversation_id",
        on_delete=models.CASCADE,
        related_name="typing_states",
    )
    updated_at = models.DateTimeField(default=timeutils.now)

    class Meta:
        db_table = "messaging_typing"
        indexes = [models.Index(fields=["conversation"], name="msg_typing_conv_idx")]

    def __str__(self) -> str:
        return f"User {self.user_id} typing in {self.conversation_id}"


class GroupChatRequest(models.Model):
    """A proposed unit group chat, routed Commander then Super Administrator.

    The Operations Center names the group and selects its unit personnel; no
    free-form approval document is produced. The request's routing thread in
    Messages is the record, and each decision is both posted there and stored
    as an event for the audit trail.
    """

    STATUS_PENDING_COMMANDER = "pending_commander"
    STATUS_PENDING_SUPER_ADMIN = "pending_super_admin"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING_COMMANDER, "Pending Unit Commander"),
        (STATUS_PENDING_SUPER_ADMIN, "Pending Administrator"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="messaging_group_chat_requests",
    )
    name = models.CharField(max_length=120)
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="messaging_group_chat_requests",
    )
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING_COMMANDER
    )
    #: Set only when the Super Administrator gives final approval.
    conversation = models.OneToOneField(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="group_chat_request",
    )
    #: The routing thread in Messages: the proposal and every decision are
    #: posted there, so the authorities act on the request inside its thread.
    thread = models.OneToOneField(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="group_chat_request_thread",
    )
    commander_acted_by = models.ForeignKey(
        "accounts.User",
        db_column="commander_acted_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    commander_acted_at = models.DateTimeField(null=True, blank=True)
    super_admin_acted_by = models.ForeignKey(
        "accounts.User",
        db_column="super_admin_acted_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    super_admin_acted_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timeutils.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "messaging_group_chat_request"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["unit", "status"], name="msg_gc_req_unit_idx"),
            models.Index(fields=["created_by"], name="msg_gc_req_creator_idx"),
        ]

    def __str__(self) -> str:
        return f"GC request {self.pk}: {self.name}"


class GroupChatRequestMember(models.Model):
    """One person the Operations Center selected for the proposed group."""

    request = models.ForeignKey(
        GroupChatRequest, on_delete=models.CASCADE, related_name="selected_members"
    )
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="messaging_group_chat_selections",
    )

    class Meta:
        db_table = "messaging_group_chat_request_member"
        constraints = [
            models.UniqueConstraint(
                fields=["request", "user"], name="msg_gc_request_member_unique"
            )
        ]

    def __str__(self) -> str:
        return f"Request {self.request_id} member {self.user_id}"


class GroupChatRequestEvent(models.Model):
    """Append-only routing record shown as the request's message thread."""

    ACTION_SUBMITTED = "submitted"
    ACTION_COMMANDER_APPROVED = "commander_approved"
    ACTION_SUPER_ADMIN_APPROVED = "super_admin_approved"
    ACTION_REJECTED = "rejected"
    ACTION_CHOICES = [
        (ACTION_SUBMITTED, "Submitted"),
        (ACTION_COMMANDER_APPROVED, "Approved by Unit Commander"),
        (ACTION_SUPER_ADMIN_APPROVED, "Approved by Administrator"),
        (ACTION_REJECTED, "Rejected"),
    ]

    request = models.ForeignKey(
        GroupChatRequest, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        "accounts.User",
        db_column="actor_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timeutils.now)

    class Meta:
        db_table = "messaging_group_chat_request_event"
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"Request {self.request_id}: {self.action}"


class MessagingSettings(models.Model):
    """System-wide messaging switches, held in one row.

    Private one-to-one messaging can be turned off for security without
    affecting the unit group channel, which is the unit's operational line and
    has its own per-person controls.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    private_messaging_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        "accounts.User",
        db_column="updated_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )

    class Meta:
        db_table = "messaging_settings"
        verbose_name_plural = "messaging settings"

    @classmethod
    def load(cls):
        row, _created = cls.objects.get_or_create(pk=1)
        return row

    def __str__(self) -> str:
        return "Messaging settings"


class PushSubscription(models.Model):
    """One browser's Web Push endpoint for one account.

    The browser owns the endpoint and its two encryption keys; the server only
    keeps them so it can ask the browser's push service to wake the device when
    TOWEMS is not open. An endpoint identifies a browser install, not a person:
    signing in as somebody else on the same browser moves the row to that
    account, so a stale subscription can never deliver another person's
    messages.
    """

    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="messaging_push_subscriptions",
    )
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(default=timeutils.now)
    updated_at = models.DateTimeField(auto_now=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "messaging_push_subscription"
        indexes = [models.Index(fields=["user"], name="msg_push_user_idx")]

    def as_subscription_info(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }

    def __str__(self) -> str:
        return f"Push subscription {self.pk} for user {self.user_id}"

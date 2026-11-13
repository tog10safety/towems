"""Command-and-control tables added by the TOWEMS enhancement.

Every model here is new (``managed = True``) and is introduced by an additive
migration. No existing table or column is altered; legacy rows are referenced
with ``db_constraint=False`` so PostgreSQL never gains a constraint on a table
the Flask schema owns.
"""

from __future__ import annotations

from django.db import models

from apps.core.roles import (
    APPOINTMENT_ROLE_CHOICES,
    ROLE_COMMANDER,
    ROLE_DEPUTY_COMMANDER,
)


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# Office functions live at module level so both the model and its Meta
# constraints can use them. ``operations_center`` is also the key in
# ``apps.core.roles.OFFICE_FUNCTION_ROLE_MAP``.
OFFICE_FUNCTION_GENERAL = "general"
OFFICE_FUNCTION_OPERATIONS_CENTER = "operations_center"
OFFICE_FUNCTION_CHOICES = [
    (OFFICE_FUNCTION_GENERAL, "General office/section"),
    (
        OFFICE_FUNCTION_OPERATIONS_CENTER,
        "Operations Center — oversees this unit's TOWEMS (routing and coordination)",
    ),
]


class UnitAppointment(TimestampedModel):
    """A named TOWEMS post held by a person inside one unit.

    The legacy ``users.authority`` enum keeps its four values untouched; these
    rows add the command structure on top. ``EXCLUSIVE_APPOINTMENT_ROLES``
    (Commander, Deputy Commander, Director for Operations) are capped at one
    active holder per unit by a partial unique index - the organisational
    constraint required by the specification.
    """

    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="appointments",
    )
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="appointments",
    )
    role = models.CharField(max_length=40, choices=APPOINTMENT_ROLE_CHOICES)
    is_active = models.BooleanField(default=True)

    appointed_by = models.ForeignKey(
        "accounts.User",
        db_column="appointed_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        "accounts.User",
        db_column="ended_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "command_unit_appointment"
        ordering = ["unit_id", "role", "id"]
        indexes = [
            models.Index(fields=["user", "is_active"], name="cmd_appt_user_idx"),
            models.Index(fields=["unit", "role"], name="cmd_appt_unit_role_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "role"],
                condition=models.Q(
                    is_active=True,
                    role__in=[ROLE_COMMANDER, ROLE_DEPUTY_COMMANDER, "director_operations"],
                ),
                name="command_one_active_holder_per_post",
            ),
            # One person, one instance of a post, in every unit: a designation
            # is never duplicated between units, so nobody is Commander of two.
            # A *different* post at a higher headquarters remains possible as
            # detached service - see ``services.create_appointment``.
            models.UniqueConstraint(
                fields=["user", "role"],
                condition=models.Q(is_active=True),
                name="command_one_active_post_per_person",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_role_display()} - unit {self.unit_id} (user {self.user_id})"

    @property
    def is_detached_service(self) -> bool:
        """True when this post sits outside the person's own unit (DS)."""
        home = getattr(self.user, "unit_id", None) if self.user_id else None
        return bool(home and self.unit_id != home)


class ActingAppointment(TimestampedModel):
    """Temporary delegation of command authority (spec §12B).

    Records who granted it, to whom, for which unit and post, why, for how long
    and exactly which capabilities were delegated. Expired rows stop applying
    automatically because every capability lookup filters on the time window.
    """

    ACTING_ROLE_CHOICES = [
        (ROLE_COMMANDER, "Acting Commander"),
        (ROLE_DEPUTY_COMMANDER, "Acting Deputy Commander"),
    ]

    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="acting_appointments",
    )
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="acting_appointments",
    )
    acting_role = models.CharField(max_length=40, choices=ACTING_ROLE_CHOICES)

    appointed_by = models.ForeignKey(
        "accounts.User",
        db_column="appointed_by",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    reason = models.TextField()

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    delegated_capabilities = models.JSONField(
        default=list,
        blank=True,
        help_text="Capability names delegated. Empty means the role's own set.",
    )

    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        "accounts.User",
        db_column="revoked_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    revoked_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "command_acting_appointment"
        ordering = ["-starts_at", "-id"]
        indexes = [
            models.Index(fields=["user", "is_active"], name="cmd_acting_user_idx"),
            models.Index(fields=["unit", "acting_role"], name="cmd_acting_unit_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="command_acting_window_valid",
            )
        ]

    def __str__(self) -> str:
        return f"Acting {self.get_acting_role_display()} - unit {self.unit_id}"

    @property
    def is_current(self) -> bool:
        from django.utils import timezone

        now = timezone.now()
        return bool(
            self.is_active
            and self.revoked_at is None
            and self.starts_at <= now < self.ends_at
        )


class OfficeSection(TimestampedModel):
    """An office or section inside a unit (Admin Officer manages these).

    ``function`` distinguishes an ordinary office from one that carries standing
    authority for the whole unit. The Operations Center is such an office: every
    active member of it holds the Operations Center role for that unit (see
    ``apps.core.roles.OFFICE_FUNCTION_ROLE_MAP``). Membership is recorded through
    ``SectionAssignment``, so the authority belongs to the office rather than to
    any individual post.
    """

    KIND_CHOICES = [("office", "Office"), ("section", "Section")]

    FUNCTION_GENERAL = OFFICE_FUNCTION_GENERAL
    FUNCTION_OPERATIONS_CENTER = OFFICE_FUNCTION_OPERATIONS_CENTER
    FUNCTION_CHOICES = OFFICE_FUNCTION_CHOICES

    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="offices",
    )
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=40)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="section")
    #: True for an office/section the user typed in via "Specify - Other"
    #: rather than one of the standard staff offices (``apps.command.offices``).
    is_custom = models.BooleanField(default=False)
    function = models.CharField(
        max_length=40, choices=OFFICE_FUNCTION_CHOICES, default=OFFICE_FUNCTION_GENERAL
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )

    class Meta:
        db_table = "command_office_section"
        ordering = ["unit_id", "kind", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "code"], name="command_office_unit_code_unique"
            ),
            # A unit has one Operations Center, not several competing ones.
            models.UniqueConstraint(
                fields=["unit"],
                condition=models.Q(
                    is_active=True, function=OFFICE_FUNCTION_OPERATIONS_CENTER
                ),
                name="command_one_operations_center_per_unit",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    @property
    def label(self) -> str:
        """How the office is presented in pickers: ``A3 - Operations``."""
        return f"{self.code} - {self.name}"


class SectionAssignment(TimestampedModel):
    """A person's primary and (optional) secondary office/section.

    History is preserved: changing a section ends the previous row
    (``is_active=False``, ``ended_at`` set) instead of deleting it.
    """

    TYPE_PRIMARY = "primary"
    TYPE_SECONDARY = "secondary"
    TYPE_CHOICES = [(TYPE_PRIMARY, "Primary"), (TYPE_SECONDARY, "Secondary")]

    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="section_assignments",
    )
    section = models.ForeignKey(
        OfficeSection, on_delete=models.PROTECT, related_name="assignments"
    )
    assignment_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    is_active = models.BooleanField(default=True)

    assigned_by = models.ForeignKey(
        "accounts.User",
        db_column="assigned_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(
        "accounts.User",
        db_column="ended_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "command_section_assignment"
        ordering = ["user_id", "assignment_type"]
        indexes = [
            models.Index(fields=["section", "is_active"], name="cmd_section_active_idx"),
        ]
        constraints = [
            # One active primary and one active secondary per person.
            models.UniqueConstraint(
                fields=["user", "assignment_type"],
                condition=models.Q(is_active=True),
                name="command_one_active_assignment_per_type",
            ),
            # The same section cannot be assigned twice to the same person.
            models.UniqueConstraint(
                fields=["user", "section", "assignment_type"],
                condition=models.Q(is_active=True),
                name="command_no_duplicate_section_assignment",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} -> {self.section_id} ({self.assignment_type})"


class Instruction(TimestampedModel):
    """A note, instruction or action item issued by an authorised post.

    The instruction, every recipient's acknowledgement, and the completion /
    verification record are all retained. Nothing is deleted when an item is
    addressed; the row simply moves to a terminal ``status``.
    """

    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_VERIFIED = "verified"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
        ("urgent", "Urgent"),
    ]

    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="instructions",
    )
    sender = models.ForeignKey(
        "accounts.User",
        db_column="sender_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="issued_instructions",
    )
    subject = models.CharField(max_length=200)
    body = models.TextField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="normal")

    mission = models.ForeignKey(
        "ops.Mission",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="instructions",
    )
    related_type = models.CharField(max_length=60, blank=True, default="")
    related_id = models.CharField(max_length=60, blank=True, default="")

    requires_acknowledgement = models.BooleanField(default=True)
    requires_completion = models.BooleanField(default=False)
    due_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    verified_by = models.ForeignKey(
        "accounts.User",
        db_column="verified_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "command_instruction"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["unit", "status"], name="cmd_instr_unit_status_idx"),
            models.Index(fields=["sender", "-created_at"], name="cmd_instr_sender_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.subject} ({self.status})"


class InstructionRecipient(TimestampedModel):
    """Per-recipient state for an instruction - acknowledgement ≠ completion."""

    instruction = models.ForeignKey(
        Instruction, on_delete=models.CASCADE, related_name="recipients"
    )
    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="instruction_items",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "command_instruction_recipient"
        ordering = ["instruction_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["instruction", "user"], name="command_instruction_recipient_unique"
            )
        ]

    @property
    def is_addressed(self) -> bool:
        return self.completed_at is not None


class InstructionAttachment(TimestampedModel):
    """Evidence a recipient attached when marking their part complete.

    Only PDF documents and pictures are accepted; the kind is sniffed from the
    bytes on upload, never trusted from the browser's filename or header.
    """

    recipient = models.ForeignKey(
        InstructionRecipient, on_delete=models.CASCADE, related_name="attachments"
    )
    file_name = models.CharField(max_length=255)
    original_name = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=100, blank=True, default="")
    size_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "command_instruction_attachment"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.original_name or self.file_name


class MissionRouting(models.Model):
    """Append-only record of a mission moving through the approval chain."""

    STAGE_OPERATIONS_CENTER = "operations_center"
    STAGE_OPERATIONS_OFFICER = "operations_officer"
    STAGE_DIRECTOR_OPERATIONS = "director_operations"
    STAGE_DEPUTY_COMMANDER = "deputy_commander"
    STAGE_COMMANDER = "commander"
    STAGE_DECISION_AUTHORITY = "decision_authority"
    STAGE_MISSION_ORDER_SIGNATURE = "mission_order_signature"
    STAGE_CHOICES = [
        (STAGE_OPERATIONS_CENTER, "Operations Center"),
        (STAGE_OPERATIONS_OFFICER, "Operations Officer"),
        (STAGE_DIRECTOR_OPERATIONS, "Director for Operations"),
        (STAGE_DEPUTY_COMMANDER, "Deputy Commander"),
        (STAGE_COMMANDER, "Commander"),
        (STAGE_DECISION_AUTHORITY, "Decision Authority"),
        (STAGE_MISSION_ORDER_SIGNATURE, "Mission Order signatory"),
    ]

    ACTION_ROUTED = "routed"
    ACTION_APPROVED = "approved"
    ACTION_REJECTED = "rejected"
    ACTION_RETURNED = "returned"
    ACTION_CHOICES = [
        (ACTION_ROUTED, "Routed"),
        (ACTION_APPROVED, "Approved"),
        (ACTION_REJECTED, "Rejected"),
        (ACTION_RETURNED, "Returned for correction"),
    ]

    mission = models.ForeignKey(
        "ops.Mission", on_delete=models.CASCADE, related_name="routing"
    )
    stage = models.CharField(max_length=40, choices=STAGE_CHOICES)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    actor = models.ForeignKey(
        "accounts.User",
        db_column="actor_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="mission_routing_actions",
    )
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "command_mission_routing"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["mission", "stage"], name="cmd_routing_mission_idx")]

    def __str__(self) -> str:
        return f"mission {self.mission_id} {self.stage}:{self.action}"


class ApprovalRule(TimestampedModel):
    """Configurable Decision Authority (spec §12C).

    A row may be unit-specific, category-specific, or global. The most specific
    active row wins; with no rows at all the defaults in ``apps.core.roles``
    apply, so routing behaves predictably on a fresh deployment.
    """

    ENTITY_MISSION = "mission"
    ENTITY_MRM = "mrm"
    ENTITY_CHOICES = [(ENTITY_MISSION, "Mission"), (ENTITY_MRM, "MRM assessment")]

    entity = models.CharField(max_length=20, choices=ENTITY_CHOICES)
    category = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Mission category (air/ground) or blank for all.",
    )
    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="approval_rules",
        help_text="Blank applies to the whole organisation.",
    )
    steps = models.JSONField(default=list, blank=True)
    approver_roles = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "command_approval_rule"
        ordering = ["entity", "unit_id", "id"]

    def __str__(self) -> str:
        return f"{self.entity}/{self.category or '*'} rule"


class HazardLocation(models.Model):
    """Georeference for a legacy hazard-registry row (the registry has none).

    Additive: ``hazard_registry`` is untouched and keeps its meaning. A hazard
    without a row here simply does not appear on the map, and the map says so
    instead of inventing a position.
    """

    hazard = models.ForeignKey(
        "legacy.HazardRegistry",
        db_column="hazard_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="locations",
    )
    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="hazard_locations",
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    mgrs = models.CharField(max_length=32, blank=True, default="")
    radius_metres = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "command_hazard_location"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["unit"], name="cmd_hazard_unit_idx")]


class VehiclePosition(models.Model):
    """Last reported position of a vehicle - never labelled as live tracking."""

    SOURCE_CHOICES = [
        ("manual", "Manual report"),
        ("trip_start", "Trip departure"),
        ("trip_arrival", "Trip arrival"),
        ("driver_report", "Driver report"),
    ]

    vehicle = models.ForeignKey(
        "ops.Vehicle", on_delete=models.CASCADE, related_name="positions"
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    mgrs = models.CharField(max_length=32, blank=True, default="")
    reported_at = models.DateTimeField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="manual")
    reported_by = models.ForeignKey(
        "accounts.User",
        db_column="reported_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "command_vehicle_position"
        ordering = ["-reported_at", "-id"]
        indexes = [models.Index(fields=["vehicle", "-reported_at"], name="cmd_vehpos_idx")]


class UnitMapMarker(TimestampedModel):
    """Where a unit sits on the map, and the seal used as its pin.

    The legacy ``units`` table has no coordinate or image column and must not be
    altered, so the location and the uploaded logo live in this additive side
    table: one row per unit, created the first time an Administrator sets either.

    The pin is drawn from ``logo_filename`` (the unit's logo or seal). When no
    image has been uploaded the map falls back to a neutral unit marker, so a
    unit is never invisible just because nobody uploaded a seal.
    """

    unit = models.OneToOneField(
        "legacy.Unit",
        db_column="unit_id",
        primary_key=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="map_marker",
    )
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    mgrs = models.CharField(max_length=40, blank=True, default="")
    #: Optional place description ("Camp Evangelista, Butuan City").
    place_name = models.CharField(max_length=200, blank=True, default="")
    logo_filename = models.CharField(max_length=200, blank=True, default="")
    logo_content_type = models.CharField(max_length=60, blank=True, default="")

    class Meta:
        db_table = "command_unit_map_marker"
        ordering = ["unit_id"]
        verbose_name = "unit map marker"

    def __str__(self) -> str:
        return f"Marker for unit {self.unit_id}"

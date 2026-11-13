"""Serializers for command structure, delegations, sections and instructions."""

from __future__ import annotations

from rest_framework import serializers

from apps.command.models import (
    ActingAppointment,
    ApprovalRule,
    HazardLocation,
    Instruction,
    InstructionAttachment,
    InstructionRecipient,
    MissionRouting,
    OfficeSection,
    SectionAssignment,
    UnitAppointment,
    VehiclePosition,
)
from apps.command.offices import (
    DEFAULT_OFFICE_CATALOGUE,
    DEFAULT_OFFICE_NAMES,
    OTHER_OFFICE_CODE,
    is_catalogue_code,
    next_custom_code,
)

from apps.core.roles import (
    APPOINTMENT_ROLE_VALUES,
    OFFICE_ROLE_VALUES,
    ROLE_LABELS,
)


class UnitAppointmentSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    #: The serial number is how a person is identified when a name is not unique.
    user_serial_number = serializers.CharField(source="user.serial_number", read_only=True)
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    #: A post held at a higher headquarters alongside the person's own unit post.
    is_detached_service = serializers.BooleanField(read_only=True)

    class Meta:
        model = UnitAppointment
        fields = [
            "id",
            "unit",
            "unit_name",
            "unit_acronym",
            "user",
            "username",
            "user_name",
            "user_serial_number",
            "role",
            "role_label",
            "is_active",
            "is_detached_service",
            "appointed_by",
            "ended_at",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class UnitAppointmentWriteSerializer(serializers.Serializer):
    """One post, one unit.

    The choice list is wide enough to explain *why* a role cannot be appointed:
    account roles live on the account, and the Operations Center is an office.
    """

    unit_id = serializers.IntegerField()
    user_id = serializers.IntegerField()
    role = serializers.ChoiceField(choices=list(ROLE_LABELS))
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_role(self, value):
        if value in OFFICE_ROLE_VALUES:
            raise serializers.ValidationError(
                f"{ROLE_LABELS[value]} is an office, not a post. Create that office for "
                "the unit and assign personnel to it under Office/Section management."
            )
        if value not in APPOINTMENT_ROLE_VALUES:
            raise serializers.ValidationError(
                f"{ROLE_LABELS.get(value, value)} is an account role, not a unit "
                "appointment. Change it on the account instead."
            )
        return value


class ActingAppointmentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    appointed_by_username = serializers.CharField(
        source="appointed_by.username", read_only=True, default=""
    )
    revoked_by_username = serializers.CharField(
        source="revoked_by.username", read_only=True, default=""
    )
    is_current = serializers.BooleanField(read_only=True)
    role_label = serializers.CharField(source="get_acting_role_display", read_only=True)

    class Meta:
        model = ActingAppointment
        fields = [
            "id",
            "unit",
            "unit_name",
            "user",
            "username",
            "user_name",
            "acting_role",
            "role_label",
            "appointed_by",
            "appointed_by_username",
            "reason",
            "starts_at",
            "ends_at",
            "delegated_capabilities",
            "is_active",
            "is_current",
            "revoked_at",
            "revoked_by",
            "revoked_by_username",
            "revoked_reason",
            "created_at",
        ]
        read_only_fields = fields


class ActingAppointmentWriteSerializer(serializers.Serializer):
    unit_id = serializers.IntegerField()
    user_id = serializers.IntegerField()
    acting_role = serializers.ChoiceField(choices=[choice[0] for choice in ActingAppointment.ACTING_ROLE_CHOICES])
    reason = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    delegated_capabilities = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )

    def validate(self, attrs):
        if attrs["ends_at"] <= attrs["starts_at"]:
            raise serializers.ValidationError(
                {"ends_at": "The end of the acting period must be after its start."}
            )
        return attrs


class OfficeSectionSerializer(serializers.ModelSerializer):
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True, default="")
    function_label = serializers.CharField(source="get_function_display", read_only=True)
    member_count = serializers.SerializerMethodField()
    #: Selection-driven fields, kept optional: a standard office is chosen by
    #: ``catalogue_code`` and a custom one by ``custom_name`` (the "Specify -
    #: Other Office/Section" option), so the client never has to invent codes.
    catalogue_code = serializers.CharField(write_only=True, required=False, default="")
    custom_name = serializers.CharField(write_only=True, required=False, default="")
    # Required only when the client is not selecting a catalogue entry; the
    # catalogue supplies both values server-side. ``validate`` enforces this.
    name = serializers.CharField(required=False, allow_blank=True)
    code = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = OfficeSection
        fields = [
            "id",
            "unit",
            "unit_name",
            "unit_acronym",
            "name",
            "code",
            "kind",
            "function",
            "function_label",
            "is_custom",
            "catalogue_code",
            "custom_name",
            "parent",
            "parent_name",
            "description",
            "is_active",
            "member_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "member_count",
            "function_label",
        ]
        # The model's unique constraints are enforced by the database; the
        # generated UniqueTogetherValidator would demand ``code``/``name``
        # before ``validate`` can supply them from the catalogue. Duplicates are
        # checked explicitly in ``validate``.
        validators = []

    def get_member_count(self, obj) -> int:
        return obj.assignments.filter(is_active=True).count()

    def validate(self, attrs):
        unit = attrs.get("unit") or getattr(self.instance, "unit", None)
        code = attrs.get("code") or getattr(self.instance, "code", "")
        parent = attrs.get("parent")
        catalogue_code = (attrs.pop("catalogue_code", "") or "").strip().upper()
        custom_name = (attrs.pop("custom_name", "") or "").strip()

        # -- catalogue selection ("Specify - Other" carries a typed name) ---
        if catalogue_code:
            if not is_catalogue_code(catalogue_code):
                raise serializers.ValidationError(
                    {"catalogue_code": "Unknown office in the standard catalogue."}
                )
            if catalogue_code == OTHER_OFFICE_CODE:
                if not custom_name:
                    raise serializers.ValidationError(
                        {"custom_name": "Enter the name of the office or section."}
                    )
                code = ""  # allocated below when the row is created
                attrs["name"] = custom_name
                attrs["kind"] = attrs.get("kind") or "section"
                attrs["is_custom"] = True
            else:
                # A standard office keeps its fixed code and default name.
                attrs["code"] = catalogue_code
                attrs["name"] = DEFAULT_OFFICE_NAMES[catalogue_code]
                attrs["kind"] = dict(
                    (row[0], row[2]) for row in DEFAULT_OFFICE_CATALOGUE
                )[catalogue_code]
                attrs["is_custom"] = False
            code = attrs.get("code") or code

        if self.instance is not None and catalogue_code and self.instance.is_custom:
            raise serializers.ValidationError(
                {"catalogue_code": "A custom office cannot be converted to a standard one."}
            )

        name = (attrs.get("name") or getattr(self.instance, "name", "") or "").strip()
        if not name:
            raise serializers.ValidationError({"name": "A name is required."})
        attrs["name"] = name

        # ``is_custom`` is server-controlled: a row is custom unless it carries
        # one of the fixed catalogue codes, so an old client that posts only
        # ``code``/``name`` is classified correctly.
        if not catalogue_code and (not self.instance or "code" in attrs):
            attrs["is_custom"] = bool(code) and not is_catalogue_code(code)

        function = attrs.get("function") or getattr(
            self.instance, "function", OfficeSection.FUNCTION_GENERAL
        )
        if parent is not None and unit is not None and parent.unit_id != unit.id:
            raise serializers.ValidationError(
                {"parent": "A parent office must belong to the same unit."}
            )
        if code:
            duplicates = OfficeSection.objects.filter(unit=unit, code=code)
            if self.instance is not None:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            existing = duplicates.first()
            if existing is not None:
                if catalogue_code and catalogue_code != OTHER_OFFICE_CODE:
                    # The interface disables catalogue entries the unit already
                    # has; a stale page or a hand-made request lands here. The
                    # error is keyed to the picker so it shows inline.
                    state = (
                        "already exists in this unit"
                        if existing.is_active
                        else "exists in this unit but is deactivated; reactivate it instead"
                    )
                    raise serializers.ValidationError(
                        {
                            "catalogue_code": (
                                f"{existing.name} {state} ({existing.code}). "
                                "Choose a different office or section."
                            )
                        }
                    )
                raise serializers.ValidationError(
                    {
                        "code": (
                            "That code is already used by another office/section in this "
                            "unit; office codes must be unique per unit."
                        )
                    }
                )

        # One unit cannot hold the same office twice, whatever its code.
        same_name = OfficeSection.objects.filter(unit=unit, name__iexact=name, is_active=True)
        if self.instance is not None:
            same_name = same_name.exclude(pk=self.instance.pk)
        if same_name.exists():
            raise serializers.ValidationError(
                {
                    "name": (
                        "This unit already has an active office/section with that name "
                        f"({same_name.first().code})."
                    )
                }
            )

        if function == OfficeSection.FUNCTION_OPERATIONS_CENTER:
            others = OfficeSection.objects.filter(
                unit=unit,
                function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
                is_active=True,
            )
            if self.instance is not None:
                others = others.exclude(pk=self.instance.pk)
            if others.exists():
                raise serializers.ValidationError(
                    {
                        "function": (
                            "This unit already has an active Operations Center office "
                            f"({others.first().code}). A unit has one Operations Center; "
                            "deactivate the existing one first."
                        )
                    }
                )
        return attrs

    def create(self, validated_data):
        if not validated_data.get("code"):
            # "Specify - Other": allocate the next X-nn code for this unit.
            validated_data["code"] = next_custom_code(validated_data["unit"])
            validated_data["is_custom"] = True
        return super().create(validated_data)


class SectionAssignmentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    section_name = serializers.CharField(source="section.name", read_only=True)
    section_code = serializers.CharField(source="section.code", read_only=True)
    section_kind = serializers.CharField(source="section.kind", read_only=True)
    unit_id = serializers.IntegerField(source="section.unit_id", read_only=True)
    unit_acronym = serializers.CharField(source="section.unit.acronym", read_only=True)
    assigned_by_username = serializers.CharField(
        source="assigned_by.username", read_only=True, default=""
    )

    class Meta:
        model = SectionAssignment
        fields = [
            "id",
            "user",
            "username",
            "user_name",
            "section",
            "section_name",
            "section_code",
            "section_kind",
            "unit_id",
            "unit_acronym",
            "assignment_type",
            "is_active",
            "assigned_by",
            "assigned_by_username",
            "notes",
            "created_at",
            "ended_at",
        ]
        read_only_fields = fields


class SectionAssignmentWriteSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    section_id = serializers.IntegerField()
    assignment_type = serializers.ChoiceField(
        choices=[SectionAssignment.TYPE_PRIMARY, SectionAssignment.TYPE_SECONDARY]
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class MissionRoutingSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True)
    actor_name = serializers.CharField(source="actor.full_name", read_only=True)

    class Meta:
        model = MissionRouting
        fields = [
            "id",
            "mission",
            "stage",
            "action",
            "actor",
            "actor_username",
            "actor_name",
            "note",
            "created_at",
        ]
        read_only_fields = fields


class InstructionAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstructionAttachment
        fields = ["id", "original_name", "content_type", "size_bytes", "created_at"]
        read_only_fields = fields


class InstructionRecipientSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    is_addressed = serializers.BooleanField(read_only=True)
    attachments = InstructionAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = InstructionRecipient
        fields = [
            "id",
            "user",
            "username",
            "user_name",
            "acknowledged_at",
            "started_at",
            "completed_at",
            "completion_note",
            "is_addressed",
            "attachments",
        ]
        read_only_fields = fields


class InstructionSerializer(serializers.ModelSerializer):
    recipients = InstructionRecipientSerializer(many=True, read_only=True)
    sender_username = serializers.CharField(source="sender.username", read_only=True)
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    mission_number = serializers.CharField(source="mission.mission_number", read_only=True, default="")

    class Meta:
        model = Instruction
        fields = [
            "id",
            "unit",
            "unit_acronym",
            "sender",
            "sender_username",
            "sender_name",
            "subject",
            "body",
            "priority",
            "mission",
            "mission_number",
            "related_type",
            "related_id",
            "requires_acknowledgement",
            "requires_completion",
            "due_at",
            "status",
            "recipients",
            "verified_by",
            "verified_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class InstructionWriteSerializer(serializers.Serializer):
    unit_id = serializers.IntegerField()
    subject = serializers.CharField(max_length=200)
    body = serializers.CharField()
    recipient_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    mission_id = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.ChoiceField(
        choices=[choice[0] for choice in Instruction.PRIORITY_CHOICES], default="normal"
    )
    requires_acknowledgement = serializers.BooleanField(default=True)
    requires_completion = serializers.BooleanField(default=False)
    due_at = serializers.DateTimeField(required=False, allow_null=True)


class ApprovalRuleSerializer(serializers.ModelSerializer):
    unit_name = serializers.CharField(source="unit.name", read_only=True, default="")

    class Meta:
        model = ApprovalRule
        fields = [
            "id",
            "entity",
            "category",
            "unit",
            "unit_name",
            "steps",
            "approver_roles",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class HazardLocationSerializer(serializers.ModelSerializer):
    hazard_description = serializers.CharField(
        source="hazard.hazard_description", read_only=True, default=""
    )
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True, default="")

    class Meta:
        model = HazardLocation
        fields = [
            "id",
            "hazard",
            "hazard_description",
            "unit",
            "unit_acronym",
            "latitude",
            "longitude",
            "mgrs",
            "radius_metres",
            "note",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class VehiclePositionSerializer(serializers.ModelSerializer):
    registration_number = serializers.CharField(
        source="vehicle.registration_number", read_only=True
    )

    class Meta:
        model = VehiclePosition
        fields = [
            "id",
            "vehicle",
            "registration_number",
            "latitude",
            "longitude",
            "mgrs",
            "reported_at",
            "source",
            "note",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

"""Serializers for the Armory Management System."""

from __future__ import annotations

from rest_framework import serializers

from apps.accounts.models import User
from apps.core import timeutils
from apps.legacy.models import Unit
from apps.ops.models import Mission

from .models import (
    Ammunition,
    ArmoryTransaction,
    ArmoryTransactionLine,
    Firearm,
    FirearmInspection,
    FirearmSerialChange,
    PersonnelFirearmAssignment,
    normalise_identifier,
)
from .services import user_unit_ids


def _user_name(user) -> str:
    if user is None:
        return ""
    getter = getattr(user, "get_full_name", None)
    return getter() if callable(getter) else ""


class UserSummarySerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "name",
            "rank",
            "serial_number",
            "branch_of_service",
            "unit",
            "unit_name",
            "unit_acronym",
            "is_active",
        ]

    def get_name(self, obj) -> str:
        return _user_name(obj)


class FirearmSummarySerializer(serializers.ModelSerializer):
    type_label = serializers.CharField(source="get_firearm_type_display", read_only=True)
    condition_label = serializers.CharField(
        source="get_condition_status_display", read_only=True
    )
    custody_label = serializers.CharField(
        source="get_custody_status_display", read_only=True
    )
    available = serializers.BooleanField(read_only=True)
    assigned_unit_name = serializers.CharField(source="assigned_unit.name", read_only=True)
    assigned_unit_acronym = serializers.CharField(
        source="assigned_unit.acronym", read_only=True
    )
    assigned_personnel_name = serializers.SerializerMethodField()

    class Meta:
        model = Firearm
        fields = [
            "id",
            "firearm_id",
            "butt_number",
            "serial_number",
            "firearm_type",
            "type_label",
            "manufacturer",
            "model",
            "caliber",
            "condition_status",
            "condition_label",
            "custody_status",
            "custody_label",
            "storage_location",
            "assigned_unit_name",
            "assigned_unit_acronym",
            "assigned_personnel_name",
            "available",
        ]

    def get_assigned_personnel_name(self, obj) -> str:
        return _user_name(obj.assigned_personnel)


class FirearmSerializer(serializers.ModelSerializer):
    firearm_id = serializers.CharField(read_only=True)
    type_label = serializers.CharField(source="get_firearm_type_display", read_only=True)
    condition_label = serializers.CharField(
        source="get_condition_status_display", read_only=True
    )
    custody_label = serializers.CharField(
        source="get_custody_status_display", read_only=True
    )
    available = serializers.BooleanField(read_only=True)
    assigned_unit_name = serializers.CharField(source="assigned_unit.name", read_only=True)
    assigned_unit_acronym = serializers.CharField(
        source="assigned_unit.acronym", read_only=True
    )
    assigned_personnel_name = serializers.SerializerMethodField()
    assigned_personnel_rank = serializers.CharField(
        source="assigned_personnel.rank", read_only=True
    )
    registered_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Firearm
        fields = [
            "id",
            "firearm_id",
            "serial_number",
            "butt_number",
            "firearm_type",
            "type_label",
            "manufacturer",
            "model",
            "caliber",
            "date_acquired",
            "acquisition_reference",
            "condition_status",
            "condition_label",
            "custody_status",
            "custody_label",
            "available",
            "assigned_personnel",
            "assigned_personnel_name",
            "assigned_personnel_rank",
            "assigned_unit",
            "assigned_unit_name",
            "assigned_unit_acronym",
            "storage_location",
            "date_last_inspected",
            "remarks",
            "registered_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "firearm_id",
            "custody_status",
            "assigned_personnel",
            "created_at",
            "updated_at",
        ]
        # The custom validators below give specific messages and normalise the
        # case before checking. DRF's generic UniqueValidator would reject a
        # case variant before the field-specific rule could explain it.
        extra_kwargs = {"serial_number": {"validators": []}}

    def get_assigned_personnel_name(self, obj) -> str:
        return _user_name(obj.assigned_personnel)

    def get_registered_by_name(self, obj) -> str:
        return _user_name(obj.registered_by)

    def validate_serial_number(self, value):
        serial = normalise_identifier(value)
        if not serial:
            raise serializers.ValidationError("A serial number is required.")
        clash = Firearm.objects.filter(serial_number__iexact=serial)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                "That serial number is already registered. Serial numbers cannot be duplicated."
            )
        if self.instance is not None and serial != self.instance.serial_number:
            raise serializers.ValidationError(
                "A registered serial number is corrected through the authorised "
                "serial-correction action, which preserves the audit trail."
            )
        return serial

    def validate_butt_number(self, value):
        butt = normalise_identifier(value)
        if not butt:
            raise serializers.ValidationError("A butt number is required.")
        firearm_type = self.initial_data.get("firearm_type")
        if not firearm_type and self.instance is not None:
            firearm_type = self.instance.firearm_type
        clash = Firearm.objects.filter(firearm_type=firearm_type, butt_number=butt)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if firearm_type and clash.exists():
            raise serializers.ValidationError(
                f"That butt number is already used by another "
                f"{dict(Firearm.TYPE_CHOICES).get(firearm_type, 'firearm')}."
            )
        return butt

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        scope = user_unit_ids(user)
        if not scope:
            raise serializers.ValidationError(
                {"assigned_unit": "Your account has no authorised unit scope."}
            )
        assigned_unit = attrs.get("assigned_unit", getattr(self.instance, "assigned_unit", None))
        if assigned_unit is None:
            own = getattr(user, "unit", None)
            if own is None:
                raise serializers.ValidationError(
                    {"assigned_unit": "Choose the armory unit."}
                )
            attrs["assigned_unit"] = own
            assigned_unit = own
        if assigned_unit.id not in scope:
            raise serializers.ValidationError(
                {"assigned_unit": "Choose a unit inside your authorised armory scope."}
            )
        if self.instance is not None:
            if self.instance.custody_status == Firearm.CUSTODY_ISSUED:
                raise serializers.ValidationError(
                    "This firearm is issued. Record its return before changing the register."
                )
            new_type = attrs.get("firearm_type", self.instance.firearm_type)
            if new_type != self.instance.firearm_type and (
                self.instance.assignments.exists()
                or self.instance.transaction_lines.exists()
                or self.instance.inspections.exists()
            ):
                raise serializers.ValidationError(
                    {
                        "firearm_type": (
                            "The type cannot change after assignment, inspection or "
                            "transaction history exists. Register a corrected record "
                            "through the accountable correction procedure instead."
                        )
                    }
                )
            if (
                assigned_unit.id != self.instance.assigned_unit_id
                and self.instance.assignments.filter(is_active=True).exists()
            ):
                raise serializers.ValidationError(
                    {
                        "assigned_unit": (
                            "Release the current personnel designation before moving "
                            "the firearm; use the transfer action so the movement is recorded."
                        )
                    }
                )
            if assigned_unit.id != self.instance.assigned_unit_id:
                raise serializers.ValidationError(
                    {
                        "assigned_unit": (
                            "Use the firearm transfer action to move a registered firearm "
                            "between units; it records an audit trail and transaction."
                        )
                    }
                )
        if attrs.get("condition_status") == Firearm.CONDITION_CONDEMNED:
            if self.instance is None:
                raise serializers.ValidationError(
                    {
                        "condition_status": (
                            "A new registration cannot be condemned. Register the firearm "
                            "and record its inspection."
                        )
                    }
                )
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if "storage_location" not in validated_data or not validated_data.get(
            "storage_location"
        ):
            validated_data["storage_location"] = "Armory"
        return Firearm.objects.create(registered_by=user, **validated_data)


class AmmunitionSerializer(serializers.ModelSerializer):
    ammunition_id = serializers.CharField(read_only=True)
    custodian_name = serializers.SerializerMethodField()
    owning_unit_name = serializers.CharField(source="owning_unit.name", read_only=True)
    owning_unit_acronym = serializers.CharField(
        source="owning_unit.acronym", read_only=True
    )
    serviceability_label = serializers.CharField(
        source="get_serviceability_display", read_only=True
    )
    available = serializers.BooleanField(read_only=True)

    class Meta:
        model = Ammunition
        fields = [
            "id",
            "ammunition_id",
            "nomenclature",
            "caliber",
            "ammunition_type",
            "lot_number",
            "unit_of_measure",
            "quantity_received",
            "quantity_issued",
            "stock_balance",
            "serviceability",
            "serviceability_label",
            "storage_location",
            "date_received",
            "expiration_date",
            "responsible_custodian",
            "custodian_name",
            "owning_unit",
            "owning_unit_name",
            "owning_unit_acronym",
            "remarks",
            "available",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "ammunition_id",
            "quantity_issued",
            "stock_balance",
            "created_at",
            "updated_at",
        ]

    def get_custodian_name(self, obj) -> str:
        return _user_name(obj.responsible_custodian)

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        scope = user_unit_ids(user)
        if not scope:
            raise serializers.ValidationError(
                {"owning_unit": "Your account has no authorised unit scope."}
            )
        unit = attrs.get("owning_unit", getattr(self.instance, "owning_unit", None))
        if unit is None:
            own = getattr(user, "unit", None)
            if own is None:
                raise serializers.ValidationError(
                    {"owning_unit": "Choose the owning armory unit."}
                )
            attrs["owning_unit"] = own
            unit = own
        if unit.id not in scope:
            raise serializers.ValidationError(
                {"owning_unit": "Choose a unit inside your authorised armory scope."}
            )
        quantity = attrs.get(
            "quantity_received", getattr(self.instance, "quantity_received", 0)
        )
        if int(quantity or 0) < 0:
            raise serializers.ValidationError(
                {"quantity_received": "Quantity received cannot be negative."}
            )
        if self.instance is not None:
            if int(quantity or 0) != self.instance.quantity_received:
                raise serializers.ValidationError(
                    {
                        "quantity_received": (
                            "Record a stock adjustment with a reason instead of "
                            "rewriting the quantity received."
                        )
                    }
                )
            if unit.id != self.instance.owning_unit_id:
                raise serializers.ValidationError(
                    {"owning_unit": "A registered ammunition lot cannot change unit."}
                )
            if self.instance.quantity_issued > 0:
                if attrs.get(
                    "serviceability", self.instance.serviceability
                ) != self.instance.serviceability:
                    raise serializers.ValidationError(
                        {
                            "serviceability": (
                                "This lot has rounds issued. Record the return before "
                                "changing its serviceability."
                            )
                        }
                    )
        if not str(attrs.get("storage_location", getattr(self.instance, "storage_location", "")) or "").strip():
            attrs["storage_location"] = "Armory"
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        quantity = int(validated_data.get("quantity_received") or 0)
        validated_data["stock_balance"] = quantity
        validated_data["quantity_issued"] = 0
        validated_data["created_by"] = user
        if not validated_data.get("storage_location"):
            validated_data["storage_location"] = "Armory"
        return Ammunition.objects.create(**validated_data)


class AssignmentSerializer(serializers.ModelSerializer):
    personnel_name = serializers.SerializerMethodField()
    rank = serializers.CharField(source="user.rank", read_only=True)
    serial_number = serializers.CharField(source="user.serial_number", read_only=True)
    branch_of_service = serializers.CharField(
        source="user.branch_of_service", read_only=True
    )
    unit_name = serializers.CharField(source="user.unit.name", read_only=True)
    unit_acronym = serializers.CharField(source="user.unit.acronym", read_only=True)
    firearm = FirearmSummarySerializer(read_only=True)
    assigned_by_name = serializers.SerializerMethodField()
    released_by_name = serializers.SerializerMethodField()
    type_label = serializers.CharField(source="get_assignment_type_display", read_only=True)

    class Meta:
        model = PersonnelFirearmAssignment
        fields = [
            "id",
            "user",
            "personnel_name",
            "rank",
            "serial_number",
            "branch_of_service",
            "unit_name",
            "unit_acronym",
            "firearm",
            "assignment_type",
            "type_label",
            "is_active",
            "sync_uuid",
            "assigned_at",
            "assigned_by_name",
            "released_at",
            "released_by_name",
            "notes",
        ]

    def get_personnel_name(self, obj) -> str:
        return _user_name(obj.user)

    def get_assigned_by_name(self, obj) -> str:
        return _user_name(obj.assigned_by)

    def get_released_by_name(self, obj) -> str:
        return _user_name(obj.released_by)


class TransactionLineSerializer(serializers.ModelSerializer):
    item_type = serializers.SerializerMethodField()
    condition_label = serializers.SerializerMethodField()
    return_lines = serializers.SerializerMethodField()

    class Meta:
        model = ArmoryTransactionLine
        fields = [
            "id",
            "item_type",
            "firearm",
            "ammunition",
            "firearm_id_snapshot",
            "butt_number_snapshot",
            "serial_number_snapshot",
            "firearm_type",
            "manufacturer",
            "model",
            "caliber",
            "ammunition_name",
            "ammunition_caliber",
            "ammunition_type",
            "lot_number",
            "unit_of_measure",
            "quantity",
            "condition_status",
            "condition_label",
            "inspection_remarks",
            "original_line",
            "return_lines",
        ]

    def get_item_type(self, obj) -> str:
        return "firearm" if obj.firearm_id else "ammunition"

    def get_condition_label(self, obj) -> str:
        if obj.firearm_id:
            return dict(Firearm.CONDITION_CHOICES).get(
                obj.condition_status, obj.condition_status
            )
        return dict(Ammunition.CONDITION_CHOICES).get(
            obj.condition_status, obj.condition_status
        )

    def get_return_lines(self, obj) -> list[dict]:
        return [
            {
                "id": row.id,
                "transaction": row.transaction_id,
                "reference_number": row.transaction.reference_number,
                "quantity": row.quantity,
                "condition_status": row.condition_status,
                "inspection_remarks": row.inspection_remarks,
            }
            for row in obj.return_lines.all()
        ]


class ArmoryTransactionSerializer(serializers.ModelSerializer):
    type_label = serializers.CharField(
        source="get_transaction_type_display", read_only=True
    )
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    lines = TransactionLineSerializer(many=True, read_only=True)
    personnel_display = serializers.SerializerMethodField()
    releasing_armorer_name = serializers.SerializerMethodField()
    receiving_personnel_name = serializers.SerializerMethodField()
    approving_authority_name = serializers.SerializerMethodField()
    mission_number = serializers.CharField(source="mission.mission_number", read_only=True)
    mission_title = serializers.CharField(source="mission.title", read_only=True)
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    return_of_reference = serializers.CharField(
        source="return_of.reference_number", read_only=True
    )
    receipt_url = serializers.SerializerMethodField()

    class Meta:
        model = ArmoryTransaction
        fields = [
            "id",
            "reference_number",
            "transaction_type",
            "type_label",
            "status",
            "status_label",
            "transaction_at",
            "mission",
            "mission_number",
            "mission_title",
            "mission_order_reference",
            "purpose",
            "sync_uuid",
            "personnel",
            "personnel_display",
            "rank",
            "personnel_name",
            "branch_of_service",
            "unit",
            "unit_name",
            "unit_acronym",
            "releasing_armorer",
            "releasing_armorer_name",
            "receiving_personnel",
            "receiving_personnel_name",
            "approving_authority",
            "approving_authority_name",
            "inspection_remarks",
            "supporting_documents",
            "discrepancy_notes",
            "acknowledgement_name",
            "acknowledged_at",
            "return_of",
            "return_of_reference",
            "returned_at",
            "lines",
            "receipt_url",
            "created_at",
            "updated_at",
        ]

    def get_personnel_display(self, obj) -> str:
        if obj.personnel_name:
            return obj.personnel_name
        return _user_name(obj.personnel)

    def get_releasing_armorer_name(self, obj) -> str:
        return _user_name(obj.releasing_armorer)

    def get_receiving_personnel_name(self, obj) -> str:
        return _user_name(obj.receiving_personnel)

    def get_approving_authority_name(self, obj) -> str:
        return _user_name(obj.approving_authority)

    def get_receipt_url(self, obj) -> str:
        return f"/armory/receipts/{obj.id}/"


class InspectionSerializer(serializers.ModelSerializer):
    firearm_id = serializers.CharField(source="firearm.firearm_id", read_only=True)
    butt_number = serializers.CharField(source="firearm.butt_number", read_only=True)
    condition_label = serializers.CharField(
        source="get_condition_status_display", read_only=True
    )
    inspected_by_name = serializers.SerializerMethodField()

    class Meta:
        model = FirearmInspection
        fields = [
            "id",
            "firearm",
            "firearm_id",
            "butt_number",
            "sync_uuid",
            "inspected_by",
            "inspected_by_name",
            "inspected_at",
            "condition_status",
            "condition_label",
            "remarks",
            "next_inspection_date",
        ]
        read_only_fields = fields

    def get_inspected_by_name(self, obj) -> str:
        return _user_name(obj.inspected_by)


class SerialChangeSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = FirearmSerialChange
        fields = [
            "id",
            "firearm",
            "old_serial_number",
            "new_serial_number",
            "old_firearm_id",
            "new_firearm_id",
            "reason",
            "changed_by",
            "changed_by_name",
            "changed_at",
        ]
        read_only_fields = fields

    def get_changed_by_name(self, obj) -> str:
        return _user_name(obj.changed_by)


# ---------------------------------------------------------------------------
# Action payloads
# ---------------------------------------------------------------------------


class IssueFirearmLineSerializer(serializers.Serializer):
    firearm_id = serializers.CharField(max_length=160)
    butt_number = serializers.CharField(max_length=80)
    inspection_status = serializers.ChoiceField(
        choices=Firearm.CONDITION_CHOICES, default=Firearm.CONDITION_SERVICEABLE
    )
    inspection_remarks = serializers.CharField(required=False, allow_blank=True, default="")


class IssueAmmoLineSerializer(serializers.Serializer):
    ammunition = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=0)


class IssueSerializer(serializers.Serializer):
    # Linking a Mission Order is optional: the armory may release against a
    # standalone withdrawal, or link an approved order when one applies.
    mission = serializers.IntegerField(
        min_value=1, required=False, allow_null=True
    )
    client_uuid = serializers.UUIDField(required=False, allow_null=True)
    purpose = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    personnel = serializers.IntegerField(min_value=1)
    firearms = IssueFirearmLineSerializer(many=True, allow_empty=False)
    ammunition = IssueAmmoLineSerializer(many=True, required=False, default=list)
    identity_verified = serializers.BooleanField()
    acknowledged = serializers.BooleanField()
    acknowledgement_name = serializers.CharField(max_length=220, allow_blank=False)
    inspection_remarks = serializers.CharField(required=False, allow_blank=True, default="")
    supporting_documents = serializers.CharField(
        required=False, allow_blank=True, default=""
    )

    def validate(self, attrs):
        if not attrs.get("identity_verified"):
            raise serializers.ValidationError(
                {"identity_verified": "Verify the person's identity before release."}
            )
        if not attrs.get("acknowledged"):
            raise serializers.ValidationError(
                {"acknowledged": "The receiving person must acknowledge receipt."}
            )
        if not attrs.get("mission") and not str(attrs.get("purpose") or "").strip():
            raise serializers.ValidationError(
                {"purpose": "Give the purpose or reason for a regular issuance."}
            )
        return attrs


class ReturnFirearmLineSerializer(serializers.Serializer):
    firearm_id = serializers.CharField(max_length=160)
    butt_number = serializers.CharField(max_length=80)
    condition_status = serializers.ChoiceField(
        choices=Firearm.CONDITION_CHOICES, default=Firearm.CONDITION_SERVICEABLE
    )
    inspection_remarks = serializers.CharField(required=False, allow_blank=True, default="")


class ReturnAmmoLineSerializer(serializers.Serializer):
    line = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=0)
    serviceability = serializers.ChoiceField(
        choices=Ammunition.CONDITION_CHOICES, required=False, allow_blank=True
    )
    discrepancy_note = serializers.CharField(required=False, allow_blank=True, default="")


class ReturnSerializer(serializers.Serializer):
    issuance = serializers.IntegerField(min_value=1)
    client_uuid = serializers.UUIDField(required=False, allow_null=True)
    firearms = ReturnFirearmLineSerializer(many=True, allow_empty=False)
    ammunition = ReturnAmmoLineSerializer(many=True, required=False, default=list)
    identity_verified = serializers.BooleanField()
    acknowledged = serializers.BooleanField()
    acknowledgement_name = serializers.CharField(max_length=220, allow_blank=False)
    inspection_remarks = serializers.CharField(required=False, allow_blank=True, default="")
    discrepancy_notes = serializers.CharField(required=False, allow_blank=True, default="")
    supporting_documents = serializers.CharField(
        required=False, allow_blank=True, default=""
    )

    def validate(self, attrs):
        if not attrs.get("identity_verified"):
            raise serializers.ValidationError(
                {"identity_verified": "Verify the returning person's identity."}
            )
        if not attrs.get("acknowledged"):
            raise serializers.ValidationError(
                {"acknowledged": "The returning person must acknowledge the deposit."}
            )
        return attrs


class AssignmentWriteSerializer(serializers.Serializer):
    personnel = serializers.IntegerField(min_value=1)
    client_uuid = serializers.UUIDField(required=False, allow_null=True)
    rifle_firearm_id = serializers.CharField(
        max_length=160, required=False, allow_blank=True, default=""
    )
    pistol_firearm_id = serializers.CharField(
        max_length=160, required=False, allow_blank=True, default=""
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if not attrs.get("rifle_firearm_id") and not attrs.get("pistol_firearm_id"):
            raise serializers.ValidationError(
                "Choose the designated rifle, the designated pistol, or both."
            )
        return attrs


class SerialCorrectionSerializer(serializers.Serializer):
    serial_number = serializers.CharField(max_length=120)
    reason = serializers.CharField(allow_blank=False)


class InspectionWriteSerializer(serializers.Serializer):
    client_uuid = serializers.UUIDField(required=False, allow_null=True)
    condition_status = serializers.ChoiceField(choices=Firearm.CONDITION_CHOICES)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
    inspected_at = serializers.DateTimeField(required=False, allow_null=True)
    next_inspection_date = serializers.DateField(required=False, allow_null=True)


class TransferSerializer(serializers.Serializer):
    assigned_unit = serializers.IntegerField(min_value=1)
    storage_location = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default="Armory"
    )
    reason = serializers.CharField(allow_blank=False)
    supporting_documents = serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class StockAdjustmentSerializer(serializers.Serializer):
    client_uuid = serializers.UUIDField(required=False, allow_null=True)
    stock_balance = serializers.IntegerField(min_value=0)
    reason = serializers.CharField(allow_blank=False)
    supporting_documents = serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class MissionOptionSerializer(serializers.ModelSerializer):
    unit_acronym = serializers.CharField(source="unit.acronym", read_only=True)
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    approval_label = serializers.CharField(
        source="get_approval_status_display", read_only=True
    )
    mission_order_label = serializers.CharField(
        source="get_mission_order_status_display", read_only=True
    )
    personnel_count = serializers.SerializerMethodField()

    class Meta:
        model = Mission
        fields = [
            "id",
            "mission_number",
            "title",
            "category",
            "category_label",
            "status",
            "start_at",
            "end_at",
            "unit",
            "unit_acronym",
            "approval_status",
            "approval_label",
            "mission_order_status",
            "mission_order_label",
            "mission_order_signed_at",
            "personnel_count",
        ]

    def get_personnel_count(self, obj) -> int:
        return obj.personnel.filter(is_active=True).count()

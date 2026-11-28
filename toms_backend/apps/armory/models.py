"""Armory Management System tables.

The armory is part of TOWEMS, not a parallel application. Personnel, units and
missions are the existing records; this module adds only the controlled armory
register, custody transactions, assignment history and audit trail.

Firearm identity follows the specified rule: ``PAF-`` + serial number. The
generated identifier is never taken from the client. A serial correction is a
separate, audited operation that also records the old identifier.

All accountability rows keep frozen identity snapshots (name, rank, unit,
firearm ID, butt number, serial number, ammunition lot). A later promotion,
reassignment or serial correction can therefore never rewrite a receipt that
was already signed.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from apps.core import timeutils


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def normalise_identifier(value: str) -> str:
    """Trim and upper-case a serial, butt number or other controlled code."""
    return str(value or "").strip().upper()


def generate_ammunition_id() -> str:
    """A permanent, human-readable ammunition identifier."""
    return f"AMM-{uuid.uuid4().hex[:10].upper()}"


def generate_reference(prefix: str) -> str:
    stamp = timeutils.now().strftime("%Y%m%d")
    return f"ARM-{prefix}-{stamp}-{uuid.uuid4().hex[:6].upper()}"


class Firearm(TimestampedModel):
    """One registered firearm held in the armory."""

    TYPE_RIFLE = "rifle"
    TYPE_PISTOL = "pistol"
    TYPE_CHOICES = [(TYPE_RIFLE, "Rifle"), (TYPE_PISTOL, "Pistol")]

    CONDITION_SERVICEABLE = "serviceable"
    CONDITION_UNSERVICEABLE = "unserviceable"
    CONDITION_MAINTENANCE = "maintenance"
    CONDITION_CONDEMNED = "condemned"
    CONDITION_CHOICES = [
        (CONDITION_SERVICEABLE, "Serviceable"),
        (CONDITION_UNSERVICEABLE, "Unserviceable"),
        (CONDITION_MAINTENANCE, "Under maintenance"),
        (CONDITION_CONDEMNED, "Condemned"),
    ]

    CUSTODY_STORED = "stored"
    CUSTODY_ISSUED = "issued"
    CUSTODY_RETIRED = "retired"
    CUSTODY_CHOICES = [
        (CUSTODY_STORED, "Stored in armory"),
        (CUSTODY_ISSUED, "Issued"),
        (CUSTODY_RETIRED, "Retired"),
    ]

    #: Permanent public identifier generated from the serial number. Never
    #: accepted from an API client; corrected only through the audited action.
    firearm_id = models.CharField(max_length=160, unique=True, editable=False, blank=True)
    serial_number = models.CharField(max_length=120, unique=True)
    butt_number = models.CharField(max_length=80)
    firearm_type = models.CharField(max_length=10, choices=TYPE_CHOICES)

    manufacturer = models.CharField(max_length=160, blank=True, default="")
    model = models.CharField(max_length=160, blank=True, default="")
    caliber = models.CharField(max_length=80, blank=True, default="")
    date_acquired = models.DateField(null=True, blank=True)
    acquisition_reference = models.CharField(max_length=160, blank=True, default="")

    condition_status = models.CharField(
        max_length=20, choices=CONDITION_CHOICES, default=CONDITION_SERVICEABLE
    )
    custody_status = models.CharField(
        max_length=16, choices=CUSTODY_CHOICES, default=CUSTODY_STORED
    )

    #: The person the weapon is designated to for accountability. The weapon
    #: remains physically stored in the armory until it is issued for a mission.
    assigned_personnel = models.ForeignKey(
        "accounts.User",
        db_column="assigned_personnel_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="designated_firearms",
    )
    assigned_unit = models.ForeignKey(
        "legacy.Unit",
        db_column="assigned_unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="armory_firearms",
    )
    storage_location = models.CharField(max_length=200, blank=True, default="Armory")

    date_last_inspected = models.DateField(null=True, blank=True)
    remarks = models.TextField(blank=True, default="")
    registered_by = models.ForeignKey(
        "accounts.User",
        db_column="registered_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="registered_firearms",
    )

    class Meta:
        db_table = "armory_firearm"
        ordering = ["firearm_type", "butt_number", "firearm_id"]
        indexes = [
            models.Index(fields=["firearm_type"], name="armory_fa_type_idx"),
            models.Index(fields=["custody_status"], name="armory_fa_custody_idx"),
            models.Index(fields=["condition_status"], name="armory_fa_cond_idx"),
            models.Index(fields=["assigned_unit"], name="armory_fa_unit_idx"),
            models.Index(fields=["assigned_personnel"], name="armory_fa_person_idx"),
        ]
        constraints = [
            # Butt numbers are unique inside their firearm type; a rifle and a
            # pistol may legitimately carry the same number.
            models.UniqueConstraint(
                fields=["firearm_type", "butt_number"],
                name="armory_fa_butt_type_uniq",
            ),
            # Serial numbers are one identity across the whole armory, compared
            # without case sensitivity so AFP-1 and afp-1 cannot both exist.
            models.UniqueConstraint(
                Lower("serial_number"), name="armory_fa_serial_ci_uniq"
            ),
        ]

    def save(self, *args, **kwargs):
        self.serial_number = normalise_identifier(self.serial_number)
        self.butt_number = normalise_identifier(self.butt_number)
        if self.serial_number:
            self.firearm_id = f"PAF-{self.serial_number}"
        super().save(*args, **kwargs)

    @property
    def is_available(self) -> bool:
        return (
            self.custody_status == self.CUSTODY_STORED
            and self.condition_status == self.CONDITION_SERVICEABLE
        )

    def __str__(self) -> str:
        return f"{self.firearm_id} · {self.get_firearm_type_display()} {self.butt_number}"


class Ammunition(TimestampedModel):
    """One ammunition lot/batch held in the armory."""

    CONDITION_SERVICEABLE = "serviceable"
    CONDITION_UNSERVICEABLE = "unserviceable"
    CONDITION_QUARANTINED = "quarantined"
    CONDITION_EXPIRED = "expired"
    CONDITION_CHOICES = [
        (CONDITION_SERVICEABLE, "Serviceable"),
        (CONDITION_UNSERVICEABLE, "Unserviceable"),
        (CONDITION_QUARANTINED, "Quarantined"),
        (CONDITION_EXPIRED, "Expired"),
    ]

    ammunition_id = models.CharField(
        max_length=40, unique=True, editable=False, default=generate_ammunition_id
    )
    nomenclature = models.CharField(max_length=200)
    caliber = models.CharField(max_length=80)
    ammunition_type = models.CharField(max_length=120)
    lot_number = models.CharField(max_length=120)
    unit_of_measure = models.CharField(max_length=30, default="rounds")

    quantity_received = models.PositiveIntegerField(default=0)
    quantity_issued = models.PositiveIntegerField(default=0)
    stock_balance = models.PositiveIntegerField(default=0)

    serviceability = models.CharField(
        max_length=20, choices=CONDITION_CHOICES, default=CONDITION_SERVICEABLE
    )
    storage_location = models.CharField(max_length=200, blank=True, default="Armory")
    date_received = models.DateField(null=True, blank=True)
    expiration_date = models.DateField(null=True, blank=True)
    responsible_custodian = models.ForeignKey(
        "accounts.User",
        db_column="responsible_custodian_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="custodied_ammunition",
    )
    owning_unit = models.ForeignKey(
        "legacy.Unit",
        db_column="owning_unit_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="armory_ammunition",
    )
    remarks = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="registered_ammunition",
    )

    class Meta:
        db_table = "armory_ammunition"
        ordering = ["caliber", "nomenclature", "lot_number"]
        indexes = [
            models.Index(fields=["caliber"], name="armory_ammo_cal_idx"),
            models.Index(fields=["serviceability"], name="armory_ammo_cond_idx"),
            models.Index(fields=["owning_unit"], name="armory_ammo_unit_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity_received__gte=0),
                name="armory_ammo_received_gte0",
            ),
            models.CheckConstraint(
                condition=Q(quantity_issued__gte=0),
                name="armory_ammo_issued_gte0",
            ),
            models.CheckConstraint(
                condition=Q(stock_balance__gte=0),
                name="armory_ammo_balance_gte0",
            ),
        ]

    @property
    def is_available(self) -> bool:
        return (
            self.serviceability == self.CONDITION_SERVICEABLE
            and self.stock_balance > 0
            and (self.expiration_date is None or self.expiration_date >= timeutils.today())
        )

    def __str__(self) -> str:
        return f"{self.ammunition_id} · {self.nomenclature} ({self.lot_number})"


class PersonnelFirearmAssignment(TimestampedModel):
    """The designated rifle or pistol held for one person.

    History is preserved by closing a row, never by deleting it. Partial unique
    constraints permit exactly one active assignment of each firearm type per
    person and exactly one active holder per firearm.
    """

    TYPE_RIFLE = Firearm.TYPE_RIFLE
    TYPE_PISTOL = Firearm.TYPE_PISTOL
    TYPE_CHOICES = Firearm.TYPE_CHOICES

    user = models.ForeignKey(
        "accounts.User",
        db_column="user_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="firearm_assignments",
    )
    firearm = models.ForeignKey(
        Firearm, on_delete=models.PROTECT, related_name="assignments"
    )
    assignment_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    is_active = models.BooleanField(default=True)
    #: Shared by the rows created by one offline sync operation.
    sync_uuid = models.UUIDField(null=True, blank=True, editable=False, db_index=True)
    assigned_at = models.DateTimeField(default=timeutils.now)
    assigned_by = models.ForeignKey(
        "accounts.User",
        db_column="assigned_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="firearm_assignments_made",
    )
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        "accounts.User",
        db_column="released_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="firearm_assignments_released",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "armory_personnel_assignment"
        ordering = ["-is_active", "user__last_name", "assignment_type", "-assigned_at"]
        indexes = [
            models.Index(fields=["user", "is_active"], name="armory_assign_user_idx"),
            models.Index(fields=["firearm", "is_active"], name="armory_assign_fa_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "assignment_type"],
                condition=Q(is_active=True),
                name="armory_assign_one_active_type_per_user",
            ),
            models.UniqueConstraint(
                fields=["firearm"],
                condition=Q(is_active=True),
                name="armory_assign_one_active_holder_per_firearm",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}: {self.firearm.firearm_id} ({self.assignment_type})"


class ArmoryTransaction(TimestampedModel):
    """Header for issuance, return, transfer and adjustment transactions."""

    TYPE_ISSUANCE = "issuance"
    TYPE_RETURN = "return"
    TYPE_TRANSFER = "transfer"
    TYPE_ADJUSTMENT = "adjustment"
    TYPE_CHOICES = [
        (TYPE_ISSUANCE, "Issuance"),
        (TYPE_RETURN, "Return / Deposit"),
        (TYPE_TRANSFER, "Transfer"),
        (TYPE_ADJUSTMENT, "Adjustment"),
    ]

    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open - firearms out"),
        (STATUS_CLOSED, "Closed"),
    ]

    reference_number = models.CharField(max_length=80, unique=True, editable=False)
    #: Set by an offline client so a retried upload cannot create a duplicate.
    sync_uuid = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_CLOSED)
    transaction_at = models.DateTimeField(default=timeutils.now)

    mission = models.ForeignKey(
        "ops.Mission",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="armory_transactions",
    )
    mission_order_reference = models.CharField(max_length=80, blank=True, default="")
    #: Required for a regular/non-mission issue; optional when a mission is linked.
    purpose = models.CharField(max_length=255, blank=True, default="")

    #: Personnel identity is frozen on the transaction. It is copied from the
    #: existing users row when the transaction is written, never re-derived
    #: later from a roster that may have changed.
    personnel = models.ForeignKey(
        "accounts.User",
        db_column="personnel_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="armory_transactions_received",
    )
    rank = models.CharField(max_length=50, blank=True, default="")
    personnel_name = models.CharField(max_length=220, blank=True, default="")
    branch_of_service = models.CharField(max_length=100, blank=True, default="")

    unit = models.ForeignKey(
        "legacy.Unit",
        db_column="unit_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="armory_transactions",
    )
    unit_name = models.CharField(max_length=160, blank=True, default="")

    releasing_armorer = models.ForeignKey(
        "accounts.User",
        db_column="releasing_armorer_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="armory_transactions_released",
    )
    receiving_personnel = models.ForeignKey(
        "accounts.User",
        db_column="receiving_personnel_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="armory_transactions_received_by",
    )
    approving_authority = models.ForeignKey(
        "accounts.User",
        db_column="approving_authority_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="armory_transactions_approved",
    )

    inspection_remarks = models.TextField(blank=True, default="")
    supporting_documents = models.TextField(
        blank=True,
        default="",
        help_text="Document reference, receipt number or storage location of supporting papers.",
    )
    discrepancy_notes = models.TextField(blank=True, default="")
    acknowledgement_name = models.CharField(max_length=220, blank=True, default="")
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    #: A return points at the issuance it closes. Every deposit must be linked
    #: to the original issue, and keeps its Mission Order when one was linked.
    return_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="returns",
    )
    returned_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        "accounts.User",
        db_column="created_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="armory_transactions_created",
    )

    class Meta:
        db_table = "armory_transaction"
        ordering = ["-transaction_at", "-id"]
        indexes = [
            models.Index(fields=["transaction_type"], name="armory_txn_type_idx"),
            models.Index(fields=["-transaction_at"], name="armory_txn_time_idx"),
            models.Index(fields=["mission", "personnel"], name="armory_txn_mission_person_idx"),
        ]
        constraints = [
            # A person cannot have two open issues against the same mission.
            models.UniqueConstraint(
                fields=["mission", "personnel"],
                condition=Q(transaction_type="issuance", status="open"),
                name="armory_txn_one_open_issue_per_mission_person",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.reference_number:
            prefix = {
                self.TYPE_ISSUANCE: "ISS",
                self.TYPE_RETURN: "RET",
                self.TYPE_TRANSFER: "TRF",
                self.TYPE_ADJUSTMENT: "ADJ",
            }.get(self.transaction_type, "TXN")
            self.reference_number = generate_reference(prefix)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.reference_number} · {self.get_transaction_type_display()}"


class ArmoryTransactionLine(models.Model):
    """Frozen snapshot of one firearm or ammunition item on a transaction."""

    transaction = models.ForeignKey(
        ArmoryTransaction, on_delete=models.CASCADE, related_name="lines"
    )
    firearm = models.ForeignKey(
        Firearm, null=True, blank=True, on_delete=models.PROTECT, related_name="transaction_lines"
    )
    ammunition = models.ForeignKey(
        Ammunition,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transaction_lines",
    )
    #: On a return, the matching line from the original issuance.
    original_line = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="return_lines",
    )

    firearm_id_snapshot = models.CharField(max_length=160, blank=True, default="")
    butt_number_snapshot = models.CharField(max_length=80, blank=True, default="")
    serial_number_snapshot = models.CharField(max_length=120, blank=True, default="")
    firearm_type = models.CharField(max_length=10, blank=True, default="")
    manufacturer = models.CharField(max_length=160, blank=True, default="")
    model = models.CharField(max_length=160, blank=True, default="")
    caliber = models.CharField(max_length=80, blank=True, default="")

    ammunition_name = models.CharField(max_length=200, blank=True, default="")
    ammunition_caliber = models.CharField(max_length=80, blank=True, default="")
    ammunition_type = models.CharField(max_length=120, blank=True, default="")
    lot_number = models.CharField(max_length=120, blank=True, default="")
    unit_of_measure = models.CharField(max_length=30, blank=True, default="")
    quantity = models.PositiveIntegerField(default=0)

    condition_status = models.CharField(max_length=20, blank=True, default="")
    inspection_remarks = models.TextField(blank=True, default="")

    class Meta:
        db_table = "armory_transaction_line"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["transaction"], name="armory_line_txn_idx"),
            models.Index(fields=["firearm"], name="armory_line_fa_idx"),
            models.Index(fields=["ammunition"], name="armory_line_ammo_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(firearm__isnull=False, ammunition__isnull=True)
                    | Q(firearm__isnull=True, ammunition__isnull=False)
                ),
                name="armory_line_one_item_kind",
            ),
            models.UniqueConstraint(
                fields=["transaction", "firearm"],
                condition=Q(firearm__isnull=False),
                name="armory_line_txn_firearm_uniq",
            ),
        ]

    def __str__(self) -> str:
        if self.firearm_id:
            return f"{self.firearm_id_snapshot} on {self.transaction.reference_number}"
        return f"{self.ammunition_name} x{self.quantity} on {self.transaction.reference_number}"


class FirearmInspection(models.Model):
    """An inspection event in a firearm's service-life history."""

    #: Set by an offline client; makes an upload retry idempotent.
    sync_uuid = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    firearm = models.ForeignKey(
        Firearm, on_delete=models.PROTECT, related_name="inspections"
    )
    inspected_by = models.ForeignKey(
        "accounts.User",
        db_column="inspected_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="firearm_inspections",
    )
    inspected_at = models.DateTimeField(default=timeutils.now)
    condition_status = models.CharField(
        max_length=20, choices=Firearm.CONDITION_CHOICES
    )
    remarks = models.TextField(blank=True, default="")
    next_inspection_date = models.DateField(null=True, blank=True)
    transaction = models.ForeignKey(
        ArmoryTransaction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspections",
    )

    class Meta:
        db_table = "armory_firearm_inspection"
        ordering = ["-inspected_at", "-id"]
        indexes = [
            models.Index(fields=["firearm", "-inspected_at"], name="armory_insp_fa_time_idx")
        ]

    def __str__(self) -> str:
        return f"{self.firearm.firearm_id} inspected {self.inspected_at:%Y-%m-%d}"


class FirearmSerialChange(models.Model):
    """Audited correction of a firearm serial and its generated identifier."""

    firearm = models.ForeignKey(
        Firearm, on_delete=models.PROTECT, related_name="serial_changes"
    )
    old_serial_number = models.CharField(max_length=120)
    new_serial_number = models.CharField(max_length=120)
    old_firearm_id = models.CharField(max_length=160)
    new_firearm_id = models.CharField(max_length=160)
    reason = models.TextField()
    changed_by = models.ForeignKey(
        "accounts.User",
        db_column="changed_by",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="firearm_serial_changes",
    )
    changed_at = models.DateTimeField(default=timeutils.now)

    class Meta:
        db_table = "armory_firearm_serial_change"
        ordering = ["-changed_at", "-id"]

    def __str__(self) -> str:
        return f"{self.old_firearm_id} -> {self.new_firearm_id}"


class ArmoryAuditLog(models.Model):
    """Append-only armory audit. Rows cannot be edited or deleted through the ORM."""

    action = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=80, blank=True, default="")
    reference = models.CharField(max_length=160, blank=True, default="")
    actor = models.ForeignKey(
        "accounts.User",
        db_column="actor_id",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="armory_audit_entries",
    )
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    note = models.TextField(blank=True, default="")
    ip_address = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timeutils.now)

    class Meta:
        db_table = "armory_audit_log"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="armory_audit_entity_idx"),
            models.Index(fields=["-created_at"], name="armory_audit_time_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Armory audit entries are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Armory audit entries cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.entity_type}:{self.entity_id}"

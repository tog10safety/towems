"""SQLite tables for the offline armory.

Only the TOWEMS armory entities needed by this client are mirrored.  Central
ids are carried in ``remote_id`` columns; the local integer primary keys are
never uploaded.  Persons, units and missions keep their central identity so a
queued transaction can name the exact records during synchronisation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class LocalUser(Base):
    __tablename__ = "local_user"

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(160), nullable=False, default="")
    rank = Column(String(50), nullable=False, default="")
    role = Column(String(30), nullable=False, default="viewer")
    is_active = Column(Boolean, nullable=False, default=True)
    must_change_password = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=now)
    last_login_at = Column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "app_setting"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False, default="")


class Unit(Base):
    __tablename__ = "unit"

    id = Column(Integer, primary_key=True)
    remote_id = Column(Integer, unique=True, nullable=True)
    name = Column(String(160), nullable=False)
    acronym = Column(String(30), nullable=False, default="")
    parent_remote_id = Column(Integer, nullable=True)

    def label(self) -> str:
        return f"{self.acronym} — {self.name}" if self.acronym else self.name


class Personnel(Base):
    __tablename__ = "personnel"

    id = Column(Integer, primary_key=True)
    remote_id = Column(Integer, unique=True, nullable=True)
    username = Column(String(80), unique=True, nullable=False)
    rank = Column(String(50), nullable=False, default="")
    first_name = Column(String(100), nullable=False)
    middle_name = Column(String(100), nullable=False, default="")
    last_name = Column(String(100), nullable=False)
    serial_number = Column(String(50), unique=True, nullable=False)
    branch_of_service = Column(String(100), nullable=False, default="")
    unit_id = Column(Integer, ForeignKey("unit.id"), nullable=True, index=True)
    contact_number = Column(String(60), nullable=False, default="")
    is_active = Column(Boolean, nullable=False, default=True)
    is_authorized = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now, onupdate=now)

    unit = relationship("Unit")

    @property
    def full_name(self) -> str:
        return " ".join(
            part for part in (self.first_name, self.middle_name, self.last_name) if part
        ).strip()

    @property
    def display_name(self) -> str:
        return " ".join(part for part in (self.rank, self.full_name) if part).strip()


class Mission(Base):
    __tablename__ = "mission"

    id = Column(Integer, primary_key=True)
    remote_id = Column(Integer, unique=True, nullable=True)
    mission_number = Column(String(80), unique=True, nullable=False)
    title = Column(String(255), nullable=False, default="")
    category = Column(String(50), nullable=False, default="")
    status = Column(String(30), nullable=False, default="")
    approval_status = Column(String(30), nullable=False, default="")
    mission_order_status = Column(String(30), nullable=False, default="")
    unit_id = Column(Integer, ForeignKey("unit.id"), nullable=True)
    start_at = Column(DateTime, nullable=True)
    end_at = Column(DateTime, nullable=True)
    order_ready = Column(Boolean, nullable=False, default=False)
    order_error = Column(String(255), nullable=False, default="")
    synced_at = Column(DateTime, nullable=True)

    unit = relationship("Unit")


class MissionPersonnel(Base):
    __tablename__ = "mission_personnel"
    __table_args__ = (UniqueConstraint("mission_id", "personnel_id"),)

    id = Column(Integer, primary_key=True)
    mission_id = Column(Integer, ForeignKey("mission.id"), nullable=False, index=True)
    personnel_id = Column(
        Integer, ForeignKey("personnel.id"), nullable=False, index=True
    )
    role = Column(String(80), nullable=False, default="")
    custom_role = Column(String(80), nullable=False, default="")


class Firearm(Base):
    __tablename__ = "firearm"
    __table_args__ = (
        UniqueConstraint("firearm_type", "butt_number", name="uq_firearm_butt_type"),
        Index("ix_firearm_serial_upper", text("upper(serial_number)"), unique=True),
        CheckConstraint("firearm_type in ('rifle','pistol')", name="ck_firearm_type"),
        CheckConstraint(
            "custody_status in ('stored','issued','retired')",
            name="ck_firearm_custody",
        ),
    )

    id = Column(Integer, primary_key=True)
    remote_id = Column(Integer, unique=True, nullable=True)
    firearm_id = Column(String(160), unique=True, nullable=False)
    serial_number = Column(String(120), unique=True, nullable=False)
    butt_number = Column(String(80), nullable=False)
    firearm_type = Column(String(10), nullable=False)
    manufacturer = Column(String(160), nullable=False, default="")
    model = Column(String(160), nullable=False, default="")
    caliber = Column(String(80), nullable=False, default="")
    condition_status = Column(String(30), nullable=False, default="serviceable")
    custody_status = Column(String(20), nullable=False, default="stored")
    assigned_personnel_id = Column(
        Integer, ForeignKey("personnel.id"), nullable=True, index=True
    )
    assigned_unit_id = Column(Integer, ForeignKey("unit.id"), nullable=True)
    storage_location = Column(String(200), nullable=False, default="Armory")
    date_acquired = Column(Date, nullable=True)
    acquisition_reference = Column(String(160), nullable=False, default="")
    date_last_inspected = Column(Date, nullable=True)
    remarks = Column(Text, nullable=False, default="")
    registered_by = Column(String(80), nullable=False, default="")
    sync_state = Column(String(20), nullable=False, default="pending")
    synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now, onupdate=now)

    assigned_personnel = relationship("Personnel")
    assigned_unit = relationship("Unit")

    @property
    def is_available(self) -> bool:
        return self.custody_status == "stored" and self.condition_status == "serviceable"


class Ammunition(Base):
    __tablename__ = "ammunition"
    __table_args__ = (
        CheckConstraint("stock_balance >= 0", name="ck_ammo_balance"),
        CheckConstraint("quantity_issued >= 0", name="ck_ammo_issued"),
        CheckConstraint("quantity_received >= 0", name="ck_ammo_received"),
    )

    id = Column(Integer, primary_key=True)
    remote_id = Column(Integer, unique=True, nullable=True)
    ammunition_id = Column(String(50), unique=True, nullable=False)
    nomenclature = Column(String(200), nullable=False)
    caliber = Column(String(80), nullable=False)
    ammunition_type = Column(String(120), nullable=False, default="")
    lot_number = Column(String(120), nullable=False)
    unit_of_measure = Column(String(30), nullable=False, default="rounds")
    quantity_received = Column(Integer, nullable=False, default=0)
    quantity_issued = Column(Integer, nullable=False, default=0)
    stock_balance = Column(Integer, nullable=False, default=0)
    serviceability = Column(String(30), nullable=False, default="serviceable")
    storage_location = Column(String(200), nullable=False, default="Armory")
    date_received = Column(Date, nullable=True)
    expiration_date = Column(Date, nullable=True)
    responsible_custodian_id = Column(
        Integer, ForeignKey("personnel.id"), nullable=True
    )
    owning_unit_id = Column(Integer, ForeignKey("unit.id"), nullable=True)
    remarks = Column(Text, nullable=False, default="")
    created_by = Column(String(80), nullable=False, default="")
    sync_state = Column(String(20), nullable=False, default="pending")
    synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now, onupdate=now)

    responsible_custodian = relationship("Personnel")
    owning_unit = relationship("Unit")

    @property
    def is_available(self) -> bool:
        return self.serviceability == "serviceable" and self.stock_balance > 0


class Assignment(Base):
    __tablename__ = "assignment"
    __table_args__ = (
        Index(
            "uq_active_assignment_person_type",
            "personnel_id",
            "assignment_type",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        Index(
            "uq_active_assignment_firearm",
            "firearm_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        CheckConstraint(
            "assignment_type in ('rifle','pistol')", name="ck_assignment_type"
        ),
    )

    id = Column(Integer, primary_key=True)
    remote_id = Column(Integer, unique=True, nullable=True)
    sync_uuid = Column(String(36), nullable=False, default=new_uuid, index=True)
    personnel_id = Column(
        Integer, ForeignKey("personnel.id"), nullable=False, index=True
    )
    firearm_id = Column(Integer, ForeignKey("firearm.id"), nullable=False, index=True)
    assignment_type = Column(String(10), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    assigned_at = Column(DateTime, nullable=False, default=now)
    assigned_by = Column(String(80), nullable=False, default="")
    released_at = Column(DateTime, nullable=True)
    released_by = Column(String(80), nullable=False, default="")
    notes = Column(Text, nullable=False, default="")
    sync_state = Column(String(20), nullable=False, default="pending")
    synced_at = Column(DateTime, nullable=True)

    personnel = relationship("Personnel")
    firearm = relationship("Firearm")


class Transaction(Base):
    __tablename__ = "armory_transaction"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False, default=new_uuid)
    remote_id = Column(Integer, unique=True, nullable=True)
    remote_reference = Column(String(80), nullable=False, default="")
    reference_number = Column(String(80), unique=True, nullable=False)
    transaction_type = Column(String(20), nullable=False)
    status = Column(String(12), nullable=False, default="closed")
    transaction_at = Column(DateTime, nullable=False, default=now)
    mission_id = Column(Integer, ForeignKey("mission.id"), nullable=True)
    mission_order_reference = Column(String(80), nullable=False, default="")
    purpose = Column(String(255), nullable=False, default="")
    personnel_id = Column(Integer, ForeignKey("personnel.id"), nullable=True)
    rank = Column(String(50), nullable=False, default="")
    personnel_name = Column(String(220), nullable=False, default="")
    branch_of_service = Column(String(100), nullable=False, default="")
    unit_id = Column(Integer, ForeignKey("unit.id"), nullable=True)
    unit_name = Column(String(160), nullable=False, default="")
    releasing_armorer = Column(String(160), nullable=False, default="")
    receiving_personnel = Column(String(160), nullable=False, default="")
    approving_authority = Column(String(160), nullable=False, default="")
    inspection_remarks = Column(Text, nullable=False, default="")
    supporting_documents = Column(Text, nullable=False, default="")
    discrepancy_notes = Column(Text, nullable=False, default="")
    acknowledgement_name = Column(String(220), nullable=False, default="")
    acknowledged_at = Column(DateTime, nullable=True)
    return_of_id = Column(
        Integer, ForeignKey("armory_transaction.id"), nullable=True, index=True
    )
    returned_at = Column(DateTime, nullable=True)
    created_by = Column(String(80), nullable=False, default="")
    sync_state = Column(String(20), nullable=False, default="pending")
    sync_error = Column(Text, nullable=False, default="")
    synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now, onupdate=now)

    mission = relationship("Mission")
    personnel = relationship("Personnel")
    unit = relationship("Unit")
    return_of = relationship("Transaction", remote_side=[id])
    lines = relationship(
        "TransactionLine",
        back_populates="transaction",
        cascade="all, delete-orphan",
        order_by="TransactionLine.id",
    )


class TransactionLine(Base):
    __tablename__ = "armory_transaction_line"

    id = Column(Integer, primary_key=True)
    remote_id = Column(Integer, unique=True, nullable=True)
    transaction_id = Column(
        Integer,
        ForeignKey("armory_transaction.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    firearm_id = Column(Integer, ForeignKey("firearm.id"), nullable=True)
    ammunition_id = Column(Integer, ForeignKey("ammunition.id"), nullable=True)
    original_line_id = Column(Integer, ForeignKey("armory_transaction_line.id"))
    firearm_id_snapshot = Column(String(160), nullable=False, default="")
    butt_number_snapshot = Column(String(80), nullable=False, default="")
    serial_number_snapshot = Column(String(120), nullable=False, default="")
    firearm_type = Column(String(10), nullable=False, default="")
    manufacturer = Column(String(160), nullable=False, default="")
    model = Column(String(160), nullable=False, default="")
    caliber = Column(String(80), nullable=False, default="")
    ammunition_name = Column(String(200), nullable=False, default="")
    ammunition_caliber = Column(String(80), nullable=False, default="")
    ammunition_type = Column(String(120), nullable=False, default="")
    lot_number = Column(String(120), nullable=False, default="")
    unit_of_measure = Column(String(30), nullable=False, default="")
    quantity = Column(Integer, nullable=False, default=0)
    condition_status = Column(String(30), nullable=False, default="")
    inspection_remarks = Column(Text, nullable=False, default="")

    transaction = relationship("Transaction", back_populates="lines")
    firearm = relationship("Firearm")
    ammunition = relationship("Ammunition")


class Inspection(Base):
    __tablename__ = "firearm_inspection"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False, default=new_uuid)
    remote_id = Column(Integer, unique=True, nullable=True)
    firearm_id = Column(Integer, ForeignKey("firearm.id"), nullable=False, index=True)
    inspected_by = Column(String(160), nullable=False, default="")
    inspected_at = Column(DateTime, nullable=False, default=now)
    condition_status = Column(String(30), nullable=False)
    remarks = Column(Text, nullable=False, default="")
    next_inspection_date = Column(Date, nullable=True)
    transaction_id = Column(Integer, ForeignKey("armory_transaction.id"), nullable=True)
    sync_state = Column(String(20), nullable=False, default="pending")
    synced_at = Column(DateTime, nullable=True)

    firearm = relationship("Firearm")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=now, index=True)
    username = Column(String(80), nullable=False, default="")
    action = Column(String(80), nullable=False)
    entity_type = Column(String(80), nullable=False)
    entity_id = Column(String(80), nullable=False, default="")
    reference = Column(String(160), nullable=False, default="")
    before_json = Column(Text, nullable=False, default="{}")
    after_json = Column(Text, nullable=False, default="{}")
    note = Column(Text, nullable=False, default="")


class SyncQueue(Base):
    __tablename__ = "sync_queue"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False, default=new_uuid)
    entity_type = Column(String(40), nullable=False)
    entity_uuid = Column(String(36), nullable=False, default="")
    action = Column(String(40), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    state = Column(String(20), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=False, default="")
    next_attempt_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now, onupdate=now)


class SyncHistory(Base):
    __tablename__ = "sync_history"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, nullable=False, default=now)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(30), nullable=False, default="running")
    uploaded = Column(Integer, nullable=False, default=0)
    downloaded = Column(Integer, nullable=False, default=0)
    message = Column(Text, nullable=False, default="")
    details = Column(Text, nullable=False, default="")


class DatabaseMeta(Base):
    __tablename__ = "database_meta"

    key = Column(String(100), primary_key=True)
    value = Column(String(255), nullable=False, default="")

"""Offline armory business rules.

The same accountability rules as the TOWEMS web armory are enforced here:
generated ``PAF-`` identifiers, butt-number uniqueness inside a firearm type,
one active designation per person/type and per firearm, all designated weapons
accounted for at issue, frozen identity snapshots, and append-only audit rows.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from . import db
from .models import (
    Ammunition,
    Assignment,
    AuditLog,
    Firearm,
    Inspection,
    LocalUser,
    Mission,
    MissionPersonnel,
    Personnel,
    SyncQueue,
    Transaction,
    TransactionLine,
    Unit,
    new_uuid,
    now,
)
from .security import (
    ROLE_ADMIN,
    ROLE_ARMORER,
    hash_password,
    is_strong_password,
    require_permission,
    verify_password,
)

FIREARM_TYPES = {"rifle": "Rifle", "pistol": "Pistol"}
CONDITIONS = {
    "serviceable": "Serviceable",
    "unserviceable": "Unserviceable",
    "maintenance": "Under maintenance",
    "condemned": "Condemned",
}
CUSTODY = {"stored": "Stored in armory", "issued": "Issued", "retired": "Retired"}
AMMO_CONDITIONS = {
    "serviceable": "Serviceable",
    "unserviceable": "Unserviceable",
    "quarantined": "Quarantined",
    "expired": "Expired",
}
INSPECTION_DUE_DAYS = 30


class ArmoryError(Exception):
    pass


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def normalise(value: str | None) -> str:
    return str(value or "").strip().upper()


def firearm_identifier(serial_number: str) -> str:
    serial = normalise(serial_number)
    return f"PAF-{serial}" if serial else ""


def ammunition_identifier() -> str:
    return f"AMM-{uuid.uuid4().hex[:10].upper()}"


def generate_reference(prefix: str) -> str:
    return f"ARM-{prefix}-{now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def _unique_reference(session: Session, prefix: str) -> str:
    for _ in range(10):
        value = generate_reference(prefix)
        if session.scalar(select(Transaction.id).where(Transaction.reference_number == value)) is None:
            return value
    raise ArmoryError("Unable to allocate a transaction reference.")


def _actor_name(actor: LocalUser | str | None) -> str:
    if actor is None:
        return "system"
    if isinstance(actor, str):
        return actor
    return actor.display_name or actor.username


def _actor_username(actor: LocalUser | str | None) -> str:
    if actor is None:
        return "system"
    if isinstance(actor, str):
        return actor
    return actor.username


def _check(actor: LocalUser, permission: str) -> None:
    if actor is None or not actor.is_active:
        raise ArmoryError("Your local account is inactive.")
    require_permission(actor.role, permission)


# ---------------------------------------------------------------------------
# Local users
# ---------------------------------------------------------------------------


def first_run() -> bool:
    with db.session_scope() as session:
        return session.scalar(select(func.count()).select_from(LocalUser)) == 0


def create_initial_admin(
    session: Session,
    *,
    username: str,
    display_name: str,
    password: str,
    rank: str = "",
    unit_name: str = "",
    unit_acronym: str = "",
) -> LocalUser:
    username = str(username or "").strip().lower()
    if not username:
        raise ArmoryError("Choose an administrator username.")
    if session.scalar(select(LocalUser.id).where(LocalUser.username == username)):
        raise ArmoryError("That username is already in use.")
    valid, message = is_strong_password(password)
    if not valid:
        raise ArmoryError(message)
    user = LocalUser(
        username=username,
        password_hash=hash_password(password),
        display_name=str(display_name or "").strip() or username,
        rank=str(rank or "").strip(),
        role=ROLE_ADMIN,
    )
    session.add(user)
    if unit_name.strip():
        unit = Unit(
            name=unit_name.strip(),
            acronym=unit_acronym.strip().upper(),
        )
        session.add(unit)
    session.flush()
    db.audit(
        session,
        username=user.username,
        action="initialise_local_administrator",
        entity_type="local_user",
        entity_id=user.id,
        after={"username": user.username, "role": user.role},
        note="First-run setup",
    )
    return user


def authenticate(session: Session, username: str, password: str) -> LocalUser | None:
    user = session.scalar(
        select(LocalUser).where(LocalUser.username == str(username or "").strip().lower())
    )
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = now()
    db.audit(
        session,
        username=user.username,
        action="login",
        entity_type="local_user",
        entity_id=user.id,
        note="Local desktop sign-in",
    )
    return user


def list_users(session: Session) -> list[LocalUser]:
    return list(session.scalars(select(LocalUser).order_by(LocalUser.username)))


def create_user(
    session: Session,
    *,
    actor: LocalUser,
    username: str,
    display_name: str,
    password: str,
    role: str,
    rank: str = "",
    is_active: bool = True,
) -> LocalUser:
    _check(actor, "manage_users")
    username = str(username or "").strip().lower()
    if not username:
        raise ArmoryError("Username is required.")
    if session.scalar(select(LocalUser.id).where(LocalUser.username == username)):
        raise ArmoryError("That username already exists.")
    valid, message = is_strong_password(password)
    if not valid:
        raise ArmoryError(message)
    role = role if role in {"administrator", "armorer", "supervisor", "viewer"} else "viewer"
    user = LocalUser(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name.strip() or username,
        rank=rank.strip(),
        role=role,
        is_active=bool(is_active),
        must_change_password=True,
    )
    session.add(user)
    session.flush()
    db.audit(
        session,
        username=actor.username,
        action="create_local_user",
        entity_type="local_user",
        entity_id=user.id,
        after={"username": user.username, "role": user.role},
    )
    return user


def update_user(
    session: Session,
    *,
    actor: LocalUser,
    user_id: int,
    display_name: str,
    role: str,
    rank: str,
    is_active: bool,
    new_password: str = "",
) -> LocalUser:
    _check(actor, "manage_users")
    user = session.get(LocalUser, user_id)
    if user is None:
        raise ArmoryError("User not found.")
    if user.id == actor.id and (not is_active or role != ROLE_ADMIN):
        raise ArmoryError("You cannot remove your own administrator access.")
    before = {
        "display_name": user.display_name,
        "role": user.role,
        "rank": user.rank,
        "is_active": user.is_active,
    }
    user.display_name = display_name.strip() or user.username
    user.role = role
    user.rank = rank.strip()
    user.is_active = bool(is_active)
    if new_password:
        valid, message = is_strong_password(new_password)
        if not valid:
            raise ArmoryError(message)
        user.password_hash = hash_password(new_password)
        user.must_change_password = True
    db.audit(
        session,
        username=actor.username,
        action="update_local_user",
        entity_type="local_user",
        entity_id=user.id,
        before=before,
        after={
            "display_name": user.display_name,
            "role": user.role,
            "rank": user.rank,
            "is_active": user.is_active,
        },
    )
    return user


def change_password(
    session: Session, *, user: LocalUser, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise ArmoryError("Current password is incorrect.")
    valid, message = is_strong_password(new_password)
    if not valid:
        raise ArmoryError(message)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.audit(
        session,
        username=user.username,
        action="change_password",
        entity_type="local_user",
        entity_id=user.id,
    )


# ---------------------------------------------------------------------------
# Register lookups
# ---------------------------------------------------------------------------


def units(session: Session) -> list[Unit]:
    return list(session.scalars(select(Unit).order_by(Unit.acronym, Unit.name)))


def personnel_rows(session: Session, search: str = "", active_only: bool = False) -> list[Personnel]:
    query = select(Personnel).options(selectinload(Personnel.unit))
    if active_only:
        query = query.where(Personnel.is_active.is_(True))
    search = str(search or "").strip()
    if search:
        needle = f"%{search}%"
        query = query.where(
            or_(
                Personnel.first_name.ilike(needle),
                Personnel.middle_name.ilike(needle),
                Personnel.last_name.ilike(needle),
                Personnel.serial_number.ilike(needle),
                Personnel.rank.ilike(needle),
                Personnel.username.ilike(needle),
            )
        )
    return list(session.scalars(query.order_by(Personnel.last_name, Personnel.first_name)))


def personnel_label(person: Personnel) -> str:
    if person is None:
        return ""
    unit = f" · {person.unit.acronym}" if person.unit else ""
    serial = f" · {person.serial_number}" if person.serial_number else ""
    return f"{person.display_name}{serial}{unit}"


def create_personnel(
    session: Session,
    *,
    actor: LocalUser,
    username: str,
    rank: str,
    first_name: str,
    middle_name: str,
    last_name: str,
    serial_number: str,
    branch: str,
    unit_id: int | None,
    contact: str = "",
    is_authorized: bool = True,
) -> Personnel:
    _check(actor, "manage_personnel")
    username = username.strip().lower()
    serial = normalise(serial_number)
    if not username or not first_name.strip() or not last_name.strip() or not serial:
        raise ArmoryError("Username, names and service number are required.")
    if session.scalar(select(Personnel.id).where(Personnel.username == username)):
        raise ArmoryError("That username already exists.")
    if session.scalar(select(Personnel.id).where(Personnel.serial_number == serial)):
        raise ArmoryError("That service number already exists.")
    person = Personnel(
        username=username,
        rank=rank.strip(),
        first_name=first_name.strip(),
        middle_name=middle_name.strip(),
        last_name=last_name.strip(),
        serial_number=serial,
        branch_of_service=branch.strip(),
        unit_id=unit_id,
        contact_number=contact.strip(),
        is_authorized=bool(is_authorized),
    )
    session.add(person)
    session.flush()
    db.audit(
        session,
        username=actor.username,
        action="create_personnel",
        entity_type="personnel",
        entity_id=person.id,
        reference=person.serial_number,
        after={
            "name": person.full_name,
            "serial_number": person.serial_number,
            "is_authorized": person.is_authorized,
        },
    )
    return person


def update_personnel(
    session: Session,
    *,
    actor: LocalUser,
    personnel_id: int,
    rank: str,
    first_name: str,
    middle_name: str,
    last_name: str,
    branch: str,
    unit_id: int | None,
    contact: str,
    is_authorized: bool,
    is_active: bool,
) -> Personnel:
    _check(actor, "manage_personnel")
    person = session.get(Personnel, personnel_id)
    if person is None:
        raise ArmoryError("Personnel record not found.")
    if person.remote_id is not None and any(
        (
            rank.strip() != person.rank,
            first_name.strip() != person.first_name,
            middle_name.strip() != person.middle_name,
            last_name.strip() != person.last_name,
            branch.strip() != person.branch_of_service,
            unit_id != person.unit_id,
            contact.strip() != person.contact_number,
        )
    ):
        raise ArmoryError(
            "This personnel record is maintained in TOWEMS. Identity, rank, unit "
            "and contact fields are read-only on the desktop; only armory "
            "authorisation and active state may be changed locally."
        )
    before = {
        "name": person.full_name,
        "rank": person.rank,
        "unit_id": person.unit_id,
        "is_authorized": person.is_authorized,
        "is_active": person.is_active,
    }
    person.rank = rank.strip()
    person.first_name = first_name.strip()
    person.middle_name = middle_name.strip()
    person.last_name = last_name.strip()
    person.branch_of_service = branch.strip()
    person.unit_id = unit_id
    person.contact_number = contact.strip()
    person.is_authorized = bool(is_authorized)
    person.is_active = bool(is_active)
    db.audit(
        session,
        username=actor.username,
        action="update_personnel",
        entity_type="personnel",
        entity_id=person.id,
        reference=person.serial_number,
        before=before,
        after={
            "name": person.full_name,
            "rank": person.rank,
            "unit_id": person.unit_id,
            "is_authorized": person.is_authorized,
            "is_active": person.is_active,
        },
    )
    return person


# ---------------------------------------------------------------------------
# Firearms
# ---------------------------------------------------------------------------


def firearm_rows(
    session: Session,
    *,
    search: str = "",
    firearm_type: str = "",
    custody: str = "",
    include_retired: bool = True,
) -> list[Firearm]:
    query = select(Firearm).options(
        selectinload(Firearm.assigned_personnel), selectinload(Firearm.assigned_unit)
    )
    if not include_retired:
        query = query.where(Firearm.custody_status != "retired")
    if firearm_type:
        query = query.where(Firearm.firearm_type == firearm_type)
    if custody:
        query = query.where(Firearm.custody_status == custody)
    search = str(search or "").strip()
    if search:
        needle = f"%{search}%"
        query = query.where(
            or_(
                Firearm.firearm_id.ilike(needle),
                Firearm.serial_number.ilike(needle),
                Firearm.butt_number.ilike(needle),
                Firearm.manufacturer.ilike(needle),
                Firearm.model.ilike(needle),
                Firearm.caliber.ilike(needle),
            )
        )
    return list(
        session.scalars(
            query.order_by(Firearm.firearm_type, Firearm.butt_number, Firearm.firearm_id)
        )
    )


def register_firearm(
    session: Session,
    *,
    actor: LocalUser,
    serial_number: str,
    butt_number: str,
    firearm_type: str,
    manufacturer: str,
    model: str,
    caliber: str,
    unit_id: int | None,
    storage_location: str = "Armory",
    date_acquired: date | None = None,
    acquisition_reference: str = "",
    remarks: str = "",
) -> Firearm:
    _check(actor, "manage_inventory")
    serial = normalise(serial_number)
    butt = normalise(butt_number)
    if not serial or not butt:
        raise ArmoryError("Serial number and butt number are required.")
    if firearm_type not in FIREARM_TYPES:
        raise ArmoryError("Choose rifle or pistol.")
    if session.scalar(select(Firearm.id).where(func.upper(Firearm.serial_number) == serial)):
        raise ArmoryError("That serial number is already registered.")
    if session.scalar(
        select(Firearm.id).where(
            Firearm.firearm_type == firearm_type,
            func.upper(Firearm.butt_number) == butt,
        )
    ):
        raise ArmoryError(
            f"That butt number is already used by another {FIREARM_TYPES[firearm_type].lower()}."
        )
    if unit_id is None and len(units(session)) == 1:
        unit_id = units(session)[0].id
    if unit_id is None:
        raise ArmoryError("Choose the armory unit.")
    firearm = Firearm(
        firearm_id=firearm_identifier(serial),
        serial_number=serial,
        butt_number=butt,
        firearm_type=firearm_type,
        manufacturer=manufacturer.strip(),
        model=model.strip(),
        caliber=caliber.strip(),
        assigned_unit_id=unit_id,
        storage_location=storage_location.strip() or "Armory",
        date_acquired=date_acquired,
        acquisition_reference=acquisition_reference.strip(),
        remarks=remarks.strip(),
        registered_by=actor.username,
    )
    session.add(firearm)
    session.flush()
    db.enqueue(
        session,
        entity_type="firearm",
        entity_uuid=str(firearm.id),
        action="firearm.create",
        payload={"firearm_id": firearm.id},
    )
    db.audit(
        session,
        username=actor.username,
        action="register_firearm",
        entity_type="firearm",
        entity_id=firearm.id,
        reference=firearm.firearm_id,
        after={
            "firearm_id": firearm.firearm_id,
            "butt_number": firearm.butt_number,
            "firearm_type": firearm.firearm_type,
            "serial_number": firearm.serial_number,
        },
    )
    return firearm


def update_firearm_local(
    session: Session,
    *,
    actor: LocalUser,
    firearm_id: int,
    manufacturer: str,
    model: str,
    caliber: str,
    storage_location: str,
    remarks: str,
) -> Firearm:
    _check(actor, "manage_inventory")
    firearm = session.get(Firearm, firearm_id)
    if firearm is None:
        raise ArmoryError("Firearm not found.")
    if firearm.remote_id is not None:
        raise ArmoryError(
            "This firearm is synchronized with TOWEMS. Use the web application for "
            "registry corrections; local changes are restricted to inspections."
        )
    if firearm.custody_status == "issued":
        raise ArmoryError("A firearm cannot be edited while it is issued.")
    before = {
        "manufacturer": firearm.manufacturer,
        "model": firearm.model,
        "caliber": firearm.caliber,
        "storage_location": firearm.storage_location,
    }
    firearm.manufacturer = manufacturer.strip()
    firearm.model = model.strip()
    firearm.caliber = caliber.strip()
    firearm.storage_location = storage_location.strip() or "Armory"
    firearm.remarks = remarks.strip()
    db.enqueue(
        session,
        entity_type="firearm",
        entity_uuid=str(firearm.id),
        action="firearm.create",
        payload={"firearm_id": firearm.id},
    )
    db.audit(
        session,
        username=actor.username,
        action="update_firearm",
        entity_type="firearm",
        entity_id=firearm.id,
        reference=firearm.firearm_id,
        before=before,
        after={
            "manufacturer": firearm.manufacturer,
            "model": firearm.model,
            "caliber": firearm.caliber,
            "storage_location": firearm.storage_location,
        },
    )
    return firearm


def inspect_firearm(
    session: Session,
    *,
    actor: LocalUser,
    firearm_id: int,
    condition_status: str,
    remarks: str,
    inspected_at: datetime | None = None,
    next_inspection_date: date | None = None,
) -> Inspection:
    _check(actor, "manage_inventory")
    firearm = session.get(Firearm, firearm_id)
    if firearm is None:
        raise ArmoryError("Firearm not found.")
    if firearm.custody_status == "issued":
        raise ArmoryError("An issued firearm is inspected when it is returned.")
    if condition_status not in CONDITIONS:
        raise ArmoryError("Choose a valid inspection result.")
    previous_condition = firearm.condition_status
    inspection = Inspection(
        firearm_id=firearm.id,
        inspected_by=_actor_name(actor),
        inspected_at=inspected_at or now(),
        condition_status=condition_status,
        remarks=remarks.strip(),
        next_inspection_date=next_inspection_date,
    )
    session.add(inspection)
    firearm.condition_status = condition_status
    firearm.date_last_inspected = inspection.inspected_at.date()
    session.flush()
    db.enqueue(
        session,
        entity_type="inspection",
        entity_uuid=inspection.uuid,
        action="inspection.create",
        payload={"inspection_id": inspection.id},
    )
    db.audit(
        session,
        username=actor.username,
        action="inspect_firearm",
        entity_type="firearm",
        entity_id=firearm.id,
        reference=firearm.firearm_id,
        before={"condition_status": previous_condition},
        after={"condition_status": condition_status},
        note=remarks.strip(),
    )
    return inspection


# ---------------------------------------------------------------------------
# Ammunition
# ---------------------------------------------------------------------------


def ammunition_rows(
    session: Session, *, search: str = "", serviceability: str = ""
) -> list[Ammunition]:
    query = select(Ammunition).options(
        selectinload(Ammunition.owning_unit),
        selectinload(Ammunition.responsible_custodian),
    )
    if serviceability:
        query = query.where(Ammunition.serviceability == serviceability)
    search = str(search or "").strip()
    if search:
        needle = f"%{search}%"
        query = query.where(
            or_(
                Ammunition.ammunition_id.ilike(needle),
                Ammunition.nomenclature.ilike(needle),
                Ammunition.caliber.ilike(needle),
                Ammunition.lot_number.ilike(needle),
                Ammunition.ammunition_type.ilike(needle),
            )
        )
    return list(
        session.scalars(
            query.order_by(Ammunition.caliber, Ammunition.nomenclature, Ammunition.lot_number)
        )
    )


def register_ammunition(
    session: Session,
    *,
    actor: LocalUser,
    nomenclature: str,
    caliber: str,
    ammunition_type: str,
    lot_number: str,
    unit_of_measure: str,
    quantity_received: int,
    unit_id: int | None,
    serviceability: str = "serviceable",
    storage_location: str = "Armory",
    date_received: date | None = None,
    expiration_date: date | None = None,
    custodian_id: int | None = None,
    remarks: str = "",
) -> Ammunition:
    _check(actor, "manage_inventory")
    try:
        quantity = int(quantity_received)
    except (TypeError, ValueError):
        raise ArmoryError("Quantity received must be a whole number.")
    if quantity < 0:
        raise ArmoryError("Quantity received cannot be negative.")
    if not nomenclature.strip() or not caliber.strip() or not lot_number.strip():
        raise ArmoryError("Nomenclature, caliber and lot number are required.")
    if serviceability not in AMMO_CONDITIONS:
        raise ArmoryError("Choose a valid serviceability result.")
    if unit_id is None:
        all_units = units(session)
        if len(all_units) == 1:
            unit_id = all_units[0].id
    if unit_id is None:
        raise ArmoryError("Choose the owning unit.")
    lot = Ammunition(
        ammunition_id=ammunition_identifier(),
        nomenclature=nomenclature.strip(),
        caliber=caliber.strip(),
        ammunition_type=ammunition_type.strip(),
        lot_number=lot_number.strip().upper(),
        unit_of_measure=unit_of_measure.strip() or "rounds",
        quantity_received=quantity,
        stock_balance=quantity,
        serviceability=serviceability,
        storage_location=storage_location.strip() or "Armory",
        date_received=date_received,
        expiration_date=expiration_date,
        responsible_custodian_id=custodian_id,
        owning_unit_id=unit_id,
        remarks=remarks.strip(),
        created_by=actor.username,
    )
    session.add(lot)
    session.flush()
    db.enqueue(
        session,
        entity_type="ammunition",
        entity_uuid=str(lot.id),
        action="ammunition.create",
        payload={"ammunition_id": lot.id},
    )
    db.audit(
        session,
        username=actor.username,
        action="register_ammunition",
        entity_type="ammunition",
        entity_id=lot.id,
        reference=lot.ammunition_id,
        after={
            "nomenclature": lot.nomenclature,
            "lot_number": lot.lot_number,
            "quantity_received": lot.quantity_received,
            "stock_balance": lot.stock_balance,
        },
    )
    return lot


def adjust_ammunition(
    session: Session,
    *,
    actor: LocalUser,
    ammunition_id: int,
    new_balance: int,
    reason: str,
    supporting_documents: str = "",
) -> Transaction:
    _check(actor, "manage_inventory")
    lot = session.get(Ammunition, ammunition_id)
    if lot is None:
        raise ArmoryError("Ammunition lot not found.")
    reason = str(reason or "").strip()
    if not reason:
        raise ArmoryError("Give the reason for the stock adjustment.")
    try:
        new_balance = int(new_balance)
    except (TypeError, ValueError):
        raise ArmoryError("Enter a whole-number stock balance.")
    if new_balance < 0:
        raise ArmoryError("Stock balance cannot be negative.")
    if new_balance == lot.stock_balance:
        raise ArmoryError("The stock balance is unchanged.")
    delta = new_balance - lot.stock_balance
    if delta > 0:
        lot.quantity_received += delta
    lot.stock_balance = new_balance
    unit = session.get(Unit, lot.owning_unit_id)
    transaction = Transaction(
        reference_number=_unique_reference(session, "ADJ"),
        transaction_type="adjustment",
        status="closed",
        unit_id=lot.owning_unit_id,
        unit_name=unit.name if unit else "",
        releasing_armorer=_actor_name(actor),
        supporting_documents=supporting_documents.strip(),
        discrepancy_notes=reason,
        created_by=actor.username,
    )
    session.add(transaction)
    session.flush()
    session.add(
        TransactionLine(
            transaction_id=transaction.id,
            ammunition_id=lot.id,
            ammunition_name=lot.nomenclature,
            ammunition_caliber=lot.caliber,
            ammunition_type=lot.ammunition_type,
            lot_number=lot.lot_number,
            unit_of_measure=lot.unit_of_measure,
            quantity=abs(delta),
            condition_status=lot.serviceability,
            inspection_remarks=reason,
        )
    )
    db.enqueue(
        session,
        entity_type="transaction",
        entity_uuid=transaction.uuid,
        action="ammunition.adjust",
        payload={
            "transaction_id": transaction.id,
            "stock_balance": lot.stock_balance,
            "reason": reason,
        },
    )
    db.audit(
        session,
        username=actor.username,
        action="adjust_ammunition",
        entity_type="ammunition",
        entity_id=lot.id,
        reference=lot.ammunition_id,
        before={"stock_balance": lot.stock_balance - delta},
        after={"stock_balance": lot.stock_balance},
        note=reason,
    )
    return transaction


# ---------------------------------------------------------------------------
# Personnel assignments
# ---------------------------------------------------------------------------


def active_assignments(session: Session, personnel_id: int) -> list[Assignment]:
    return list(
        session.scalars(
            select(Assignment)
            .options(selectinload(Assignment.firearm))
            .where(
                Assignment.personnel_id == personnel_id,
                Assignment.is_active.is_(True),
            )
            .order_by(Assignment.assignment_type)
        )
    )


def assignment_for_type(
    session: Session, personnel_id: int, assignment_type: str
) -> Assignment | None:
    return session.scalar(
        select(Assignment)
        .options(selectinload(Assignment.firearm))
        .where(
            Assignment.personnel_id == personnel_id,
            Assignment.assignment_type == assignment_type,
            Assignment.is_active.is_(True),
        )
    )


def designation_summary(session: Session, person: Personnel) -> dict:
    rows = {row.assignment_type: row.firearm for row in active_assignments(session, person.id)}
    blockers: list[str] = []
    if not person.is_active:
        blockers.append("Personnel record is inactive.")
    if not person.is_authorized:
        blockers.append("Personnel is not authorized to receive firearms.")
    for kind in ("rifle", "pistol"):
        firearm = rows.get(kind)
        if firearm is None:
            blockers.append(f"No designated {kind}.")
        elif not firearm.is_available:
            blockers.append(
                f"{kind.title()} {firearm.butt_number} is "
                f"{CUSTODY.get(firearm.custody_status, firearm.custody_status)} / "
                f"{CONDITIONS.get(firearm.condition_status, firearm.condition_status)}."
            )
    return {
        "personnel": person,
        "rifle": rows.get("rifle"),
        "pistol": rows.get("pistol"),
        "blockers": blockers,
        "ready": not blockers,
    }


def assign_designated_firearms(
    session: Session,
    *,
    actor: LocalUser,
    personnel_id: int,
    rifle_firearm_id: int | None,
    pistol_firearm_id: int | None,
    notes: str = "",
) -> list[Assignment]:
    _check(actor, "assign")
    person = session.get(Personnel, personnel_id)
    if person is None or not person.is_active:
        raise ArmoryError("Choose an active personnel record.")
    if not person.is_authorized:
        raise ArmoryError("This person is not authorized to receive firearms.")
    requested = {
        "rifle": rifle_firearm_id,
        "pistol": pistol_firearm_id,
    }
    for kind, local_id in requested.items():
        if local_id is None:
            continue
        firearm = session.get(Firearm, local_id)
        if firearm is None:
            raise ArmoryError("Choose a registered firearm.")
        if firearm.firearm_type != kind:
            raise ArmoryError(f"{firearm.firearm_id} is not a {kind}.")
        if firearm.custody_status == "issued":
            raise ArmoryError(f"{firearm.firearm_id} is currently issued.")
        if firearm.condition_status in {"unserviceable", "condemned"}:
            raise ArmoryError(f"{firearm.firearm_id} is not serviceable.")
        if firearm.assigned_unit_id != person.unit_id:
            raise ArmoryError(
                f"{firearm.firearm_id} belongs to another unit. A person may only be "
                "designated a firearm from their own armory."
            )
    created: list[Assignment] = []
    operation_uuid = new_uuid()
    for kind, local_id in requested.items():
        if local_id is None:
            continue
        firearm = session.get(Firearm, local_id)
        current = assignment_for_type(session, person.id, kind)
        if current is not None and current.firearm_id == firearm.id:
            created.append(current)
            continue
        if current is not None:
            current.is_active = False
            current.released_at = now()
            current.released_by = actor.username
            if current.firearm.assigned_personnel_id == person.id:
                current.firearm.assigned_personnel_id = None
            db.audit(
                session,
                username=actor.username,
                action="release_assignment",
                entity_type="assignment",
                entity_id=current.id,
                reference=current.firearm.firearm_id,
                before={"assignment_type": kind, "is_active": True},
                after={"is_active": False},
                note="Reassigned",
            )
        holder = session.scalar(
            select(Assignment).where(
                Assignment.firearm_id == firearm.id,
                Assignment.is_active.is_(True),
            )
        )
        if holder is not None and holder.personnel_id != person.id:
            raise ArmoryError(f"{firearm.firearm_id} is already designated to another person.")
        assignment = Assignment(
            sync_uuid=operation_uuid,
            personnel_id=person.id,
            firearm_id=firearm.id,
            assignment_type=kind,
            assigned_by=actor.username,
            notes=str(notes or "").strip(),
        )
        session.add(assignment)
        firearm.assigned_personnel_id = person.id
        created.append(assignment)
        db.audit(
            session,
            username=actor.username,
            action="assign_firearm",
            entity_type="assignment",
            entity_id=assignment.sync_uuid,
            reference=firearm.firearm_id,
            after={
                "personnel": person.full_name,
                "firearm_id": firearm.firearm_id,
                "butt_number": firearm.butt_number,
                "assignment_type": kind,
            },
            note=str(notes or "").strip(),
        )
    session.flush()
    for kind in ("rifle", "pistol"):
        if assignment_for_type(session, person.id, kind) is None:
            raise ArmoryError(
                "Every authorized person must hold one designated rifle and one "
                "designated pistol. Select both before saving."
            )
    payload = {
        "personnel_id": person.id,
        "rifle_id": requested["rifle"] or (
            assignment_for_type(session, person.id, "rifle").firearm_id
            if assignment_for_type(session, person.id, "rifle")
            else None
        ),
        "pistol_id": requested["pistol"] or (
            assignment_for_type(session, person.id, "pistol").firearm_id
            if assignment_for_type(session, person.id, "pistol")
            else None
        ),
        "notes": str(notes or "").strip(),
        "client_uuid": operation_uuid,
    }
    db.enqueue(
        session,
        entity_type="assignment_state",
        entity_uuid=str(person.id),
        action="assignment.set",
        payload=payload,
    )
    try:
        session.flush()
    except IntegrityError as exc:
        raise ArmoryError("That firearm or assignment conflicts with an existing record.") from exc
    return created


def release_assignment(
    session: Session, *, actor: LocalUser, assignment_id: int, reason: str = ""
) -> None:
    _check(actor, "assign")
    assignment = session.get(Assignment, assignment_id)
    if assignment is None or not assignment.is_active:
        raise ArmoryError("That assignment is already closed.")
    assignment.is_active = False
    assignment.released_at = now()
    assignment.released_by = actor.username
    if assignment.firearm.assigned_personnel_id == assignment.personnel_id:
        assignment.firearm.assigned_personnel_id = None
    person = session.get(Personnel, assignment.personnel_id)
    required = {"rifle", "pistol"}
    remaining = {
        row.assignment_type for row in active_assignments(session, assignment.personnel_id)
    }
    if required - remaining:
        # This is expected while a person is being fully reassigned; the UI
        # calls the assign dialog, not this action, for that case.
        pass
    db.enqueue(
        session,
        entity_type="assignment_state",
        entity_uuid=str(assignment.personnel_id),
        action="assignment.set",
        payload={
            "personnel_id": assignment.personnel_id,
            "rifle_id": next(
                (
                    row.firearm_id
                    for row in active_assignments(session, assignment.personnel_id)
                    if row.assignment_type == "rifle"
                ),
                None,
            ),
            "pistol_id": next(
                (
                    row.firearm_id
                    for row in active_assignments(session, assignment.personnel_id)
                    if row.assignment_type == "pistol"
                ),
                None,
            ),
            "notes": reason.strip(),
            "client_uuid": new_uuid(),
        },
    )
    db.audit(
        session,
        username=actor.username,
        action="release_assignment",
        entity_type="assignment",
        entity_id=assignment.id,
        reference=assignment.firearm.firearm_id,
        before={"is_active": True, "assignment_type": assignment.assignment_type},
        after={"is_active": False},
        note=reason,
    )


# ---------------------------------------------------------------------------
# Missions and eligibility
# ---------------------------------------------------------------------------


def mission_rows(session: Session, only_ready: bool = False) -> list[Mission]:
    query = select(Mission).options(selectinload(Mission.unit))
    if only_ready:
        query = query.where(Mission.order_ready.is_(True))
    return list(
        session.scalars(query.order_by(Mission.start_at.desc(), Mission.id.desc()))
    )


def mission_personnel_ids(session: Session, mission_id: int) -> set[int]:
    return set(
        session.scalars(
            select(MissionPersonnel.personnel_id).where(
                MissionPersonnel.mission_id == mission_id
            )
        )
    )


def eligibility_rows(session: Session, mission_id: int | None = None) -> list[dict]:
    people = personnel_rows(session, active_only=True)
    if mission_id is not None:
        allowed = mission_personnel_ids(session, mission_id)
        people = [person for person in people if person.id in allowed]
    rows = []
    for person in people:
        summary = designation_summary(session, person)
        summary["mission_role"] = ""
        if mission_id is not None:
            membership = session.scalar(
                select(MissionPersonnel).where(
                    MissionPersonnel.mission_id == mission_id,
                    MissionPersonnel.personnel_id == person.id,
                )
            )
            if membership is not None:
                summary["mission_role"] = (
                    membership.custom_role
                    if membership.role == "Specify" and membership.custom_role
                    else membership.role
                )
        rows.append(summary)
    return rows


# ---------------------------------------------------------------------------
# Issuance
# ---------------------------------------------------------------------------


def _validate_acknowledgement(person: Personnel, acknowledged: bool, name: str) -> None:
    if not acknowledged:
        raise ArmoryError("The receiving person must acknowledge receipt before release.")
    expected = {
        " ".join(person.full_name.lower().split()),
        person.username.lower().strip(),
    }
    supplied = " ".join(str(name or "").strip().lower().split())
    if supplied not in expected:
        raise ArmoryError("The acknowledgement name must match the receiving person.")


def issue_firearms(
    session: Session,
    *,
    actor: LocalUser,
    personnel_id: int,
    mission_id: int | None,
    purpose: str,
    firearm_lines: list[dict],
    ammunition_lines: list[dict],
    identity_verified: bool,
    acknowledged: bool,
    acknowledgement_name: str,
    inspection_remarks: str = "",
    supporting_documents: str = "",
) -> Transaction:
    _check(actor, "issue")
    if not identity_verified:
        raise ArmoryError("Verify the person's identity before releasing any firearm.")
    person = session.get(Personnel, personnel_id)
    if person is None or not person.is_active:
        raise ArmoryError("Choose an active personnel record.")
    if not person.is_authorized:
        raise ArmoryError("This person is not authorized to receive firearms.")
    _validate_acknowledgement(person, acknowledged, acknowledgement_name)
    mission = session.get(Mission, mission_id) if mission_id else None
    if mission is not None:
        if not mission.order_ready:
            raise ArmoryError(
                mission.order_error or "The linked Mission Order is not ready for release."
            )
        if person.id not in mission_personnel_ids(session, mission.id):
            raise ArmoryError(
                "This person is not included in the selected Mission Order."
            )
    else:
        purpose = str(purpose or "").strip()
        if not purpose:
            raise ArmoryError("Give the purpose or reason for a regular issuance.")

    assigned = active_assignments(session, person.id)
    by_type = {row.assignment_type: row for row in assigned}
    if set(by_type) != {"rifle", "pistol"}:
        raise ArmoryError(
            "This person must have one designated rifle and one designated pistol "
            "before a firearm can be issued."
        )
    active_issue = session.scalar(
        select(Transaction).where(
            Transaction.personnel_id == person.id,
            Transaction.transaction_type == "issuance",
            Transaction.status == "open",
        )
    )
    if active_issue is not None:
        raise ArmoryError(
            f"This person already has open issue {active_issue.reference_number}. "
            "Record the return first."
        )
    submitted: dict[str, dict] = {}
    for line in firearm_lines:
        identifier = str(line.get("firearm_id") or "").strip()
        butt = normalise(line.get("butt_number"))
        if not identifier or not butt:
            raise ArmoryError("Verify both the Firearm ID and Butt Number for every weapon.")
        assignment = next(
            (
                row
                for row in assigned
                if row.firearm.firearm_id.lower() == identifier.lower()
            ),
            None,
        )
        if assignment is None:
            raise ArmoryError(f"{identifier} is not designated to this person.")
        firearm = assignment.firearm
        if normalise(firearm.butt_number) != butt:
            raise ArmoryError(f"{identifier} / {butt} does not match the designated firearm.")
        if not firearm.is_available:
            raise ArmoryError(
                f"{firearm.firearm_id} is not stored and serviceable for issue."
            )
        if line.get("inspection_status", "serviceable") != "serviceable":
            raise ArmoryError(f"{identifier} did not pass the physical inspection.")
        submitted[firearm.firearm_id] = line
    expected_ids = {row.firearm.firearm_id for row in assigned}
    missing = expected_ids - set(submitted)
    if missing:
        raise ArmoryError(
            "All designated firearms must be accounted for at issue. Missing: "
            + ", ".join(sorted(missing))
        )

    ammo_quantities: dict[int, int] = {}
    for line in ammunition_lines:
        try:
            ammo_id = int(line.get("ammunition_id"))
            quantity = int(line.get("quantity") or 0)
        except (TypeError, ValueError):
            raise ArmoryError("Ammunition quantities must be whole numbers.")
        if quantity <= 0:
            continue
        lot = session.get(Ammunition, ammo_id)
        if lot is None:
            raise ArmoryError("One of the ammunition lots no longer exists.")
        if lot.serviceability != "serviceable":
            raise ArmoryError(f"{lot.ammunition_id} is not serviceable.")
        if lot.expiration_date and lot.expiration_date < date.today():
            raise ArmoryError(f"{lot.ammunition_id} has expired.")
        if quantity > lot.stock_balance:
            raise ArmoryError(
                f"Only {lot.stock_balance} {lot.unit_of_measure} of {lot.nomenclature} "
                "are available."
            )
        ammo_quantities[ammo_id] = ammo_quantities.get(ammo_id, 0) + quantity

    unit = mission.unit if mission is not None and mission.unit else person.unit
    transaction = Transaction(
        reference_number=_unique_reference(session, "ISS"),
        transaction_type="issuance",
        status="open",
        mission_id=mission.id if mission else None,
        mission_order_reference=(
            mission.mission_number if mission else ""
        ),
        purpose=str(purpose or "").strip(),
        personnel_id=person.id,
        rank=person.rank,
        personnel_name=person.full_name,
        branch_of_service=person.branch_of_service,
        unit_id=unit.id if unit else None,
        unit_name=unit.name if unit else "",
        releasing_armorer=_actor_name(actor),
        receiving_personnel=person.full_name,
        supporting_documents=str(supporting_documents or "").strip(),
        acknowledgement_name=str(acknowledgement_name or "").strip(),
        acknowledged_at=now(),
        created_by=actor.username,
    )
    # The keyword above cannot use a conditional name; assign the field
    # explicitly to keep the constructor readable.
    transaction.inspection_remarks = str(inspection_remarks or "").strip()
    session.add(transaction)
    session.flush()
    for assignment in assigned:
        firearm = assignment.firearm
        line = submitted[firearm.firearm_id]
        session.add(
            TransactionLine(
                transaction_id=transaction.id,
                firearm_id=firearm.id,
                firearm_id_snapshot=firearm.firearm_id,
                butt_number_snapshot=firearm.butt_number,
                serial_number_snapshot=firearm.serial_number,
                firearm_type=firearm.firearm_type,
                manufacturer=firearm.manufacturer,
                model=firearm.model,
                caliber=firearm.caliber,
                condition_status="serviceable",
                inspection_remarks=str(line.get("inspection_remarks") or "").strip(),
            )
        )
        firearm.custody_status = "issued"
        firearm.date_last_inspected = date.today()
        inspection = Inspection(
            firearm_id=firearm.id,
            inspected_by=_actor_name(actor),
            inspected_at=now(),
            condition_status="serviceable",
            remarks=str(line.get("inspection_remarks") or "").strip(),
            transaction_id=transaction.id,
            sync_state="local_only",
        )
        session.add(inspection)
        db.audit(
            session,
            username=actor.username,
            action="issue_firearm",
            entity_type="transaction",
            entity_id=transaction.uuid,
            reference=transaction.reference_number,
            after={
                "firearm_id": firearm.firearm_id,
                "butt_number": firearm.butt_number,
                "personnel_id": person.id,
                "personnel": person.full_name,
            },
            note=mission.mission_number if mission else purpose,
        )
    for ammo_id, quantity in ammo_quantities.items():
        lot = session.get(Ammunition, ammo_id)
        session.add(
            TransactionLine(
                transaction_id=transaction.id,
                ammunition_id=lot.id,
                ammunition_name=lot.nomenclature,
                ammunition_caliber=lot.caliber,
                ammunition_type=lot.ammunition_type,
                lot_number=lot.lot_number,
                unit_of_measure=lot.unit_of_measure,
                quantity=quantity,
                condition_status=lot.serviceability,
            )
        )
        lot.quantity_issued += quantity
        lot.stock_balance -= quantity
    session.flush()
    db.enqueue(
        session,
        entity_type="transaction",
        entity_uuid=transaction.uuid,
        action="transaction.issue",
        payload={"transaction_id": transaction.id},
    )
    db.audit(
        session,
        username=actor.username,
        action="issue_firearms",
        entity_type="transaction",
        entity_id=transaction.uuid,
        reference=transaction.reference_number,
        after={
            "mission": mission.mission_number if mission else "",
            "purpose": transaction.purpose,
            "personnel": person.full_name,
            "status": "open",
        },
    )
    return transaction


# ---------------------------------------------------------------------------
# Return / deposit
# ---------------------------------------------------------------------------


def open_issues(session: Session) -> list[Transaction]:
    return list(
        session.scalars(
            select(Transaction)
            .options(
                selectinload(Transaction.personnel),
                selectinload(Transaction.lines),
            )
            .where(
                Transaction.transaction_type == "issuance",
                Transaction.status == "open",
            )
            .order_by(Transaction.transaction_at.desc())
        )
    )


def return_firearms(
    session: Session,
    *,
    actor: LocalUser,
    issuance_id: int,
    firearm_lines: list[dict],
    ammunition_lines: list[dict],
    identity_verified: bool,
    acknowledged: bool,
    acknowledgement_name: str,
    inspection_remarks: str = "",
    discrepancy_notes: str = "",
    supporting_documents: str = "",
) -> Transaction:
    _check(actor, "return")
    issuance = session.get(Transaction, issuance_id)
    if issuance is None or issuance.transaction_type != "issuance":
        raise ArmoryError("Choose an original issuance.")
    if issuance.status != "open":
        raise ArmoryError("That issuance is already closed.")
    if not identity_verified:
        raise ArmoryError("Verify the returning person's identity.")
    expected_name = {
        " ".join((issuance.personnel_name or "").lower().split()),
        (issuance.personnel.username.lower() if issuance.personnel else ""),
    }
    if " ".join(str(acknowledgement_name or "").lower().split()) not in expected_name:
        raise ArmoryError("The acknowledgement name must match the returning person.")
    if not acknowledged:
        raise ArmoryError("The returning person must acknowledge the deposit.")

    original_firearms = [line for line in issuance.lines if line.firearm_id]
    original_ammo = {line.id: line for line in issuance.lines if line.ammunition_id}
    submitted = {}
    for item in firearm_lines:
        identifier = str(item.get("firearm_id") or "").strip()
        butt = normalise(item.get("butt_number"))
        line = next(
            (
                row
                for row in original_firearms
                if row.firearm_id_snapshot.lower() == identifier.lower()
            ),
            None,
        )
        if line is None or normalise(line.butt_number_snapshot) != butt:
            raise ArmoryError(f"{identifier} / {butt} does not match the original issuance.")
        submitted[line.id] = item
    if len(submitted) != len(original_firearms):
        raise ArmoryError("All firearms on the original issuance must be returned together.")

    returned_ammo = {}
    for item in ammunition_lines:
        line_id = int(item.get("line_id"))
        line = original_ammo.get(line_id)
        if line is None:
            raise ArmoryError("Ammunition line does not belong to this issuance.")
        try:
            quantity = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            raise ArmoryError("Returned ammunition quantities must be whole numbers.")
        if quantity < 0 or quantity > line.quantity:
            raise ArmoryError(
                f"Returned quantity for {line.ammunition_name} must be between 0 and {line.quantity}."
            )
        returned_ammo[line_id] = {**item, "quantity": quantity}

    transaction = Transaction(
        reference_number=_unique_reference(session, "RET"),
        transaction_type="return",
        status="closed",
        mission_id=issuance.mission_id,
        mission_order_reference=issuance.mission_order_reference,
        purpose=issuance.purpose,
        personnel_id=issuance.personnel_id,
        rank=issuance.rank,
        personnel_name=issuance.personnel_name,
        branch_of_service=issuance.branch_of_service,
        unit_id=issuance.unit_id,
        unit_name=issuance.unit_name,
        releasing_armorer=_actor_name(actor),
        receiving_personnel=issuance.receiving_personnel,
        inspection_remarks=str(inspection_remarks or "").strip(),
        supporting_documents=str(supporting_documents or "").strip(),
        discrepancy_notes=str(discrepancy_notes or "").strip(),
        acknowledgement_name=str(acknowledgement_name or "").strip(),
        acknowledged_at=now(),
        return_of_id=issuance.id,
        returned_at=now(),
        created_by=actor.username,
    )
    session.add(transaction)
    session.flush()
    for line in original_firearms:
        item = submitted[line.id]
        firearm = session.get(Firearm, line.firearm_id)
        if firearm is None:
            raise ArmoryError(f"{line.firearm_id_snapshot} is no longer registered.")
        if firearm.custody_status != "issued":
            raise ArmoryError(f"{firearm.firearm_id} is not currently issued.")
        condition = str(item.get("condition_status") or "serviceable")
        if condition not in CONDITIONS:
            raise ArmoryError("Choose a valid return inspection condition.")
        remarks = str(item.get("inspection_remarks") or "").strip()
        session.add(
            TransactionLine(
                transaction_id=transaction.id,
                firearm_id=firearm.id,
                original_line_id=line.id,
                firearm_id_snapshot=line.firearm_id_snapshot,
                butt_number_snapshot=line.butt_number_snapshot,
                serial_number_snapshot=line.serial_number_snapshot,
                firearm_type=line.firearm_type,
                manufacturer=line.manufacturer,
                model=line.model,
                caliber=line.caliber,
                condition_status=condition,
                inspection_remarks=remarks,
            )
        )
        firearm.custody_status = "stored"
        firearm.condition_status = condition
        firearm.date_last_inspected = date.today()
        session.add(
            Inspection(
                firearm_id=firearm.id,
                inspected_by=_actor_name(actor),
                inspected_at=now(),
                condition_status=condition,
                remarks=remarks,
                transaction_id=transaction.id,
                sync_state="local_only",
            )
        )
        db.audit(
            session,
            username=actor.username,
            action="return_firearm",
            entity_type="transaction",
            entity_id=transaction.uuid,
            reference=transaction.reference_number,
            after={
                "firearm_id": firearm.firearm_id,
                "butt_number": firearm.butt_number,
                "condition": condition,
            },
            note=remarks,
        )
    for line in original_ammo.values():
        item = returned_ammo.get(line.id, {"quantity": 0})
        quantity = int(item.get("quantity") or 0)
        lot = session.get(Ammunition, line.ammunition_id)
        if lot is None:
            raise ArmoryError(f"{line.ammunition_name} is no longer registered.")
        consumed = line.quantity - quantity
        if consumed:
            note = (
                f"{line.ammunition_name} lot {line.lot_number}: issued {line.quantity}, "
                f"returned {quantity}, not returned {consumed}."
            )
            transaction.discrepancy_notes = (
                (transaction.discrepancy_notes + " " + note).strip()
            )
        session.add(
            TransactionLine(
                transaction_id=transaction.id,
                ammunition_id=lot.id,
                original_line_id=line.id,
                ammunition_name=line.ammunition_name,
                ammunition_caliber=line.ammunition_caliber,
                ammunition_type=line.ammunition_type,
                lot_number=line.lot_number,
                unit_of_measure=line.unit_of_measure,
                quantity=quantity,
                condition_status=str(item.get("serviceability") or lot.serviceability),
                inspection_remarks=str(item.get("discrepancy_note") or "").strip(),
            )
        )
        lot.quantity_issued = max(0, lot.quantity_issued - line.quantity)
        lot.stock_balance += quantity
    session.flush()
    issuance.status = "closed"
    issuance.returned_at = now()
    db.enqueue(
        session,
        entity_type="transaction",
        entity_uuid=transaction.uuid,
        action="transaction.return",
        payload={"transaction_id": transaction.id},
    )
    db.audit(
        session,
        username=actor.username,
        action="return_firearms",
        entity_type="transaction",
        entity_id=transaction.uuid,
        reference=transaction.reference_number,
        before={"issuance": issuance.reference_number, "status": "open"},
        after={
            "issuance": issuance.reference_number,
            "status": "closed",
            "discrepancy_notes": transaction.discrepancy_notes,
        },
    )
    return transaction


# ---------------------------------------------------------------------------
# Queries and dashboard
# ---------------------------------------------------------------------------


def transaction_rows(
    session: Session,
    *,
    search: str = "",
    transaction_type: str = "",
    status: str = "",
    limit: int = 1000,
) -> list[Transaction]:
    query = select(Transaction).options(
        selectinload(Transaction.lines),
        selectinload(Transaction.mission),
        selectinload(Transaction.personnel),
    )
    if transaction_type:
        query = query.where(Transaction.transaction_type == transaction_type)
    if status:
        query = query.where(Transaction.status == status)
    search = str(search or "").strip()
    if search:
        needle = f"%{search}%"
        query = query.where(
            or_(
                Transaction.reference_number.ilike(needle),
                Transaction.mission_order_reference.ilike(needle),
                Transaction.personnel_name.ilike(needle),
                Transaction.purpose.ilike(needle),
                Transaction.discrepancy_notes.ilike(needle),
                Transaction.lines.any(TransactionLine.firearm_id_snapshot.ilike(needle)),
                Transaction.lines.any(TransactionLine.butt_number_snapshot.ilike(needle)),
                Transaction.lines.any(TransactionLine.serial_number_snapshot.ilike(needle)),
                Transaction.lines.any(TransactionLine.lot_number.ilike(needle)),
            )
        )
    return list(
        session.scalars(
            query.order_by(Transaction.transaction_at.desc(), Transaction.id.desc()).limit(limit)
        )
    )


def dashboard(session: Session) -> dict:
    total = session.scalar(select(func.count()).select_from(Firearm)) or 0
    stored = session.scalar(
        select(func.count()).select_from(Firearm).where(Firearm.custody_status == "stored")
    ) or 0
    issued = session.scalar(
        select(func.count()).select_from(Firearm).where(Firearm.custody_status == "issued")
    ) or 0
    ammo_stock = session.scalar(select(func.sum(Ammunition.stock_balance))) or 0
    ammo_lots = session.scalar(select(func.count()).select_from(Ammunition)) or 0
    open_issue_count = session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.transaction_type == "issuance", Transaction.status == "open")
    ) or 0
    cut = date.today() - timedelta(days=INSPECTION_DUE_DAYS)
    due = session.scalar(
        select(func.count())
        .select_from(Firearm)
        .where(
            Firearm.custody_status != "retired",
            or_(Firearm.date_last_inspected.is_(None), Firearm.date_last_inspected <= cut),
        )
    ) or 0
    unserviceable = session.scalar(
        select(func.count())
        .select_from(Firearm)
        .where(Firearm.condition_status != "serviceable")
    ) or 0
    pending = db.pending_sync_count(session)
    failed = db.failed_sync_count(session)
    recent = transaction_rows(session, limit=8)
    return {
        "total": total,
        "stored": stored,
        "issued": issued,
        "ammo_stock": ammo_stock,
        "ammo_lots": ammo_lots,
        "open_issues": open_issue_count,
        "inspection_due": due,
        "unserviceable": unserviceable,
        "pending_sync": pending,
        "failed_sync": failed,
        "recent": recent,
    }


def transaction_by_id(session: Session, transaction_id: int) -> Transaction | None:
    return session.scalar(
        select(Transaction)
        .options(
            selectinload(Transaction.lines),
            selectinload(Transaction.mission),
            selectinload(Transaction.personnel),
            selectinload(Transaction.unit),
        )
        .where(Transaction.id == transaction_id)
    )


def assignment_history(session: Session, personnel_id: int | None = None) -> list[Assignment]:
    query = select(Assignment).options(
        selectinload(Assignment.firearm), selectinload(Assignment.personnel)
    )
    if personnel_id:
        query = query.where(Assignment.personnel_id == personnel_id)
    return list(
        session.scalars(
            query.order_by(Assignment.is_active.desc(), Assignment.assigned_at.desc())
        )
    )


def audit_rows(session: Session, limit: int = 500) -> list[AuditLog]:
    return list(
        session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
    )


def queue_rows(session: Session, state: str = "") -> list[SyncQueue]:
    query = select(SyncQueue)
    if state:
        query = query.where(SyncQueue.state == state)
    return list(session.scalars(query.order_by(SyncQueue.created_at, SyncQueue.id)))


def appointment_missing(session: Session) -> bool:
    return False


def seed_reference_units(session: Session) -> None:
    """Add one local unit only when no unit exists and sync has never run."""
    if session.scalar(select(func.count()).select_from(Unit)):
        return
    session.add(Unit(name="Main Armory", acronym="MAIN"))

"""Armory business rules and atomic custody workflows.

Every state change that affects accountability goes through this module. The
views are thin: they authenticate, authorise, validate request shape and call
one of these functions. That keeps the same rules in force for the web API,
tests and any later command-line or Android client.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from django.db import transaction as db_transaction
from django.db.models import F, Q, Sum
from django.utils.dateparse import parse_date

from apps.accounts.models import User
from apps.core import timeutils
from apps.core.audit import log_action, snapshot
from apps.core.scoping import accessible_unit_ids
from apps.ops.models import Mission, MissionPersonnel

from .models import (
    Ammunition,
    ArmoryAuditLog,
    ArmoryTransaction,
    ArmoryTransactionLine,
    Firearm,
    FirearmInspection,
    FirearmSerialChange,
    PersonnelFirearmAssignment,
    normalise_identifier,
)

#: A firearm is due for inspection when this many days have passed since its
#: last recorded inspection. It is a monitoring threshold, not a legal limit.
INSPECTION_DUE_DAYS = 30


class ArmoryError(Exception):
    """A rule violation that should be shown to the operator."""


# ---------------------------------------------------------------------------
# Scope and access helpers
# ---------------------------------------------------------------------------


def user_unit_ids(user) -> set[int]:
    """Units whose armory records this user may see.

    Only active units are considered. An account with no unit scope gets an
    empty set, never the whole register.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    return set(accessible_unit_ids(user))


def scope_filter(queryset, user, field: str = "assigned_unit_id"):
    ids = user_unit_ids(user)
    if not ids:
        return queryset.none()
    return queryset.filter(**{f"{field}__in": ids})


def scoped_firearms(user):
    return scope_filter(
        Firearm.objects.select_related(
            "assigned_unit", "assigned_personnel", "registered_by"
        ),
        user,
    )


def scoped_ammunition(user):
    return scope_filter(
        Ammunition.objects.select_related(
            "owning_unit", "responsible_custodian", "created_by"
        ),
        user,
        "owning_unit_id",
    )


def scoped_assignments(user):
    return scope_filter(
        PersonnelFirearmAssignment.objects.select_related(
            "user", "user__unit", "firearm", "firearm__assigned_unit", "assigned_by"
        ),
        user,
        "firearm__assigned_unit_id",
    )


def scoped_transactions(user):
    return scope_filter(
        ArmoryTransaction.objects.select_related(
            "mission",
            "personnel",
            "unit",
            "releasing_armorer",
            "receiving_personnel",
            "approving_authority",
            "return_of",
            "created_by",
        ).prefetch_related(
            "lines__firearm",
            "lines__ammunition",
            "lines__original_line",
            "inspections__firearm",
        ),
        user,
        "unit_id",
    )


def _require_unit_in_scope(user, unit_id):
    ids = user_unit_ids(user)
    if not unit_id or unit_id not in ids:
        raise ArmoryError("That unit is outside your authorised armory scope.")


def _default_unit(user):
    unit = getattr(user, "unit", None)
    if unit is None:
        raise ArmoryError("Your account has no unit. Ask the Administrator to assign one.")
    return unit


def _actor_name(user) -> str:
    if user is None:
        return ""
    return (
        getattr(user, "get_full_name", lambda: "")()
        or getattr(user, "username", "")
        or ""
    )


def _normalise_name(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _audit(
    *,
    action: str,
    entity_type: str,
    entity_id="",
    actor=None,
    before=None,
    after=None,
    reference="",
    note="",
    request=None,
):
    """Append to both the armory audit and the system-wide audit trail."""
    ip_address = ""
    user_agent = ""
    if request is not None:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip_address = (
            forwarded.split(",")[0].strip()
            or request.META.get("REMOTE_ADDR", "")
            or ""
        )[:64]
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:400]
    ArmoryAuditLog.objects.create(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id or ""),
        reference=reference or "",
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        before=before or {},
        after=after or {},
        note=note or "",
        ip_address=ip_address,
        user_agent=user_agent,
    )
    log_action(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        note=note,
        request=request,
    )


def _firearm_snapshot(firearm: Firearm) -> dict:
    return snapshot(
        firearm,
        [
            "firearm_id",
            "serial_number",
            "butt_number",
            "firearm_type",
            "manufacturer",
            "model",
            "caliber",
            "condition_status",
            "custody_status",
            "assigned_personnel_id",
            "assigned_unit_id",
            "storage_location",
            "date_last_inspected",
            "remarks",
        ],
    )


# ---------------------------------------------------------------------------
# Mission-order authorization
# ---------------------------------------------------------------------------


def mission_order_error(mission: Mission) -> str:
    """Return the reason an approved Mission Order is not in force, or ''.

    The check is deliberately made against the mission order, not merely the
    mission status: a signature only covers the revision it was given for, and
    a material change after approval invalidates it.
    """
    if mission is None:
        return "A mission order is required."
    if mission.status == "completed" or mission.status == "cancelled":
        return "This mission is closed. Firearms cannot be withdrawn against it."
    if mission.status not in {"approved", "active"}:
        return (
            "This mission is not in an approved/active state. A firearm can only "
            "be withdrawn against an approved Mission Order."
        )
    if mission.is_held:
        return "This mission is on hold. Release the hold before withdrawing firearms."
    if mission.approval_status != Mission.APPROVAL_APPROVED:
        return "The mission itself has not been approved."
    if mission.approved_revision != mission.revision:
        return (
            "The mission has changed since its approval. The Mission Order must be "
            "re-approved before firearms can be withdrawn."
        )
    if mission.mission_order_status != Mission.MISSION_ORDER_APPROVED:
        return "The Mission Order has not been signed and approved."
    if mission.mission_order_signed_revision != mission.revision:
        return (
            "The Mission Order signature no longer covers the current mission "
            "revision. It must be signed again."
        )
    if not mission.mission_order_signed_by_id:
        return "The Mission Order has no recorded approving authority."
    return ""


def mission_approving_authority(mission: Mission):
    return mission.mission_order_signed_by or mission.approved_by


def mission_personnel(mission: Mission, user_id) -> MissionPersonnel | None:
    return (
        MissionPersonnel.objects.select_related("user", "user__unit")
        .filter(mission=mission, user_id=user_id, is_active=True)
        .first()
    )


def eligible_personnel(mission: Mission | None = None, *, actor=None) -> list[dict]:
    """Roster rows for the armorer, with each person's designated firearms.

    Without a mission, every active person in the actor's scope who has an
    active designation is returned. This is the standalone withdrawal path
    used when no Mission Order is linked.
    """
    if mission is not None:
        roster = [
            (
                row.user,
                row.custom_role
                if row.role == "Specify" and row.custom_role
                else row.role,
            )
            for row in MissionPersonnel.objects.select_related(
                "user", "user__unit"
            )
            .filter(mission=mission, is_active=True)
            .order_by("user__last_name", "user__first_name", "id")
        ]
        user_ids = [user.id for user, _ in roster]
        assignments = (
            PersonnelFirearmAssignment.objects.select_related(
                "firearm", "firearm__assigned_unit"
            )
            .filter(is_active=True, user_id__in=user_ids)
            .order_by("assignment_type", "id")
        )
        open_issue_qs = ArmoryTransaction.objects.filter(
            mission=mission,
            transaction_type=ArmoryTransaction.TYPE_ISSUANCE,
            status=ArmoryTransaction.STATUS_OPEN,
        )
    else:
        ids = user_unit_ids(actor)
        if not ids:
            return []
        assigned_user_ids = list(
            PersonnelFirearmAssignment.objects.filter(
                is_active=True,
                user__is_active=True,
                user__unit_id__in=ids,
                firearm__assigned_unit_id__in=ids,
            )
            .values_list("user_id", flat=True)
            .distinct()
        )
        users = (
            User.objects.select_related("unit")
            .filter(id__in=assigned_user_ids, unit_id__in=ids, is_active=True)
            .order_by("last_name", "first_name", "id")
        )
        roster = [(user, "") for user in users]
        user_ids = [user.id for user, _ in roster]
        assignments = (
            PersonnelFirearmAssignment.objects.select_related(
                "firearm", "firearm__assigned_unit"
            )
            .filter(
                is_active=True,
                user_id__in=user_ids,
                firearm__assigned_unit_id__in=ids,
            )
            .order_by("assignment_type", "id")
        )
        open_issue_qs = ArmoryTransaction.objects.filter(
            personnel_id__in=user_ids,
            transaction_type=ArmoryTransaction.TYPE_ISSUANCE,
            status=ArmoryTransaction.STATUS_OPEN,
        )

    by_user: dict[int, dict[str, PersonnelFirearmAssignment]] = defaultdict(dict)
    for assignment in assignments:
        by_user[assignment.user_id][assignment.assignment_type] = assignment

    open_issues: dict[int, ArmoryTransaction] = {}
    for row in open_issue_qs.order_by("-id"):
        open_issues.setdefault(row.personnel_id, row)

    result = []
    for user, mission_role in roster:
        designated = by_user.get(user.id, {})
        rifle = designated.get(Firearm.TYPE_RIFLE)
        pistol = designated.get(Firearm.TYPE_PISTOL)
        blockers: list[str] = []
        if not rifle:
            blockers.append("No designated rifle.")
        elif not rifle.firearm.is_available:
            blockers.append(
                f"Rifle {rifle.firearm.butt_number} is "
                f"{rifle.firearm.get_custody_status_display().lower()} / "
                f"{rifle.firearm.get_condition_status_display().lower()}."
            )
        if not pistol:
            blockers.append("No designated pistol.")
        elif not pistol.firearm.is_available:
            blockers.append(
                f"Pistol {pistol.firearm.butt_number} is "
                f"{pistol.firearm.get_custody_status_display().lower()} / "
                f"{pistol.firearm.get_condition_status_display().lower()}."
            )
        if user.id in open_issues:
            blockers.append(
                f"Already issued under {open_issues[user.id].reference_number}; "
                "return it before issuing again."
            )
        if not getattr(user, "is_active", True):
            blockers.append("Personnel account is inactive.")
        result.append(
            {
                "personnel": user.id,
                "username": user.username,
                "name": _actor_name(user),
                "rank": user.rank or "",
                "serial_number": user.serial_number or "",
                "branch_of_service": user.branch_of_service or "",
                "unit": user.unit.acronym if getattr(user, "unit", None) else "",
                "mission_role": mission_role,
                "rifle": _assignment_payload(rifle),
                "pistol": _assignment_payload(pistol),
                "open_issue": (
                    {
                        "id": open_issues[user.id].id,
                        "reference_number": open_issues[user.id].reference_number,
                    }
                    if user.id in open_issues
                    else None
                ),
                "ready": not blockers,
                "blockers": blockers,
            }
        )
    return result


def _assignment_payload(assignment: PersonnelFirearmAssignment | None):
    if assignment is None:
        return None
    firearm = assignment.firearm
    return {
        "assignment_id": assignment.id,
        "firearm": firearm.id,
        "firearm_id": firearm.firearm_id,
        "butt_number": firearm.butt_number,
        "serial_number": firearm.serial_number,
        "make": firearm.manufacturer,
        "model": firearm.model,
        "caliber": firearm.caliber,
        "type": firearm.firearm_type,
        "type_label": firearm.get_firearm_type_display(),
        "condition": firearm.condition_status,
        "condition_label": firearm.get_condition_status_display(),
        "custody": firearm.custody_status,
        "custody_label": firearm.get_custody_status_display(),
        "available": firearm.is_available,
        "storage_location": firearm.storage_location,
    }


# ---------------------------------------------------------------------------
# Firearm registration corrections and inspections
# ---------------------------------------------------------------------------


def correct_firearm_serial(
    *, firearm: Firearm, serial_number: str, reason: str, actor, request=None
) -> Firearm:
    new_serial = normalise_identifier(serial_number)
    reason = str(reason or "").strip()
    if not new_serial:
        raise ArmoryError("Enter the corrected serial number.")
    if not reason:
        raise ArmoryError("Give the authority and reason for correcting the serial number.")
    if firearm.custody_status == Firearm.CUSTODY_ISSUED:
        raise ArmoryError("A firearm cannot have its serial corrected while it is issued.")
    if firearm.transaction_lines.filter(transaction__status=ArmoryTransaction.STATUS_OPEN).exists():
        raise ArmoryError("The firearm has an open accountability transaction.")
    if new_serial == firearm.serial_number:
        raise ArmoryError("The corrected serial number is unchanged.")
    if Firearm.objects.filter(serial_number__iexact=new_serial).exclude(pk=firearm.pk).exists():
        raise ArmoryError("That serial number already belongs to another firearm.")
    rows = list(firearm.transaction_lines.all())
    if any(
        row.transaction.transaction_type == ArmoryTransaction.TYPE_ISSUANCE
        and row.transaction.status == ArmoryTransaction.STATUS_OPEN
        for row in rows
    ):
        raise ArmoryError("An issued firearm cannot be corrected.")

    old_serial = firearm.serial_number
    old_id = firearm.firearm_id
    before = _firearm_snapshot(firearm)
    with db_transaction.atomic():
        firearm = Firearm.objects.select_for_update().get(pk=firearm.pk)
        firearm.serial_number = new_serial
        firearm.save(update_fields=["serial_number", "firearm_id", "updated_at"])
        FirearmSerialChange.objects.create(
            firearm=firearm,
            old_serial_number=old_serial,
            new_serial_number=new_serial,
            old_firearm_id=old_id,
            new_firearm_id=firearm.firearm_id,
            reason=reason,
            changed_by=actor,
        )
        _audit(
            action="correct_firearm_serial",
            entity_type="armory_firearm",
            entity_id=firearm.pk,
            actor=actor,
            before=before,
            after=_firearm_snapshot(firearm),
            reference=firearm.firearm_id,
            note=(
                f"Serial corrected from {old_serial} ({old_id}) to {new_serial} "
                f"({firearm.firearm_id}). Reason: {reason}"
            ),
            request=request,
        )
    return firearm


def inspect_firearm(
    *,
    firearm: Firearm,
    condition_status: str,
    remarks: str,
    inspected_at=None,
    next_inspection_date=None,
    sync_uuid=None,
    actor,
    request=None,
) -> FirearmInspection:
    if firearm.custody_status == Firearm.CUSTODY_ISSUED:
        raise ArmoryError("An issued firearm is inspected when it is returned.")
    condition_status = str(condition_status or "").strip()
    valid = {key for key, _label in Firearm.CONDITION_CHOICES}
    if condition_status not in valid:
        raise ArmoryError("Choose a valid condition/serviceability result.")
    inspected_at = inspected_at or timeutils.now()
    if isinstance(inspected_at, dt.date) and not isinstance(inspected_at, dt.datetime):
        inspected_at = timeutils.now().replace(
            year=inspected_at.year, month=inspected_at.month, day=inspected_at.day
        )
    before = _firearm_snapshot(firearm)
    with db_transaction.atomic():
        firearm = Firearm.objects.select_for_update().get(pk=firearm.pk)
        inspection = FirearmInspection.objects.create(
            firearm=firearm,
            sync_uuid=sync_uuid,
            inspected_by=actor,
            inspected_at=inspected_at,
            condition_status=condition_status,
            remarks=str(remarks or "").strip(),
            next_inspection_date=next_inspection_date,
        )
        firearm.condition_status = condition_status
        firearm.date_last_inspected = inspected_at.date()
        firearm.save(
            update_fields=["condition_status", "date_last_inspected", "updated_at"]
        )
        _audit(
            action="inspect_firearm",
            entity_type="armory_firearm",
            entity_id=firearm.pk,
            actor=actor,
            before=before,
            after=_firearm_snapshot(firearm),
            reference=firearm.firearm_id,
            note=str(remarks or "").strip(),
            request=request,
        )
    return inspection


def transfer_firearm(
    *,
    firearm: Firearm,
    assigned_unit,
    storage_location: str,
    reason: str,
    actor,
    supporting_documents: str = "",
    request=None,
) -> ArmoryTransaction:
    reason = str(reason or "").strip()
    if not reason:
        raise ArmoryError("Give the reason for transferring this firearm.")
    if firearm.custody_status == Firearm.CUSTODY_ISSUED:
        raise ArmoryError("A firearm cannot be transferred while it is issued.")
    if firearm.assignments.filter(is_active=True).exists():
        raise ArmoryError(
            "Release the personnel designation before transferring the firearm to another unit."
        )
    _require_unit_in_scope(actor, firearm.assigned_unit_id)
    _require_unit_in_scope(actor, assigned_unit.id)
    old_unit = firearm.assigned_unit_id
    if int(old_unit) == int(assigned_unit.id):
        raise ArmoryError("The firearm is already assigned to that unit.")
    before = _firearm_snapshot(firearm)
    with db_transaction.atomic():
        firearm = Firearm.objects.select_for_update().get(pk=firearm.pk)
        transaction = ArmoryTransaction.objects.create(
            transaction_type=ArmoryTransaction.TYPE_TRANSFER,
            status=ArmoryTransaction.STATUS_CLOSED,
            unit_id=assigned_unit.id,
            unit_name=assigned_unit.name,
            releasing_armorer=actor,
            supporting_documents=supporting_documents or "",
            discrepancy_notes=reason,
            created_by=actor,
        )
        ArmoryTransactionLine.objects.create(
            transaction=transaction,
            firearm=firearm,
            firearm_id_snapshot=firearm.firearm_id,
            butt_number_snapshot=firearm.butt_number,
            serial_number_snapshot=firearm.serial_number,
            firearm_type=firearm.firearm_type,
            manufacturer=firearm.manufacturer,
            model=firearm.model,
            caliber=firearm.caliber,
            condition_status=firearm.condition_status,
            inspection_remarks=reason,
        )
        firearm.assigned_unit = assigned_unit
        firearm.storage_location = storage_location or "Armory"
        firearm.assigned_personnel = None
        firearm.save(
            update_fields=[
                "assigned_unit",
                "storage_location",
                "assigned_personnel",
                "updated_at",
            ]
        )
        _audit(
            action="transfer_firearm",
            entity_type="armory_transaction",
            entity_id=transaction.pk,
            actor=actor,
            before=before,
            after=_firearm_snapshot(firearm),
            reference=transaction.reference_number,
            note=reason,
            request=request,
        )
    return transaction


# ---------------------------------------------------------------------------
# Personnel designation
# ---------------------------------------------------------------------------


def _validate_assignable_firearm(user: User, firearm: Firearm, assignment_type: str):
    if firearm.firearm_type != assignment_type:
        raise ArmoryError(
            f"{firearm.firearm_id} is a {firearm.get_firearm_type_display().lower()}, "
            f"not a {assignment_type}."
        )
    if firearm.custody_status == Firearm.CUSTODY_ISSUED:
        raise ArmoryError(f"{firearm.firearm_id} is currently issued.")
    if firearm.condition_status in {
        Firearm.CONDITION_UNSERVICEABLE,
        Firearm.CONDITION_CONDEMNED,
    }:
        raise ArmoryError(
            f"{firearm.firearm_id} is {firearm.get_condition_status_display().lower()} "
            "and cannot be designated."
        )
    if user.unit_id is None:
        raise ArmoryError("The selected person has no unit.")
    if firearm.assigned_unit_id != user.unit_id:
        raise ArmoryError(
            f"{firearm.firearm_id} belongs to another unit. A person may only be "
            "designated a firearm from their own armory."
        )


def assign_designated_firearms(
    *,
    user: User,
    rifle_firearm_id: str | None = None,
    pistol_firearm_id: str | None = None,
    notes: str = "",
    sync_uuid=None,
    actor,
    request=None,
) -> list[PersonnelFirearmAssignment]:
    """Assign or replace a person's designated rifle and pistol.

    Existing active assignments of the same type are closed first, preserving
    their history. A firearm already actively assigned to somebody else is
    refused by a database constraint as well as this check.
    """
    if user is None or not getattr(user, "is_active", True):
        raise ArmoryError("Choose an active personnel record.")
    _require_unit_in_scope(actor, user.unit_id)
    requested: dict[str, str] = {}
    if rifle_firearm_id not in (None, ""):
        requested[Firearm.TYPE_RIFLE] = str(rifle_firearm_id).strip()
    if pistol_firearm_id not in (None, ""):
        requested[Firearm.TYPE_PISTOL] = str(pistol_firearm_id).strip()
    if not requested:
        raise ArmoryError("Choose at least one designated firearm.")

    result: list[PersonnelFirearmAssignment] = []
    with db_transaction.atomic():
        locked_user = User.objects.get(pk=user.pk)
        for assignment_type, identifier in requested.items():
            firearm = (
                Firearm.objects.select_for_update()
                .select_related("assigned_unit")
                .filter(firearm_id__iexact=identifier)
                .first()
            )
            if firearm is None:
                raise ArmoryError(f"No firearm is registered as {identifier}.")
            _validate_assignable_firearm(locked_user, firearm, assignment_type)

            current = (
                PersonnelFirearmAssignment.objects.select_for_update()
                .filter(user_id=locked_user.id, assignment_type=assignment_type, is_active=True)
                .first()
            )
            if current is not None and current.firearm_id == firearm.id:
                result.append(current)
                continue
            if current is not None:
                current.is_active = False
                current.released_at = timeutils.now()
                current.released_by = actor
                current.save(
                    update_fields=["is_active", "released_at", "released_by", "updated_at"]
                )
                old_firearm = current.firearm
                if old_firearm.assigned_personnel_id == locked_user.id:
                    old_firearm.assigned_personnel = None
                    old_firearm.save(update_fields=["assigned_personnel", "updated_at"])
                _audit(
                    action="release_firearm_assignment",
                    entity_type="armory_assignment",
                    entity_id=current.pk,
                    actor=actor,
                    before=snapshot(
                        current,
                        ["user_id", "firearm_id", "assignment_type", "is_active"],
                    ),
                    after={"is_active": False},
                    reference=old_firearm.firearm_id,
                    note=f"Reassigned from {old_firearm.firearm_id}.",
                    request=request,
                )

            if PersonnelFirearmAssignment.objects.filter(
                firearm=firearm, is_active=True
            ).exclude(user_id=locked_user.id).exists():
                raise ArmoryError(
                    f"{firearm.firearm_id} is already designated to another person."
                )
            assignment = PersonnelFirearmAssignment.objects.create(
                user=locked_user,
                firearm=firearm,
                assignment_type=assignment_type,
                sync_uuid=sync_uuid,
                assigned_by=actor,
                notes=str(notes or "").strip(),
            )
            firearm.assigned_personnel = locked_user
            firearm.save(update_fields=["assigned_personnel", "updated_at"])
            _audit(
                action="assign_firearm",
                entity_type="armory_assignment",
                entity_id=assignment.pk,
                actor=actor,
                before={},
                after={
                    "user_id": locked_user.id,
                    "personnel": _actor_name(locked_user),
                    "firearm_id": firearm.firearm_id,
                    "butt_number": firearm.butt_number,
                    "assignment_type": assignment_type,
                },
                reference=firearm.firearm_id,
                note=str(notes or "").strip(),
                request=request,
            )
            result.append(assignment)
    return result


def release_assignment(
    *, assignment: PersonnelFirearmAssignment, actor, reason: str = "", request=None
):
    if not assignment.is_active:
        raise ArmoryError("That assignment is already closed.")
    with db_transaction.atomic():
        assignment = (
            PersonnelFirearmAssignment.objects.select_for_update()
            .select_related("firearm", "user")
            .get(pk=assignment.pk)
        )
        if assignment.firearm.custody_status == Firearm.CUSTODY_ISSUED:
            raise ArmoryError("The firearm is issued and cannot be released from its holder.")
        before = {
            "user_id": assignment.user_id,
            "firearm_id": assignment.firearm.firearm_id,
            "assignment_type": assignment.assignment_type,
            "is_active": True,
        }
        assignment.is_active = False
        assignment.released_at = timeutils.now()
        assignment.released_by = actor
        assignment.save(
            update_fields=["is_active", "released_at", "released_by", "updated_at"]
        )
        firearm = assignment.firearm
        if firearm.assigned_personnel_id == assignment.user_id:
            firearm.assigned_personnel = None
            firearm.save(update_fields=["assigned_personnel", "updated_at"])
        _audit(
            action="release_firearm_assignment",
            entity_type="armory_assignment",
            entity_id=assignment.pk,
            actor=actor,
            before=before,
            after={"is_active": False},
            reference=firearm.firearm_id,
            note=str(reason or "").strip(),
            request=request,
        )
    return assignment


# ---------------------------------------------------------------------------
# Issuance
# ---------------------------------------------------------------------------


def _validate_acknowledgement(user: User, acknowledged: bool, name: str):
    if not acknowledged:
        raise ArmoryError("The receiving person must acknowledge receipt before release.")
    expected = {_normalise_name(user.get_full_name()), _normalise_name(user.username)}
    supplied = _normalise_name(name)
    if supplied not in expected:
        raise ArmoryError(
            "The acknowledgement name must match the receiving person's full name."
        )


def _ammunition_scope_for_issue(user, ammo_id) -> Ammunition:
    ammunition = scoped_ammunition(user).filter(pk=ammo_id).first()
    if ammunition is None:
        raise ArmoryError("One of the ammunition lots is outside your armory scope.")
    if ammunition.serviceability != Ammunition.CONDITION_SERVICEABLE:
        raise ArmoryError(
            f"{ammunition.ammunition_id} is {ammunition.get_serviceability_display().lower()} "
            "and cannot be issued."
        )
    if ammunition.expiration_date and ammunition.expiration_date < timeutils.today():
        raise ArmoryError(
            f"{ammunition.ammunition_id} expired on {ammunition.expiration_date}."
        )
    return ammunition


def issue_firearms(
    *,
    mission: Mission | None,
    personnel: User,
    firearms: list[dict],
    ammunition: list[dict] | None,
    identity_verified: bool,
    acknowledged: bool,
    acknowledgement_name: str,
    purpose: str = "",
    inspection_remarks: str = "",
    supporting_documents: str = "",
    sync_uuid=None,
    actor,
    request=None,
) -> ArmoryTransaction:
    """Issue designated firearms and ammunition atomically.

    A Mission Order is optional. When one is linked the existing order and
    mission-personnel checks still apply; without one this is a standalone
    armory withdrawal, scoped to the receiving person's unit.
    """
    if not identity_verified:
        raise ArmoryError("Verify the person's identity before releasing any firearm.")
    _validate_acknowledgement(personnel, acknowledged, acknowledgement_name)
    if mission is not None:
        error = mission_order_error(mission)
        if error:
            raise ArmoryError(error)
        _require_unit_in_scope(actor, mission.unit_id)
        personnel_row = mission_personnel(mission, personnel.id)
        if personnel_row is None:
            raise ArmoryError(
                "This person is not included in the approved Mission Order. Withdrawal is refused."
            )
    else:
        _require_unit_in_scope(actor, personnel.unit_id)
    designated = list(
        PersonnelFirearmAssignment.objects.select_related("firearm")
        .filter(user_id=personnel.id, is_active=True)
        .order_by("assignment_type")
    )
    if not designated:
        raise ArmoryError("This person has no designated firearms.")
    by_id = {row.firearm.firearm_id: row for row in designated}
    by_butt = {
        (row.firearm.firearm_type, row.firearm.butt_number): row
        for row in designated
    }

    submitted = list(firearms or [])
    if not submitted:
        raise ArmoryError("Present at least one designated firearm for issue.")
    selected: list[tuple[PersonnelFirearmAssignment, dict]] = []
    seen_ids: set[str] = set()
    for item in submitted:
        identifier = str(item.get("firearm_id") or "").strip()
        butt = normalise_identifier(item.get("butt_number"))
        if not identifier or not butt:
            raise ArmoryError(
                "Every firearm must be verified with BOTH its Firearm ID and Butt Number."
            )
        assignment = by_id.get(identifier)
        if assignment is None or by_butt.get(
            (assignment.firearm.firearm_type, butt)
        ) is not assignment:
            raise ArmoryError(
                f"{identifier} / {butt} does not match one of this person's "
                "designated firearms."
            )
        if identifier in seen_ids:
            raise ArmoryError(f"{identifier} is listed more than once.")
        seen_ids.add(identifier)
        selected.append((assignment, item))
    if len(selected) != len(designated):
        missing = [
            row.firearm.firearm_id
            for row in designated
            if row.firearm.firearm_id not in seen_ids
        ]
        raise ArmoryError(
            "All designated firearms must be accounted for at issue. Missing: "
            + ", ".join(missing)
        )

    ammo_quantities: dict[int, int] = defaultdict(int)
    for item in ammunition or []:
        try:
            ammo_id = int(item.get("ammunition"))
            quantity = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            raise ArmoryError("Ammunition quantities must be whole numbers.")
        if quantity < 0:
            raise ArmoryError("Ammunition quantities cannot be negative.")
        if quantity:
            ammo_quantities[ammo_id] += quantity

    now = timeutils.now()
    with db_transaction.atomic():
        if mission is not None:
            mission = Mission.objects.select_for_update().get(pk=mission.pk)
            error = mission_order_error(mission)
            if error:
                raise ArmoryError(error)
            if not MissionPersonnel.objects.filter(
                mission=mission, user_id=personnel.id, is_active=True
            ).exists():
                raise ArmoryError(
                    "This person is no longer included in the Mission Order."
                )
            open_issue = ArmoryTransaction.objects.filter(
                mission=mission,
                personnel_id=personnel.id,
                transaction_type=ArmoryTransaction.TYPE_ISSUANCE,
                status=ArmoryTransaction.STATUS_OPEN,
            )
            open_issue_error = (
                "This person already has an open issue for this mission. "
                "Return it first."
            )
        else:
            open_issue = ArmoryTransaction.objects.filter(
                personnel_id=personnel.id,
                transaction_type=ArmoryTransaction.TYPE_ISSUANCE,
                status=ArmoryTransaction.STATUS_OPEN,
            )
            open_issue_error = (
                "This person already has an open armory issue. Return it first."
            )
        if open_issue.exists():
            raise ArmoryError(open_issue_error)

        # Lock and re-check every firearm against live state.
        locked_firearms: dict[str, Firearm] = {}
        for assignment, item in selected:
            firearm = (
                Firearm.objects.select_for_update()
                .select_related("assigned_unit")
                .get(pk=assignment.firearm_id)
            )
            if firearm.assigned_personnel_id != personnel.id:
                raise ArmoryError(
                    f"{firearm.firearm_id} is not currently designated to this person."
                )
            if firearm.custody_status != Firearm.CUSTODY_STORED:
                raise ArmoryError(
                    f"{firearm.firearm_id} is not stored; it cannot be issued."
                )
            if firearm.condition_status != Firearm.CONDITION_SERVICEABLE:
                raise ArmoryError(
                    f"{firearm.firearm_id} is "
                    f"{firearm.get_condition_status_display().lower()}."
                )
            inspection_status = item.get("inspection_status") or Firearm.CONDITION_SERVICEABLE
            if inspection_status != Firearm.CONDITION_SERVICEABLE:
                raise ArmoryError(
                    f"{firearm.firearm_id} did not pass the physical inspection."
                )
            locked_firearms[firearm.firearm_id] = firearm

        ammo_rows: dict[int, Ammunition] = {}
        for ammo_id, quantity in ammo_quantities.items():
            ammunition_row = (
                Ammunition.objects.select_for_update()
                .select_related("owning_unit")
                .filter(pk=ammo_id)
                .first()
            )
            if ammunition_row is None:
                raise ArmoryError("One of the ammunition lots no longer exists.")
            if ammunition_row.owning_unit_id not in user_unit_ids(actor):
                raise ArmoryError("One of the ammunition lots is outside your armory scope.")
            if ammunition_row.serviceability != Ammunition.CONDITION_SERVICEABLE:
                raise ArmoryError(
                    f"{ammunition_row.ammunition_id} is not serviceable."
                )
            if ammunition_row.expiration_date and ammunition_row.expiration_date < timeutils.today():
                raise ArmoryError(
                    f"{ammunition_row.ammunition_id} has expired."
                )
            if quantity > ammunition_row.stock_balance:
                raise ArmoryError(
                    f"Only {ammunition_row.stock_balance} {ammunition_row.unit_of_measure} "
                    f"of {ammunition_row.nomenclature} are available."
                )
            ammo_rows[ammo_id] = ammunition_row

        # The transaction belongs to the armory that released the weapons, not
        # the person's home unit. A multi-unit Mission Order must remain visible
        # to the releasing armorer after the issue has been committed.
        unit = (mission.unit if mission is not None else None) or personnel.unit
        transaction = ArmoryTransaction.objects.create(
            transaction_type=ArmoryTransaction.TYPE_ISSUANCE,
            status=ArmoryTransaction.STATUS_OPEN,
            sync_uuid=sync_uuid,
            mission=mission,
            mission_order_reference=mission.mission_number if mission else "",
            purpose=str(purpose or "").strip(),
            personnel=personnel,
            rank=personnel.rank or "",
            personnel_name=personnel.get_full_name(),
            branch_of_service=personnel.branch_of_service or "",
            unit=unit,
            unit_name=unit.name if unit is not None else "",
            releasing_armorer=actor,
            receiving_personnel=personnel,
            approving_authority=(
                mission_approving_authority(mission) if mission is not None else None
            ),
            inspection_remarks=str(inspection_remarks or "").strip(),
            supporting_documents=str(supporting_documents or "").strip(),
            acknowledgement_name=str(acknowledgement_name or "").strip(),
            acknowledged_at=now,
            created_by=actor,
        )

        for assignment, item in selected:
            firearm = locked_firearms[assignment.firearm.firearm_id]
            ArmoryTransactionLine.objects.create(
                transaction=transaction,
                firearm=firearm,
                firearm_id_snapshot=firearm.firearm_id,
                butt_number_snapshot=firearm.butt_number,
                serial_number_snapshot=firearm.serial_number,
                firearm_type=firearm.firearm_type,
                manufacturer=firearm.manufacturer,
                model=firearm.model,
                caliber=firearm.caliber,
                condition_status=Firearm.CONDITION_SERVICEABLE,
                inspection_remarks=str(item.get("inspection_remarks") or "").strip(),
            )
            firearm.custody_status = Firearm.CUSTODY_ISSUED
            firearm.date_last_inspected = now.date()
            firearm.save(
                update_fields=["custody_status", "date_last_inspected", "updated_at"]
            )
            FirearmInspection.objects.create(
                firearm=firearm,
                inspected_by=actor,
                inspected_at=now,
                condition_status=Firearm.CONDITION_SERVICEABLE,
                remarks=str(item.get("inspection_remarks") or "").strip(),
                transaction=transaction,
            )
            _audit(
                action="issue_firearm",
                entity_type="armory_transaction",
                entity_id=transaction.pk,
                actor=actor,
                before={"custody_status": Firearm.CUSTODY_STORED},
                after={
                    "custody_status": Firearm.CUSTODY_ISSUED,
                    "firearm_id": firearm.firearm_id,
                    "butt_number": firearm.butt_number,
                    "personnel_id": personnel.id,
                },
                reference=transaction.reference_number,
                note=mission.mission_number if mission else "No Mission Order linked",
                request=request,
            )

        for ammo_id, quantity in ammo_quantities.items():
            ammunition_row = ammo_rows[ammo_id]
            ArmoryTransactionLine.objects.create(
                transaction=transaction,
                ammunition=ammunition_row,
                ammunition_name=ammunition_row.nomenclature,
                ammunition_caliber=ammunition_row.caliber,
                ammunition_type=ammunition_row.ammunition_type,
                lot_number=ammunition_row.lot_number,
                unit_of_measure=ammunition_row.unit_of_measure,
                quantity=quantity,
                condition_status=ammunition_row.serviceability,
            )
            ammunition_row.quantity_issued = F("quantity_issued") + quantity
            ammunition_row.stock_balance = F("stock_balance") - quantity
            ammunition_row.save(
                update_fields=["quantity_issued", "stock_balance", "updated_at"]
            )

        _audit(
            action="issue_firearms",
            entity_type="armory_transaction",
            entity_id=transaction.pk,
            actor=actor,
            before={},
            after=snapshot(
                transaction,
                [
                    "reference_number",
                    "mission_id",
                    "personnel_id",
                    "unit_id",
                    "status",
                    "acknowledgement_name",
                ],
            ),
            reference=transaction.reference_number,
            note=(
                f"Mission Order {mission.mission_number}"
                if mission is not None
                else "No Mission Order linked"
            ),
            request=request,
        )
    return transaction


# ---------------------------------------------------------------------------
# Return / deposit
# ---------------------------------------------------------------------------


def return_firearms(
    *,
    issuance: ArmoryTransaction,
    firearms: list[dict],
    ammunition: list[dict] | None,
    identity_verified: bool,
    acknowledged: bool,
    acknowledgement_name: str,
    inspection_remarks: str = "",
    discrepancy_notes: str = "",
    supporting_documents: str = "",
    sync_uuid=None,
    actor,
    request=None,
) -> ArmoryTransaction:
    if not identity_verified:
        raise ArmoryError("Verify the person's identity before accepting the return.")
    if not acknowledged:
        raise ArmoryError("The returning person must acknowledge the deposit.")
    if issuance.transaction_type != ArmoryTransaction.TYPE_ISSUANCE:
        raise ArmoryError("Returns must be linked to an original issuance.")
    if issuance.status != ArmoryTransaction.STATUS_OPEN:
        raise ArmoryError("That issuance is already closed.")
    if issuance.personnel_id is None:
        raise ArmoryError("The issuance has no receiving personnel.")
    expected_name = {_normalise_name(issuance.personnel_name), _normalise_name(issuance.personnel.username)}
    if _normalise_name(acknowledgement_name) not in expected_name:
        raise ArmoryError("The acknowledgement name must match the returning person.")
    _require_unit_in_scope(actor, issuance.unit_id)

    original_firearm_lines = list(issuance.lines.filter(firearm__isnull=False))
    submitted = list(firearms or [])
    by_snapshot = {
        (line.firearm_id_snapshot, line.butt_number_snapshot): line
        for line in original_firearm_lines
    }
    selected: dict[int, dict] = {}
    for item in submitted:
        identifier = str(item.get("firearm_id") or "").strip()
        butt = normalise_identifier(item.get("butt_number"))
        line = by_snapshot.get((identifier, butt))
        if line is None:
            raise ArmoryError(
                f"{identifier} / {butt} does not match the original issuance."
            )
        if line.id in selected:
            raise ArmoryError(f"{identifier} is listed more than once.")
        selected[line.id] = item
    missing = [line for line in original_firearm_lines if line.id not in selected]
    if missing:
        raise ArmoryError(
            "All firearms on the original issuance must be presented for return. Missing: "
            + ", ".join(line.firearm_id_snapshot for line in missing)
        )

    original_ammo_lines = {
        line.id: line for line in issuance.lines.filter(ammunition__isnull=False)
    }
    returned_ammo: dict[int, dict] = {}
    for item in ammunition or []:
        try:
            line_id = int(item.get("line"))
            quantity = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            raise ArmoryError("Ammunition return quantities must be whole numbers.")
        line = original_ammo_lines.get(line_id)
        if line is None:
            raise ArmoryError("An ammunition return line does not belong to this issuance.")
        if quantity < 0 or quantity > line.quantity:
            raise ArmoryError(
                f"Returned quantity for {line.ammunition_name} must be between 0 and "
                f"{line.quantity} {line.unit_of_measure}."
            )
        returned_ammo[line_id] = {**item, "quantity": quantity}

    now = timeutils.now()
    with db_transaction.atomic():
        # Lock only the transaction row. `mission`, `personnel` and `unit` are
        # nullable, so PostgreSQL refuses FOR UPDATE on the outer-joined side;
        # `of=("self",)` keeps the row lock and leaves the joins unlocked.
        issuance = (
            ArmoryTransaction.objects.select_for_update(of=("self",))
            .select_related("mission", "personnel", "unit")
            .get(pk=issuance.pk)
        )
        if issuance.status != ArmoryTransaction.STATUS_OPEN:
            raise ArmoryError("That issuance is already closed.")
        return_transaction = ArmoryTransaction.objects.create(
            transaction_type=ArmoryTransaction.TYPE_RETURN,
            status=ArmoryTransaction.STATUS_CLOSED,
            sync_uuid=sync_uuid,
            mission=issuance.mission,
            mission_order_reference=issuance.mission_order_reference,
            personnel=issuance.personnel,
            rank=issuance.rank,
            personnel_name=issuance.personnel_name,
            branch_of_service=issuance.branch_of_service,
            unit=issuance.unit,
            unit_name=issuance.unit_name,
            releasing_armorer=actor,
            receiving_personnel=issuance.receiving_personnel,
            approving_authority=issuance.approving_authority,
            inspection_remarks=str(inspection_remarks or "").strip(),
            supporting_documents=str(supporting_documents or "").strip(),
            discrepancy_notes=str(discrepancy_notes or "").strip(),
            acknowledgement_name=str(acknowledgement_name or "").strip(),
            acknowledged_at=now,
            return_of=issuance,
            returned_at=now,
            created_by=actor,
        )

        for line in original_firearm_lines:
            item = selected[line.id]
            firearm = (
                Firearm.objects.select_for_update()
                .select_related("assigned_unit")
                .filter(pk=line.firearm_id)
                .first()
            )
            if firearm is None:
                raise ArmoryError(
                    f"{line.firearm_id_snapshot} is no longer in the firearm register."
                )
            if firearm.custody_status != Firearm.CUSTODY_ISSUED:
                raise ArmoryError(
                    f"{firearm.firearm_id} is not in the issued state; return cannot be recorded."
                )
            condition = str(
                item.get("condition_status") or Firearm.CONDITION_SERVICEABLE
            )
            if condition not in {key for key, _label in Firearm.CONDITION_CHOICES}:
                raise ArmoryError("Choose a valid return inspection condition.")
            remarks = str(item.get("inspection_remarks") or "").strip()
            ArmoryTransactionLine.objects.create(
                transaction=return_transaction,
                firearm=firearm,
                original_line=line,
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
            before = _firearm_snapshot(firearm)
            firearm.custody_status = Firearm.CUSTODY_STORED
            firearm.condition_status = condition
            firearm.date_last_inspected = now.date()
            firearm.save(
                update_fields=[
                    "custody_status",
                    "condition_status",
                    "date_last_inspected",
                    "updated_at",
                ]
            )
            FirearmInspection.objects.create(
                firearm=firearm,
                inspected_by=actor,
                inspected_at=now,
                condition_status=condition,
                remarks=remarks,
                transaction=return_transaction,
            )
            _audit(
                action="return_firearm",
                entity_type="armory_transaction",
                entity_id=return_transaction.pk,
                actor=actor,
                before=before,
                after=_firearm_snapshot(firearm),
                reference=return_transaction.reference_number,
                note=remarks,
                request=request,
            )

        for line in original_ammo_lines.values():
            item = returned_ammo.get(line.id, {"quantity": 0})
            quantity = int(item["quantity"])
            ammunition_row = (
                Ammunition.objects.select_for_update().filter(pk=line.ammunition_id).first()
            )
            if ammunition_row is None:
                raise ArmoryError(
                    f"Ammunition {line.ammunition_name} is no longer in the register."
                )
            if quantity != line.quantity:
                consumed = line.quantity - quantity
                discrepancy = (
                    f"{line.ammunition_name} lot {line.lot_number}: issued "
                    f"{line.quantity}, returned {quantity}, not returned {consumed}."
                )
                return_transaction.discrepancy_notes = (
                    (return_transaction.discrepancy_notes + " " + discrepancy).strip()
                )
            ArmoryTransactionLine.objects.create(
                transaction=return_transaction,
                ammunition=ammunition_row,
                original_line=line,
                ammunition_name=line.ammunition_name,
                ammunition_caliber=line.ammunition_caliber,
                ammunition_type=line.ammunition_type,
                lot_number=line.lot_number,
                unit_of_measure=line.unit_of_measure,
                quantity=quantity,
                condition_status=str(
                    item.get("serviceability") or ammunition_row.serviceability
                ),
                inspection_remarks=str(item.get("discrepancy_note") or "").strip(),
            )
            # The whole issue leaves the outstanding balance; only the rounds
            # physically returned go back on the shelf. The difference is the
            # documented consumption/discrepancy.
            ammunition_row.quantity_issued = F("quantity_issued") - line.quantity
            ammunition_row.stock_balance = F("stock_balance") + quantity
            ammunition_row.save(
                update_fields=["quantity_issued", "stock_balance", "updated_at"]
            )

        return_transaction.save(update_fields=["discrepancy_notes"])
        issuance.status = ArmoryTransaction.STATUS_CLOSED
        issuance.returned_at = now
        issuance.save(update_fields=["status", "returned_at", "updated_at"])
        _audit(
            action="return_firearms",
            entity_type="armory_transaction",
            entity_id=return_transaction.pk,
            actor=actor,
            before={"issuance": issuance.reference_number, "status": "open"},
            after=snapshot(
                return_transaction,
                [
                    "reference_number",
                    "mission_id",
                    "personnel_id",
                    "status",
                    "discrepancy_notes",
                    "acknowledgement_name",
                ],
            ),
            reference=return_transaction.reference_number,
            note=str(discrepancy_notes or "").strip(),
            request=request,
        )
    return return_transaction


# ---------------------------------------------------------------------------
# Ammunition stock adjustment
# ---------------------------------------------------------------------------


def adjust_ammunition_stock(
    *,
    ammunition: Ammunition,
    new_balance: int,
    reason: str,
    sync_uuid=None,
    actor,
    supporting_documents: str = "",
    request=None,
) -> ArmoryTransaction:
    reason = str(reason or "").strip()
    if not reason:
        raise ArmoryError("Give the reason for adjusting ammunition stock.")
    try:
        new_balance = int(new_balance)
    except (TypeError, ValueError):
        raise ArmoryError("Enter a whole-number stock balance.")
    if new_balance < 0:
        raise ArmoryError("Stock balance cannot be negative.")
    if new_balance == ammunition.stock_balance:
        raise ArmoryError("The stock balance is unchanged.")
    _require_unit_in_scope(actor, ammunition.owning_unit_id)
    delta = new_balance - ammunition.stock_balance
    with db_transaction.atomic():
        ammunition = Ammunition.objects.select_for_update().get(pk=ammunition.pk)
        before = {
            "stock_balance": ammunition.stock_balance,
            "quantity_received": ammunition.quantity_received,
            "quantity_issued": ammunition.quantity_issued,
        }
        if delta > 0:
            ammunition.quantity_received += delta
        ammunition.stock_balance = new_balance
        ammunition.save(
            update_fields=[
                "quantity_received",
                "stock_balance",
                "updated_at",
            ]
        )
        transaction = ArmoryTransaction.objects.create(
            transaction_type=ArmoryTransaction.TYPE_ADJUSTMENT,
            status=ArmoryTransaction.STATUS_CLOSED,
            sync_uuid=sync_uuid,
            unit=ammunition.owning_unit,
            unit_name=ammunition.owning_unit.name,
            releasing_armorer=actor,
            supporting_documents=str(supporting_documents or "").strip(),
            discrepancy_notes=reason,
            created_by=actor,
        )
        ArmoryTransactionLine.objects.create(
            transaction=transaction,
            ammunition=ammunition,
            ammunition_name=ammunition.nomenclature,
            ammunition_caliber=ammunition.caliber,
            ammunition_type=ammunition.ammunition_type,
            lot_number=ammunition.lot_number,
            unit_of_measure=ammunition.unit_of_measure,
            quantity=abs(delta),
            condition_status=ammunition.serviceability,
            inspection_remarks=reason,
        )
        _audit(
            action="adjust_ammunition",
            entity_type="armory_transaction",
            entity_id=transaction.pk,
            actor=actor,
            before=before,
            after={
                "stock_balance": ammunition.stock_balance,
                "quantity_received": ammunition.quantity_received,
                "quantity_issued": ammunition.quantity_issued,
            },
            reference=transaction.reference_number,
            note=reason,
            request=request,
        )
    return transaction


# ---------------------------------------------------------------------------
# Dashboard and reporting helpers
# ---------------------------------------------------------------------------


def dashboard(user) -> dict:
    firearms = scoped_firearms(user)
    ammunition = scoped_ammunition(user)
    transactions = scoped_transactions(user)
    today = timeutils.today()
    due_cutoff = today - dt.timedelta(days=INSPECTION_DUE_DAYS)

    total = firearms.count()
    rifles = firearms.filter(firearm_type=Firearm.TYPE_RIFLE).count()
    pistols = firearms.filter(firearm_type=Firearm.TYPE_PISTOL).count()
    stored = firearms.filter(custody_status=Firearm.CUSTODY_STORED).count()
    issued = firearms.filter(custody_status=Firearm.CUSTODY_ISSUED).count()
    unserviceable = firearms.exclude(
        condition_status=Firearm.CONDITION_SERVICEABLE
    ).count()
    pending_inspections = firearms.exclude(
        custody_status=Firearm.CUSTODY_RETIRED
    ).filter(
        Q(date_last_inspected__isnull=True) | Q(date_last_inspected__lte=due_cutoff)
    ).count()
    pending_returns = transactions.filter(
        transaction_type=ArmoryTransaction.TYPE_ISSUANCE,
        status=ArmoryTransaction.STATUS_OPEN,
    ).count()
    ammo_total = ammunition.aggregate(total=Sum("stock_balance"))["total"] or 0
    ammo_lots = ammunition.count()

    by_type = [
        {
            "type": key,
            "label": label,
            "count": firearms.filter(firearm_type=key).count(),
        }
        for key, label in Firearm.TYPE_CHOICES
    ]
    by_custody = [
        {
            "status": key,
            "label": label,
            "count": firearms.filter(custody_status=key).count(),
        }
        for key, label in Firearm.CUSTODY_CHOICES
    ]
    by_condition = [
        {
            "status": key,
            "label": label,
            "count": firearms.filter(condition_status=key).count(),
        }
        for key, label in Firearm.CONDITION_CHOICES
    ]
    ammo_by_caliber = list(
        ammunition.values("caliber")
        .annotate(stock=Sum("stock_balance"))
        .order_by("-stock", "caliber")
    )
    for row in ammo_by_caliber:
        row["lots"] = ammunition.filter(caliber=row["caliber"]).count()
    since = today - dt.timedelta(days=30)
    tx_by_type = [
        {
            "type": key,
            "label": label,
            "count": transactions.filter(
                transaction_type=key, transaction_at__date__gte=since
            ).count(),
        }
        for key, label in ArmoryTransaction.TYPE_CHOICES
    ]
    recent = transactions.select_related(
        "mission", "personnel", "unit", "releasing_armorer"
    ).prefetch_related("lines")[:12]

    return {
        "total_firearms": total,
        "total_rifles": rifles,
        "total_pistols": pistols,
        "stored_firearms": stored,
        "issued_firearms": issued,
        "unserviceable_firearms": unserviceable,
        "pending_returns": pending_returns,
        "pending_inspections": pending_inspections,
        "inspection_due_days": INSPECTION_DUE_DAYS,
        "total_ammunition_stock": ammo_total,
        "ammunition_lots": ammo_lots,
        "transactions_30d": transactions.filter(
            transaction_at__date__gte=since
        ).count(),
        "by_type": by_type,
        "by_custody": by_custody,
        "by_condition": by_condition,
        "ammunition_by_caliber": ammo_by_caliber,
        "transactions_by_type": tx_by_type,
        "recent_transactions": recent,
    }


def ammo_consumption(issuance_lines):
    """Return issued/returned/consumed totals for reconciliation reports."""
    totals = {"issued": 0, "returned": 0, "consumed": 0}
    for line in issuance_lines:
        totals["issued"] += int(line.quantity or 0)
        returned = sum(
            int(row.quantity or 0)
            for row in line.return_lines.filter(transaction__transaction_type="return")
        )
        totals["returned"] += returned
        totals["consumed"] += max(0, int(line.quantity or 0) - returned)
    return totals

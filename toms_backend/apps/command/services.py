"""Domain services for command structure, delegations, sections and instructions.

Validation lives here (not only in serializers) so every entry point - API,
management command, test - applies the same organisational rules.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.roles import (
    APPOINTMENT_ROLE_VALUES,
    DETACHED_SERVICE_ROLES,
    EXCLUSIVE_APPOINTMENT_ROLES,
    OFFICE_ROLE_VALUES,
    ROLE_ADMINISTRATOR,
    ROLE_COMMANDER,
    ROLE_DEPUTY_COMMANDER,
    ROLE_LABELS,
)


class CommandError(Exception):
    """A rule violation the caller should surface as HTTP 400."""


def _person_label(user) -> str:
    """Name and serial number: the serial is what makes a person unique."""
    name = getattr(user, "full_name", "") or getattr(user, "username", "") or "This person"
    serial = getattr(user, "serial_number", "") or ""
    return f"{name} (serial {serial})" if serial else name


# ---------------------------------------------------------------------------
# Unit hierarchy helpers
# ---------------------------------------------------------------------------


def ancestor_ids(unit_id) -> list[int]:
    """``[unit, parent, grandparent, ...]`` for post-scoping lookups."""
    from apps.legacy.models import Unit

    chain = dict(Unit.objects.values_list("id", "parent_id"))
    out: list[int] = []
    current = unit_id
    seen: set[int] = set()
    while current is not None and current not in seen:
        seen.add(current)
        out.append(int(current))
        current = chain.get(current)
    return out


def descendant_ids(unit_id) -> set[int]:
    from apps.legacy.models import Unit

    units = list(Unit.objects.filter(is_active=True).values_list("id", "parent_id"))
    children: dict[int | None, list[int]] = {}
    for child_id, parent_id in units:
        children.setdefault(parent_id, []).append(child_id)

    result: set[int] = set()
    stack = [unit_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------


def users_holding_post(unit_id, *roles):
    """Users holding any of ``roles`` for ``unit_id``.

    An appointment at the parent command reaches subordinate units, which is
    exactly how the spec describes command oversight. The Operations Center is
    an *office*, so its members are collected separately and only for the unit
    the office belongs to.
    """
    from apps.accounts.models import User
    from apps.command.models import OfficeSection, SectionAssignment, UnitAppointment
    from apps.core.roles import ROLE_OPERATIONS_CENTER

    if unit_id is None or not roles:
        return User.objects.none()

    people = set(
        UnitAppointment.objects.filter(
            is_active=True, role__in=list(roles), unit_id__in=ancestor_ids(unit_id)
        ).values_list("user_id", flat=True)
    )

    if ROLE_OPERATIONS_CENTER in roles:
        people.update(
            SectionAssignment.objects.filter(
                is_active=True,
                section__is_active=True,
                section__unit_id=unit_id,
                section__function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
            ).values_list("user_id", flat=True)
        )

    return User.objects.filter(id__in=list(people), is_active=True)


def administrators():
    from apps.accounts.models import User

    return User.objects.filter(is_active=True, authority="Administrator")


def responsible_users(unit_id, *roles, fallback_to_admin: bool = True):
    """Post holders for a unit, falling back to administrators when empty."""
    users = list(users_holding_post(unit_id, *roles))
    if not users and fallback_to_admin:
        users = list(administrators())
    return users


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------


@transaction.atomic
def create_appointment(*, unit, user, role, actor=None, notes=""):
    from apps.command.models import UnitAppointment

    if unit is None:
        raise CommandError("An appointment must belong to a unit.")
    if user is None:
        raise CommandError("An appointment must name a person.")
    if not user.is_active:
        raise CommandError("Inactive personnel cannot hold an appointment.")
    if role not in APPOINTMENT_ROLE_VALUES:
        if role in OFFICE_ROLE_VALUES:
            raise CommandError(
                f"{ROLE_LABELS[role]} is an office, not a post. Create that office for the "
                "unit and assign personnel to it under Office/Section management."
            )
        if role in ROLE_LABELS:
            raise CommandError(
                f"{ROLE_LABELS[role]} is an account role, not a unit appointment. "
                "Change it on the account instead."
            )
        raise CommandError(f"Unknown appointment role {role!r}.")

    if role in EXCLUSIVE_APPOINTMENT_ROLES:
        existing = UnitAppointment.objects.filter(
            unit=unit, role=role, is_active=True
        ).exclude(user=user)
        if existing.exists():
            holder = existing.select_related("user").first()
            raise CommandError(
                f"{ROLE_LABELS[role]} is already filled at this unit "
                f"({holder.user.username}). End that appointment first: each unit "
                "may have only one active holder of this post."
            )

    already = UnitAppointment.objects.filter(unit=unit, user=user, role=role, is_active=True)
    if already.exists():
        raise CommandError("This person already holds that appointment at this unit.")

    # -- one person, one instance of a post, across every unit --------------
    # A designation is never duplicated between units: nobody is Commander of
    # two. The serial number identifies the person, so the same name in two
    # units cannot slip through.
    elsewhere = (
        UnitAppointment.objects.filter(user=user, is_active=True)
        .exclude(unit=unit)
        .select_related("unit")
    )
    same_post = elsewhere.filter(role=role).first()
    if same_post is not None:
        raise CommandError(
            f"{_person_label(user)} is already {ROLE_LABELS[role]} of "
            f"{same_post.unit.acronym}. One person holds one {ROLE_LABELS[role]} "
            "appointment at a time - end that appointment before appointing them here."
        )

    held_elsewhere = elsewhere.first()
    if held_elsewhere is not None:
        # A second post is detached service (DS): a non-command post at a higher
        # headquarters, held alongside the person's own unit post.
        if role not in DETACHED_SERVICE_ROLES:
            raise CommandError(
                f"{_person_label(user)} is {ROLE_LABELS[held_elsewhere.role]} of "
                f"{held_elsewhere.unit.acronym}. {ROLE_LABELS[role]} cannot also be "
                f"held at {unit.acronym}: command posts are held only in the "
                "person's own unit. End the existing appointment first."
            )
        home_unit_id = user.unit_id or held_elsewhere.unit_id
        if unit.id not in ancestor_ids(home_unit_id)[1:]:
            raise CommandError(
                f"{_person_label(user)} already holds "
                f"{ROLE_LABELS[held_elsewhere.role]} at "
                f"{held_elsewhere.unit.acronym}. A second post is permitted only as "
                "detached service to a higher headquarters, and "
                f"{unit.acronym} is not above that unit."
            )

    appointment = UnitAppointment.objects.create(
        unit=unit,
        user=user,
        role=role,
        appointed_by=actor,
        notes=notes or "",
    )

    # Filling one of the required posts closes the vacancy the bell has been
    # showing for it.
    from apps.command import vacancies

    vacancies.clear_vacancy(unit, role, actor=actor)
    return appointment


@transaction.atomic
def end_appointment(appointment, *, actor=None, reason=""):
    if not appointment.is_active:
        raise CommandError("This appointment is already ended.")
    from apps.core.audit import log_action

    before = {
        "is_active": True,
        "role": appointment.role,
        "role_label": appointment.get_role_display(),
        "unit_id": appointment.unit_id,
        "user_id": appointment.user_id,
    }
    appointment.is_active = False
    appointment.ended_at = timezone.now()
    appointment.ended_by = actor
    if reason:
        appointment.notes = (appointment.notes + "\n" + reason).strip()
    appointment.save(update_fields=["is_active", "ended_at", "ended_by", "notes", "updated_at"])

    log_action(
        actor=actor,
        action="end_appointment",
        entity_type="appointment",
        entity_id=appointment.pk,
        before=before,
        after={"is_active": False, "reason": reason},
    )

    # A command post that just lost its holder is a vacancy the unit cannot
    # leave unnoticed. Shared posts only notify when the last holder leaves.
    from apps.command import vacancies

    vacancies.notify_if_vacant(appointment.unit, appointment.role, actor=actor)
    return appointment


# ---------------------------------------------------------------------------
# Acting authority
# ---------------------------------------------------------------------------


@transaction.atomic
def create_acting_appointment(
    *, unit, user, acting_role, appointed_by, reason, starts_at, ends_at, capabilities=None
):
    from apps.command.models import ActingAppointment

    if acting_role not in {ROLE_COMMANDER, ROLE_DEPUTY_COMMANDER}:
        raise CommandError("Acting authority can only be delegated for Commander or Deputy.")
    if not reason or not reason.strip():
        raise CommandError("A reason is required to appoint an acting commander.")
    if ends_at <= starts_at:
        raise CommandError("The end of the acting period must be after its start.")
    if not user.is_active:
        raise CommandError("Inactive personnel cannot receive acting authority.")

    # Acting command is exercised inside the person's own unit. Delegating a
    # higher unit's command to somebody organic elsewhere would put one person
    # in command of two units, which the one-post rule exists to prevent.
    home_unit_id = getattr(user, "unit_id", None)
    if home_unit_id and int(home_unit_id) != int(unit.id):
        from apps.legacy.models import Unit

        home = Unit.objects.filter(id=home_unit_id).values_list("acronym", flat=True).first()
        raise CommandError(
            f"{_person_label(user)} belongs to {home or 'another unit'}. Acting command "
            "is exercised inside the person's own unit, so it cannot be delegated for "
            f"{unit.acronym}. End their post there, or reassign them first."
        )

    # Retire windows that have already elapsed so they cannot block a new one.
    ActingAppointment.objects.filter(
        unit=unit, acting_role=acting_role, is_active=True, ends_at__lte=timezone.now()
    ).update(is_active=False, updated_at=timezone.now())

    overlapping = ActingAppointment.objects.filter(
        unit=unit,
        acting_role=acting_role,
        is_active=True,
        revoked_at__isnull=True,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    )
    if overlapping.exists():
        raise CommandError(
            "Another acting appointment already covers this period for the same "
            "post. Conflicting command authority is not permitted."
        )

    return ActingAppointment.objects.create(
        unit=unit,
        user=user,
        acting_role=acting_role,
        appointed_by=appointed_by,
        reason=reason.strip(),
        starts_at=starts_at,
        ends_at=ends_at,
        delegated_capabilities=list(capabilities or []),
    )


@transaction.atomic
def revoke_acting_appointment(appointment, *, actor, reason=""):
    from apps.core.audit import log_action

    if appointment.revoked_at is not None:
        raise CommandError("This acting appointment is already revoked.")
    appointment.is_active = False
    appointment.revoked_at = timezone.now()
    appointment.revoked_by = actor
    appointment.revoked_reason = (reason or "").strip()
    appointment.save(
        update_fields=[
            "is_active",
            "revoked_at",
            "revoked_by",
            "revoked_reason",
            "updated_at",
        ]
    )
    log_action(
        actor=actor,
        action="revoke_acting_appointment",
        entity_type="acting_appointment",
        entity_id=appointment.pk,
        after={"revoked_at": appointment.revoked_at.isoformat(), "reason": reason},
    )
    return appointment


# ---------------------------------------------------------------------------
# Office / section assignment
# ---------------------------------------------------------------------------


@transaction.atomic
def assign_section(*, user, section, assignment_type, actor=None, notes=""):
    """Point a person's primary or secondary assignment at ``section``.

    The previous active row of the same type is ended, never deleted, so the
    assignment history survives.
    """
    from apps.command.models import SectionAssignment

    if section is None:
        raise CommandError("Select an office or section.")
    if not section.is_active:
        raise CommandError("That office/section is inactive.")
    if user is None:
        raise CommandError("Select a person.")
    if user.unit_id is None:
        raise CommandError("This person has no assigned unit; set a unit first.")
    if section.unit_id != user.unit_id:
        raise CommandError(
            "The office/section belongs to a different unit than the person. "
            "Assignments must stay inside the person's own unit."
        )

    # The same section may not be held twice by one person - not even once as
    # primary and once as secondary. The assignment *type* is not part of this
    # check on purpose (spec §5.7).
    duplicate = SectionAssignment.objects.filter(
        user_id=user.id, section=section, is_active=True
    ).first()
    if duplicate:
        raise CommandError("This section is already assigned to that person.")

    SectionAssignment.objects.filter(
        user_id=user.id, assignment_type=assignment_type, is_active=True
    ).update(is_active=False, ended_at=timezone.now(), ended_by=actor)

    return SectionAssignment.objects.create(
        user=user,
        section=section,
        assignment_type=assignment_type,
        assigned_by=actor,
        notes=notes or "",
    )


@transaction.atomic
def end_section_assignment(assignment, *, actor=None):
    if not assignment.is_active:
        raise CommandError("This assignment is already ended.")
    assignment.is_active = False
    assignment.ended_at = timezone.now()
    assignment.ended_by = actor
    assignment.save(update_fields=["is_active", "ended_at", "ended_by", "updated_at"])
    return assignment


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

#: Completion evidence: PDF or picture, up to five files of 10 MiB each.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 5
ATTACHMENT_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def instruction_attachment_directory() -> Path:
    directory = Path(settings.LEGACY_UPLOAD_DIR) / "instructions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def instruction_attachment_path(file_name: str) -> Path | None:
    """Absolute path of a stored attachment, guarded against traversal."""
    if not file_name or os.path.basename(file_name) != file_name or file_name.startswith("."):
        return None
    path = instruction_attachment_directory() / file_name
    return path if path.is_file() else None


def prepare_instruction_attachments(files) -> list[dict]:
    """Validate uploaded evidence before anything is recorded.

    The kind is sniffed from the bytes, so renaming a non-document cannot pass
    the check. Raises ``CommandError`` with the offending filename.
    """
    from apps.accounts.profile import sniff_image

    uploads = [upload for upload in (files or []) if upload is not None]
    if not uploads:
        return []
    if len(uploads) > MAX_ATTACHMENTS:
        raise CommandError(f"Attach no more than {MAX_ATTACHMENTS} files.")

    prepared: list[dict] = []
    for upload in uploads:
        name = os.path.basename(getattr(upload, "name", "") or "attachment")
        if getattr(upload, "size", 0) <= 0:
            raise CommandError(f"{name}: the file is empty.")
        if upload.size > MAX_ATTACHMENT_BYTES:
            raise CommandError(
                f"{name}: the maximum file size is {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MiB."
            )
        data = upload.read()
        if data.startswith(b"%PDF-"):
            kind = "pdf"
        else:
            kind = sniff_image(data[:16])
        if kind not in ATTACHMENT_CONTENT_TYPES:
            raise CommandError(
                f"{name}: only PDF documents and pictures (PNG, JPEG, WebP, GIF) are accepted."
            )
        prepared.append({"data": data, "kind": kind, "original_name": name[:255]})
    return prepared


def _save_instruction_attachments(item, prepared) -> list:
    from apps.command.models import InstructionAttachment

    if not prepared:
        return []
    directory = instruction_attachment_directory()
    written: list[Path] = []
    try:
        created = []
        for entry in prepared:
            kind = entry["kind"]
            filename = (
                f"instruction_{item.instruction_id}_{item.user_id}_"
                f"{uuid.uuid4().hex[:10]}.{kind}"
            )
            path = directory / filename
            path.write_bytes(entry["data"])
            written.append(path)
            created.append(
                InstructionAttachment.objects.create(
                    recipient=item,
                    file_name=filename,
                    original_name=entry["original_name"],
                    content_type=ATTACHMENT_CONTENT_TYPES[kind],
                    size_bytes=len(entry["data"]),
                )
            )
        return created
    except Exception:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best effort cleanup
                pass
        raise


@transaction.atomic
def issue_instruction(
    *,
    unit,
    sender,
    subject,
    body,
    recipients,
    mission=None,
    related=("", ""),
    priority="normal",
    requires_acknowledgement=True,
    requires_completion=False,
    due_at=None,
):
    from apps.command.models import Instruction, InstructionRecipient
    from apps.ops.notifications import notify

    if not subject.strip():
        raise CommandError("An instruction needs a subject.")
    if not body.strip():
        raise CommandError("An instruction needs a body.")
    recipient_ids = []
    for person in recipients or []:
        person_id = getattr(person, "id", person)
        if person_id and person_id not in recipient_ids:
            recipient_ids.append(person_id)
    if not recipient_ids:
        raise CommandError("Select at least one recipient.")

    from apps.accounts.models import User

    people = list(User.objects.filter(id__in=recipient_ids, is_active=True))
    if len(people) != len(recipient_ids):
        raise CommandError("One or more recipients are unknown or inactive.")

    instruction = Instruction.objects.create(
        unit=unit,
        sender=sender,
        subject=subject.strip(),
        body=body.strip(),
        priority=priority,
        mission=mission,
        related_type=related[0] or "",
        related_id=str(related[1]) if related and related[1] else "",
        requires_acknowledgement=requires_acknowledgement,
        requires_completion=requires_completion,
        due_at=due_at,
    )

    for person in people:
        InstructionRecipient.objects.create(instruction=instruction, user=person)
        notify(
            person,
            title=f"Instruction: {instruction.subject}",
            message=body.strip()[:2000],
            level="warning" if priority in {"high", "urgent"} else "info",
            category="instruction",
            related=("instruction", instruction.id),
            actor=sender,
            priority=priority,
            requires_action=True,
            action_label="Acknowledge" if requires_acknowledgement else "Action required",
            action_url=f"/instructions/{instruction.id}",
            due_at=due_at,
        )

    return instruction


def _sender_name(user) -> str:
    return getattr(user, "full_name", "") or getattr(user, "username", "recipient")


def _notify_sender(instruction, *, actor, title, message, requires_action=False, action_label=""):
    """Tell the issuing post that a recipient acted on their instruction.

    The sender's own action never notifies them, so an instruction they
    acknowledge or complete themselves produces no echo.
    """
    from apps.ops.notifications import notify

    if not instruction.sender_id or instruction.sender_id == actor.id:
        return None
    return notify(
        instruction.sender,
        title=title,
        message=message,
        category="instruction",
        related=("instruction", instruction.id),
        actor=actor,
        requires_action=requires_action,
        action_label=action_label,
        action_url=f"/instructions/{instruction.id}",
    )


@transaction.atomic
def acknowledge_instruction(instruction, *, user, note=""):
    from apps.command.models import InstructionRecipient

    item = InstructionRecipient.objects.filter(instruction=instruction, user_id=user.id).first()
    if item is None:
        raise CommandError("You are not a recipient of this instruction.")
    if item.acknowledged_at is not None:
        return item
    item.acknowledged_at = timezone.now()
    item.save(update_fields=["acknowledged_at", "updated_at"])

    if instruction.status == instruction.STATUS_OPEN:
        instruction.status = instruction.STATUS_IN_PROGRESS
        instruction.save(update_fields=["status", "updated_at"])

    # Acknowledging is recorded separately from completion (spec §4.9).
    from apps.ops.models import Notification

    Notification.objects.filter(
        recipient_id=user.id,
        related_type="instruction",
        related_id=str(instruction.id),
        requires_action=True,
        action_status=Notification.ACTION_OPEN,
    ).update(action_status=Notification.ACTION_IN_PROGRESS)

    _notify_sender(
        instruction,
        actor=user,
        title=f"{_sender_name(user)} acknowledged: {instruction.subject}",
        message=(note or "").strip() or "Acknowledged. The work item remains open.",
    )
    return item


@transaction.atomic
def complete_instruction(instruction, *, user, note="", attachments=None):
    from apps.command.models import InstructionRecipient

    item = InstructionRecipient.objects.filter(instruction=instruction, user_id=user.id).first()
    if item is None:
        raise CommandError("You are not a recipient of this instruction.")
    if item.acknowledged_at is None and instruction.requires_acknowledgement:
        raise CommandError("Acknowledge this instruction before marking it complete.")
    if item.completed_at is not None:
        return item

    item.completed_at = timezone.now()
    item.completion_note = (note or "").strip()
    item.save(update_fields=["completed_at", "completion_note", "updated_at"])
    _save_instruction_attachments(item, attachments or [])

    outstanding = instruction.recipients.filter(completed_at__isnull=True).exists()
    if not outstanding:
        instruction.status = (
            instruction.STATUS_COMPLETED
            if instruction.requires_completion
            else instruction.STATUS_VERIFIED
        )
        instruction.save(update_fields=["status", "updated_at"])

    from apps.ops.models import Notification
    from apps.ops.notifications import complete_action

    complete_action(
        related_type="instruction",
        related_ids=[instruction.id],
        recipients=[user],
        note=item.completion_note,
        actor=user,
    )
    Notification.objects.filter(
        recipient_id=user.id,
        related_type="instruction",
        related_id=str(instruction.id),
    ).update(is_read=True, read_at=timezone.now())

    evidence = item.attachments.count()
    _notify_sender(
        instruction,
        actor=user,
        title=f"{_sender_name(user)} completed: {instruction.subject}",
        message=(
            (item.completion_note or "Marked their part complete.")
            + (f" ({evidence} document{'s' if evidence != 1 else ''} attached.)" if evidence else "")
        ),
        requires_action=instruction.requires_completion,
        action_label="Verify completion" if instruction.requires_completion else "",
    )
    return item


@transaction.atomic
def verify_instruction(instruction, *, user, note=""):
    from apps.command.models import Instruction
    from apps.core.audit import log_action

    if instruction.requires_completion and instruction.recipients.filter(
        completed_at__isnull=True
    ).exists():
        raise CommandError("Recipients have not completed this instruction yet.")
    instruction.status = Instruction.STATUS_VERIFIED
    instruction.verified_by = user
    instruction.verified_at = timezone.now()
    instruction.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])
    log_action(
        actor=user,
        action="verify_instruction",
        entity_type="instruction",
        entity_id=instruction.pk,
        after={"status": instruction.status, "note": note},
    )
    return instruction

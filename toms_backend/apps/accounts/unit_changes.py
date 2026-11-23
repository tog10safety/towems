"""Unit transfers are requested and decided, never applied on the spot.

Moving a person to another unit ends every post they hold where they were and
resets the account role to Creator. Because that is destructive and cannot be
undone from the profile page, a move is recorded as a request. An Administrator
or the Super Administrator decides it; they are told through the notification
bell, and the request stays ``pending`` until the decision is recorded here.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts import ownership
from apps.accounts.models import UnitChangeRequest
from apps.core import roles
from apps.core.audit import log_action

logger = logging.getLogger(__name__)

#: ``Notification.related_type`` for a unit change request. The review page is
#: the only place the decision can be made, so the bell links straight to it.
RELATED_TYPE = "unit_change_request"
REVIEW_URL = "/admin/unit-changes"


class UnitChangeError(ValueError):
    """A rule violation the API surfaces as HTTP 400."""


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def pending_for(user):
    """The person's outstanding request, or ``None``."""
    if user is None or not getattr(user, "id", None):
        return None
    return (
        UnitChangeRequest.objects.select_related("from_unit", "to_unit", "decided_by")
        .filter(requester_id=user.id, status=UnitChangeRequest.STATUS_PENDING)
        .first()
    )


def approvers():
    """Every account that may decide a unit change.

    Both halves of the rule are explicit: an Administrator account, and the one
    account holding the Super Administrator seat (which is itself an
    Administrator, so the second clause only matters if the seat is ever
    attached to another role).
    """
    from apps.accounts.models import User

    return (
        User.objects.filter(is_active=True)
        .filter(Q(authority=roles.BASE_ADMIN) | Q(profile__is_system_owner=True))
        .distinct()
    )


def can_decide(user) -> bool:
    """Administrators and the Super Administrator seat, nobody else."""
    if user is None or not getattr(user, "is_active", False):
        return False
    if ownership.is_system_owner(user):
        return True
    return roles.has_capability(user, roles.CAP_MANAGE_USERS)


def request_payload(row) -> dict | None:
    """One request in the shape the profile page and the review queue read."""
    if row is None:
        return None
    return {
        "id": row.id,
        "status": row.status,
        "status_label": row.get_status_display(),
        "from_unit_id": row.from_unit_id,
        "from_unit_acronym": row.from_unit.acronym if row.from_unit_id else "",
        "to_unit_id": row.to_unit_id,
        "to_unit_acronym": row.to_unit.acronym if row.to_unit_id else "",
        "to_unit_name": row.to_unit.name if row.to_unit_id else "",
        "requester_note": row.requester_note,
        "decision_note": row.decision_note,
        "decided_by": getattr(row.decided_by, "username", "") or "",
        "created_at": row.created_at,
        "decided_at": row.decided_at,
    }


# ---------------------------------------------------------------------------
# Requesting
# ---------------------------------------------------------------------------


def _label(user) -> str:
    name = getattr(user, "full_name", "") or getattr(user, "username", "") or "A user"
    serial = getattr(user, "serial_number", "") or ""
    return f"{name} (serial {serial})" if serial else name


def _unit_label(unit) -> str:
    if unit is None:
        return "no unit"
    return f"{unit.acronym} - {unit.name}"


def _notify_approvers(row, *, actor) -> None:
    from apps.ops.notifications import notify_many

    requester = row.requester
    notify_many(
        approvers(),
        title=f"Unit change request: {_label(requester)}",
        message=(
            f"{_label(requester)} asks to move from "
            f"{_unit_label(row.from_unit)} to {_unit_label(row.to_unit)}. "
            "Approving ends every post held in the old unit and leaves the "
            "Creator role only; nothing follows the person across."
        ),
        level="warning",
        category="unit_change",
        related=(RELATED_TYPE, row.id),
        actor=actor,
        priority="high",
        requires_action=True,
        action_label="Review unit change",
        action_url=REVIEW_URL,
    )


@transaction.atomic
def request_unit_change(user, unit, *, note="", request=None):
    """Record (or replace) the caller's pending move to ``unit``.

    The account does not move here: no post is ended and no role changes until
    an approver decides. Asking again for the same target is idempotent; asking
    for a different target replaces the pending request and retires the open
    notification that pointed at it.
    """
    from apps.ops.notifications import complete_action

    if user is None or not getattr(user, "id", None):
        raise UnitChangeError("Sign in before requesting a unit change.")
    if unit is None or not getattr(unit, "is_active", False):
        raise UnitChangeError("That unit is not active.")
    if ownership.is_system_owner(user):
        raise UnitChangeError(
            "The Super Administrator's unit is changed by another administrator, "
            "after handing the seat over."
        )
    if unit.id == user.unit_id:
        raise UnitChangeError("You are already in that unit.")

    existing = pending_for(user)
    if existing is not None:
        if existing.to_unit_id == unit.id:
            return existing
        existing.status = UnitChangeRequest.STATUS_CANCELLED
        existing.decision_note = "Replaced by a newer request."
        existing.save(update_fields=["status", "decision_note", "updated_at"])
        complete_action(
            related_type=RELATED_TYPE,
            related_ids=[existing.id],
            note="A newer unit change request replaced this one.",
            actor=user,
        )

    row = UnitChangeRequest.objects.create(
        requester_id=user.id,
        from_unit_id=user.unit_id,
        to_unit_id=unit.id,
        requester_note=(note or "").strip()[:2000],
    )

    log_action(
        actor=user,
        action="request_unit_change",
        entity_type=RELATED_TYPE,
        entity_id=row.id,
        before={"unit_id": user.unit_id},
        after={"requested_unit_id": unit.id, "status": row.status},
        request=request,
    )

    _notify_approvers(row, actor=user)
    return row


@transaction.atomic
def cancel_request(user, *, request=None):
    """Withdraw the caller's own pending move.

    Nothing about the account changes either way; the request simply stops
    waiting for a decision, so a person who picked the wrong unit is not stuck
    until an Administrator gets to the queue. The pending bell items close
    themselves, because a workflow that cannot be acted on must not sit in an
    approver's action list.
    """
    from apps.ops.notifications import complete_action

    row = (
        UnitChangeRequest.objects.select_related("from_unit", "to_unit")
        .filter(
            requester_id=getattr(user, "id", None),
            status=UnitChangeRequest.STATUS_PENDING,
        )
        .first()
    )
    if row is None:
        raise UnitChangeError("You have no unit change request waiting for approval.")

    row.status = UnitChangeRequest.STATUS_CANCELLED
    row.decision_note = "Withdrawn by the requester."
    row.save(update_fields=["status", "decision_note", "updated_at"])

    complete_action(
        related_type=RELATED_TYPE,
        related_ids=[row.id],
        note="The person withdrew this request.",
        actor=user,
    )

    log_action(
        actor=user,
        action="cancel_unit_change",
        entity_type=RELATED_TYPE,
        entity_id=row.id,
        before={
            "status": UnitChangeRequest.STATUS_PENDING,
            "to_unit_id": row.to_unit_id,
        },
        after={"status": row.status},
        request=request,
    )
    return row


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


def _check_decidable(row, actor) -> None:
    if not can_decide(actor):
        raise UnitChangeError(
            "Only an Administrator or the Super Administrator may decide a unit "
            "change request."
        )
    if row.requester_id == getattr(actor, "id", None):
        raise UnitChangeError(
            "You cannot decide your own unit change request. Another Administrator "
            "or the Super Administrator must review it."
        )


@transaction.atomic
def approve_request(row, *, actor, note="", request=None):
    """Approve a pending move and apply it in one transaction."""
    from apps.ops.notifications import complete_action, notify

    row = UnitChangeRequest.objects.select_related(
        "requester", "from_unit", "to_unit", "decided_by"
    ).get(pk=row.pk)
    _check_decidable(row, actor)
    if row.status != UnitChangeRequest.STATUS_PENDING:
        raise UnitChangeError("This request has already been decided.")

    target = row.to_unit
    if target is None or not target.is_active:
        raise UnitChangeError(
            "That unit is no longer active. Decline this request and ask the "
            "person to choose another unit."
        )

    requester = row.requester
    note = (note or "").strip()[:2000]
    applied: dict | None = None
    if requester.unit_id != target.id:
        from apps.accounts.profile import change_unit

        applied = change_unit(requester, target, request=request, actor=actor)

    row.status = UnitChangeRequest.STATUS_APPROVED
    row.decided_by = actor
    row.decided_at = timezone.now()
    row.decision_note = note
    row.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"])

    complete_action(
        related_type=RELATED_TYPE,
        related_ids=[row.id],
        note=note or "Unit change approved.",
        actor=actor,
    )

    notify(
        requester,
        title=f"Unit change approved: you are now in {target.acronym}",
        message=(
            f"Your move from {_unit_label(row.from_unit)} to {_unit_label(target)} "
            "was approved. "
            + (
                "Every post you held in the previous unit was ended; you hold the "
                "Creator role only in the new one."
                if applied is not None
                else "You were already in that unit."
            )
        ),
        level="success",
        category="unit_change",
        related=(RELATED_TYPE, row.id),
        actor=actor,
        action_url="/profile",
    )

    log_action(
        actor=actor,
        action="approve_unit_change",
        entity_type=RELATED_TYPE,
        entity_id=row.id,
        before={
            "requester_id": requester.id,
            "unit_id": applied["from_unit_id"] if applied else requester.unit_id,
            "status": UnitChangeRequest.STATUS_PENDING,
        },
        after={
            "unit_id": target.id,
            "status": row.status,
            "authority": roles.BASE_CREATOR if applied else requester.authority,
        },
        note=note,
        request=request,
    )
    return row


@transaction.atomic
def reject_request(row, *, actor, note="", request=None):
    """Decline a pending move. The account keeps everything it holds."""
    from apps.ops.notifications import complete_action, notify

    row = UnitChangeRequest.objects.select_related(
        "requester", "from_unit", "to_unit", "decided_by"
    ).get(pk=row.pk)
    _check_decidable(row, actor)
    if row.status != UnitChangeRequest.STATUS_PENDING:
        raise UnitChangeError("This request has already been decided.")

    note = (note or "").strip()[:2000]
    if not note:
        raise UnitChangeError(
            "A reason is required to decline a request, so the person knows what "
            "to do next."
        )

    row.status = UnitChangeRequest.STATUS_REJECTED
    row.decided_by = actor
    row.decided_at = timezone.now()
    row.decision_note = note
    row.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"])

    complete_action(
        related_type=RELATED_TYPE,
        related_ids=[row.id],
        note=note,
        actor=actor,
    )

    notify(
        row.requester,
        title="Unit change request declined",
        message=(
            f"Your request to move to {_unit_label(row.to_unit)} was declined. "
            f"Reason: {note}. You remain in {_unit_label(row.from_unit)} with your "
            "current posts."
        ),
        level="warning",
        category="unit_change",
        related=(RELATED_TYPE, row.id),
        actor=actor,
        action_url="/profile",
    )

    log_action(
        actor=actor,
        action="reject_unit_change",
        entity_type=RELATED_TYPE,
        entity_id=row.id,
        before={"status": UnitChangeRequest.STATUS_PENDING, "unit_id": row.requester.unit_id},
        after={"status": row.status},
        note=note,
        request=request,
    )
    return row

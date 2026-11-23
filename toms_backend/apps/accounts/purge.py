"""Seat-only permanent deletion of an account.

An account is normally deactivated: its missions, assessments, approvals and
audit entries keep pointing at a person who can still be named. The Super
Administrator may erase an account that was opened in error, but only while no
record refers to it. An account that appears in a mission, an assessment, the
inventory ledger or the audit trail is evidence, and erasing it would leave
history referring to nobody - so that account is deactivated instead.
"""

from __future__ import annotations

from django.db import transaction

from apps.accounts import ownership


class PurgeError(ValueError):
    """The account cannot be erased; the message is safe to show to the caller."""


def blockers(account) -> dict[str, int]:
    """Counts of the records that make ``account`` part of the history."""
    if account is None:
        return {}
    from apps.legacy.models import MRMForm
    from apps.ops.models import AuditLog, InventoryTransaction, Mission

    return {
        "missions": Mission.objects.filter(created_by_id=account.id).count(),
        "assessments": MRMForm.objects.filter(created_by_id=account.id).count(),
        "inventory movements": InventoryTransaction.objects.filter(
            actor_id=account.id
        ).count(),
        "audit entries": AuditLog.objects.filter(actor_id=account.id).count(),
    }


@transaction.atomic
def delete_account(account, *, actor, reason: str, request=None):
    """Erase ``account`` and the rows that belong only to it.

    Everything that gives the account meaning in the record is refused rather
    than cascaded: the caller is told what to do instead (deactivate), and no
    history is rewritten by a delete.
    """
    from apps.core.audit import log_action

    reason = (reason or "").strip()
    if len(reason) < 3:
        raise PurgeError("Record why the account is being deleted.")
    if not ownership.is_system_owner(actor):
        raise PurgeError(
            "Only the Super Administrator may permanently delete an account."
        )
    if account is None:
        raise PurgeError("Choose an account to delete.")
    if account.id == getattr(actor, "id", None):
        raise PurgeError("You cannot delete your own account.")
    if ownership.is_system_owner(account):
        raise PurgeError(
            "Hand the Super Administrator seat over before deleting this account."
        )

    found = {name: count for name, count in blockers(account).items() if count}
    if found:
        detail = ", ".join(f"{count} {name}" for name, count in found.items())
        raise PurgeError(
            f"This account has operational history ({detail}). Deactivate it "
            "instead, so the record keeps its author."
        )

    from apps.accounts.models import PasswordResetToken, UserMFA, UserProfile
    from apps.command.models import (
        ActingAppointment,
        SectionAssignment,
        UnitAppointment,
    )
    from apps.ops.models import MRMAttentionDismissal, Notification

    account_id = account.id
    label = {
        "username": account.username,
        "full_name": account.full_name,
        "authority": account.authority,
        "unit_id": account.unit_id,
    }

    # Rows that exist only because this account does.
    UserProfile.objects.filter(user_id=account_id).delete()
    PasswordResetToken.objects.filter(user_id=account_id).delete()
    # Recovery codes cascade from this row.
    UserMFA.objects.filter(user_id=account_id).delete()
    MRMAttentionDismissal.objects.filter(user_id=account_id).delete()
    Notification.objects.filter(recipient_id=account_id).delete()
    SectionAssignment.objects.filter(user_id=account_id).delete()
    UnitAppointment.objects.filter(user_id=account_id).delete()
    ActingAppointment.objects.filter(user_id=account_id).delete()

    try:
        account.delete()
    except Exception as exc:  # pragma: no cover - depends on deployed schema
        raise PurgeError(
            "The account is still referenced by other records and cannot be "
            f"deleted: {exc}"
        ) from exc

    log_action(
        actor=actor,
        action="delete_user",
        entity_type="user",
        entity_id=account_id,
        before=label,
        note=reason,
        request=request,
    )
    return account_id

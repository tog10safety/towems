"""The single Super Administrator seat.

TOWEMS has one account that owns the system: the *Super Administrator*. It is an
ordinary Administrator account - ``users.authority`` is ``Administrator``, so it
holds every capability and reaches every active unit - and what makes it special
is that exactly one account may hold it at a time.

Three rules keep it that way, each enforced where it cannot be bypassed:

1. **The database.** A partial unique index on ``is_system_owner`` refuses a
   second owner even if application code is wrong.
2. **The API.** The seat is never assigned through a normal account edit. The
   handover action moves it from the current holder to a successor in one
   transaction, with a written reason and an audit record.
3. **The command line.** ``manage.py appoint_system_owner`` is the break-glass
   path for the case the API cannot solve: nobody who can sign in holds the
   seat. It needs shell access and writes the same audit entry, with no actor.

The seat deliberately carries no extra capability. Power in TOWEMS comes from the
Administrator role; this module only decides *which* administrator is the owner,
so approval authority stays with the command posts that must sign for it.
"""

from __future__ import annotations

from django.db import transaction

from apps.accounts.models import UserProfile


class SystemOwnerError(ValueError):
    """A seat rule was broken; the message is safe to show to an operator."""


def profile_for(user):
    """The account's additive profile row, created on first use."""
    if user is None or not getattr(user, "id", None):
        return None
    profile, _created = UserProfile.objects.get_or_create(user_id=user.id)
    return profile


def is_system_owner(user) -> bool:
    if user is None or not getattr(user, "id", None):
        return False
    return UserProfile.objects.filter(user_id=user.id, is_system_owner=True).exists()


def can_delete_records(actor) -> bool:
    """Permanent deletion of operational records belongs to the seat holder.

    Cancelling a mission, archiving an AOR, deactivating an office and
    deactivating an account stay with the roles that own those records. Erasing
    the record itself - an assessment that may be signed, a mission, a boundary
    or an office - is the Super Administrator's decision alone.
    """
    return is_system_owner(actor)


def can_grant_administrator(actor) -> bool:
    """Only the seat holder may confer the Administrator account role.

    Every Administrator can run day-to-day account work - register personnel,
    reset passwords, edit records - but growing the Administrator roll itself
    is the owner's decision, so the role cannot quietly multiply.
    """
    return is_system_owner(actor)


def current_owner():
    """The account holding the seat, or ``None`` when the seat is vacant."""
    profile = (
        UserProfile.objects.filter(is_system_owner=True)
        .select_related("user")
        .first()
    )
    return getattr(profile, "user", None) if profile is not None else None


def _log(action, *, actor, entity_id, before, after, note, request=None):
    from apps.core.audit import log_action

    log_action(
        actor=actor,
        action=action,
        entity_type="user",
        entity_id=entity_id,
        before=before,
        after=after,
        note=note,
        request=request,
    )


@transaction.atomic
def hand_over(
    successor,
    *,
    actor,
    reason: str,
    request=None,
    require_owner: bool = True,
    promote: bool = False,
):
    """Move the seat to ``successor`` in one transaction.

    ``require_owner`` is what the API relies on: only the current holder may
    hand the seat on. The break-glass command runs the same transaction with
    ``require_owner=False`` and ``actor=None``, because the operator behind it
    has shell access rather than a TOWEMS session.
    """
    from apps.core import roles

    reason = (reason or "").strip()
    if len(reason) < 3:
        raise SystemOwnerError("Record why the seat is changing hands.")
    if successor is None:
        raise SystemOwnerError("Choose the account that will hold the seat.")
    if not successor.is_active:
        raise SystemOwnerError("The successor account must be active.")

    outgoing = current_owner()
    if outgoing is not None and outgoing.id == successor.id:
        raise SystemOwnerError("That account already holds the seat.")
    if require_owner and outgoing is not None and (
        actor is None or outgoing.id != getattr(actor, "id", None)
    ):
        raise SystemOwnerError(
            "Only the current Administrator may hand the seat over."
        )

    previous_authority = successor.authority
    if previous_authority != roles.BASE_ADMIN:
        if not promote:
            raise SystemOwnerError(
                "The account taking the seat must already be an Administrator."
            )
        successor.authority = roles.BASE_ADMIN
        successor.save(update_fields=["authority"])

    if outgoing is not None:
        UserProfile.objects.filter(is_system_owner=True).update(is_system_owner=False)

    profile = profile_for(successor)
    profile.is_system_owner = True
    profile.save(update_fields=["is_system_owner"])

    _log(
        "handover_system_owner",
        actor=actor,
        entity_id=successor.id,
        before={
            "system_owner_id": outgoing.id if outgoing is not None else None,
            "authority": previous_authority,
        },
        after={"system_owner_id": successor.id, "authority": successor.authority},
        note=reason,
        request=request,
    )
    return successor


@transaction.atomic
def vacate(*, actor=None, reason: str, request=None):
    """Empty the seat. Used by the break-glass command when decommissioning."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise SystemOwnerError("Record why the seat is being vacated.")

    outgoing = current_owner()
    if outgoing is None:
        return None

    UserProfile.objects.filter(is_system_owner=True).update(is_system_owner=False)
    _log(
        "vacate_system_owner",
        actor=actor,
        entity_id=outgoing.id,
        before={"system_owner_id": outgoing.id},
        after={"system_owner_id": None},
        note=reason,
        request=request,
    )
    return outgoing

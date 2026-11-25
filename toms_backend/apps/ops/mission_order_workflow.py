"""Mission Order designation, signature routing and approval.

The Mission Order is drafted from the mission during planning. It travels with
the mission through the staff approval route; once the approving authority
releases the mission, the order is routed to the designated signatory. Dispatch
is refused until that signatory signs and approves the order.

The PDF is only a rendering of this record, so the workflow is enforced here on
the server rather than by whether a button appears in the React app.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.ops.models import Mission

ORDER_LABELS = dict(Mission.MISSION_ORDER_STATUS_CHOICES)


class MissionOrderError(Exception):
    """A refusal written for the person who tried the action."""


def _signatory_name(mission) -> str:
    signatory = getattr(mission, "mission_order_signatory", None)
    if signatory is None:
        return ""
    return getattr(signatory, "full_name", "") or getattr(signatory, "username", "")


def _notify_signatory(mission, *, actor=None):
    from apps.ops.notifications import notify

    signatory = mission.mission_order_signatory
    if signatory is None:
        return None
    return notify(
        signatory,
        title=f"Mission Order {mission.mission_number} awaits your signature",
        message=mission.title,
        category="approval",
        priority="high",
        related=("mission", mission.id),
        actor=actor,
        requires_action=True,
        action_label="Review and sign Mission Order",
        action_url=f"/missions/{mission.id}",
    )


@transaction.atomic
def assign_signatory(mission, *, user_id, actor, request=None):
    """Designate the person who signs this mission's Mission Order.

    The signatory comes from the same live command roster as the approving
    authority, so a person who cannot lawfully sign a mission cannot be named
    here. Changing the signatory after the order was signed is refused: the
    signed order belongs to the person who signed it.
    """
    from apps.core.audit import log_action
    from apps.ops import approvals
    from apps.ops.notifications import notify

    locked = Mission.objects.select_for_update().get(pk=mission.pk)
    if locked.category == Mission.CATEGORY_AIR:
        raise MissionOrderError("Mission Orders apply to ground operations only.")
    if locked.mission_order_status == Mission.MISSION_ORDER_APPROVED:
        raise MissionOrderError(
            "This Mission Order has already been signed and approved."
        )
    if locked.status in {"completed", "cancelled"}:
        raise MissionOrderError("This mission is closed.")

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        raise MissionOrderError("Select the person who will sign the Mission Order.") from None

    eligible = approvals.eligible_authorities(locked)
    row = next((item for item in eligible if item["user_id"] == user_id), None)
    if row is None:
        raise MissionOrderError(
            "The Mission Order signatory must be an active commander, deputy "
            "commander, director for operations or operations officer in this "
            "mission's unit."
        )
    from apps.accounts.models import User

    signatory = User.objects.get(pk=user_id)

    before = {
        "mission_order_signatory": locked.mission_order_signatory_id,
        "mission_order_status": locked.mission_order_status,
    }
    locked.mission_order_signatory = signatory
    update_fields = ["mission_order_signatory", "updated_at"]
    # If the mission is already approved, naming a new signatory puts the order
    # back in that person's queue without re-approving the mission itself.
    if locked.approval_status == Mission.APPROVAL_APPROVED:
        locked.mission_order_status = Mission.MISSION_ORDER_AWAITING_SIGNATURE
        locked.mission_order_assigned_at = timezone.now()
        update_fields += [
            "mission_order_status",
            "mission_order_assigned_at",
        ]
    locked.save(update_fields=update_fields)

    log_action(
        actor=actor,
        action="assign_mission_order_signatory",
        entity_type="mission",
        entity_id=locked.pk,
        before=before,
        after={
            "mission_order_signatory": signatory.id,
            "mission_order_status": locked.mission_order_status,
        },
        request=request,
    )
    if locked.mission_order_status == Mission.MISSION_ORDER_AWAITING_SIGNATURE:
        _notify_signatory(locked, actor=actor)
    else:
        notify(
            signatory,
            title=f"You are designated to sign Mission Order {locked.mission_number}",
            message=(
                "The Mission Order will be routed to you after the mission is approved."
            ),
            category="approval",
            priority="normal",
            related=("mission", locked.id),
            actor=actor,
            action_url=f"/missions/{locked.id}",
        )
    return locked


def reset(mission, *, save=True):
    """Return the order to planning when the mission approval is invalidated."""
    mission.mission_order_status = Mission.MISSION_ORDER_DRAFT
    mission.mission_order_assigned_at = None
    mission.mission_order_signed_at = None
    mission.mission_order_signed_by = None
    mission.mission_order_note = ""
    mission.mission_order_signed_revision = 0
    mission.mission_order_signer_designation = ""
    mission.mission_order_signer_as_commander = False
    if save:
        mission.save(
            update_fields=[
                "mission_order_status",
                "mission_order_assigned_at",
                "mission_order_signed_at",
                "mission_order_signed_by",
                "mission_order_note",
                "mission_order_signed_revision",
                "mission_order_signer_designation",
                "mission_order_signer_as_commander",
                "updated_at",
            ]
        )
    return mission


def release_for_signature(mission, *, actor, request=None):
    """Route an approved mission's order to its designated signatory."""
    # Air operations have no Mission Order; the approval stands on its own.
    if mission.category == Mission.CATEGORY_AIR:
        return mission
    signatory = mission.mission_order_signatory or mission.approving_authority
    if signatory is None:
        raise MissionOrderError(
            "Select a designated Mission Order signatory before approving this mission."
        )

    mission.mission_order_signatory = signatory
    mission.mission_order_status = Mission.MISSION_ORDER_AWAITING_SIGNATURE
    mission.mission_order_assigned_at = timezone.now()
    mission.mission_order_signed_at = None
    mission.mission_order_signed_by = None
    mission.mission_order_note = ""
    mission.mission_order_signed_revision = 0
    mission.mission_order_signer_designation = ""
    mission.mission_order_signer_as_commander = False
    mission.save(
        update_fields=[
            "mission_order_signatory",
            "mission_order_status",
            "mission_order_assigned_at",
            "mission_order_signed_at",
            "mission_order_signed_by",
            "mission_order_note",
            "mission_order_signed_revision",
            "mission_order_signer_designation",
            "mission_order_signer_as_commander",
            "updated_at",
        ]
    )
    _notify_signatory(mission, actor=actor)
    return mission


@transaction.atomic
def approve(mission, *, actor, note: str = "", request=None):
    """Record the designated signatory's signature and approval."""
    from apps.command.models import MissionRouting
    from apps.core.audit import log_action
    from apps.ops.notifications import complete_action, notify

    locked = Mission.objects.select_for_update().get(pk=mission.pk)
    if locked.category == Mission.CATEGORY_AIR:
        raise MissionOrderError("Mission Orders apply to ground operations only.")
    if locked.approval_status != Mission.APPROVAL_APPROVED:
        raise MissionOrderError(
            "The mission must be approved before its Mission Order can be signed."
        )
    if locked.mission_order_signatory_id is None:
        raise MissionOrderError("No Mission Order signatory is designated.")
    if locked.mission_order_signatory_id != actor.id:
        raise MissionOrderError(
            "Only the designated Mission Order signatory may sign and approve it."
        )
    if locked.mission_order_status == Mission.MISSION_ORDER_APPROVED:
        raise MissionOrderError("This Mission Order has already been signed.")
    if locked.mission_order_status != Mission.MISSION_ORDER_AWAITING_SIGNATURE:
        raise MissionOrderError(
            "This Mission Order is not awaiting signature. The mission must be "
            "approved and the order routed to its signatory first."
        )

    from apps.ops import mission_order_pdf

    # The signature block belongs to the signed document: record the post the
    # signer holds today, so a later reassignment or appointment change cannot
    # rewrite an order that has already been signed.
    as_commander = mission_order_pdf._signing_as_commander(locked, actor)
    note = (note or "").strip()
    locked.mission_order_status = Mission.MISSION_ORDER_APPROVED
    locked.mission_order_signed_at = timezone.now()
    locked.mission_order_signed_by = actor
    locked.mission_order_note = note
    locked.mission_order_signed_revision = locked.revision
    locked.mission_order_signer_designation = (
        mission_order_pdf._signer_designation(
            locked, actor, as_commander=as_commander
        )
        or ""
    )[:120]
    locked.mission_order_signer_as_commander = as_commander
    locked.save(
        update_fields=[
            "mission_order_status",
            "mission_order_signed_at",
            "mission_order_signed_by",
            "mission_order_note",
            "mission_order_signed_revision",
            "mission_order_signer_designation",
            "mission_order_signer_as_commander",
            "updated_at",
        ]
    )

    MissionRouting.objects.create(
        mission=locked,
        stage=MissionRouting.STAGE_MISSION_ORDER_SIGNATURE,
        action=MissionRouting.ACTION_APPROVED,
        actor=actor,
        note=note or "Mission Order signed and approved",
    )
    log_action(
        actor=actor,
        action="mission_order_approve",
        entity_type="mission",
        entity_id=locked.pk,
        before={"mission_order_status": Mission.MISSION_ORDER_AWAITING_SIGNATURE},
        after={
            "mission_order_status": locked.mission_order_status,
            "note": note,
            "revision": locked.mission_order_signed_revision,
        },
        request=request,
    )
    complete_action(
        related_type="mission",
        related_ids=[locked.id],
        note=f"Mission Order signed by {actor.username}",
        actor=actor,
    )
    notify(
        locked.created_by,
        title=f"Mission Order {locked.mission_number} signed",
        message=note or "The designated signatory approved the Mission Order.",
        level="success",
        category="approval",
        priority="high",
        related=("mission", locked.id),
        actor=actor,
    )
    # The order was the last gate: hand the Operations Center its dispatch task.
    from apps.ops import approvals

    approvals.notify_dispatch_ready(locked, actor=actor)
    return locked


def state(mission, user=None) -> dict:
    """The Mission Order block the detail page and monitoring payload use."""
    status = mission.mission_order_status
    label = ORDER_LABELS.get(status, status)
    signatory = getattr(mission, "mission_order_signatory", None)
    signed_by = getattr(mission, "mission_order_signed_by", None)
    stale = bool(
        status == Mission.MISSION_ORDER_APPROVED
        and mission.mission_order_signed_revision != mission.revision
    )
    if stale:
        label = "Signature stale"
    from apps.ops import mission_order_pdf

    return {
        "status": status,
        "label": label,
        "signatory_id": getattr(signatory, "id", None),
        "signatory_name": _signatory_name(mission),
        "signatory_rank": getattr(signatory, "rank", "") if signatory else "",
        # A signed order shows the post recorded at signature, not the roster's.
        "signatory_designation": (
            mission.mission_order_signer_designation
            if mission_order_pdf._has_recorded_signature_block(mission)
            else (getattr(signatory, "designation", "") if signatory else "")
        ),
        "assigned_at": mission.mission_order_assigned_at,
        "signed_at": mission.mission_order_signed_at,
        "signed_by_name": (
            (getattr(signed_by, "full_name", "") or getattr(signed_by, "username", ""))
            if signed_by
            else ""
        ),
        "note": mission.mission_order_note,
        "signed_revision": mission.mission_order_signed_revision,
        "revision": mission.revision,
        "stale": stale,
        "can_approve": bool(
            user is not None
            and getattr(user, "is_authenticated", False)
            and status == Mission.MISSION_ORDER_AWAITING_SIGNATURE
            and mission.mission_order_signatory_id == getattr(user, "id", None)
            and mission.approval_status == Mission.APPROVAL_APPROVED
        ),
    }


def execution_blocker(mission) -> dict | None:
    """The Mission Order half of the execution gate."""
    # Air operations monitor the flight itself and have no Mission Order.
    if mission.category == Mission.CATEGORY_AIR:
        return None
    status = mission.mission_order_status
    if status == Mission.MISSION_ORDER_APPROVED:
        if mission.mission_order_signed_revision == mission.revision:
            return None
        return {
            "code": "mission_order_stale",
            "label": "Mission Order signature stale",
            "detail": (
                "The mission changed after the Mission Order was signed, so the "
                "signature no longer covers it. The order must be signed again."
            ),
        }

    name = _signatory_name(mission)
    if status == Mission.MISSION_ORDER_AWAITING_SIGNATURE:
        detail = (
            f"The Mission Order is awaiting signature and approval from {name}."
            if name
            else "The Mission Order is awaiting its designated signatory."
        )
    else:
        detail = (
            "The Mission Order must be signed and approved by its designated "
            "signatory before this mission can be dispatched."
        )
    return {
        "code": "mission_order_not_approved",
        "label": "Mission Order not signed",
        "detail": detail,
    }

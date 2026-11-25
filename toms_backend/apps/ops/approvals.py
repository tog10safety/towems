"""Mission approval workflow and the execution gate (spec §2 and §3).

The Operations Center names an **approving authority**: one of the four
eligible command posts *and the person currently holding it*. Naming the post
alone is never enough - the approval task is assigned to a specific account, so
a change of incumbent cannot silently move an approval to somebody who never
saw the mission.

Everything here is server-side. The React app only ever renders what these
rules allow, and every action re-checks them.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone

from apps.core import roles
from apps.ops import formation_rules
from apps.ops.models import Mission

#: The posts that may hold approval authority for a mission, in display order.
AUTHORITY_ROLES = (
    Mission.AUTHORITY_COMMANDER,
    Mission.AUTHORITY_DEPUTY_COMMANDER,
    Mission.AUTHORITY_DIRECTOR_OPERATIONS,
    Mission.AUTHORITY_OPERATIONS_OFFICER,
)
AUTHORITY_LABELS = dict(Mission.APPROVING_AUTHORITY_CHOICES)

DECISION_APPROVE = "approve"
DECISION_RETURN = "return"
DECISION_REJECT = "reject"
DECISIONS = (DECISION_APPROVE, DECISION_RETURN, DECISION_REJECT)

APPROVAL_LABELS = dict(Mission.APPROVAL_STATUS_CHOICES)

#: The command ladder an approving authority can sit on, most junior first.
#: The selected final post decides how much of this ladder a mission walks.
ROUTE_ROLE_ORDER = (
    Mission.AUTHORITY_OPERATIONS_OFFICER,
    Mission.AUTHORITY_DIRECTOR_OPERATIONS,
    Mission.AUTHORITY_DEPUTY_COMMANDER,
    Mission.AUTHORITY_COMMANDER,
)
ROUTE_STAGE_BY_ROLE = {
    Mission.AUTHORITY_OPERATIONS_OFFICER: "operations_officer",
    Mission.AUTHORITY_DIRECTOR_OPERATIONS: "director_operations",
    Mission.AUTHORITY_DEPUTY_COMMANDER: "deputy_commander",
    Mission.AUTHORITY_COMMANDER: "commander",
}
ROUTE_ROLE_LABELS = {
    role: AUTHORITY_LABELS.get(role, role) for role in ROUTE_ROLE_ORDER
}


class ApprovalError(Exception):
    """A refusal the caller should see, with a message written for humans."""


class ExecutionBlocked(Exception):
    """Raised when a mission may not enter an executable state."""

    def __init__(self, blockers):
        self.blockers = blockers
        super().__init__(
            "Mission execution is blocked. "
            + " ".join(blocker["detail"] for blocker in blockers)
        )


# ---------------------------------------------------------------------------
# MRM completion
# ---------------------------------------------------------------------------


def mrm_form(mission):
    """The authoritative MRM form for a mission, or ``None``."""
    assessment = getattr(mission, "risk_assessment", None)
    if assessment is None or assessment.mrm_form_id is None:
        return None
    form = getattr(assessment, "mrm_form", None)
    if form is None:
        return None
    # A form that belongs to another mission is never authoritative here.
    if form.id != mission.risk_form_id and mission.risk_form_id:
        return None
    return form


def mrm_complete(mission) -> bool:
    """True when the mission's MRM requirement is satisfied.

    Air operations monitor the flight itself and carry no MRM, so they are not
    held for one. For ground operations, the legacy workflow's definition
    applies: the form exists, it was not returned or rejected, and the decision
    authority approved it. A draft, submitted-but-unevaluated or returned MRM
    never satisfies the gate.
    """
    if mission.category == Mission.CATEGORY_AIR:
        return True
    form = mrm_form(mission)
    if form is None:
        return False
    if not form.authority_approved:
        return False
    return form.review_status not in {"needs_revision", "rejected"}


def mrm_label(mission) -> str:
    from apps.ops.serializers import mrm_state

    return mrm_state(mission).get("label", "")


# ---------------------------------------------------------------------------
# Eligible authorities
# ---------------------------------------------------------------------------


def _authority_roles_held(user, unit_id) -> set[str]:
    return roles.effective_roles(user, unit_id) & set(AUTHORITY_ROLES)


def eligible_for_unit(unit_id, *, creator_id=None, include_user_id=None):
    """Eligible authorities for a unit, usable before the mission exists.

    The planning form needs the roster while it is still being filled in, so
    this mirrors :func:`eligible_authorities` without a mission row.
    """

    from types import SimpleNamespace

    probe = SimpleNamespace(unit_id=unit_id, created_by_id=creator_id)
    return eligible_authorities(probe, include_user_id=include_user_id)


def eligible_authorities(mission, *, include_user_id=None):
    """Active people who may approve ``mission``, scoped to its own unit.

    Eligibility is resolved from the actual appointment/acting records through
    :func:`apps.core.roles.effective_roles`, so an expired acting appointment
    stops appearing on its own. The mission creator is excluded: the spec
    forbids self-approval, and offering the option would only produce a refusal.
    """
    from apps.accounts.models import User
    from apps.command.models import ActingAppointment, UnitAppointment

    unit_id = mission.unit_id
    if unit_id is None:
        return []
    creator_id = mission.created_by_id

    candidate_ids = set(
        UnitAppointment.objects.filter(
            is_active=True, role__in=AUTHORITY_ROLES, user__is_active=True
        ).values_list("user_id", flat=True)
    )
    candidate_ids |= set(
        ActingAppointment.objects.filter(
            is_active=True, acting_role__in=AUTHORITY_ROLES, user__is_active=True
        ).values_list("user_id", flat=True)
    )
    if include_user_id:
        candidate_ids.add(include_user_id)

    rows = []
    for user in User.objects.select_related("unit").filter(
        id__in=candidate_ids, is_active=True
    ):
        held = _authority_roles_held(user, unit_id)
        if not held:
            continue
        if creator_id == user.id:
            continue
        # One row per person, showing the highest post they hold.
        role = next((name for name in AUTHORITY_ROLES if name in held), None)
        rows.append(
            {
                "user_id": user.id,
                "username": user.username,
                "full_name": user.full_name or user.username,
                "rank": user.rank or "",
                "designation": user.designation or "",
                "unit_id": user.unit_id,
                "unit_acronym": getattr(user.unit, "acronym", "") if user.unit_id else "",
                "unit_name": getattr(user.unit, "name", "") if user.unit_id else "",
                "role": role,
                "role_label": AUTHORITY_LABELS.get(role, ""),
                "label": _person_label(user, AUTHORITY_LABELS.get(role, "")),
            }
        )

    rows.sort(key=lambda row: (AUTHORITY_ROLES.index(row["role"]), row["full_name"]))
    return rows


def _person_label(user, role_label: str) -> str:
    parts = [part for part in (user.rank, user.full_name or user.username) if part]
    label = " ".join(parts)
    if user.designation:
        label = f"{label} ({user.designation})"
    unit = getattr(user.unit, "acronym", "") if user.unit_id else ""
    if unit:
        label = f"{label} — {unit}"
    if role_label:
        label = f"{label} · {role_label}"
    return label


def routing_roles(mission) -> list[str]:
    """The posts that must sign this mission, in order.

    The designated final authority sets the length of the route: an Operations
    Officer signs alone, while a Commander route passes through the Operations
    Officer, Director for Operations and Deputy Commander first.
    """
    role = mission.approving_authority_role
    if role not in ROUTE_ROLE_ORDER:
        return []
    return list(ROUTE_ROLE_ORDER[: ROUTE_ROLE_ORDER.index(role) + 1])


def route_holder(mission, role):
    """The person currently holding one step of the route, or ``None``.

    The final step is the person the Operations Center named, not merely the
    current occupant of the post. Intermediate steps follow the live unit
    appointments (and a current acting appointment) so a change of incumbent is
    reflected where the task lands.
    """
    from apps.command.models import ActingAppointment, UnitAppointment

    if role == mission.approving_authority_role and mission.approving_authority_id:
        return mission.approving_authority

    appointment = (
        UnitAppointment.objects.filter(
            unit_id=mission.unit_id,
            role=role,
            is_active=True,
            user__is_active=True,
        )
        .select_related("user")
        .first()
    )
    if appointment is not None:
        return appointment.user

    now = timezone.now()
    acting = (
        ActingAppointment.objects.filter(
            unit_id=mission.unit_id,
            acting_role=role,
            is_active=True,
            revoked_at__isnull=True,
            starts_at__lte=now,
            ends_at__gt=now,
            user__is_active=True,
        )
        .select_related("user")
        .first()
    )
    return acting.user if acting is not None else None


def validate_route_holders(mission) -> None:
    """Refuse to start a route that has an unfilled signature step."""
    if mission.approving_authority_id is None:
        raise ApprovalError(
            "Select the approving authority before routing this mission."
        )
    missing = [
        ROUTE_ROLE_LABELS[role]
        for role in routing_roles(mission)
        if route_holder(mission, role) is None
    ]
    if missing:
        raise ApprovalError(
            "Cannot start approval routing because no active person holds: "
            + ", ".join(missing)
            + ". Appoint someone to each post first."
        )


def current_route_role(mission):
    """The first route step without an approval, or ``None`` when all signed."""
    from apps.command.models import MissionRouting

    approved = set(
        MissionRouting.objects.filter(mission=mission, action="approved").values_list(
            "stage", flat=True
        )
    )
    for role in routing_roles(mission):
        if ROUTE_STAGE_BY_ROLE[role] not in approved:
            return role
    return None


def route_plan(mission) -> dict:
    """The configured route, its current step and every comment so far."""
    from apps.command.models import MissionRouting

    rows = list(mission.routing.select_related("actor").all())
    roles = routing_roles(mission)
    current_role = current_route_role(mission) if roles else None
    stages = []
    current_seen = False
    for role in roles:
        stage_key = ROUTE_STAGE_BY_ROLE[role]
        stage_rows = [row for row in rows if row.stage == stage_key]
        approved = next(
            (row for row in reversed(stage_rows) if row.action == "approved"), None
        )
        stopped = next(
            (
                row
                for row in reversed(stage_rows)
                if row.action in {"returned", "rejected"}
            ),
            None,
        )
        # A resubmission re-opens the step it answers: the routed row the
        # Operations Center added is newer than the return, so the step is
        # pending again and the same authority reviews the revised mission.
        routed = next(
            (row for row in reversed(stage_rows) if row.action == "routed"), None
        )
        reopened = bool(
            routed is not None
            and (approved is None or approved.created_at < routed.created_at)
            and (stopped is None or stopped.created_at < routed.created_at)
        )
        if approved is not None and not reopened:
            status = "approved"
            active_row = approved
        elif stopped is not None and not reopened:
            status = stopped.action
            active_row = stopped
        elif role == current_role and not current_seen:
            status = "pending"
            active_row = next(
                (row for row in reversed(stage_rows) if row.action == "routed"), None
            )
            current_seen = True
        else:
            status = "waiting"
            active_row = None
        holder = route_holder(mission, role)
        stages.append(
            {
                "role": role,
                "label": ROUTE_ROLE_LABELS[role],
                "stage": stage_key,
                "status": status,
                "assignee_id": getattr(holder, "id", None),
                "assignee_name": (
                    (getattr(holder, "full_name", "") or getattr(holder, "username", ""))
                    if holder is not None
                    else ""
                ),
                "comment": active_row.note if active_row is not None else "",
                "decided_at": (
                    active_row.created_at.isoformat()
                    if active_row is not None and status != "pending"
                    else None
                ),
            }
        )

    final_role = roles[-1] if roles else ""
    current_holder = route_holder(mission, current_role) if current_role else None
    higher = []
    if final_role in ROUTE_ROLE_ORDER:
        for role in ROUTE_ROLE_ORDER[ROUTE_ROLE_ORDER.index(final_role) + 1 :]:
            holder = route_holder(mission, role)
            if holder is not None:
                higher.append(
                    {
                        "role": role,
                        "label": ROUTE_ROLE_LABELS[role],
                        "user_id": holder.id,
                        "name": getattr(holder, "full_name", "") or holder.username,
                    }
                )

    started = any(
        row.stage == "operations_center" and row.action == "routed" for row in rows
    )
    return {
        "stages": stages,
        "started": started,
        "current_role": current_role,
        "current_label": ROUTE_ROLE_LABELS.get(current_role, ""),
        "current_stage": ROUTE_STAGE_BY_ROLE.get(current_role, ""),
        "current_assignee_id": getattr(current_holder, "id", None),
        "current_assignee_name": (
            (getattr(current_holder, "full_name", "") or getattr(current_holder, "username", ""))
            if current_holder is not None
            else ""
        ),
        "final_role": final_role,
        "final_label": ROUTE_ROLE_LABELS.get(final_role, ""),
        "higher_authorities": higher,
        "complete": bool(roles) and current_role is None,
    }


def notify_route_higher_authorities(mission, actor=None):
    """Tell posts above the final authority that the mission is moving.

    They are outside the formal route, so the notification is informative and
    carries no action. One notice per mission and recipient is enough.
    """
    from apps.command.models import MissionRouting
    from apps.ops.models import Notification
    from apps.ops.notifications import notify_many

    plan = route_plan(mission)
    if not plan["higher_authorities"]:
        return []
    existing = set(
        Notification.objects.filter(
            category="mission_routing",
            related_type="mission",
            related_id=str(mission.id),
        ).values_list("recipient_id", flat=True)
    )
    from apps.accounts.models import User

    people = list(
        User.objects.filter(
            id__in=[row["user_id"] for row in plan["higher_authorities"]],
            is_active=True,
        )
    )
    people = [person for person in people if person.id not in existing]
    if not people:
        return []
    route_text = " → ".join(
        ["Operations Center"] + [row["label"] for row in plan["stages"]]
    )
    return notify_many(
        people,
        title=f"Mission {mission.mission_number} routed for approval",
        message=(
            f"Approval route: {route_text}. {plan['final_label']} is the designated "
            "approving authority; your post is notified for visibility and no action "
            "is required."
        ),
        category="mission_routing",
        level="info",
        priority="normal",
        related=("mission", mission.id),
        actor=actor,
        action_url=f"/missions/{mission.id}",
    )


def notify_higher_authorities_of_approval(mission, *, actor=None):
    """Tell posts above the approving authority that approval is final.

    They sit outside the route, so this carries no action: it records that the
    mission was approved and that their post was informed.
    """
    from apps.accounts.models import User
    from apps.ops.notifications import notify_many

    plan = route_plan(mission)
    higher = plan.get("higher_authorities") or []
    if not higher:
        return []
    people = list(
        User.objects.filter(
            id__in=[row["user_id"] for row in higher], is_active=True
        )
    )
    final_label = plan.get("final_label") or ROUTE_ROLE_LABELS.get(
        mission.approving_authority_role, "the approving authority"
    )
    return notify_many(
        people,
        title=f"Mission {mission.mission_number} approved",
        message=(
            f"{final_label} approved this mission. Approval is final; your post is "
            "notified for information only and no action is required."
        ),
        category="mission_routing",
        level="info",
        priority="normal",
        related=("mission", mission.id),
        actor=actor,
    )


def notify_dispatch_ready(mission, *, actor=None):
    """Ask the Operations Center to dispatch once every gate is open.

    Air missions are dispatchable the moment they are approved; ground missions
    only after the Mission Order is signed. Callers invoke this after either
    event, and it stays silent while a blocker remains or an open dispatch task
    already exists.
    """
    from apps.ops.models import Notification
    from apps.ops.notifications import notify

    if mission.created_by_id is None:
        return None
    if readiness(mission).get("state") != "ready":
        return None
    if Notification.objects.filter(
        related_type="mission",
        related_id=str(mission.id),
        recipient_id=mission.created_by_id,
        requires_action=True,
        action_status__in=[
            Notification.ACTION_OPEN,
            Notification.ACTION_IN_PROGRESS,
        ],
    ).exists():
        return None
    return notify(
        mission.created_by,
        title=f"Mission {mission.mission_number} is ready to dispatch",
        message=(
            "The mission and its Mission Order are approved. Dispatch it when ready."
        ),
        category="mission_routing",
        priority="high",
        related=("mission", mission.id),
        actor=actor,
        requires_action=True,
        action_label="Dispatch mission",
        action_url=f"/missions/{mission.id}",
    )


def start_route(mission, *, actor, note: str = "", request=None) -> dict:
    """Open the configured approval route and notify its first signer."""
    from apps.command.models import MissionRouting
    from apps.ops.notifications import notify

    if mission.category == Mission.CATEGORY_AIR:
        raise ApprovalError("Air operations do not use approval routing.")
    validate_route_holders(mission)
    roles = routing_roles(mission)
    if not roles:
        raise ApprovalError("Select an approving authority before routing this mission.")
    if mission.status in {"completed", "cancelled"}:
        raise ApprovalError("This mission is closed.")

    MissionRouting.objects.get_or_create(
        mission=mission,
        stage=MissionRouting.STAGE_OPERATIONS_CENTER,
        action=MissionRouting.ACTION_ROUTED,
        defaults={"actor": actor, "note": note or "Submitted to the Operations Center"},
    )
    first_role = roles[0]
    first_stage = ROUTE_STAGE_BY_ROLE[first_role]
    MissionRouting.objects.get_or_create(
        mission=mission,
        stage=first_stage,
        action=MissionRouting.ACTION_ROUTED,
        defaults={"actor": actor, "note": "Routed to the " + ROUTE_ROLE_LABELS[first_role]},
    )
    mission.status = "planned"
    mission.approval_status = Mission.APPROVAL_PENDING
    mission.approval_assigned_at = timezone.now()
    mission.save(
        update_fields=["status", "approval_status", "approval_assigned_at", "updated_at"]
    )

    first = route_holder(mission, first_role)
    notify(
        first,
        title=f"Mission {mission.mission_number} needs {ROUTE_ROLE_LABELS[first_role]} review",
        message=mission.title,
        category="approval",
        priority="high",
        related=("mission", mission.id),
        actor=actor,
        requires_action=True,
        action_label="Review and comment",
        action_url=f"/missions/{mission.id}",
    )
    notify_route_higher_authorities(mission, actor=actor)
    return route_plan(mission)


def resubmit_route(mission, *, actor, note: str = "", request=None):
    """Send a returned mission back to the step that asked for the revision.

    The return stays in the history. A fresh ``routed`` row makes the step
    pending again, so the same authority reviews the revised mission and every
    later step keeps waiting.
    """
    from apps.command.models import MissionRouting
    from apps.core.audit import log_action
    from apps.ops.notifications import complete_action, notify

    # Read the stored row: the caller's instance may predate a decision.
    mission.refresh_from_db()
    if mission.approval_status != Mission.APPROVAL_RETURNED:
        raise ApprovalError("Only a mission returned for revision can be resubmitted.")
    if mission.status in {"completed", "cancelled"}:
        raise ApprovalError("This mission is closed.")
    roles = routing_roles(mission)
    if not roles or mission.approving_authority_id is None:
        raise ApprovalError("Select the approving authority before resubmitting this mission.")
    validate_route_holders(mission)
    current = current_route_role(mission)
    if current is None:
        raise ApprovalError("Every step of this route is already approved.")
    holder = route_holder(mission, current)
    if holder is None:
        raise ApprovalError(
            f"No active person holds the {ROUTE_ROLE_LABELS[current]} post."
        )

    stage = ROUTE_STAGE_BY_ROLE[current]
    mission.approval_status = Mission.APPROVAL_PENDING
    mission.status = "planned"
    mission.approval_assigned_at = timezone.now()
    mission.approval_decided_at = None
    mission.approval_note = note
    mission.save(
        update_fields=[
            "approval_status",
            "status",
            "approval_assigned_at",
            "approval_decided_at",
            "approval_note",
            "updated_at",
        ]
    )
    MissionRouting.objects.create(
        mission=mission,
        stage=stage,
        action=MissionRouting.ACTION_ROUTED,
        actor=actor,
        note=note or f"Resubmitted to the {ROUTE_ROLE_LABELS[current]}",
    )
    log_action(
        actor=actor,
        action="mission_resubmit",
        entity_type="mission",
        entity_id=mission.pk,
        after={
            "stage": current,
            "approval_status": mission.approval_status,
            "status": mission.status,
        },
        request=request,
    )
    # The return asked the Operations Center to revise the mission; resubmitting
    # is that action, so the notice that asked for it must not stay pending.
    complete_action(
        related_type="mission",
        related_ids=[mission.pk],
        note=note or "Resubmitted for approval",
        actor=actor,
    )
    notify(
        holder,
        title=f"Mission {mission.mission_number} resubmitted for {ROUTE_ROLE_LABELS[current]} review",
        message=note or mission.title,
        category="approval",
        priority="high",
        related=("mission", mission.id),
        actor=actor,
        requires_action=True,
        action_label="Review and comment",
        action_url=f"/missions/{mission.id}",
    )
    return mission


def _notify_current_step(mission, role, actor=None):
    from apps.ops.notifications import notify

    holder = route_holder(mission, role)
    notify(
        holder,
        title=f"Mission {mission.mission_number} needs {ROUTE_ROLE_LABELS[role]} approval",
        message=mission.title,
        category="approval",
        priority="high",
        related=("mission", mission.id),
        actor=actor,
        requires_action=True,
        action_label="Review and comment",
        action_url=f"/missions/{mission.id}",
    )


def _finalise(mission, *, actor, note: str, request=None):
    """Record the final signature and open the execution gate."""
    from apps.command.models import MissionRouting
    from apps.core.audit import log_action
    from apps.ops import mission_order_workflow
    from apps.ops.notifications import complete_action, notify

    if not mrm_complete(mission):
        raise ApprovalError(
            "This mission's MRM assessment must be completed and approved before "
            "the mission can be approved."
        )
    mission.approval_status = Mission.APPROVAL_APPROVED
    mission.status = "approved"
    mission.approved_by = actor
    mission.approved_at = timezone.now()
    mission.approved_revision = mission.revision
    mission.approval_decided_at = timezone.now()
    mission.approval_note = note
    mission.save(
        update_fields=[
            "approval_status",
            "status",
            "approved_by",
            "approved_at",
            "approved_revision",
            "approval_decided_at",
            "approval_note",
            "updated_at",
        ]
    )
    # Close the approving authority's own task first. Clearing every action on
    # the mission here would also close the signatory notice created below.
    complete_action(
        related_type="mission",
        related_ids=[mission.id],
        recipients=[actor],
        note=f"Approved by {actor.username}",
        actor=actor,
    )
    # The approved mission order is now the signatory's task. The order itself
    # was drafted during planning and travelled with the mission route.
    mission_order_workflow.release_for_signature(mission, actor=actor, request=request)
    # Keep the legacy decision-authority marker for old readers of the history.
    MissionRouting.objects.create(
        mission=mission,
        stage=MissionRouting.STAGE_DECISION_AUTHORITY,
        action=MissionRouting.ACTION_APPROVED,
        actor=actor,
        note=note or "Final approval",
    )
    log_action(
        actor=actor,
        action="mission_approve",
        entity_type="mission",
        entity_id=mission.pk,
        after={"approval_status": mission.approval_status, "status": mission.status, "note": note},
        request=request,
    )
    # Deputy Commander and Commander (or whoever sits above the named authority)
    # are outside the route; they learn the outcome without being given a task.
    notify_higher_authorities_of_approval(mission, actor=actor)
    notify(
        mission.created_by,
        title=f"Mission {mission.mission_number} approved",
        message=note or mission.title,
        level="success",
        category="approval",
        priority="high",
        related=("mission", mission.id),
        actor=actor,
    )
    # Air missions are ready now; ground ones wait for the order signature,
    # which notifies the Operations Center itself when it happens.
    notify_dispatch_ready(mission, actor=actor)
    return mission


@transaction.atomic
def record_route_decision(mission, *, decision, note: str, actor, request=None):
    """Record one authority's comment and decision at the current route step."""
    from apps.command.models import MissionRouting
    from apps.core.audit import log_action
    from apps.ops import mission_order_workflow
    from apps.ops.notifications import complete_action, notify

    if decision not in DECISIONS:
        raise ApprovalError("Decision must be approve, return or reject.")
    note = (note or "").strip()
    if not note:
        raise ApprovalError("Add your comment before recording the routing decision.")

    locked = Mission.objects.select_for_update().get(pk=mission.pk)
    if locked.category == Mission.CATEGORY_AIR:
        raise ApprovalError("Air operations do not use approval routing.")
    if locked.approval_status == Mission.APPROVAL_RETURNED:
        raise ApprovalError(
            "This mission was returned for revision. The Operations Center must "
            "revise and resubmit it before a decision can be recorded."
        )
    roles = routing_roles(locked)
    if not roles:
        raise ApprovalError("Select the approving authority before routing this mission.")
    if not MissionRouting.objects.filter(
        mission=locked,
        stage=MissionRouting.STAGE_OPERATIONS_CENTER,
        action=MissionRouting.ACTION_ROUTED,
    ).exists():
        raise ApprovalError(
            "Approval routing has not started. The Operations Center must start the "
            "configured route before an authority can decide."
        )
    current = current_route_role(locked)
    final_role = roles[-1]
    if current is None:
        # Every intermediate step has signed; only the final authority is left.
        current = final_role
    stage = ROUTE_STAGE_BY_ROLE[current]
    holder = route_holder(locked, current)

    if current == final_role:
        if locked.approving_authority_id != actor.id:
            raise ApprovalError(
                "Only the designated approving authority may give final approval for "
                "this mission."
            )
    elif holder is None or holder.id != actor.id:
        name = getattr(holder, "full_name", "") if holder is not None else "the assigned person"
        raise ApprovalError(
            f"Only {name}, the current {ROUTE_ROLE_LABELS[current]}, may act at this step."
        )

    before = {
        "approval_status": locked.approval_status,
        "status": locked.status,
        "stage": current,
    }

    if decision == DECISION_APPROVE:
        MissionRouting.objects.get_or_create(
            mission=locked,
            stage=stage,
            action=MissionRouting.ACTION_APPROVED,
            defaults={"actor": actor, "note": note},
        )
        if current == final_role:
            _finalise(locked, actor=actor, note=note, request=request)
        else:
            next_role = roles[roles.index(current) + 1]
            # The actor has finished their step: close their work item before
            # creating the next authority's action, otherwise it lingers in the
            # bell even though the route has already moved on.
            complete_action(
                related_type="mission",
                related_ids=[locked.id],
                recipients=[actor],
                note=f"Forwarded to {ROUTE_ROLE_LABELS[next_role]}",
                actor=actor,
            )
            MissionRouting.objects.get_or_create(
                mission=locked,
                stage=ROUTE_STAGE_BY_ROLE[next_role],
                action=MissionRouting.ACTION_ROUTED,
                defaults={
                    "actor": actor,
                    "note": f"Forwarded from {ROUTE_ROLE_LABELS[current]}",
                },
            )
            locked.approval_status = Mission.APPROVAL_PENDING
            locked.approval_assigned_at = timezone.now()
            locked.approval_note = note
            locked.save(
                update_fields=[
                    "approval_status",
                    "approval_assigned_at",
                    "approval_note",
                    "updated_at",
                ]
            )
            _notify_current_step(locked, next_role, actor=actor)
            notify_route_higher_authorities(locked, actor=actor)
    else:
        MissionRouting.objects.create(
            mission=locked,
            stage=stage,
            action=(
                MissionRouting.ACTION_RETURNED
                if decision == DECISION_RETURN
                else MissionRouting.ACTION_REJECTED
            ),
            actor=actor,
            note=note,
        )
        locked.approval_status = (
            Mission.APPROVAL_RETURNED if decision == DECISION_RETURN else Mission.APPROVAL_REJECTED
        )
        locked.status = "planned" if decision == DECISION_RETURN else "cancelled"
        locked.approval_decided_at = timezone.now()
        locked.approval_note = note
        locked.approved_revision = 0
        locked.save(
            update_fields=[
                "approval_status",
                "status",
                "approval_decided_at",
                "approval_note",
                "approved_revision",
                "updated_at",
            ]
        )
        mission_order_workflow.reset(locked)
        complete_action(
            related_type="mission",
            related_ids=[locked.id],
            note=note,
            actor=actor,
        )
        notify(
            locked.created_by,
            title=f"Mission {locked.mission_number} {locked.approval_status}",
            message=note,
            level="warning",
            category="approval",
            priority="high",
            related=("mission", locked.id),
            actor=actor,
            requires_action=decision == DECISION_RETURN,
            action_label="Revise and resubmit" if decision == DECISION_RETURN else "",
            action_url=f"/missions/{locked.id}",
        )

    log_action(
        actor=actor,
        action=f"route_{decision}_{current}",
        entity_type="mission",
        entity_id=locked.pk,
        before=before,
        after={
            "stage": current,
            "decision": decision,
            "note": note,
            "approval_status": locked.approval_status,
            "status": locked.status,
        },
        request=request,
    )
    return locked


def resolve_authority(mission, role, user_id):
    """Validate an authority choice and return the person it refers to."""
    from apps.accounts.models import User

    if role not in AUTHORITY_ROLES:
        raise ApprovalError(
            "Select the approving authority's post: Commander, Deputy Commander, "
            "Director for Operations or Operations Officer."
        )
    if user_id in (None, ""):
        raise ApprovalError("Select the person who will approve this mission.")

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        raise ApprovalError("Invalid approving authority selection.") from None

    user = User.objects.select_related("unit").filter(pk=user_id).first()
    if user is None:
        raise ApprovalError("The selected approving authority no longer exists.")
    if not user.is_active:
        raise ApprovalError("The selected approving authority's account is inactive.")
    if mission.created_by_id == user.id:
        raise ApprovalError("A mission creator cannot approve their own mission.")
    if role not in _authority_roles_held(user, mission.unit_id):
        raise ApprovalError(
            f"{user.full_name or user.username} does not hold the "
            f"{AUTHORITY_LABELS.get(role, role)} post for this mission's unit."
        )
    return user


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


@transaction.atomic
def assign_authority(
    mission,
    *,
    role,
    user_id,
    actor,
    note: str = "",
    request=None,
):
    """Assign (or explicitly reassign) the mission's approving authority."""
    from apps.core.audit import log_action
    from apps.ops import mission_order_workflow
    from apps.ops.notifications import notify

    locked = Mission.objects.select_for_update().get(pk=mission.pk)
    if locked.category == Mission.CATEGORY_AIR:
        raise ApprovalError("Air operations do not use an approving authority.")
    user = resolve_authority(locked, role, user_id)

    before = {
        "approving_authority": locked.approving_authority_id,
        "approving_authority_role": locked.approving_authority_role,
        "approval_status": locked.approval_status,
    }
    reassignment = locked.approving_authority_id not in (None, user.id)

    locked.approving_authority = user
    locked.approving_authority_role = role
    locked.approval_status = Mission.APPROVAL_PENDING
    locked.approval_assigned_at = timezone.now()
    locked.approval_decided_at = None
    locked.approval_note = note
    # A new approver has not approved anything yet, whatever the previous one
    # decided. The old decision stays in the routing history and audit trail.
    locked.approved_by = None
    locked.approved_at = None
    locked.approved_revision = 0
    # The Mission Order was part of the approval that has just been reset.
    mission_order_workflow.reset(locked, save=False)
    locked.save(
        update_fields=[
            "approving_authority",
            "approving_authority_role",
            "approval_status",
            "approval_assigned_at",
            "approval_decided_at",
            "approval_note",
            "approved_by",
            "approved_at",
            "approved_revision",
            "mission_order_status",
            "mission_order_assigned_at",
            "mission_order_signed_at",
            "mission_order_signed_by",
            "mission_order_note",
            "mission_order_signed_revision",
            "mission_order_signer_designation",
            "mission_order_signer_as_commander",
            "mission_order_signer_name",
            "mission_order_signer_rank",
            "mission_order_signer_branch",
            "updated_at",
        ]
    )

    log_action(
        actor=actor,
        action="reassign_approver" if reassignment else "assign_approver",
        entity_type="mission",
        entity_id=locked.pk,
        before=before,
        after={
            "approving_authority": user.id,
            "approving_authority_role": role,
            "approval_status": locked.approval_status,
            "note": note,
        },
        request=request,
    )

    notify(
        user,
        title=f"Mission {locked.mission_number} awaits your approval",
        message=locked.title,
        category="approval",
        priority="high",
        related=("mission", locked.id),
        actor=actor,
        requires_action=True,
        action_label="Review and approve",
        action_url=f"/missions/{locked.id}",
    )
    return locked


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@transaction.atomic
def record_decision(mission, *, decision, note: str, actor, request=None):
    """Approve, return or reject - only the assigned authority may."""
    from apps.command.models import MissionRouting
    from apps.core.audit import log_action
    from apps.ops import mission_order_workflow
    from apps.ops.notifications import complete_action, notify

    if decision not in DECISIONS:
        raise ApprovalError("Decision must be approve, return or reject.")

    locked = Mission.objects.select_for_update().get(pk=mission.pk)

    if locked.category == Mission.CATEGORY_AIR:
        raise ApprovalError("Air operations do not use an approving authority.")
    if locked.approving_authority_id is None:
        raise ApprovalError("This mission has no approving authority assigned yet.")
    if locked.approving_authority_id != actor.id:
        assigned = locked.approving_authority
        name = getattr(assigned, "full_name", "") or getattr(assigned, "username", "")
        raise ApprovalError(
            f"Only the assigned approving authority ({name}) may decide this mission. "
            "An authority change must be recorded by the Operations Center."
        )
    if locked.approval_status == Mission.APPROVAL_APPROVED:
        raise ApprovalError("This mission has already been approved.")
    if locked.status in {"completed", "cancelled"}:
        raise ApprovalError("This mission can no longer be approved.")
    if locked.approval_status == Mission.APPROVAL_RETURNED:
        raise ApprovalError(
            "This mission was returned for revision. The Operations Center must "
            "revise and resubmit it before a decision can be recorded."
        )

    note = (note or "").strip()
    if decision in {DECISION_RETURN, DECISION_REJECT} and not note:
        raise ApprovalError(
            "Explain why the mission is being returned or rejected."
        )

    # Approval certifies a mission that is ready to execute. Without an evaluated
    # and authority-approved MRM that certification would be false, so the
    # decision is refused rather than merely warned about.
    if decision == DECISION_APPROVE and not mrm_complete(locked):
        raise ApprovalError(
            "This mission's MRM assessment must be completed and approved before "
            "the mission can be approved."
        )

    before = {
        "approval_status": locked.approval_status,
        "status": locked.status,
        "revision": locked.revision,
    }

    if decision == DECISION_APPROVE:
        locked.approving_authority  # noqa: B018 - keep the FK cached for the response
        locked.approval_status = Mission.APPROVAL_APPROVED
        locked.status = "approved"
        locked.approved_by = actor
        locked.approved_at = timezone.now()
        # The approval covers exactly the revision that was reviewed.
        locked.approved_revision = locked.revision
    elif decision == DECISION_RETURN:
        locked.approval_status = Mission.APPROVAL_RETURNED
        locked.status = "planned"
        locked.approved_revision = 0
    else:
        locked.approval_status = Mission.APPROVAL_REJECTED
        locked.status = "cancelled"
        locked.approved_revision = 0

    locked.approval_decided_at = timezone.now()
    locked.approval_note = note
    locked.save(
        update_fields=[
            "approval_status",
            "status",
            "approved_by",
            "approved_at",
            "approved_revision",
            "approval_decided_at",
            "approval_note",
            "updated_at",
        ]
    )

    if decision == DECISION_APPROVE:
        mission_order_workflow.release_for_signature(
            locked, actor=actor, request=request
        )
    else:
        # Returning or rejecting the mission withdraws the order from the
        # signatory; it will be routed again after a fresh mission approval.
        mission_order_workflow.reset(locked)

    MissionRouting.objects.create(
        mission=locked,
        stage=MissionRouting.STAGE_DECISION_AUTHORITY,
        action={
            DECISION_APPROVE: MissionRouting.ACTION_APPROVED,
            DECISION_RETURN: MissionRouting.ACTION_RETURNED,
            DECISION_REJECT: MissionRouting.ACTION_REJECTED,
        }[decision],
        actor=actor,
        note=note or f"{AUTHORITY_LABELS.get(locked.approving_authority_role, 'Approver')} decision",
    )

    log_action(
        actor=actor,
        action=f"mission_{decision}",
        entity_type="mission",
        entity_id=locked.pk,
        before=before,
        after={
            "approval_status": locked.approval_status,
            "status": locked.status,
            "note": note,
        },
        request=request,
    )

    if decision == DECISION_APPROVE:
        complete_action(
            related_type="mission",
            related_ids=[locked.id],
            note=f"Approved by {actor.username}",
            actor=actor,
        )

    notify(
        locked.created_by,
        title=f"Mission {locked.mission_number} {locked.approval_status}",
        message=note or locked.title,
        level="success" if decision == DECISION_APPROVE else "warning",
        category="approval",
        priority="high",
        related=("mission", locked.id),
        actor=actor,
        requires_action=decision == DECISION_RETURN,
        action_label="Revise and resubmit" if decision == DECISION_RETURN else "",
        action_url=f"/missions/{locked.id}",
    )
    return locked


# ---------------------------------------------------------------------------
# Execution gate
# ---------------------------------------------------------------------------


def revision_changed(mission) -> bool:
    """An approval exists but the mission changed afterwards."""
    return (
        mission.approval_status == Mission.APPROVAL_APPROVED
        and mission.approved_revision != mission.revision
    )


def approval_display(mission) -> dict:
    """Readable approval state, used by the detail view and the monitoring table."""
    status = mission.approval_status
    label = APPROVAL_LABELS.get(status, status)
    if revision_changed(mission):
        label = "Reapproval required"
    authority = mission.approving_authority
    # "Returned by" must name the post that actually returned the mission, not
    # the approving authority the mission was addressed to. The routing log is
    # the record of who acted.
    from apps.command.models import MissionRouting

    returned = (
        mission.routing.filter(
            action__in=[MissionRouting.ACTION_RETURNED, MissionRouting.ACTION_REJECTED]
        )
        .select_related("actor")
        .order_by("-created_at", "-id")
        .first()
    )
    returned_actor = returned.actor if returned else None
    return {
        "status": status,
        "label": label,
        "revision_changed": revision_changed(mission),
        "authority_id": mission.approving_authority_id,
        "authority_name": (
            (getattr(authority, "full_name", "") or getattr(authority, "username", ""))
            if authority
            else ""
        ),
        "authority_rank": getattr(authority, "rank", "") if authority else "",
        "authority_role": mission.approving_authority_role,
        "authority_role_label": AUTHORITY_LABELS.get(
            mission.approving_authority_role, ""
        ),
        "assigned_at": mission.approval_assigned_at,
        "decided_at": mission.approval_decided_at,
        "note": mission.approval_note,
        "returned_by_id": returned_actor.id if returned_actor else None,
        "returned_by_name": (
            (returned_actor.full_name or returned_actor.username) if returned_actor else ""
        ),
        "returned_by_stage": returned.stage if returned else "",
        "returned_by_stage_label": (
            dict(MissionRouting.STAGE_CHOICES).get(returned.stage, "") if returned else ""
        ),
        "returned_action": returned.action if returned else "",
        "revision": mission.revision,
        "approved_revision": mission.approved_revision,
    }


def _planned_departure(mission):
    """The mission's planned start, from the category detail where available."""
    if mission.category == Mission.CATEGORY_AIR:
        detail = getattr(mission, "air_detail", None)
        return getattr(detail, "scheduled_departure", None) or mission.start_at
    return mission.start_at


def dependency_state(mission) -> dict:
    """The predecessor's release state and the earliest departure it allows."""
    predecessor = mission.depends_on
    if predecessor is None:
        return {"enabled": False}

    release = mission.dependency_release or Mission.DEPENDENCY_COMPLETION
    air = getattr(predecessor, "air_detail", None)
    eta = getattr(air, "scheduled_arrival", None)
    actual_arrival = getattr(air, "actual_arrival", None)
    rendezvous_ready_at = getattr(air, "rendezvous_ready_at", None)
    completed_at = predecessor.completed_at
    buffer_minutes = int(mission.dependency_buffer_minutes or 0)

    if release == Mission.DEPENDENCY_COMPLETION:
        satisfied = predecessor.status == "completed"
        release_at = completed_at
    else:
        satisfied = bool(rendezvous_ready_at) or predecessor.status == "completed"
        release_at = rendezvous_ready_at or completed_at

    if predecessor.status == "cancelled":
        state = "failed"
    elif satisfied:
        state = "ready"
    else:
        state = "waiting"

    # Before the predecessor is released, its planned ETA is still useful for
    # planning; after release, the actual time is what the next departure uses.
    base_at = release_at or actual_arrival or eta
    earliest_departure = (
        base_at + dt.timedelta(minutes=buffer_minutes) if base_at is not None else None
    )

    release_labels = dict(Mission.DEPENDENCY_RELEASE_CHOICES)
    if state == "failed":
        label = f"Mission {predecessor.mission_number} was cancelled."
    elif state == "ready":
        label = f"Mission {predecessor.mission_number} has released this mission."
    elif release == Mission.DEPENDENCY_COMPLETION:
        label = f"Waiting for mission {predecessor.mission_number} to complete."
    else:
        label = (
            f"Pending aircraft arrival: waiting for mission "
            f"{predecessor.mission_number} to arrive and be rendezvous-ready."
        )

    return {
        "enabled": True,
        "id": predecessor.pk,
        "mission_number": predecessor.mission_number,
        "title": predecessor.title,
        "status": predecessor.status,
        "status_label": predecessor.get_status_display(),
        "release": release,
        "release_label": release_labels.get(release, release),
        "buffer_minutes": buffer_minutes,
        "eta": eta,
        "actual_arrival": actual_arrival,
        "rendezvous_ready_at": rendezvous_ready_at,
        "completed_at": completed_at,
        "earliest_departure": earliest_departure,
        "satisfied": satisfied,
        "state": state,
        "label": label,
    }


def dependency_blockers(mission) -> list[dict]:
    """The sequencing gate: a dependent mission waits for its predecessor."""
    state = dependency_state(mission)
    if not state.get("enabled"):
        return []
    if state["state"] == "failed":
        return [
            {
                "code": "dependency_failed",
                "label": "Preposition mission cancelled",
                "detail": state["label"],
            }
        ]
    if not state["satisfied"]:
        return [
            {
                "code": "dependency_waiting",
                "label": (
                    "Pending aircraft arrival"
                    if state["release"] == Mission.DEPENDENCY_RENDEZVOUS_READY
                    else "Waiting on preposition mission"
                ),
                "detail": state["label"],
            }
        ]

    earliest = state.get("earliest_departure")
    planned = _planned_departure(mission)
    if earliest is not None and planned is not None and planned < earliest:
        return [
            {
                "code": "dependency_buffer",
                "label": "Preposition buffer not met",
                "detail": (
                    f"The planned departure is before {earliest:%d %b %Y, %H%M}H, "
                    f"the predecessor's release time plus {state['buffer_minutes']} "
                    "minutes. Move the departure later before dispatching."
                ),
            }
        ]
    return []


def formation_blockers(mission) -> list[dict]:
    """Re-check the 5 km Element rule against the aircraft's live locations.

    This runs at dispatch time, not only when the plan is saved. A mission that
    depends on a preposition is released from the dependency gate only when the
    configured release condition is met, but it still may not launch while the
    register shows the aircraft more than 5 km apart.
    """
    if mission.category != Mission.CATEGORY_AIR:
        return []
    detail = getattr(mission, "air_detail", None)
    if detail is None:
        return []
    analysis = formation_rules.analyze_instance(detail)
    if not (analysis["checked"] and analysis["separated"]):
        return []
    if mission.depends_on_id is None:
        return [
            {
                "code": "formation_not_colocated",
                "label": "Aircraft not co-located",
                "detail": (
                    "The aircraft are recorded more than 5 km apart. Create a "
                    "prepositioning mission and link it before dispatch."
                ),
            }
        ]
    state = dependency_state(mission)
    if not state.get("satisfied"):
        # The dependency gate already gives the operator the precise hold state.
        return []
    return [
        {
            "code": "formation_not_colocated",
            "label": "Arrival location not verified",
            "detail": (
                "The preposition mission is complete, but the register still "
                "places the formation more than 5 km apart. Update the aircraft "
                "location at the assembly point before dispatch."
            ),
        }
    ]


def execution_blockers(mission) -> list[dict]:
    """Everything still standing between this mission and execution."""
    blockers: list[dict] = []

    # Air operations monitor the flight itself: the air form records no MRM
    # team or assessment, so an air mission is not held for one. Ground
    # operations keep the MRM prerequisite.
    if mission.category != Mission.CATEGORY_AIR and not mrm_complete(mission):
        blockers.append(
            {
                "code": "mrm_incomplete",
                "label": "MRM not completed",
                "detail": (
                    "The MRM assessment must be evaluated and approved before this "
                    "mission can be executed."
                ),
            }
        )

    # Air operations carry no approving authority: the Operations Center plans
    # the flight and monitoring begins once it is dispatched. Ground operations
    # keep the mission-details approval prerequisite.
    if mission.category != Mission.CATEGORY_AIR:
        if mission.approval_status != Mission.APPROVAL_APPROVED:
            detail = {
                Mission.APPROVAL_NOT_ROUTED: "The mission has not been routed to an approving authority.",
                Mission.APPROVAL_PENDING: "The approving authority has not decided yet.",
                Mission.APPROVAL_RETURNED: "The mission was returned for revision and must be resubmitted.",
                Mission.APPROVAL_REJECTED: "The mission was rejected and cannot be executed.",
            }.get(
                mission.approval_status,
                "The mission has no valid approval.",
            )
            blockers.append(
                {
                    "code": "approval_incomplete",
                    "label": "Mission not approved",
                    "detail": detail,
                }
            )
        elif revision_changed(mission):
            blockers.append(
                {
                    "code": "approval_stale",
                    "label": "Reapproval required",
                    "detail": (
                        "The mission changed after it was approved, so the approval no "
                        "longer covers it. Send it back to the approving authority."
                    ),
                }
            )

    # The Mission Order is the third dispatch prerequisite, alongside the MRM
    # and the mission-details approval. It is never inferred from approval.
    from apps.ops import mission_order_workflow

    order_blocker = mission_order_workflow.execution_blocker(mission)
    if order_blocker is not None:
        blockers.append(order_blocker)

    blockers.extend(dependency_blockers(mission))
    blockers.extend(formation_blockers(mission))
    return blockers


def readiness(mission) -> dict:
    """Execution readiness for the monitoring table (spec §4.3)."""
    blockers = execution_blockers(mission)
    status = mission.status

    if status in {"completed", "cancelled"}:
        label = "Completed" if status == "completed" else "Cancelled"
        state = status
    elif status == "active":
        label = "In progress"
        state = "in_progress"
    elif blockers:
        label = "Blocked"
        state = "blocked"
    else:
        label = "Ready for dispatch"
        state = "ready"

    return {
        "state": state,
        "label": label,
        "ready": not blockers and status not in {"completed", "cancelled", "active"},
        "blockers": blockers,
    }


def assert_dispatchable(mission) -> None:
    """The one rule every execution path calls."""
    blockers = execution_blockers(mission)
    if blockers:
        raise ExecutionBlocked(blockers)


def bump_revision(mission, fields) -> bool:
    """Record a material change and drop any approval it invalidates.

    Returns True when the revision moved so the caller can tell the user their
    approval no longer covers the mission.
    """
    if not mission.pk:
        return False
    material = set(fields) & Mission.MATERIAL_FIELDS
    if not material:
        return False
    previous = mission.revision
    mission.revision = previous + 1
    return True

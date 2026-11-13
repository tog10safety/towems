"""Who may see, delete and filter MRM assessments.

The unit's read policy for the risk register:

======================================  ======================================
Caller                                  Sees
======================================  ======================================
Administrator                           every MRM, in every active unit
Unit Safety Officer                     own unit and subordinates (plus the
                                        units they are assigned to)
Commander, Deputy Commander, Director   own unit and subordinates
for Operations, Operations Officer,
Operations Center member
Everyone else                           only the MRMs they raised, were
                                        assigned to (evaluator or routed
                                        decision authority), or were tagged on
======================================  ======================================

Deletion is narrower than reading: the Administrator may delete any MRM, the
Unit Safety Officer only one that has not been approved. Nothing else may
delete. Deleting removes the legacy rows that hang off the form (hazards,
personnel, feedback), the TOWEMS mission link and the stored approved PDF, so no
dangling reference is left behind, and it is written to both audit trails
first.
"""

from __future__ import annotations

from django.db.models import Q

from apps.core.permissions import ADMIN, UNIT_SAFETY
from apps.core.roles import (
    ROLE_COMMANDER,
    ROLE_DEPUTY_COMMANDER,
    ROLE_DIRECTOR_OPERATIONS,
    ROLE_OPERATIONS_CENTER,
    ROLE_OPERATIONS_OFFICER,
    ROLE_SAFETY_OFFICER,
    active_acting_appointments,
    active_appointments,
    active_office_roles,
    effective_roles,
)
from apps.core.scoping import accessible_unit_ids

#: Posts that carry oversight of their unit's risk register.
OVERSEER_ROLES = frozenset(
    {
        ROLE_COMMANDER,
        ROLE_DEPUTY_COMMANDER,
        ROLE_DIRECTOR_OPERATIONS,
        ROLE_OPERATIONS_OFFICER,
    }
)


def _authenticated(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False))


def is_administrator(user) -> bool:
    return _authenticated(user) and getattr(user, "authority", None) == ADMIN


def is_unit_safety(user) -> bool:
    return _authenticated(user) and getattr(user, "authority", None) == UNIT_SAFETY


def is_safety_officer(user) -> bool:
    """The Safety Officer role, whether held by account or by unit post.

    Deletion keeps the narrower :func:`is_unit_safety` rule; this wider check
    decides the read scope of the register and the safety audit, because a
    Safety Officer appointment is a real safety-office role in the role
    catalogue.
    """
    if not _authenticated(user):
        return False
    if getattr(user, "authority", None) == UNIT_SAFETY:
        return True
    return ROLE_SAFETY_OFFICER in effective_roles(user)


# ---------------------------------------------------------------------------
# The posts a caller holds
# ---------------------------------------------------------------------------


def overseer_posts(user) -> list[str]:
    """Which oversight posts the caller currently holds, for the API's hints."""
    if not _authenticated(user):
        return []
    posts = {row["role"] for row in active_appointments(user)} & OVERSEER_ROLES
    posts |= {
        row.acting_role for row in active_acting_appointments(user)
    } & OVERSEER_ROLES
    offices = [
        row
        for row in active_office_roles(user)
        if row.get("role") == ROLE_OPERATIONS_CENTER
    ]
    if offices:
        posts.add(ROLE_OPERATIONS_CENTER)
    return sorted(posts)


def holds_oversight(user) -> bool:
    return bool(overseer_posts(user))


def oversight_unit_ids(user) -> set[int]:
    """The caller's own unit plus subordinates, and each post's own unit tree.

    A post held at a parent command reaches its subordinates - that is how the
    unit records command oversight - while the caller's own unit is always
    included, as the policy says "own unit and subordinate".
    """
    from apps.command.services import descendant_ids

    roots: set[int] = set()
    for row in active_appointments(user):
        if row["role"] in OVERSEER_ROLES and row.get("unit_id"):
            roots.add(row["unit_id"])
    for row in active_acting_appointments(user):
        if row.acting_role in OVERSEER_ROLES and row.unit_id:
            roots.add(row.unit_id)
    for row in active_office_roles(user):
        if row.get("role") == ROLE_OPERATIONS_CENTER and row.get("unit_id"):
            roots.add(row["unit_id"])
    if getattr(user, "unit_id", None):
        roots.add(user.unit_id)

    units: set[int] = set()
    for root in roots:
        units |= descendant_ids(root)
    return units


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def personal_mrm_filter(user) -> Q:
    """MRMs the caller is personally attached to.

    "Raised, assigned or tagged": the creator, the assigned evaluator, the
    decision authority the risk band routed to, and anyone tagged for
    post-mission feedback.
    """
    return (
        Q(created_by_id=user.id)
        | Q(reviewed_by_id=user.id)
        | Q(authority_assigned_to_id=user.id)
        | Q(tagged_personnel__user_id=user.id, tagged_personnel__is_active=True)
    )


def safety_scope_ids(user) -> set[int]:
    """The Safety Officer's own/assigned units and their subordinates.

    ``accessible_unit_ids`` already covers the account's primary unit and
    assignments. A Safety Officer *post* held at another unit adds that unit's
    tree as well, so an appointed officer does not lose the post's scope just
    because their account's primary unit is elsewhere.
    """
    from apps.command.services import descendant_ids

    unit_ids = accessible_unit_ids(user)
    for row in active_appointments(user):
        if row["role"] == ROLE_SAFETY_OFFICER and row.get("unit_id"):
            unit_ids |= descendant_ids(row["unit_id"])
    for row in active_acting_appointments(user):
        if row.acting_role == ROLE_SAFETY_OFFICER and row.unit_id:
            unit_ids |= descendant_ids(row.unit_id)
    return unit_ids


def unit_scope_ids(user) -> set[int]:
    """Unit ids the caller's read policy covers; empty when personal only."""
    if is_administrator(user):
        return set()
    if is_safety_officer(user):
        return safety_scope_ids(user)
    if holds_oversight(user):
        return oversight_unit_ids(user)
    return set()


def visible_mrms(user, queryset=None):
    """The MRM queryset ``user`` may read."""
    from apps.legacy.models import MRMForm

    base = queryset if queryset is not None else MRMForm.objects.all()
    if not _authenticated(user):
        return base.none()
    if is_administrator(user):
        return base

    unit_ids = unit_scope_ids(user)
    if unit_ids:
        # Rows with no captured unit are kept readable for the oversight roles:
        # there is no unit to test them against, and hiding them would lose
        # records the legacy import never scoped.
        return base.filter(
            Q(original_unit_id__in=unit_ids) | Q(original_unit_id__isnull=True)
        ).distinct()
    return base.filter(personal_mrm_filter(user)).distinct()


def filter_units(user):
    """The units offered in the register's unit filter."""
    from apps.legacy.models import MRMForm, Unit

    from apps.core.scoping import hidden_unit_ids

    units = Unit.objects.filter(is_active=True).exclude(id__in=hidden_unit_ids(user))
    if is_administrator(user):
        return units.order_by("name")

    unit_ids = unit_scope_ids(user)
    if not unit_ids:
        # Personal scope: only the units the caller's own MRMs belong to.
        unit_ids = set(
            visible_mrms(user)
            .exclude(original_unit_id__isnull=True)
            .values_list("original_unit_id", flat=True)
        )
    if not unit_ids:
        return units.none()
    return units.filter(id__in=unit_ids).order_by("name")


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


def can_delete(user, form) -> tuple[bool, str]:
    """Whether ``user`` may delete ``form``, and the reason when not.

    Permanent deletion of an assessment - approved or not - is the Super
    Administrator's decision alone. Every other account, the Administrator role
    included, keeps the review, evaluation and approval workflow and edits the
    record instead.
    """
    from apps.accounts.ownership import can_delete_records

    if can_delete_records(user):
        return True, ""
    return False, (
        "Only the Super Administrator may permanently delete an MRM assessment."
    )


def delete_mrm(form, *, actor, request=None) -> dict:
    """Delete one MRM and everything that hangs off it.

    Returns the counts that were removed, which the bulk endpoint and the audit
    note both use. The legacy tables carry no database cascade (``DO_NOTHING``
    with ``db_constraint=False``), so each dependent row is removed explicitly.
    """
    from django.db import transaction

    from apps.core.audit import log_action
    from apps.core.legacy_audit import record_activity
    from apps.legacy.models import MRMFeedback, MRMPersonnel, MissionHazards
    from apps.ops import pdf as pdf_service
    from apps.ops.models import Mission, RiskAssessment

    pdf_path = pdf_service.stored_pdf_path(form)
    before = {
        "mrm_number": form.mrm_number,
        "activity_mission": form.activity_mission,
        "status": form.status,
        "review_status": form.review_status,
        "authority_approved": bool(form.authority_approved),
        "total_percent": form.total_percent,
        "original_unit_id": form.original_unit_id,
        "created_by_id": form.created_by_id,
    }

    with transaction.atomic():
        missions_detached = Mission.objects.filter(risk_form_id=form.id).update(
            risk_form=None
        )
        links = RiskAssessment.objects.filter(mrm_form_id=form.id)
        mission_links = links.count()
        links.delete()
        hazards = MissionHazards.objects.filter(mrm_id=form.id).delete()[0]
        personnel = MRMPersonnel.objects.filter(mrm_id=form.id).delete()[0]
        feedback = MRMFeedback.objects.filter(mrm_id=form.id).delete()[0]
        from apps.ops.models import MRMAttentionDismissal

        MRMAttentionDismissal.objects.filter(mrm_form_id=form.id).delete()
        form_id = form.pk
        form.delete()

    if pdf_path is not None:
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - a locked file must not fail the delete
            pdf_path = None

    log_action(
        actor=actor,
        action="delete",
        entity_type="mrm_form",
        entity_id=form_id,
        before=before,
        note=f"Deleted MRM {before['mrm_number']}",
        request=request,
    )
    record_activity(
        user=actor,
        activity_type="delete_mrm",
        module="mrm",
        description=f"Deleted MRM {before['mrm_number']}",
        request=request,
        resource_id=form_id,
        resource_type="mrm_form",
    )
    return {
        "id": form_id,
        "mrm_number": before["mrm_number"],
        "hazards": hazards,
        "personnel": personnel,
        "feedback": feedback,
        "mission_links": mission_links,
        "missions_detached": missions_detached,
        "pdf_removed": pdf_path is not None,
    }

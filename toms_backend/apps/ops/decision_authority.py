"""Who signs an MRM, from its residual risk and the unit's command structure.

The unit's policy, as specified:

===================  =========================================================
Residual risk        Decision Authority
===================  =========================================================
50% and below        The creator who filled up the MRM signs
51 - 65%             The supervisor or evaluator signs
66 - 75%             The Squadron Commander signs; a unit with no Squadron
                     Commander routes to its Director for Operations
76 - 85%             The Director for Operations signs
86 - 100%            The Commander signs
===================  =========================================================

"Designated" means a post held in ``UnitAppointment`` (Commander, Director for
Operations, ...) or a user whose free-text ``designation`` names the post - the
two ways the unit actually records appointments. Lookup stays inside the MRM's
own unit, except that Director and Commander may be found at a parent unit,
which is how a squadron inherits its group's director.

Nothing here decides the *risk*: ``apps.ops.risk`` still computes the
percentage. This module only maps that percentage onto a signer.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bands, in ascending risk order.
CREATOR = "creator"
SUPERVISOR = "supervisor"
SQUADRON_COMMANDER = "squadron_commander"
DIRECTOR = "director_operations"
COMMANDER = "commander"

BAND_LABELS = {
    CREATOR: "Creator",
    SUPERVISOR: "Supervisor / Evaluator",
    SQUADRON_COMMANDER: "Squadron Commander",
    DIRECTOR: "Director for Operations",
    COMMANDER: "Commander",
}


@dataclass(frozen=True)
class DecisionAuthority:
    band: str
    label: str
    user: object | None
    reason: str

    @property
    def resolved(self) -> bool:
        return self.user is not None


def band_for(total_percent) -> str:
    """The policy ladder. A missing percentage is treated as the lowest band."""
    try:
        percent = float(total_percent)
    except (TypeError, ValueError):
        return CREATOR
    if percent <= 50:
        return CREATOR
    if percent <= 65:
        return SUPERVISOR
    if percent <= 75:
        return SQUADRON_COMMANDER
    if percent <= 85:
        return DIRECTOR
    return COMMANDER


def label_for(band: str) -> str:
    return BAND_LABELS.get(band, band)


# ---------------------------------------------------------------------------
# Finding the person who holds the post
# ---------------------------------------------------------------------------


def _user(user_id):
    if not user_id:
        return None
    from apps.accounts.models import User

    return User.objects.filter(pk=user_id, is_active=True).first()


def _appointment(unit, roles):
    """The active holder of one of ``roles`` in ``unit``."""
    if unit is None:
        return None
    from apps.command.models import UnitAppointment

    row = (
        UnitAppointment.objects.filter(unit_id=unit.id, role__in=roles, is_active=True)
        .select_related("user")
        .order_by("id")
        .first()
    )
    if row is None or row.user is None or not row.user.is_active:
        return None
    return row.user


def _designated(unit, needles, excludes=()):
    """A user in ``unit`` whose designation names the post."""
    if unit is None:
        return None
    from apps.accounts.models import User

    for user in (
        User.objects.filter(unit_id=unit.id, is_active=True)
        .exclude(designation__isnull=True)
        .exclude(designation="")
        .order_by("last_name", "first_name")
    ):
        text = (user.designation or "").lower()
        if any(needle in text for needle in needles) and not any(
            blocked in text for blocked in excludes
        ):
            return user
    return None


def _unit_chain(unit, depth: int = 3):
    """The unit, then its parents - how a squadron reaches its group's director."""
    seen = set()
    node = unit
    while node is not None and node.id not in seen and len(seen) < depth:
        seen.add(node.id)
        yield node
        node = node.parent


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve(form, *, cache: dict | None = None) -> DecisionAuthority:
    """The decision authority for ``form`` at its current residual risk."""
    from apps.legacy.models import Unit

    from apps.core import roles as core_roles

    band = band_for(form.total_percent)
    unit = Unit.objects.filter(pk=form.original_unit_id).first() if form.original_unit_id else None
    key = (getattr(unit, "id", None), band)
    if cache is not None and key in cache:
        cached = cache[key]
        # The creator/evaluator bands depend on the form, not just the unit.
        if band not in (CREATOR, SUPERVISOR):
            return cached

    user = None
    reason = ""

    if band == CREATOR:
        user = _user(form.created_by_id)
        reason = "The creator signs an assessment at or below 50%."
    elif band == SUPERVISOR:
        user = _user(form.reviewed_by_id)
        if user is not None:
            reason = "The assigned evaluator signs as supervisor for this band."
        else:
            user = _appointment(unit, [core_roles.ROLE_SAFETY_OFFICER])
            reason = "No evaluator is assigned, so the unit safety officer signs."
            if user is None:
                user = _appointment(unit, [core_roles.ROLE_OPERATIONS_OFFICER])
                reason = "No evaluator or safety officer, so the operations officer signs."
    elif band == SQUADRON_COMMANDER:
        # The unit's own commander when it is a squadron; otherwise the nearest
        # squadron above it, which is the one a flight answers to.
        for node in _unit_chain(unit):
            if (node.category or "").lower() != "squadron":
                continue
            user = _appointment(node, [core_roles.ROLE_COMMANDER])
            if user is not None:
                reason = (
                    "The unit is a squadron; its Commander is the Squadron Commander."
                    if node.id == getattr(unit, "id", None)
                    else f"{node.name} is the parent squadron; its Commander signs."
                )
                break
        if user is None:
            user = _designated(unit, ["squadron commander", "squadron cmdr"])
            reason = "A member designated Squadron Commander signs."
        if user is None:
            user = _appointment(
                _parent_for(unit, core_roles.ROLE_DIRECTOR_OPERATIONS), [core_roles.ROLE_DIRECTOR_OPERATIONS]
            )
            if user is None:
                user = _appointment(unit, [core_roles.ROLE_DIRECTOR_OPERATIONS])
            reason = (
                "This unit has no Squadron Commander, so the request routes to the "
                "Director for Operations."
            )
    elif band == DIRECTOR:
        for node in _unit_chain(unit):
            user = _appointment(node, [core_roles.ROLE_DIRECTOR_OPERATIONS])
            if user is not None:
                reason = "The Director for Operations signs this band."
                break
        if user is None:
            user = _designated(unit, ["director for operations", "director"])
            if user is not None:
                reason = "A member designated Director for Operations signs."
    else:  # COMMANDER
        for node in _unit_chain(unit):
            user = _appointment(node, [core_roles.ROLE_COMMANDER])
            if user is not None:
                reason = "The Commander signs this band."
                break
        if user is None:
            user = _designated(unit, ["commander"], excludes=["squadron", "deputy"])
            if user is not None:
                reason = "A member designated Commander signs."
        if user is None:
            for node in _unit_chain(unit):
                user = _appointment(node, [core_roles.ROLE_DEPUTY_COMMANDER])
                if user is not None:
                    reason = "No Commander is appointed; the Deputy Commander signs."
                    break

    if user is None:
        reason = (
            f"{label_for(band)} signs an assessment at {_percent(form.total_percent)}, "
            "but no one holds that post in this unit. Appoint them or record the "
            "designation, then route the assessment again."
        )

    authority = DecisionAuthority(band=band, label=label_for(band), user=user, reason=reason)
    if cache is not None and band not in (CREATOR, SUPERVISOR):
        cache[key] = authority
    return authority


def _parent_for(unit, role):
    """First parent unit that holds ``role`` (used for the squadron fallback)."""
    if unit is None:
        return None
    node = unit.parent
    seen = {unit.id}
    depth = 0
    while node is not None and node.id not in seen and depth < 3:
        seen.add(node.id)
        if _appointment(node, [role]) is not None:
            return node
        node = node.parent
        depth += 1
    return None


def _percent(value) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "its current risk"


def assign(form, *, cache: dict | None = None) -> DecisionAuthority:
    """Record the resolved authority on the form (the legacy sign-off column)."""
    authority = resolve(form, cache=cache)
    if authority.user is not None and form.authority_assigned_to_id != authority.user.id:
        form.authority_assigned_to = authority.user
        form.save(update_fields=["authority_assigned_to"])
    return authority

"""Unit scoping - mirrors ``User.get_accessible_unit_ids`` from the legacy app.

Administrators reach every active unit. Unit Safety Officers reach their
assigned units (``unit_assignments``) plus their primary unit and all
subordinate units. Everyone else reaches their own unit and subordinates.
"""

from __future__ import annotations

from functools import lru_cache

from apps.core.permissions import ADMIN, UNIT_SAFETY

#: The unit that exists only to carry system administration accounts. It is not
#: part of the operational order of battle, so it never appears in a unit
#: selector - the Super Administrator is the only account that sees it.
HIDDEN_UNIT_NAME = "IT Department"


def _descendants(root_ids: set[int], units_by_id: dict[int, object]) -> set[int]:
    """Return the given units plus every active subordinate, transitively."""
    result: set[int] = set()
    stack = [uid for uid in root_ids if uid in units_by_id]
    children: dict[int, list[int]] = {}
    for unit in units_by_id.values():
        if getattr(unit, "parent_id", None):
            children.setdefault(unit.parent_id, []).append(unit.id)

    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def hidden_unit_ids(user) -> set[int]:
    """Units that are never offered to ``user`` in a selector.

    Empty for the Super Administrator, who is the one account that sees the
    system-administration unit; every other account - including an ordinary
    Administrator - gets the ids to subtract.
    """
    from apps.accounts.ownership import is_system_owner
    from apps.legacy.models import Unit

    if is_system_owner(user):
        return set()
    return set(
        Unit.objects.filter(is_active=True, name__iexact=HIDDEN_UNIT_NAME).values_list(
            "id", flat=True
        )
    )


def accessible_unit_ids(user) -> set[int]:
    """Unit ids the user may read/write within. Empty set means 'no scoping info'."""
    from apps.legacy.models import Unit, UnitAssignment

    if not user or not getattr(user, "is_authenticated", False):
        return set()

    units_by_id = {u.id: u for u in Unit.objects.filter(is_active=True)}
    role = getattr(user, "authority", None)

    if role == ADMIN:
        roots = set(units_by_id)
    elif role == UNIT_SAFETY:
        roots = set(
            UnitAssignment.objects.filter(user_id=user.id).values_list("unit_id", flat=True)
        )
        if user.unit_id:
            roots.add(user.unit_id)
    else:
        roots = {user.unit_id} if user.unit_id else set()

    return _descendants({r for r in roots if r}, units_by_id) - hidden_unit_ids(user)


def own_unit_tree_ids(user) -> set[int]:
    """The caller's own unit(s) and their active subordinates only.

    ``accessible_unit_ids`` widens to every active unit for the Administrator.
    Instruction issuing is a command act that must stay inside the issuing
    account's own chain, so this helper deliberately never does that.
    """
    from apps.legacy.models import Unit, UnitAssignment

    if not user or not getattr(user, "is_authenticated", False):
        return set()

    units_by_id = {u.id: u for u in Unit.objects.filter(is_active=True)}
    roots: set[int] = set()
    if getattr(user, "unit_id", None):
        roots.add(user.unit_id)
    if getattr(user, "authority", None) == UNIT_SAFETY:
        roots |= set(
            UnitAssignment.objects.filter(user_id=user.id).values_list("unit_id", flat=True)
        )

    return _descendants({root for root in roots if root}, units_by_id) - hidden_unit_ids(
        user
    )


def unit_ids_for_filter(user) -> list[int] | None:
    """Return unit ids for queryset filtering, or ``None`` when unscoped.

    A user with no unit information is treated as unscoped (legacy behaviour):
    blocking them entirely would break accounts that predate unit hierarchy.
    """
    ids = accessible_unit_ids(user)
    return sorted(ids) if ids else None

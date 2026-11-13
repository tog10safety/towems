"""Domain services: stock movements, numbering, evaluator assignment.

All stock movements run inside ``transaction.atomic`` with a row lock
(``select_for_update``), so two concurrent issues cannot both succeed against
the same remaining quantity. PostgreSQL honours the lock; SQLite (tests) makes
it a no-op but still runs inside a transaction.
"""

from __future__ import annotations

import datetime as dt

from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.core import roles as role_rules
from apps.core.permissions import ADMIN, EVALUATOR
from apps.core.scoping import accessible_unit_ids
from apps.ops.models import (
    InventoryItem,
    InventoryTransaction,
    Mission,
    VehicleTrip,
)


class InventoryError(Exception):
    """Raised when a stock movement is not permitted."""


# ---------------------------------------------------------------------------
# Mission visibility
# ---------------------------------------------------------------------------


def visible_missions(user):
    """Missions ``user`` may see at all.

    Mission records are restricted: command and officer posts and the unit's
    Operations Center office see everything inside their unit scope, while
    everybody else sees nothing - except the missions they are personally
    attached to (creator, assigned personnel/MRM team, or the driver of a
    vehicle run), which they need in order to do the work they were given. The
    queryset is always bounded by the user's unit scope, so a wider capability
    never reaches an unrelated unit.
    """
    queryset = Mission.objects.all()
    if user is None or not getattr(user, "is_authenticated", False):
        return queryset.none()

    unit_ids = accessible_unit_ids(user)
    if not unit_ids:
        return queryset.none()

    # The destination unit of a mission that entered its AOR must be able to
    # monitor that mission. The handoff notification is the grant: only a
    # recipient of it, on that mission, sees across the unit boundary - and the
    # write path still refuses changes outside the caller's own units.
    from apps.ops import aor_handoff

    shared = Q(id__in=aor_handoff.shared_mission_ids(user))
    queryset = queryset.filter(Q(unit_id__in=unit_ids) | shared).distinct()

    if role_rules.has_capability(user, role_rules.CAP_VIEW_MISSIONS):
        return queryset

    return queryset.filter(
        Q(created_by=user)
        | Q(personnel__user=user, personnel__is_active=True)
        | Q(mrm_team__user=user, mrm_team__is_active=True)
        # A designated driver must reach the mission's movement board even when
        # they are not on the mission's personnel roster.
        | Q(vehicle_runs__driver=user)
        | shared
    ).distinct()

class StockError(InventoryError):
    """Insufficient quantity."""


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------


def _today() -> str:
    # Philippine date, independent of the host's own time zone (spec §7).
    from apps.core import timeutils

    return timeutils.today().strftime("%Y%m%d")


def _next_sequence(prefix: str, existing_count: int) -> str:
    return f"{prefix}{existing_count + 1:03d}"


def next_mission_number(category: str) -> str:
    code = "AO" if category == Mission.CATEGORY_AIR else "GO"
    prefix = f"TOWEMS-{code}-{_today()}-"
    count = Mission.objects.filter(mission_number__startswith=prefix).count()
    return _next_sequence(prefix, count)


def next_trip_number() -> str:
    prefix = f"TRIP-{_today()}-"
    count = VehicleTrip.objects.filter(trip_number__startswith=prefix).count()
    return _next_sequence(prefix, count)


def next_mrm_number() -> str:
    """Legacy MRM numbering: ``YYYYMMDD`` + two-digit daily sequence.

    Matches ``MRMForm.generate_mrm_number`` so links created from TOWEMS are
    indistinguishable from ones created in the legacy application.
    """
    from apps.legacy.models import MRMForm

    prefix = _today()
    numbers = MRMForm.objects.filter(mrm_number__startswith=prefix).values_list(
        "mrm_number", flat=True
    )
    highest = 0
    for number in numbers:
        suffix = str(number)[len(prefix) :]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:02d}"


def create_mission_with_number(**kwargs) -> Mission:
    """Create a mission, retrying on the rare numbering race."""
    last_error: Exception | None = None
    for _ in range(5):
        kwargs["mission_number"] = next_mission_number(kwargs["category"])
        try:
            with transaction.atomic():
                return Mission.objects.create(**kwargs)
        except IntegrityError as exc:  # duplicate mission_number
            last_error = exc
            continue
    raise InventoryError(f"Could not allocate a mission number: {last_error}")


def create_trip_with_number(**kwargs) -> VehicleTrip:
    last_error: Exception | None = None
    for _ in range(5):
        kwargs["trip_number"] = next_trip_number()
        try:
            with transaction.atomic():
                return VehicleTrip.objects.create(**kwargs)
        except IntegrityError as exc:
            last_error = exc
            continue
    raise InventoryError(f"Could not allocate a trip number: {last_error}")


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

_RECEIPT_KINDS = {"receipt", "return"}
_ISSUE_KINDS = {"issue"}


@transaction.atomic
def record_stock_movement(
    *,
    item_id: int,
    kind: str,
    quantity: int,
    actor,
    from_unit_id: int | None = None,
    to_unit_id: int | None = None,
    mission=None,
    reference: str = "",
    notes: str = "",
) -> InventoryTransaction:
    """Apply a stock movement atomically and write the ledger entry.

    Semantics (documented so the UI can rely on them):

    * ``receipt``      - total and available both increase
    * ``issue``        - available decreases; rejected if insufficient
    * ``return``       - available increases (total unchanged)
    * ``transfer``     - unit changes; quantities unchanged
    * ``maintenance``  - serviceability becomes 'maintenance'
    * ``inspection``   - recorded only
    * ``adjustment``   - available is set to ``quantity`` (absolute correction)
    """
    item = InventoryItem.objects.select_for_update().get(pk=item_id)
    quantity = int(quantity or 0)
    if quantity < 0:
        raise InventoryError("Quantity must not be negative.")

    if kind in {"issue", "receipt", "return"} and quantity == 0:
        raise InventoryError("Quantity must be greater than zero for this movement.")

    if kind == "receipt":
        item.quantity_total += quantity
        item.quantity_available += quantity
    elif kind in _ISSUE_KINDS:
        if quantity > item.quantity_available:
            raise StockError(
                f"Cannot issue {quantity} {item.unit_of_measure}(s) of "
                f"{item.name}: only {item.quantity_available} available."
            )
        item.quantity_available -= quantity
    elif kind == "return":
        if item.quantity_available + quantity > item.quantity_total:
            raise InventoryError(
                "Return would exceed the recorded total stock for this item."
            )
        item.quantity_available += quantity
    elif kind == "transfer":
        if to_unit_id is None:
            raise InventoryError("A transfer requires a destination unit.")
        item.unit_id = to_unit_id
    elif kind == "maintenance":
        item.serviceability = "maintenance"
    elif kind == "inspection":
        pass
    elif kind == "adjustment":
        if quantity > item.quantity_total:
            item.quantity_total = quantity
        item.quantity_available = quantity
    else:
        raise InventoryError(f"Unknown stock movement kind {kind!r}.")

    item.save()

    return InventoryTransaction.objects.create(
        item=item,
        kind=kind,
        quantity=quantity,
        actor=actor,
        from_unit_id=from_unit_id or item.unit_id,
        to_unit_id=to_unit_id,
        mission=mission,
        reference=reference,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Evaluator assignment (the fixed MRMS bug, enforced server-side)
# ---------------------------------------------------------------------------


class EvaluatorError(Exception):
    """Raised when an evaluator assignment is not permitted."""


def resolve_evaluator(
    evaluator_id, actor, *, allow_self: bool = False, unit_id=None
):
    """Validate an evaluator assignment.

    Mirrors ``routes.mrm.resolve_evaluator`` in the Flask app. The rules are
    enforced here - not in the UI - so a crafted request cannot bypass them:

    * the id must reference an existing, active user;
    * that user must hold the ``Evaluator`` authority;
    * self-assignment is refused unless explicitly allowed;
    * when ``unit_id`` is given, the evaluator must be assigned to that exact
      unit (a manual MRM has no mission to widen the pool);
    * otherwise the evaluator must be inside the actor's authorised units
      (administrators are unscoped).
    """
    from apps.accounts.models import User
    from apps.core.scoping import accessible_unit_ids

    if evaluator_id in (None, ""):
        raise EvaluatorError("Please select an evaluator.")

    try:
        evaluator_id = int(evaluator_id)
    except (TypeError, ValueError):
        raise EvaluatorError("Invalid evaluator selection.") from None

    try:
        evaluator = User.objects.get(pk=evaluator_id)
    except User.DoesNotExist:
        raise EvaluatorError("The selected evaluator no longer exists.") from None

    if not evaluator.is_active:
        raise EvaluatorError("The selected evaluator account is inactive.")
    if evaluator.authority != EVALUATOR:
        raise EvaluatorError("The selected user is not an evaluator.")
    if not allow_self and actor is not None and evaluator.id == actor.id:
        raise EvaluatorError("You cannot assign yourself as the evaluator.")

    if unit_id is not None:
        if evaluator.unit_id != unit_id:
            raise EvaluatorError(
                "The selected evaluator must belong to the same unit as this assessment."
            )
    elif actor is not None and getattr(actor, "authority", None) != ADMIN:
        allowed = accessible_unit_ids(actor)
        if allowed and evaluator.unit_id not in allowed:
            raise EvaluatorError(
                "The selected evaluator is outside your authorised units."
            )

    return evaluator

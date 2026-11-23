"""POL-01 an aircraft burned, confirmed by the crew at engine shutdown.

The store's own unit is the accounting unit. A drum batch leaves the register
in whole drums and a bulk tank in whole litres, so the crew's figure is posted
as entered - a draw never decrements a fraction of a drum. ``litres`` on the
draw exists for consumption reporting only.

Editing a draw posts the difference, so re-entering the same figure costs
nothing and a corrected figure costs only the correction. A store that cannot
cover the draw records the crew's figure as ``pending`` instead of refusing the
shutdown time, and no ledger entry is written until the stock lands.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q
from rest_framework.exceptions import ValidationError

from apps.core.scoping import accessible_unit_ids
from apps.ops import services
from apps.ops.models import (
    AirFuelDraw,
    AirMissionAircraft,
    AirMissionItinerary,
    InventoryItem,
    Mission,
)
from apps.ops.services import InventoryError

POL_AVIATION_FUEL = "POL-01"

FORM_FOR_SOURCE = {
    AirFuelDraw.SOURCE_DRUM: "drum",
    AirFuelDraw.SOURCE_BULK: "bulk",
}


def _scoped_items(user):
    queryset = InventoryItem.objects.filter(
        category__code=POL_AVIATION_FUEL
    ).select_related("category", "unit")
    unit_ids = accessible_unit_ids(user)
    if unit_ids:
        queryset = queryset.filter(Q(unit_id__in=unit_ids) | Q(unit_id__isnull=True))
    return queryset.order_by(F("expiry_date").asc(nulls_last=True), "id")


def candidate_items(user, source):
    """POL-01 stock of the chosen form the caller may draw, FEFO first."""
    return _scoped_items(user).filter(fuel_form=FORM_FOR_SOURCE[source])


def station_tokens(text) -> str:
    """Letters and digits only, so "HTOG 10" and "TOG10" compare equal."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


#: Words a station name shares with every other one, so they identify nothing.
STATION_NOISE = {
    "map",
    "point",
    "ramp",
    "city",
    "airport",
    "airfield",
    "airstrip",
    "the",
    "and",
}


def station_keys(text) -> set[str]:
    """The place names an itinerary point contributes to the store match.

    A station is written as "TOG10, HTOG10, Bancasi, Butuan City, 51PYK731...",
    while a store is "TOG10 POL Dump": the names have to be matched one by one
    rather than as one string. Grid references and filler words are dropped.
    """
    raw = re.sub(r"[()\[\],;/]", " ", (text or "").lower())
    keys = set()
    for chunk in raw.split():
        token = re.sub(r"[^a-z0-9]", "", chunk)
        if len(token) < 3 or token.isdigit():
            continue
        if re.fullmatch(r"\d{2}[a-z]{3}\d+", token):
            continue  # an MGRS grid reference, not a place
        if token in STATION_NOISE:
            continue
        keys.add(token)
    if not keys:
        # A bare two-letter field code is all we have; match it as written.
        whole = station_tokens(text)
        if whole:
            keys.add(whole)
    return keys


def leg_station(leg) -> str:
    """Where the aircraft stands when this leg's fuel figure is owed."""
    for field in ("destination", "origin"):
        value = (getattr(leg, field, "") or "").strip()
        if value:
            return value
    return ""


def items_payload(user, *, mission=None, leg=None) -> list[dict]:
    """The POL-01 stores this figure may be drawn from, nearest expiry first.

    Only stores standing at the aircraft's own station are offered, and only
    within the mission's unit: a sortie that shut down at TOG 10 cannot draw on
    another base's stock. ``expiry_date`` sorts oldest first with the undated
    batches last, so the picker opens on the batch that should leave the
    register next.
    """
    queryset = _scoped_items(user)
    if mission is not None and mission.unit_id:
        queryset = queryset.filter(
            Q(unit_id=mission.unit_id) | Q(unit_id__isnull=True)
        )
    keys = station_keys(leg_station(leg)) if leg is not None else set()
    rows = []
    for item in queryset:
        if keys:
            location = station_tokens(item.storage_location)
            if not any(key in location for key in keys):
                continue
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "fuel_grade": item.fuel_grade,
                "fuel_form": item.fuel_form,
                "unit_of_measure": item.unit_of_measure,
                "drum_capacity_litres": item.drum_capacity_litres,
                "quantity_available": item.quantity_available,
                "storage_location": item.storage_location,
                "expiry_date": item.expiry_date,
                "unit_name": item.unit.name if item.unit_id else "",
            }
        )
    return rows


def draw_litres(source, quantity, item) -> Decimal:
    if source == AirFuelDraw.SOURCE_DRUM:
        return Decimal(int(quantity)) * Decimal(int(item.drum_capacity_litres or 0))
    return Decimal(int(quantity))


def _resolve_item(user, source, item_id=None, *, existing=None):
    candidates = candidate_items(user, source)
    if item_id:
        item = candidates.filter(pk=item_id).first()
        if item is None:
            raise ValidationError(
                {
                    "item": (
                        "Choose a POL-01 store of the matching form inside your "
                        "unit scope."
                    )
                }
            )
        return item
    if existing is not None and existing.source == source:
        if candidates.filter(pk=existing.item_id).exists():
            return existing.item
    # FEFO: the first batch with stock on hand, else the first batch at all so
    # a shortage can still be recorded as pending.
    return candidates.filter(quantity_available__gte=1).first() or candidates.first()


def _unit_label(item) -> str:
    return "drum(s)" if item.fuel_form == "drum" else "litre(s)"


def _issue(actor, mission, item, quantity, aircraft):
    return services.record_stock_movement(
        item_id=item.id,
        kind="issue",
        quantity=quantity,
        actor=actor,
        mission=mission,
        reference=f"FUEL {mission.mission_number}",
        notes=(
            f"{quantity} {_unit_label(item)} of {item.name} for "
            f"{aircraft.display_name}"
        ),
    )


def _give_back(actor, mission, draw):
    """Return what a posted draw took, so an edit starts from a clean slate."""
    if draw.state != AirFuelDraw.STATE_POSTED:
        return
    services.record_stock_movement(
        item_id=draw.item_id,
        kind="return",
        quantity=draw.quantity,
        actor=actor,
        mission=mission,
        reference=f"FUEL CORRECTION {mission.mission_number}",
        notes=f"Corrected fuel draw for {draw.aircraft.display_name}",
    )


@transaction.atomic
def record_draw(
    *,
    mission: Mission,
    aircraft_id,
    source,
    quantity,
    actor,
    leg_id=None,
    item_id=None,
    note: str = "",
    allow_pending: bool = False,
    no_consumption: bool = False,
) -> AirFuelDraw:
    """Record (or correct) the fuel an airframe burned on one itinerary leg."""
    if mission.category != Mission.CATEGORY_AIR:
        raise ValidationError({"detail": "Fuel draws apply to air missions only."})

    source = (source or "").strip().lower()

    aircraft = (
        AirMissionAircraft.objects.filter(
            pk=aircraft_id or 0,
            element__air_detail__mission=mission,
        )
        .select_related("element")
        .first()
    )
    if aircraft is None:
        raise ValidationError({"aircraft": "Choose an aircraft on this mission."})

    leg = (
        AirMissionItinerary.objects.filter(pk=leg_id or 0, aircraft=aircraft).first()
    )
    if leg is None:
        raise ValidationError(
            {"leg": "Choose the itinerary leg this fuel figure belongs to."}
        )
    if not leg_shutdown(leg):
        raise ValidationError(
            {
                "leg": (
                    "Record this leg's landing and engine shutdown before its "
                    "fuel figure."
                )
            }
        )

    # A "no fuel" draw carries no item, so joining it makes the item side of the
    # query nullable; PostgreSQL refuses FOR UPDATE on that side of an outer
    # join, hence the lock is asked for on this table only.
    existing = (
        AirFuelDraw.objects.select_for_update(of=("self",))
        .filter(aircraft=aircraft, leg=leg)
        .select_related("item")
        .first()
    )

    if no_consumption:
        # The crew confirmed there is nothing to record: whatever an earlier
        # figure took goes back to its store and no stock leaves the register.
        # A zero figure still clears the mission's outstanding list, which is
        # exactly what the crew is telling us.
        if existing is not None:
            _give_back(actor, mission, existing)
        draw = existing or AirFuelDraw(aircraft=aircraft, leg=leg, mission=mission)
        draw.mission = mission
        draw.leg = leg
        draw.source = source if source in FORM_FOR_SOURCE else AirFuelDraw.SOURCE_DRUM
        draw.quantity = 0
        draw.litres = 0
        draw.item = None
        draw.note = (note or "")[:255]
        draw.recorded_by = actor
        draw.state = AirFuelDraw.STATE_NONE
        draw.transaction = None
        draw.no_consumption = True
        draw.save()
        return draw

    if source not in FORM_FOR_SOURCE:
        raise ValidationError({"source": "Choose drums or bulk litres."})

    try:
        quantity = int(str(quantity).strip())
    except (TypeError, ValueError):
        raise ValidationError(
            {"quantity": "Enter a whole number of drums or litres."}
        )
    if quantity < 1:
        raise ValidationError({"quantity": "Enter at least one drum or one litre."})

    item = _resolve_item(actor, source, item_id, existing=existing)
    if item is None:
        raise ValidationError(
            {
                "item": (
                    "No POL-01 stock of that form is held in your unit scope. "
                    "Add a drum batch or a bulk tank first."
                )
            }
        )

    previous_source = existing.source if existing else None
    previous_item_id = existing.item_id if existing else None
    previous_state = existing.state if existing else None
    posted_transaction = (
        existing.transaction
        if existing is not None and previous_state == AirFuelDraw.STATE_POSTED
        else None
    )

    # Same store and same figure: nothing to post, only the note may change.
    if (
        existing is not None
        and previous_item_id == item.id
        and previous_source == source
        and existing.quantity == quantity
    ):
        if note != existing.note:
            existing.note = (note or "")[:255]
            existing.save(update_fields=["note", "updated_at"])
        return existing

    same_store = (
        existing is not None
        and previous_item_id == item.id
        and previous_source == source
    )

    new_state = AirFuelDraw.STATE_POSTED
    transaction_row = None
    try:
        if same_store and previous_state == AirFuelDraw.STATE_POSTED:
            # A correction posts only the difference against the same store.
            delta = quantity - existing.quantity
            if delta > 0:
                transaction_row = _issue(actor, mission, item, delta, aircraft)
            elif delta < 0:
                services.record_stock_movement(
                    item_id=item.id,
                    kind="return",
                    quantity=-delta,
                    actor=actor,
                    mission=mission,
                    reference=f"FUEL CORRECTION {mission.mission_number}",
                    notes=f"Reduced fuel draw for {aircraft.display_name}",
                )
        else:
            # A different store or form: give the old draw back, then take the
            # new figure from the store the crew chose.
            if existing is not None:
                _give_back(actor, mission, existing)
            transaction_row = _issue(actor, mission, item, quantity, aircraft)
    except InventoryError as error:
        # Only a draw with nothing posted yet can be held for stock; a posted
        # one already took its drums, so a further shortage is an error.
        if not allow_pending or previous_state == AirFuelDraw.STATE_POSTED:
            raise ValidationError({"quantity": str(error)})
        new_state = AirFuelDraw.STATE_PENDING
        transaction_row = None

    draw = existing or AirFuelDraw(aircraft=aircraft, leg=leg, mission=mission)
    draw.mission = mission
    draw.leg = leg
    draw.source = source
    draw.quantity = quantity
    draw.litres = draw_litres(source, quantity, item)
    draw.item = item
    draw.note = (note or "")[:255]
    draw.recorded_by = actor
    draw.no_consumption = False
    draw.state = new_state
    if new_state == AirFuelDraw.STATE_PENDING:
        draw.transaction = None
    elif same_store and previous_state == AirFuelDraw.STATE_POSTED:
        draw.transaction = posted_transaction or transaction_row
    else:
        draw.transaction = transaction_row
    draw.save()
    return draw


def leg_shutdown(leg) -> bool:
    """True once this leg has landed and shut its engines down.

    Fuel belongs to the leg that ends with the shutdown, so the figure is owed
    (and accepted) as soon as that leg's SD is recorded. An overflight leg ends
    over the target and never shuts down, so it never carries a figure.
    """
    if leg is None or leg.status == "cancelled" or leg.overflight:
        return False
    return bool(leg.sd)


def draw_payload(draw: AirFuelDraw) -> dict:
    return {
        "id": draw.id,
        "aircraft": draw.aircraft_id,
        "aircraft_label": draw.aircraft.display_name,
        "tail_number": draw.aircraft.tail_number,
        "source": draw.source,
        "source_label": draw.get_source_display(),
        "quantity": draw.quantity,
        "litres": float(draw.litres or 0),
        "item": draw.item_id,
        "item_name": draw.item.name if draw.item_id else "",
        "item_form": draw.item.fuel_form if draw.item_id else "",
        "no_consumption": draw.no_consumption,
        "state": draw.state,
        "state_label": draw.get_state_display(),
        "transaction": draw.transaction_id,
        "note": draw.note,
        "recorded_by_name": (
            getattr(draw.recorded_by, "full_name", "") if draw.recorded_by_id else ""
        ),
        "leg": draw.leg_id,
        "leg_number": draw.leg.sequence if draw.leg_id else None,
        "recorded_at": draw.created_at,
    }


def mission_payload(mission: Mission) -> dict | None:
    """The Fuel card: what was drawn, and which airframes still owe a figure."""
    if mission.category != Mission.CATEGORY_AIR or not hasattr(mission, "air_detail"):
        return None
    # Plain ``all()`` keeps the prefetched rows the mission list already loaded;
    # the detail query prefetches the same relations.
    draws = list(mission.air_fuel_draws.all())
    recorded = {
        (draw.aircraft_id, draw.leg_id)
        for draw in draws
        if draw.state != AirFuelDraw.STATE_VOID
    }
    outstanding = []
    for element in mission.air_detail.elements.all():
        for aircraft in element.aircraft.all():
            for leg in aircraft.itineraries.all():
                if (aircraft.id, leg.id) in recorded or not leg_shutdown(leg):
                    continue
                outstanding.append(
                    {
                        "aircraft": aircraft.id,
                        "leg": leg.id,
                        "leg_number": leg.sequence,
                        "label": aircraft.display_name,
                    }
                )
    total_litres = sum(float(draw.litres or 0) for draw in draws)
    return {
        "draws": [draw_payload(draw) for draw in draws],
        "outstanding": outstanding,
        "total_litres": round(total_litres, 1),
    }


def unrecorded_missions(user, *, air_date=None) -> list[dict]:
    """Landed air sorties that still owe a fuel figure."""
    missions = (
        services.visible_missions(user)
        .filter(category=Mission.CATEGORY_AIR)
        .exclude(status__in=["cancelled"])
        .select_related("unit")
        .prefetch_related(
            "air_detail__elements__aircraft__itineraries",
            "air_fuel_draws__leg",
        )
    )
    if air_date is not None:
        missions = missions.filter(air_detail__operation_date=air_date)

    rows = []
    for mission in missions.order_by("mission_number"):
        if not hasattr(mission, "air_detail"):
            continue
        recorded = {
            (draw.aircraft_id, draw.leg_id)
            for draw in mission.air_fuel_draws.all()
            if draw.state != AirFuelDraw.STATE_VOID
        }
        missing = []
        for element in mission.air_detail.elements.all():
            for aircraft in element.aircraft.all():
                for leg in aircraft.itineraries.all():
                    if (aircraft.id, leg.id) in recorded or not leg_shutdown(leg):
                        continue
                    missing.append(
                        {
                            "aircraft": aircraft.id,
                            "leg": leg.id,
                            "leg_number": leg.sequence,
                            "label": aircraft.display_name,
                            "tail_number": aircraft.tail_number,
                        }
                    )
        if not missing:
            continue
        rows.append(
            {
                "mission": mission.id,
                "mission_number": mission.mission_number,
                "title": mission.title,
                "status": mission.status,
                "unit_acronym": mission.unit.acronym if mission.unit_id else "",
                "operation_date": mission.air_detail.operation_date,
                "aircraft": missing,
            }
        )
    return rows

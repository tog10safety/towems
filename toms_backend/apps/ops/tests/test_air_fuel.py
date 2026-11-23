"""Aircraft fuel draws: whole drums or bulk litres, per itinerary leg."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ops.models import (
    AirFuelDraw,
    AirMissionAircraft,
    InventoryCategory,
    InventoryItem,
    InventoryTransaction,
)
from apps.ops.tests.test_air_flight_operations import flight_operations_payload

pytestmark = pytest.mark.django_db


@pytest.fixture
def drum_stock(unit):
    return InventoryItem.objects.create(
        category=InventoryCategory.objects.get(code="POL-01"),
        name="Jet A1 drums",
        unit=unit,
        quantity_total=10,
        quantity_available=10,
        unit_of_measure="drum",
        fuel_grade="jet_a1",
        fuel_form="drum",
        drum_capacity_litres=200,
        storage_location="HTOG 10 POL point",
    )


@pytest.fixture
def bulk_stock(unit):
    return InventoryItem.objects.create(
        category=InventoryCategory.objects.get(code="POL-01"),
        name="Jet A1 bulk tank",
        unit=unit,
        quantity_total=5000,
        quantity_available=5000,
        unit_of_measure="litre",
        fuel_grade="jet_a1",
        fuel_form="bulk",
        storage_location="HTOG 10 tank",
    )


def live_legs(aircraft):
    return list(
        aircraft.itineraries.exclude(status="cancelled").order_by("sequence", "id")
    )


def shutdown_leg(aircraft):
    """The leg the aircraft last shut down on: where its figure belongs."""
    legs = [leg for leg in live_legs(aircraft) if not leg.overflight]
    return legs[-1] if legs else None


def landed_aircraft(client, unit, drawer=None):
    """A flight-operations mission whose first airframe has landed and shut down.

    Only the leg that ends the sortie carries a shutdown, so exactly one fuel
    figure is owed. ``client`` plans the mission (the Operations Center); the
    returned client is ``drawer`` - the Logistics post that records the figure.
    """
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content
    mission = created.json()
    aircraft = (
        AirMissionAircraft.objects.filter(
            element__air_detail__mission_id=mission["id"]
        )
        .order_by("element__sequence", "sequence")
        .first()
    )
    now = timezone.now()
    aircraft.itineraries.update(
        atd=now - timedelta(hours=2), ata=now - timedelta(hours=1)
    )
    leg = shutdown_leg(aircraft)
    leg.sd = now
    leg.save(update_fields=["sd"])
    return drawer or client, mission, aircraft


def post_draw(client, mission, aircraft, leg=None, **body):
    leg = leg or shutdown_leg(aircraft)
    return client.post(
        f"/api/missions/{mission['id']}/fuel-draws/",
        {"aircraft": aircraft.id, "leg": leg.id, **body},
        format="json",
    )


def test_whole_drums_leave_the_register_and_carry_the_mission_reference(as_user, planner, logistics, unit, drum_stock):
    client, mission, aircraft = landed_aircraft(as_user(planner), unit, as_user(logistics))

    response = post_draw(
        client, mission, aircraft, source="drum", quantity=5, item=drum_stock.id
    )
    assert response.status_code == 200, response.content

    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 5
    assert drum_stock.quantity_total == 10

    draw = AirFuelDraw.objects.get(aircraft=aircraft)
    assert draw.quantity == 5
    assert draw.source == "drum"
    assert float(draw.litres) == 1000.0
    assert draw.state == "posted"

    ledger = InventoryTransaction.objects.get(pk=draw.transaction_id)
    assert ledger.kind == "issue"
    assert ledger.quantity == 5
    assert ledger.mission_id == mission["id"]
    assert ledger.reference == f"FUEL {mission['mission_number']}"

    body = response.json()["air_fuel"]
    assert body["draws"][0]["quantity"] == 5
    assert body["draws"][0]["litres"] == 1000.0
    assert body["draws"][0]["leg"] == shutdown_leg(aircraft).id
    assert body["draws"][0]["leg_number"] == shutdown_leg(aircraft).sequence
    assert body["outstanding"] == []


def test_bulk_fuel_is_drawn_in_whole_litres(
    as_user, planner, logistics, unit, bulk_stock
):
    client, mission, aircraft = landed_aircraft(as_user(planner), unit, as_user(logistics))

    response = post_draw(
        client, mission, aircraft, source="bulk", quantity=950, item=bulk_stock.id
    )
    assert response.status_code == 200, response.content

    bulk_stock.refresh_from_db()
    assert bulk_stock.quantity_available == 4050

    draw = AirFuelDraw.objects.get(aircraft=aircraft)
    assert draw.source == "bulk"
    assert draw.quantity == 950
    assert float(draw.litres) == 950.0
    ledger = InventoryTransaction.objects.get(pk=draw.transaction_id)
    assert ledger.quantity == 950


def test_a_shortage_is_refused_unless_it_is_held_awaiting_stock(as_user, planner, logistics, unit, drum_stock):
    client, mission, aircraft = landed_aircraft(as_user(planner), unit, as_user(logistics))
    drum_stock.quantity_total = 3
    drum_stock.quantity_available = 3
    drum_stock.save(update_fields=["quantity_total", "quantity_available"])

    refused = post_draw(
        client, mission, aircraft, source="drum", quantity=5, item=drum_stock.id
    )
    assert refused.status_code == 400
    assert "only 3 available" in str(refused.json()).lower()
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 3
    assert not AirFuelDraw.objects.filter(aircraft=aircraft).exists()

    held = post_draw(
        client,
        mission,
        aircraft,
        source="drum",
        quantity=5,
        item=drum_stock.id,
        allow_pending=True,
    )
    assert held.status_code == 200, held.content
    draw = AirFuelDraw.objects.get(aircraft=aircraft)
    assert draw.state == "pending"
    assert draw.transaction_id is None
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 3


def test_editing_the_figure_posts_only_the_difference(as_user, planner, logistics, unit, drum_stock):
    client, mission, aircraft = landed_aircraft(as_user(planner), unit, as_user(logistics))

    first = post_draw(
        client, mission, aircraft, source="drum", quantity=5, item=drum_stock.id
    )
    assert first.status_code == 200, first.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 5

    raised = post_draw(
        client, mission, aircraft, source="drum", quantity=7, item=drum_stock.id
    )
    assert raised.status_code == 200, raised.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 3

    lowered = post_draw(
        client, mission, aircraft, source="drum", quantity=4, item=drum_stock.id
    )
    assert lowered.status_code == 200, lowered.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 6

    kinds = list(
        InventoryTransaction.objects.filter(mission_id=mission["id"])
        .order_by("id")
        .values_list("kind", "quantity")
    )
    assert kinds == [("issue", 5), ("issue", 2), ("return", 3)]
    assert AirFuelDraw.objects.filter(aircraft=aircraft).count() == 1


def test_switching_from_drums_to_bulk_returns_the_drums(
    as_user, planner, logistics, unit, drum_stock, bulk_stock
):
    client, mission, aircraft = landed_aircraft(as_user(planner), unit, as_user(logistics))

    drums = post_draw(
        client, mission, aircraft, source="drum", quantity=5, item=drum_stock.id
    )
    assert drums.status_code == 200, drums.content

    switched = post_draw(
        client, mission, aircraft, source="bulk", quantity=900, item=bulk_stock.id
    )
    assert switched.status_code == 200, switched.content

    drum_stock.refresh_from_db()
    bulk_stock.refresh_from_db()
    assert drum_stock.quantity_available == 10
    assert bulk_stock.quantity_available == 4100

    draw = AirFuelDraw.objects.get(aircraft=aircraft)
    assert draw.source == "bulk"
    assert draw.item_id == bulk_stock.id
    assert float(draw.litres) == 900.0


def test_fuel_cannot_be_recorded_before_the_shutdown(as_user, planner, logistics, unit, drum_stock):
    client = as_user(planner)
    drawer = as_user(logistics)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content
    mission = created.json()
    aircraft = (
        AirMissionAircraft.objects.filter(
            element__air_detail__mission_id=mission["id"]
        )
        .order_by("element__sequence", "sequence")
        .first()
    )

    response = post_draw(
        drawer, mission, aircraft, source="drum", quantity=2, item=drum_stock.id
    )
    assert response.status_code == 400
    assert "shutdown" in str(response.json()).lower()
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 10


def test_a_landed_airframe_without_a_figure_is_listed_until_it_is_recorded(as_user, planner, logistics, unit, drum_stock):
    client, mission, aircraft = landed_aircraft(as_user(planner), unit, as_user(logistics))
    operation_date = mission["air_detail"]["operation_date"]

    listed = client.get(
        f"/api/missions/fuel-unrecorded/?air_date={operation_date}"
    )
    assert listed.status_code == 200
    rows = listed.json()["results"]
    match = next(row for row in rows if row["mission"] == mission["id"])
    assert [row["label"] for row in match["aircraft"]] == [aircraft.display_name]
    assert [row["leg"] for row in match["aircraft"]] == [shutdown_leg(aircraft).id]

    recorded = post_draw(
        client, mission, aircraft, source="drum", quantity=1, item=drum_stock.id
    )
    assert recorded.status_code == 200, recorded.content

    remaining = client.get(
        f"/api/missions/fuel-unrecorded/?air_date={operation_date}"
    ).json()["results"]
    assert all(mission["id"] != row["mission"] for row in remaining)


def test_the_store_picker_offers_the_station_nearest_expiry_first(
    as_user, planner, logistics, unit, other_unit
):
    """Only the mission unit's stores at the aircraft's station are offered."""
    client, mission, aircraft = landed_aircraft(
        as_user(planner), unit, as_user(logistics)
    )
    leg = shutdown_leg(aircraft)
    station = leg.destination or leg.origin
    category = InventoryCategory.objects.get(code="POL-01")

    def store(location, expiry, owner=unit):
        return InventoryItem.objects.create(
            category=category,
            name="Petron Corporation Jet A1",
            unit=owner,
            quantity_total=5,
            quantity_available=5,
            unit_of_measure="drum",
            fuel_grade="jet_a1",
            fuel_form="drum",
            drum_capacity_litres=200,
            storage_location=location,
            expiry_date=expiry,
        )

    later = store(f"{station} POL point", "2030-06-01")
    sooner = store(f"{station} drum yard", "2027-01-15")
    store("Some other base", "2026-01-01")
    store(f"{station} POL point", "2026-02-01", owner=other_unit)

    response = client.get(
        f"/api/missions/fuel-items/?mission={mission['id']}&leg={leg.id}"
    )
    assert response.status_code == 200, response.content
    rows = response.json()["results"]
    assert [row["id"] for row in rows] == [sooner.id, later.id]
    assert [row["expiry_date"] for row in rows] == ["2027-01-15", "2030-06-01"]


def test_only_pol_01_stock_can_be_drawn(as_user, planner, logistics, unit):
    client, mission, aircraft = landed_aircraft(as_user(planner), unit, as_user(logistics))
    wrong = InventoryItem.objects.create(
        category=InventoryCategory.objects.get(code="POL-02"),
        name="Diesel drums",
        unit=unit,
        quantity_total=10,
        quantity_available=10,
        unit_of_measure="drum",
        fuel_grade="diesel",
        fuel_form="drum",
        drum_capacity_litres=200,
    )

    refused = post_draw(
        client, mission, aircraft, source="drum", quantity=1, item=wrong.id
    )
    assert refused.status_code == 400
    assert "POL-01" in str(refused.json())


@pytest.fixture
def ncoic(db, unit):
    """A Creator account holding the Logistic NCOIC post."""
    from apps.command.models import UnitAppointment
    from apps.core.factories import make_user

    user = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=user, role="logistics_ncoic")
    return user


def test_the_logistic_ncoic_records_fuel_too(
    as_user, planner, ncoic, unit, drum_stock
):
    client, mission, aircraft = landed_aircraft(
        as_user(planner), unit, as_user(ncoic)
    )

    response = post_draw(
        client, mission, aircraft, source="drum", quantity=2, item=drum_stock.id
    )
    assert response.status_code == 200, response.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 8


def test_the_operations_center_records_fuel_too(
    as_user, planner, unit, drum_stock
):
    """The office that plans the sortie also records its leg figures."""
    client, mission, aircraft = landed_aircraft(as_user(planner), unit)

    recorded = post_draw(
        client, mission, aircraft, source="drum", quantity=5, item=drum_stock.id
    )
    assert recorded.status_code == 200, recorded.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 5
    assert AirFuelDraw.objects.filter(aircraft=aircraft).count() == 1


def test_no_fuel_consumption_clears_the_figure_without_touching_stock(
    as_user, planner, logistics, unit, drum_stock
):
    client, mission, aircraft = landed_aircraft(
        as_user(planner), unit, as_user(logistics)
    )

    recorded = post_draw(
        client, mission, aircraft, source="drum", quantity=5, item=drum_stock.id
    )
    assert recorded.status_code == 200, recorded.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 5

    nothing = post_draw(client, mission, aircraft, no_consumption=True)
    assert nothing.status_code == 200, nothing.content

    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 10

    draw = AirFuelDraw.objects.get(aircraft=aircraft)
    assert draw.no_consumption is True
    assert draw.state == "none"
    assert draw.quantity == 0
    assert float(draw.litres) == 0.0
    assert draw.transaction_id is None

    body = nothing.json()["air_fuel"]
    assert body["outstanding"] == []
    assert body["draws"][0]["no_consumption"] is True
    assert body["draws"][0]["state_label"] == "No fuel consumed"

    # The landing list no longer owes a figure for this airframe.
    operation_date = mission["air_detail"]["operation_date"]
    rows = client.get(
        f"/api/missions/fuel-unrecorded/?air_date={operation_date}"
    ).json()["results"]
    assert all(mission["id"] != row["mission"] for row in rows)

    # Switching back to a real figure posts the drums again.
    back = post_draw(
        client, mission, aircraft, source="drum", quantity=3, item=drum_stock.id
    )
    assert back.status_code == 200, back.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 7
    assert AirFuelDraw.objects.get(aircraft=aircraft).no_consumption is False


def test_a_leg_without_a_shutdown_cannot_take_a_figure(
    as_user, planner, logistics, unit, drum_stock
):
    """The option follows the shutdown per leg: no SD, no fuel figure."""
    client, mission, aircraft = landed_aircraft(
        as_user(planner), unit, as_user(logistics)
    )
    legs = [leg for leg in live_legs(aircraft) if not leg.overflight]
    open_leg = next(leg for leg in legs if not leg.sd)

    refused = post_draw(
        client, mission, aircraft, leg=open_leg, source="drum", quantity=2,
        item=drum_stock.id,
    )
    assert refused.status_code == 400
    assert "shutdown" in str(refused.json()).lower()
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 10
    assert not AirFuelDraw.objects.filter(aircraft=aircraft).exists()

    # Nor does the unflown leg show up as owing a figure.
    operation_date = mission["air_detail"]["operation_date"]
    rows = client.get(
        f"/api/missions/fuel-unrecorded/?air_date={operation_date}"
    ).json()["results"]
    match = next(row for row in rows if row["mission"] == mission["id"])
    assert [row["leg"] for row in match["aircraft"]] == [shutdown_leg(aircraft).id]


def test_each_leg_records_its_own_figure(
    as_user, planner, logistics, unit, drum_stock
):
    """A stop en route records that leg; the sortie's end records its own."""
    client, mission, aircraft = landed_aircraft(
        as_user(planner), unit, as_user(logistics)
    )
    legs = [leg for leg in live_legs(aircraft) if not leg.overflight]
    assert len(legs) >= 2
    first, last = legs[0], legs[-1]

    now = timezone.now()
    first.atd = now - timedelta(hours=4)
    first.ata = now - timedelta(hours=3)
    first.sd = now - timedelta(hours=3) + timedelta(minutes=5)
    first.save()

    outbound = post_draw(
        client, mission, aircraft, leg=first, source="drum", quantity=2,
        item=drum_stock.id,
    )
    assert outbound.status_code == 200, outbound.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 8

    home = post_draw(
        client, mission, aircraft, leg=last, source="drum", quantity=1,
        item=drum_stock.id,
    )
    assert home.status_code == 200, home.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 7

    draws = AirFuelDraw.objects.filter(aircraft=aircraft)
    assert draws.count() == 2
    assert sorted(draws.values_list("leg_id", flat=True)) == sorted(
        [first.id, last.id]
    )

    body = home.json()["air_fuel"]
    assert sorted(row["leg_number"] for row in body["draws"]) == sorted(
        [first.sequence, last.sequence]
    )
    assert body["outstanding"] == []
    assert body["total_litres"] == 600.0

    # Correcting one leg leaves the other leg's figure alone.
    corrected = post_draw(
        client, mission, aircraft, leg=first, source="drum", quantity=3,
        item=drum_stock.id,
    )
    assert corrected.status_code == 200, corrected.content
    drum_stock.refresh_from_db()
    assert drum_stock.quantity_available == 6
    assert AirFuelDraw.objects.get(aircraft=aircraft, leg=first).quantity == 3
    assert AirFuelDraw.objects.get(aircraft=aircraft, leg=last).quantity == 1

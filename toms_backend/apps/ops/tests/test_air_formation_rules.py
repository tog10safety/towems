"""Aircraft co-location and Element formation rules for OPCON units."""

from __future__ import annotations

from datetime import datetime

import pytest

from apps.core.mgrs import to_mgrs
from apps.ops import approvals
from apps.ops.models import AirAsset, AirMissionItinerary, Mission
from apps.ops.tests.test_missions import air_payload, make_air_asset

pytestmark = pytest.mark.django_db

ASSEMBLY = (8.95, 125.48)  # HTOG 10, Butuan
FAR = (8.50, 125.48)  # roughly 50 km away
ASSEMBLY_TEXT = f"HTOG 10 ({to_mgrs(*ASSEMBLY, 5)})"
FAR_TEXT = f"RPMY ({to_mgrs(*FAR, 5)})"


def asset_at(unit, tail, location, **overrides):
    return make_air_asset(unit, tail, current_location=location, **overrides)


def linked_payload(unit, assets, lead, start, end, **overrides):
    payload = air_payload(unit, **overrides)
    payload["air_detail"].update(
        {
            "mission_configuration": "element",
            "aircraft_count": len(assets),
            "air_asset_ids": [asset.id for asset in assets],
            "air_asset": lead.id,
            "departure_location": ASSEMBLY_TEXT,
            "destination_location": "Surigao Area",
            "scheduled_departure": start,
            "scheduled_arrival": end,
        }
    )
    return payload


def preposition_payload(unit, wing, start="2027-11-20T06:00:00", end="2027-11-20T08:00:00"):
    payload = air_payload(unit, title="Preposition to HTOG 10")
    payload["air_detail"].update(
        {
            "mission_configuration": "single",
            "aircraft_count": 1,
            "air_asset_ids": [wing.id],
            "air_asset": wing.id,
            "departure_location": FAR_TEXT,
            "destination_location": ASSEMBLY_TEXT,
            "scheduled_departure": start,
            "scheduled_arrival": end,
        }
    )
    return payload


def manual_element_payload(unit, first, second):
    payload = air_payload(unit, title="Manual Element")
    payload["air_detail"] = {
        "operation_date": "2027-11-20",
        "mission_configuration": "element",
        "aircraft_count": 2,
        "elements": [
            {
                "mission": "Troop insertion",
                "aircraft": [
                    {
                        "aircraft_type": first.model,
                        "tail_number": first.tail_number,
                        "callsign": "HAWK 01",
                        "is_lead": True,
                        "itineraries": [
                            {
                                "etd": "2027-11-20T08:00:00",
                                "eta": "2027-11-20T10:00:00",
                                "origin": ASSEMBLY_TEXT,
                                "destination": "Surigao Area",
                            }
                        ],
                    },
                    {
                        "aircraft_type": second.model,
                        "tail_number": second.tail_number,
                        "callsign": "HAWK 02",
                        "is_lead": False,
                        "itineraries": [
                            {
                                "etd": "2027-11-20T08:00:00",
                                "eta": "2027-11-20T10:00:00",
                                "origin": ASSEMBLY_TEXT,
                                "destination": "Surigao Area",
                            }
                        ],
                    },
                ],
            }
        ],
    }
    return payload


def manual_single_payload(unit, aircraft, start, end):
    payload = air_payload(unit, title="Manual preposition")
    payload["air_detail"] = {
        "operation_date": "2027-11-20",
        "mission_configuration": "single",
        "aircraft_count": 1,
        "elements": [
            {
                "aircraft": [
                    {
                        "aircraft_type": aircraft.model,
                        "tail_number": aircraft.tail_number,
                        "callsign": "HAWK 02",
                        "is_lead": True,
                        "itineraries": [
                            {
                                "etd": start,
                                "eta": end,
                                "origin": FAR_TEXT,
                                "destination": ASSEMBLY_TEXT,
                            }
                        ],
                    }
                ]
            }
        ],
    }
    return payload


def test_colocated_aircraft_form_an_element_without_a_preposition(as_user, planner, unit):
    lead = asset_at(unit, "101", ASSEMBLY_TEXT)
    wing = asset_at(unit, "102", ASSEMBLY_TEXT)
    payload = linked_payload(
        unit, [lead, wing], lead, "2027-11-20T08:00:00", "2027-11-20T12:00:00"
    )

    response = as_user(planner).post("/api/missions/", payload, format="json")

    assert response.status_code == 201, response.content
    mission = Mission.objects.get(pk=response.json()["id"])
    assert approvals.formation_blockers(mission) == []


def test_separated_aircraft_are_refused_without_a_preposition(as_user, planner, unit):
    lead = asset_at(unit, "111", ASSEMBLY_TEXT)
    wing = asset_at(unit, "112", FAR_TEXT)
    payload = linked_payload(
        unit, [lead, wing], lead, "2027-11-20T08:00:00", "2027-11-20T12:00:00"
    )

    response = as_user(planner).post("/api/missions/", payload, format="json")

    assert response.status_code == 400, response.content
    messages = " ".join(response.json()["errors"]["air_detail"])
    assert "more than 5 km apart" in messages
    assert "prepositioning mission" in messages
    assert Mission.objects.count() == 0


def test_element_refuses_aircraft_without_a_recorded_location(as_user, planner, unit):
    lead = asset_at(unit, "115", ASSEMBLY_TEXT)
    wing = make_air_asset(unit, "116", current_location="")
    payload = linked_payload(
        unit, [lead, wing], lead, "2027-11-20T08:00:00", "2027-11-20T12:00:00"
    )

    response = as_user(planner).post("/api/missions/", payload, format="json")

    assert response.status_code == 400, response.content
    messages = " ".join(response.json()["errors"]["air_detail"])
    assert "Record a current MGRS location" in messages


def test_separated_element_holds_until_the_preposition_is_completed(
    as_user, planner, unit
):
    lead = asset_at(unit, "121", ASSEMBLY_TEXT)
    wing = asset_at(unit, "122", FAR_TEXT)
    client = as_user(planner)

    first = client.post(
        "/api/missions/", preposition_payload(unit, wing), format="json"
    )
    assert first.status_code == 201, first.content

    formation = linked_payload(
        unit, [lead, wing], lead, "2027-11-20T08:00:00", "2027-11-20T12:00:00"
    )
    formation.update(
        {
            "depends_on": first.json()["id"],
            "dependency_release": "completion",
            "dependency_buffer_minutes": 0,
        }
    )
    second = client.post("/api/missions/", formation, format="json")
    assert second.status_code == 201, second.content

    mission = Mission.objects.get(pk=second.json()["id"])
    assert [row["code"] for row in approvals.dependency_blockers(mission)] == [
        "dependency_waiting"
    ]
    assert approvals.dependency_blockers(mission)[0]["label"] == (
        "Waiting on preposition mission"
    )
    preposition = Mission.objects.get(pk=first.json()["id"])
    preposition.status = "active"
    preposition.save(update_fields=["status"])
    arrival = client.post(
        f"/api/missions/{preposition.id}/air-arrival/",
        {"at": "2027-11-20T08:00:00", "ready": True},
        format="json",
    )
    assert arrival.status_code == 200, arrival.content

    wing.refresh_from_db()
    assert wing.current_location == ASSEMBLY_TEXT
    mission.refresh_from_db()
    assert [row["code"] for row in approvals.dependency_blockers(mission)] == [
        "dependency_waiting"
    ]

    preposition.status = "completed"
    preposition.completed_at = datetime(2027, 11, 20, 8, 0)
    preposition.save(update_fields=["status", "completed_at"])
    mission.refresh_from_db()
    assert approvals.dependency_blockers(mission) == []
    assert approvals.formation_blockers(mission) == []


def test_daily_board_landing_completes_the_preposition_and_releases_the_joint_mission(
    as_user, planner, unit
):
    lead = asset_at(unit, "401", ASSEMBLY_TEXT)
    wing = asset_at(unit, "402", FAR_TEXT)
    client = as_user(planner)

    first = client.post(
        "/api/missions/",
        manual_single_payload(
            unit, wing, "2027-11-20T06:00:00", "2027-11-20T08:00:00"
        ),
        format="json",
    )
    assert first.status_code == 201, first.content
    preposition = Mission.objects.get(pk=first.json()["id"])
    leg = AirMissionItinerary.objects.get(
        aircraft__element__air_detail__mission=preposition
    )

    takeoff = client.post(
        f"/api/missions/{preposition.id}/itineraries/{leg.id}/actual/",
        {"event": "takeoff", "at": "2027-11-20T06:05:00"},
        format="json",
    )
    assert takeoff.status_code == 200, takeoff.content
    landing = client.post(
        f"/api/missions/{preposition.id}/itineraries/{leg.id}/actual/",
        {"event": "landing", "at": "2027-11-20T08:00:00"},
        format="json",
    )
    assert landing.status_code == 200, landing.content

    preposition.refresh_from_db()
    assert preposition.status == "completed"
    assert preposition.completed_at == datetime(2027, 11, 20, 8, 0)
    wing.refresh_from_db()
    assert wing.current_location == ASSEMBLY_TEXT

    formation = manual_element_payload(unit, lead, wing)
    formation.update(
        {
            "depends_on": preposition.id,
            "dependency_release": "completion",
            "dependency_buffer_minutes": 0,
        }
    )
    second = client.post("/api/missions/", formation, format="json")
    assert second.status_code == 201, second.content
    joint = Mission.objects.get(pk=second.json()["id"])
    assert approvals.dependency_blockers(joint) == []
    assert approvals.formation_blockers(joint) == []


def test_manual_preposition_arrival_moves_the_asset_and_releases_the_joint_mission(
    as_user, planner, unit
):
    lead = AirAsset.objects.create(
        aircraft_type="rotary_wing",
        categories=["utility"],
        model="S-70i BH",
        tail_number="201",
        callsign="HAWK 01",
        assigned_unit="205th_thw",
        opcon_unit=unit,
        current_location=ASSEMBLY_TEXT,
    )
    wing = AirAsset.objects.create(
        aircraft_type="rotary_wing",
        categories=["utility"],
        model="S-70i BH",
        tail_number="202",
        callsign="HAWK 02",
        assigned_unit="205th_thw",
        opcon_unit=unit,
        current_location=FAR_TEXT,
    )
    client = as_user(planner)

    first = client.post(
        "/api/missions/",
        manual_single_payload(
            unit, wing, "2027-11-20T06:00:00", "2027-11-20T08:00:00"
        ),
        format="json",
    )
    assert first.status_code == 201, first.content

    formation = manual_element_payload(unit, lead, wing)
    formation.update(
        {
            "depends_on": first.json()["id"],
            "dependency_release": "completion",
            "dependency_buffer_minutes": 0,
        }
    )
    second = client.post("/api/missions/", formation, format="json")
    assert second.status_code == 201, second.content

    mission = Mission.objects.get(pk=second.json()["id"])
    leg_id = AirMissionItinerary.objects.filter(
        aircraft__element__air_detail__mission=mission
    ).values_list("id", flat=True).first()
    blocked = client.post(
        f"/api/missions/{mission.id}/itineraries/{leg_id}/actual/",
        {"event": "takeoff", "at": "2027-11-20T08:00:00"},
        format="json",
    )
    assert blocked.status_code == 400, blocked.content
    assert "Waiting for mission" in blocked.content.decode()

    preposition = Mission.objects.get(pk=first.json()["id"])
    preposition.status = "active"
    preposition.save(update_fields=["status"])
    arrival = client.post(
        f"/api/missions/{preposition.id}/air-arrival/",
        {"at": "2027-11-20T08:00:00", "ready": True},
        format="json",
    )
    assert arrival.status_code == 200, arrival.content

    wing.refresh_from_db()
    assert wing.current_location == ASSEMBLY_TEXT
    assert [row["code"] for row in approvals.dependency_blockers(mission)] == [
        "dependency_waiting"
    ]
    still_blocked = client.post(
        f"/api/missions/{mission.id}/itineraries/{leg_id}/actual/",
        {"event": "takeoff", "at": "2027-11-20T08:00:00"},
        format="json",
    )
    assert still_blocked.status_code == 400, still_blocked.content

    preposition.status = "completed"
    preposition.completed_at = datetime(2027, 11, 20, 8, 0)
    preposition.save(update_fields=["status", "completed_at"])
    mission.refresh_from_db()
    assert approvals.dependency_blockers(mission) == []
    assert approvals.formation_blockers(mission) == []
    released = client.post(
        f"/api/missions/{mission.id}/itineraries/{leg_id}/actual/",
        {"event": "takeoff", "at": "2027-11-20T08:00:00"},
        format="json",
    )
    assert released.status_code == 200, released.content


def test_joint_etd_must_exactly_match_the_incoming_aircraft_eta(
    as_user, planner, unit
):
    lead = asset_at(unit, "131", ASSEMBLY_TEXT)
    wing = asset_at(unit, "132", FAR_TEXT)
    client = as_user(planner)

    first = client.post(
        "/api/missions/", preposition_payload(unit, wing), format="json"
    )
    assert first.status_code == 201, first.content

    formation = linked_payload(
        unit, [lead, wing], lead, "2027-11-20T09:00:00", "2027-11-20T12:00:00"
    )
    formation.update(
        {
            "depends_on": first.json()["id"],
            "dependency_release": "completion",
            "dependency_buffer_minutes": 0,
        }
    )
    response = client.post("/api/missions/", formation, format="json")

    assert response.status_code == 400, response.content
    messages = " ".join(response.json()["errors"]["air_detail"])
    assert "must exactly match" in messages


def test_delayed_preposition_keeps_the_joint_mission_on_hold(as_user, planner, unit):
    lead = asset_at(unit, "141", ASSEMBLY_TEXT)
    wing = asset_at(unit, "142", FAR_TEXT)
    client = as_user(planner)

    first = client.post(
        "/api/missions/", preposition_payload(unit, wing), format="json"
    )
    assert first.status_code == 201, first.content

    formation = linked_payload(
        unit, [lead, wing], lead, "2027-11-20T08:00:00", "2027-11-20T12:00:00"
    )
    formation.update(
        {
            "depends_on": first.json()["id"],
            "dependency_release": "completion",
            "dependency_buffer_minutes": 0,
        }
    )
    second = client.post("/api/missions/", formation, format="json")
    assert second.status_code == 201, second.content

    preposition = Mission.objects.get(pk=first.json()["id"])
    preposition.status = "active"
    preposition.save(update_fields=["status"])
    arrival = client.post(
        f"/api/missions/{preposition.id}/air-arrival/",
        {"at": "2027-11-20T09:00:00", "ready": True},
        format="json",
    )
    assert arrival.status_code == 200, arrival.content

    mission = Mission.objects.get(pk=second.json()["id"])
    assert [row["code"] for row in approvals.dependency_blockers(mission)] == [
        "dependency_waiting"
    ]
    preposition.status = "completed"
    preposition.completed_at = datetime(2027, 11, 20, 9, 0)
    preposition.save(update_fields=["status", "completed_at"])
    mission.refresh_from_db()
    assert [row["code"] for row in approvals.dependency_blockers(mission)] == [
        "dependency_buffer"
    ]


def test_manual_element_resolves_air_assets_and_applies_the_same_rule(
    as_user, planner, unit
):
    lead = AirAsset.objects.create(
        aircraft_type="rotary_wing",
        categories=["utility"],
        model="S-70i BH",
        tail_number="001",
        callsign="HAWK 01",
        assigned_unit="205th_thw",
        opcon_unit=unit,
        current_location=ASSEMBLY_TEXT,
    )
    wing = AirAsset.objects.create(
        aircraft_type="rotary_wing",
        categories=["utility"],
        model="S-70i BH",
        tail_number="002",
        callsign="HAWK 02",
        assigned_unit="205th_thw",
        opcon_unit=unit,
        current_location=FAR_TEXT,
    )

    response = as_user(planner).post(
        "/api/missions/", manual_element_payload(unit, lead, wing), format="json"
    )

    assert response.status_code == 400, response.content
    messages = " ".join(response.json()["errors"]["air_detail"])
    assert "more than 5 km apart" in messages


def test_manual_element_aircraft_cannot_be_double_booked_either(
    as_user, planner, unit
):
    lead = AirAsset.objects.create(
        aircraft_type="rotary_wing",
        categories=["utility"],
        model="S-70i BH",
        tail_number="301",
        callsign="HAWK 01",
        assigned_unit="205th_thw",
        opcon_unit=unit,
        current_location=ASSEMBLY_TEXT,
    )
    client = as_user(planner)
    first = client.post(
        "/api/missions/",
        manual_single_payload(
            unit, lead, "2027-11-20T06:00:00", "2027-11-20T10:00:00"
        ),
        format="json",
    )
    assert first.status_code == 201, first.content

    second = client.post(
        "/api/missions/",
        manual_single_payload(
            unit, lead, "2027-11-20T09:00:00", "2027-11-20T11:00:00"
        ),
        format="json",
    )

    assert second.status_code == 400, second.content
    messages = " ".join(second.json()["errors"]["air_detail"])
    assert "already assigned" in messages

    # The availability endpoint must see manual element rows too; otherwise the
    # picker would still offer an airframe the serializer has just rejected.
    conflicts = client.get(
        "/api/inventory/air-assets/conflicts/",
        {
            "air_asset_ids": str(lead.id),
            "start": "2027-11-20T09:00:00",
            "end": "2027-11-20T11:00:00",
        },
    )
    assert conflicts.status_code == 200, conflicts.content
    rows = conflicts.json()["conflicts"]
    assert len(rows) == 1
    assert rows[0]["air_asset"] == lead.id
    assert rows[0]["mission_id"] == first.json()["id"]
    assert rows[0]["tail_number"] == "301"


def test_an_aircraft_cannot_be_double_booked_into_an_overlapping_sortie(
    as_user, planner, unit
):
    lead = asset_at(unit, "151", ASSEMBLY_TEXT)
    client = as_user(planner)
    first = air_payload(unit, title="First sortie")
    first["air_detail"].update(
        {
            "mission_configuration": "single",
            "aircraft_count": 1,
            "air_asset_ids": [lead.id],
            "air_asset": lead.id,
            "scheduled_departure": "2027-11-20T06:00:00",
            "scheduled_arrival": "2027-11-20T10:00:00",
        }
    )
    created = client.post("/api/missions/", first, format="json")
    assert created.status_code == 201, created.content

    second = air_payload(unit, title="Overlapping sortie")
    second["air_detail"].update(
        {
            "mission_configuration": "single",
            "aircraft_count": 1,
            "air_asset_ids": [lead.id],
            "air_asset": lead.id,
            "scheduled_departure": "2027-11-20T09:00:00",
            "scheduled_arrival": "2027-11-20T11:00:00",
        }
    )
    response = client.post("/api/missions/", second, format="json")

    assert response.status_code == 400, response.content
    messages = " ".join(response.json()["errors"]["air_detail"])
    assert "already assigned" in messages

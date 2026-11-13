"""Daily Flight Operations: elements, shared missions and actual leg times."""

from __future__ import annotations

import pytest
import fitz

from apps.ops.models import AirAsset, AirMissionItinerary
from apps.ops.tests.test_missions import air_payload

pytestmark = pytest.mark.django_db


def flight_operations_payload(unit):
    # The daily sheet now selects from the Air Assets register. Give the test
    # unit a co-located airframe for every row so the 5 km rule can resolve it.
    for aircraft_type, model, tail, assigned_unit in (
        ("fixed_wing", "C-295", "204", "220aw"),
        ("fixed_wing", "NC-212i", "2120", "220aw"),
        ("rotary_wing", "S-70i BH", "760", "205th_thw"),
        ("rotary_wing", "S-70i BH", "761", "205th_thw"),
        ("rotary_wing", "S-70i BH", "762", "205th_thw"),
    ):
        AirAsset.objects.get_or_create(
            aircraft_type=aircraft_type,
            model=model,
            tail_number=tail,
            defaults={
                "assigned_unit": assigned_unit,
                "opcon_unit": unit,
                "current_location": "HTOG 10 (51PZK1060483777)",
            },
        )
    payload = air_payload(unit, title="Flight Operations 11 November 2026")
    payload["air_detail"] = {
        "operation_date": "2099-11-11",
        "elements": [
            {
                "mission": "",
                "aircraft": [
                    {
                        "aircraft_type": "C-295",
                        "tail_number": "204",
                        "is_lead": True,
                        "crew": [
                            {"role": "IP", "name": "DE VILLA A"},
                            {"role": "PIC", "name": "APALE"},
                            {"role": "CP", "name": "MENDOZA J"},
                        ],
                        "itineraries": [
                            {
                                "etd": "2099-11-11T06:00:00",
                                "eta": "2099-11-11T07:30:00",
                                "origin": "LL",
                                "destination": "VP",
                                "route": "LL – VP",
                                "mission": "Ferry mission essential personnel c/o CMOR, PA.",
                            },
                            {
                                "etd": "2099-11-11T08:30:00",
                                "eta": "2099-11-11T10:00:00",
                                "origin": "VP",
                                "destination": "RAS",
                                "route": "VP – RAS",
                                "mission": (
                                    "Transport assorted cargoes c/o CEISAFP.\n"
                                    "Accommodate cargoes c/o OA-4/TOWWEST."
                                ),
                            },
                            {
                                "etd": "2099-11-11T11:00:00",
                                "eta": "2099-11-11T12:30:00",
                                "origin": "RAS",
                                "destination": "VP",
                                "route": "RAS – VP",
                                "mission": "Preposition aircraft at ABAB.",
                            },
                            {
                                "etd": "2099-11-11T13:30:00",
                                "eta": "2099-11-11T15:00:00",
                                "origin": "VP",
                                "destination": "LL",
                                "route": "VP – LL",
                                "mission": "Ferry personnel c/o SSG, PAF.",
                            },
                        ],
                    }
                ],
            },
            {
                "mission": "Conduct MARPAT c/o SOLCOM.",
                "aircraft": [
                    {
                        "aircraft_type": "NC-212i",
                        "tail_number": "2120",
                        "crew": [
                            {"role": "PIC", "name": "CORPUZ"},
                            {"role": "CP", "name": "SORISO"},
                        ],
                        "itineraries": [
                            {
                                "etd": "2099-11-11T07:00:00",
                                "origin": "LC",
                                "destination": "LC",
                                "route": "LC – Over SOLCOM AO – LC",
                                "overflight": True,
                                "return_to_origin": True,
                            }
                        ],
                    }
                ],
            },
            {
                "mission": "Troops Insertions c/o 402BDE.",
                "aircraft": [
                    {
                        "aircraft_type": "S-70i BH",
                        "tail_number": "760",
                        "crew": [
                            {"role": "PIC", "name": "MARASIGAN"},
                            {"role": "CP", "name": "BLANCO"},
                        ],
                        "itineraries": [
                            {
                                "origin": "HTOG 10",
                                "destination": "LZ (51PZK1060483777)",
                                "route": "HTOG 10 – LZ (51PZK1060483777)",
                            },
                            {
                                "origin": "LZ (51PZK1060483777)",
                                "destination": "HTOG 10",
                                "route": "LZ – HTOG 10",
                            },
                        ],
                    },
                    {
                        "aircraft_type": "S-70i BH",
                        "tail_number": "761",
                        "crew": [
                            {"role": "PIC", "name": "MARASIGAN"},
                            {"role": "CP", "name": "BLANCO"},
                        ],
                        "itineraries": [
                            {
                                "origin": "HTOG 10",
                                "destination": "LZ (51PZK1060483777)",
                                "route": "HTOG 10 – LZ (51PZK1060483777)",
                            },
                            {
                                "origin": "LZ (51PZK1060483777)",
                                "destination": "HTOG 10",
                                "route": "LZ – HTOG 10",
                            },
                        ],
                    },
                ],
            },
        ],
    }
    return payload


def test_manual_daily_sheet_stores_elements_crew_and_legs(as_user, planner, unit):
    response = as_user(planner).post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert response.status_code == 201, response.content
    detail = response.json()["air_detail"]

    assert detail["operation_date"] == "2099-11-11"
    assert len(detail["elements"]) == 3
    assert [element["sequence"] for element in detail["elements"]] == [1, 2, 3]
    assert len(detail["elements"][0]["aircraft"][0]["crew"]) == 3
    assert len(detail["elements"][0]["aircraft"][0]["itineraries"]) == 4
    assert detail["elements"][1]["aircraft"][0]["itineraries"][0]["etd"]
    assert detail["elements"][1]["aircraft"][0]["itineraries"][0]["eta"] is None
    assert detail["elements"][1]["aircraft"][0]["itineraries"][0]["overflight"] is True
    assert len(detail["elements"][2]["aircraft"]) == 2
    assert detail["elements"][2]["aircraft"][0]["itineraries"][0][
        "display_mission"
    ] == "Troops Insertions c/o 402BDE."


def test_eta_must_be_after_the_mission_etd(as_user, planner, unit):
    payload = flight_operations_payload(unit)
    first_leg = payload["air_detail"]["elements"][0]["aircraft"][0]["itineraries"][0]
    first_leg["eta"] = first_leg["etd"]

    response = as_user(planner).post("/api/missions/", payload, format="json")

    assert response.status_code == 400, response.content
    assert "after the etd" in response.content.decode().lower()


def test_same_aircraft_cannot_be_used_twice_on_one_sheet(as_user, planner, unit):
    payload = flight_operations_payload(unit)
    first = payload["air_detail"]["elements"][0]["aircraft"][0]
    duplicate = payload["air_detail"]["elements"][2]["aircraft"][1]
    duplicate["aircraft_type"] = first["aircraft_type"]
    duplicate["tail_number"] = first["tail_number"]

    response = as_user(planner).post("/api/missions/", payload, format="json")

    assert response.status_code == 400, response.content
    assert "same aircraft" in response.content.decode().lower()


def test_daily_filter_and_schedule_pdf(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content

    listed = client.get("/api/missions/?category=air&air_date=2099-11-11")
    assert listed.status_code == 200, listed.content
    assert listed.json()["count"] == 1

    schedule = client.get("/api/missions/air-operations/?date=2099-11-11&mode=schedule")
    assert schedule.status_code == 200, schedule.content
    assert schedule["Content-Type"] == "application/pdf"
    assert schedule.content.startswith(b"%PDF")
    schedule_text = "\n".join(
        page.get_text() for page in fitz.open(stream=schedule.content, filetype="pdf")
    )
    assert "ETD" in schedule_text
    assert "ETA" not in schedule_text
    assert "C-295" in schedule_text and "LC – Over SOLCOM AO – LC" in schedule_text
    assert "ATD" not in schedule_text


def test_board_hold_blocks_takeoff_until_released(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content
    mission = created.json()
    leg = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"][0]

    held = client.post(
        f"/api/missions/{mission['id']}/hold/",
        {"hold": True, "reason": "Crew rest"},
        format="json",
    )
    assert held.status_code == 200, held.content
    assert held.json()["is_held"] is True

    blocked = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T06:05:00"},
        format="json",
    )
    assert blocked.status_code == 400
    assert "hold" in str(blocked.json()).lower()

    released = client.post(
        f"/api/missions/{mission['id']}/hold/",
        {"hold": False},
        format="json",
    )
    assert released.status_code == 200, released.content
    assert released.json()["is_held"] is False

    takeoff = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T06:05:00"},
        format="json",
    )
    assert takeoff.status_code == 200, takeoff.content


def test_board_cancel_closes_the_mission(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content
    mission = created.json()
    leg = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"][0]

    cancelled = client.post(
        f"/api/missions/{mission['id']}/cancel/",
        {"reason": "Weather below minima"},
        format="json",
    )
    assert cancelled.status_code == 200, cancelled.content
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["is_held"] is False

    blocked = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T06:05:00"},
        format="json",
    )
    assert blocked.status_code == 400
    assert "closed" in str(blocked.json()).lower()


def test_hold_and_cancel_wait_until_the_aircraft_has_landed(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content
    mission = created.json()
    legs = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"]
    first, second = legs[0], legs[1]

    takeoff = client.post(
        f"/api/missions/{mission['id']}/itineraries/{first['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T06:05:00"},
        format="json",
    )
    assert takeoff.status_code == 200, takeoff.content
    assert takeoff.json()["is_airborne"] is True

    held = client.post(
        f"/api/missions/{mission['id']}/hold/", {"hold": True}, format="json"
    )
    assert held.status_code == 400
    assert "landed" in str(held.json()).lower()

    cancelled = client.post(
        f"/api/missions/{mission['id']}/cancel/", {}, format="json"
    )
    assert cancelled.status_code == 400
    assert "landed" in str(cancelled.json()).lower()

    landing = client.post(
        f"/api/missions/{mission['id']}/itineraries/{first['id']}/actual/",
        {"event": "landing", "at": "2099-11-11T07:00:00"},
        format="json",
    )
    assert landing.status_code == 200, landing.content
    assert landing.json()["is_airborne"] is False

    held = client.post(
        f"/api/missions/{mission['id']}/hold/", {"hold": True}, format="json"
    )
    assert held.status_code == 200, held.content
    assert held.json()["is_held"] is True
    released = client.post(
        f"/api/missions/{mission['id']}/hold/", {"hold": False}, format="json"
    )
    assert released.status_code == 200, released.content

    cancelled = client.post(
        f"/api/missions/{mission['id']}/cancel/",
        {"reason": "Mission stopped after the first leg"},
        format="json",
    )
    assert cancelled.status_code == 200, cancelled.content
    assert cancelled.json()["status"] == "cancelled"

    first_row = AirMissionItinerary.objects.get(pk=first["id"])
    second_row = AirMissionItinerary.objects.get(pk=second["id"])
    assert first_row.ata is not None
    assert second_row.status == AirMissionItinerary.STATUS_CANCELLED


def test_change_itinerary_only_allows_unflown_legs(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content
    mission = created.json()
    legs = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"]
    first, second = legs[0], legs[1]

    assert client.post(
        f"/api/missions/{mission['id']}/itineraries/{first['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T06:05:00"},
        format="json",
    ).status_code == 200
    assert client.post(
        f"/api/missions/{mission['id']}/itineraries/{first['id']}/actual/",
        {"event": "landing", "at": "2099-11-11T07:00:00"},
        format="json",
    ).status_code == 200

    changed = client.post(
        f"/api/missions/{mission['id']}/change-itinerary/",
        {
            "legs": [
                {
                    "id": second["id"],
                    "destination": "ZZZ",
                    "point_role": "target",
                    "eta": "2099-11-11T10:30:00",
                }
            ]
        },
        format="json",
    )
    assert changed.status_code == 200, changed.content
    changed_legs = changed.json()["air_detail"]["elements"][0]["aircraft"][0][
        "itineraries"
    ]
    assert changed_legs[1]["destination"] == "ZZZ"
    assert changed_legs[1]["point_role"] == "target"
    assert changed_legs[0]["ata"] is not None

    blocked = client.post(
        f"/api/missions/{mission['id']}/change-itinerary/",
        {"legs": [{"id": first["id"], "destination": "CHANGED"}]},
        format="json",
    )
    assert blocked.status_code == 400
    assert "started" in str(blocked.json()).lower() or "finished" in str(
        blocked.json()
    ).lower()


def test_actual_takeoff_and_landing_are_additive(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content
    mission = created.json()
    leg = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"][0]

    takeoff = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T06:05:00"},
        format="json",
    )
    assert takeoff.status_code == 200, takeoff.content
    changed = takeoff.json()["air_detail"]["elements"][0]["aircraft"][0][
        "itineraries"
    ][0]
    assert takeoff.json()["timing_warnings"] == []
    assert changed["etd"].startswith("2099-11-11T06:00")
    assert changed["atd"].startswith("2099-11-11T06:05")
    assert changed["status"] == "airborne"

    landing = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "landing", "at": "2099-11-11T07:37:00"},
        format="json",
    )
    assert landing.status_code == 200, landing.content
    landed = landing.json()["air_detail"]["elements"][0]["aircraft"][0][
        "itineraries"
    ][0]
    assert landed["eta"].startswith("2099-11-11T07:30")
    assert landed["ata"].startswith("2099-11-11T07:37")
    assert landed["status"] == "landed"

    actual_pdf = client.get("/api/missions/air-operations/?date=2099-11-11&mode=actual")
    assert actual_pdf.status_code == 200, actual_pdf.content
    assert actual_pdf.content.startswith(b"%PDF")
    actual_text = "\n".join(
        page.get_text() for page in fitz.open(stream=actual_pdf.content, filetype="pdf")
    )
    assert "ATD" in actual_text and "ATA" in actual_text
    assert "0605" in actual_text and "0737" in actual_text


def test_takeoff_far_from_the_plan_is_reported_as_a_timing_warning(
    as_user, planner, unit
):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content
    mission = created.json()
    leg = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"][0]

    takeoff = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T03:00:00"},
        format="json",
    )

    assert takeoff.status_code == 200, takeoff.content
    warnings = takeoff.json()["timing_warnings"]
    assert len(warnings) == 1
    assert warnings[0]["code"] == "actual_departure_outside_plan"
    assert warnings[0]["leg"] == 1
    assert warnings[0]["difference_minutes"] == 180
    assert "leg 1:" in warnings[0]["message"]
    assert "before the planned ETD" in warnings[0]["message"]


def test_actual_time_cannot_precede_takeoff(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    mission = created.json()
    leg = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"][0]
    client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T06:05:00"},
        format="json",
    )
    response = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "landing", "at": "2099-11-11T06:00:00"},
        format="json",
    )
    assert response.status_code == 400
    assert "ATA" in response.json()["errors"]["ata"]
    assert AirMissionItinerary.objects.filter(pk=leg["id"]).exists()


def test_overflight_tot_and_toot_are_additive(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    assert created.status_code == 201, created.content
    mission = created.json()
    leg = mission["air_detail"]["elements"][1]["aircraft"][0]["itineraries"][0]

    takeoff = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T07:05:00"},
        format="json",
    )
    assert takeoff.status_code == 200, takeoff.content

    totality = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "tot", "at": "2099-11-11T07:20:00"},
        format="json",
    )
    assert totality.status_code == 200, totality.content
    changed = totality.json()["air_detail"]["elements"][1]["aircraft"][0][
        "itineraries"
    ][0]
    assert changed["tot"].startswith("2099-11-11T07:20")
    assert changed["toot"] is None
    assert changed["status"] == "airborne"

    departure = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "toot", "at": "2099-11-11T08:10:00"},
        format="json",
    )
    assert departure.status_code == 200, departure.content
    changed = departure.json()["air_detail"]["elements"][1]["aircraft"][0][
        "itineraries"
    ][0]
    assert changed["tot"].startswith("2099-11-11T07:20")
    assert changed["toot"].startswith("2099-11-11T08:10")


def test_tot_is_rejected_for_a_non_overflight_leg(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    mission = created.json()
    leg = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"][0]
    response = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "tot", "at": "2099-11-11T07:20:00"},
        format="json",
    )
    assert response.status_code == 400
    assert "overflight" in response.json()["errors"]["detail"]


def test_toot_requires_tot(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    mission = created.json()
    leg = mission["air_detail"]["elements"][1]["aircraft"][0]["itineraries"][0]
    client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "takeoff", "at": "2099-11-11T07:05:00"},
        format="json",
    )
    response = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "toot", "at": "2099-11-11T08:10:00"},
        format="json",
    )
    assert response.status_code == 400
    assert "TOT" in response.json()["errors"]["toot"]


def test_normal_landing_requires_takeoff(as_user, planner, unit):
    client = as_user(planner)
    created = client.post(
        "/api/missions/", flight_operations_payload(unit), format="json"
    )
    mission = created.json()
    leg = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"][0]
    response = client.post(
        f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
        {"event": "landing", "at": "2099-11-11T07:37:00"},
        format="json",
    )
    assert response.status_code == 400
    assert "ATD" in response.json()["errors"]["ata"]


def test_overflight_sequence_gates_the_next_arrival(as_user, planner, unit):
    payload = flight_operations_payload(unit)
    payload["air_detail"]["elements"] = [
        {
            "mission": "Troop insertion sequence",
            "aircraft": [
                {
                    "aircraft_type": "C-295",
                    "tail_number": "204",
                    "is_lead": True,
                    "itineraries": [
                        {
                            "etd": "2099-11-11T06:00:00",
                            "eta": "2099-11-11T07:00:00",
                            "origin": "HTOG 10",
                            "destination": "HTOG 11",
                            "route": "HTOG 10 – HTOG 11",
                        },
                        {
                            "etd": "2099-11-11T07:30:00",
                            "origin": "HTOG 11",
                            "destination": "Midsayap",
                            "route": "HTOG 11 – Midsayap",
                            "overflight": True,
                        },
                        {
                            "etd": "2099-11-11T09:00:00",
                            "eta": "2099-11-11T10:00:00",
                            "origin": "Midsayap",
                            "destination": "HTOG 10",
                            "route": "Midsayap – HTOG 10",
                        },
                    ],
                }
            ],
        }
    ]

    client = as_user(planner)
    created = client.post("/api/missions/", payload, format="json")
    assert created.status_code == 201, created.content
    mission = created.json()
    legs = mission["air_detail"]["elements"][0]["aircraft"][0]["itineraries"]

    def record(leg, event, at):
        return client.post(
            f"/api/missions/{mission['id']}/itineraries/{leg['id']}/actual/",
            {"event": event, "at": at},
            format="json",
        )

    early_arrival = record(legs[2], "landing", "2099-11-11T09:30:00")
    assert early_arrival.status_code == 400
    assert "TOOT" in early_arrival.json()["errors"]["ata"]

    early_second_takeoff = record(legs[1], "takeoff", "2099-11-11T07:10:00")
    assert early_second_takeoff.status_code == 400
    assert "ATA" in early_second_takeoff.json()["errors"]["atd"]

    assert record(legs[0], "takeoff", "2099-11-11T06:05:00").status_code == 200
    assert record(legs[0], "landing", "2099-11-11T07:00:00").status_code == 200
    assert record(legs[1], "takeoff", "2099-11-11T07:10:00").status_code == 200
    assert record(legs[1], "tot", "2099-11-11T07:30:00").status_code == 200
    assert record(legs[1], "toot", "2099-11-11T08:10:00").status_code == 200

    arrival = record(legs[2], "landing", "2099-11-11T09:30:00")
    assert arrival.status_code == 200, arrival.content
    landed = arrival.json()["air_detail"]["elements"][0]["aircraft"][0][
        "itineraries"
    ][2]
    assert landed["ata"].startswith("2099-11-11T09:30")


def test_air_eta_estimate_uses_route_mgrs_and_ground_speed(as_user, planner):
    response = as_user(planner).post(
        "/api/gis/air-eta/",
        {
            "cruising_speed_knots": 135,
            "legs": [
                {
                    "origin": "Bancasi, Butuan City, 51PYK7316589902",
                    "destination": "Sasa, Davao City, 51NYH9319389013",
                }
            ],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    leg = response.json()["legs"][0]
    assert leg["resolved"] is True
    assert leg["origin_mgrs"] == "51PYK7316589902"
    assert leg["destination_mgrs"] == "51NYH9319389013"
    assert leg["distance_km"] > 0
    assert leg["duration_minutes"] == pytest.approx(
        leg["distance_km"] / (135 * 1.852) * 60, rel=1e-3
    )


def test_air_eta_estimate_reports_legs_without_mgrs(as_user, planner):
    response = as_user(planner).post(
        "/api/gis/air-eta/",
        {
            "cruising_speed_knots": 135,
            "legs": [{"origin": "LL", "destination": "VP"}],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    leg = response.json()["legs"][0]
    assert leg["resolved"] is False
    assert leg["distance_km"] is None
    assert leg["duration_minutes"] is None
    assert "MGRS" in leg["reason"]

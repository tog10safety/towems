"""Mission sequencing: a formation waits for its preposition mission."""

from __future__ import annotations

import pytest

from apps.ops import approvals
from apps.ops.models import Mission
from apps.ops.tests.test_missions import air_payload, make_air_asset

pytestmark = pytest.mark.django_db


def test_air_dependency_waits_until_the_preposition_is_rendezvous_ready(
    as_user, planner, unit
):
    wing = make_air_asset(unit, "011")
    lead = make_air_asset(unit, "012")
    client = as_user(planner)

    preposition = air_payload(unit, title="Preposition to RPME")
    preposition["air_detail"].update(
        {
            "mission_configuration": "single",
            "aircraft_count": 1,
            "air_asset_ids": [wing.id],
            "air_asset": wing.id,
            "scheduled_departure": "2026-11-01T06:00:00",
            "scheduled_arrival": "2026-11-01T07:00:00",
        }
    )
    first = client.post("/api/missions/", preposition, format="json")
    assert first.status_code == 201, first.content

    formation = air_payload(unit, title="Formation sortie")
    formation.update(
        {
            "depends_on": first.json()["id"],
            "dependency_release": "rendezvous_ready",
            "dependency_buffer_minutes": 30,
        }
    )
    formation["air_detail"].update(
        {
            "mission_configuration": "element",
            "aircraft_count": 2,
            "air_asset_ids": [lead.id, wing.id],
            "air_asset": lead.id,
            "scheduled_departure": "2026-11-01T08:00:00",
            "scheduled_arrival": "2026-11-01T12:00:00",
        }
    )
    second = client.post("/api/missions/", formation, format="json")
    assert second.status_code == 201, second.content
    body = second.json()
    assert body["dependency"]["state"] == "waiting"
    assert body["dependency"]["satisfied"] is False

    mission_first = Mission.objects.get(pk=first.json()["id"])
    mission_first.status = "active"
    mission_first.save(update_fields=["status"])

    arrival = client.post(
        f"/api/missions/{mission_first.id}/air-arrival/",
        {"at": "2026-11-01T07:00:00", "ready": True},
        format="json",
    )
    assert arrival.status_code == 200, arrival.content
    assert arrival.json()["air_detail"]["actual_arrival"].startswith(
        "2026-11-01T07:00"
    )
    assert arrival.json()["air_detail"]["rendezvous_ready_at"].startswith(
        "2026-11-01T07:00"
    )

    mission_second = Mission.objects.get(pk=body["id"])
    assert approvals.dependency_blockers(mission_second) == []


def test_air_dependency_blocks_a_departure_inside_the_buffer(as_user, planner, unit):
    wing = make_air_asset(unit, "021")
    lead = make_air_asset(unit, "022")
    client = as_user(planner)

    preposition = air_payload(unit, title="Preposition inside buffer")
    preposition["air_detail"].update(
        {
            "mission_configuration": "single",
            "aircraft_count": 1,
            "air_asset_ids": [wing.id],
            "air_asset": wing.id,
            "scheduled_departure": "2026-11-01T06:00:00",
            "scheduled_arrival": "2026-11-01T07:00:00",
        }
    )
    first = client.post("/api/missions/", preposition, format="json")
    assert first.status_code == 201, first.content

    formation = air_payload(unit, title="Early formation")
    formation.update(
        {
            "depends_on": first.json()["id"],
            "dependency_release": "rendezvous_ready",
            "dependency_buffer_minutes": 30,
        }
    )
    formation["air_detail"].update(
        {
            "mission_configuration": "element",
            "aircraft_count": 2,
            "air_asset_ids": [lead.id, wing.id],
            "air_asset": lead.id,
            "scheduled_departure": "2026-11-01T07:15:00",
            "scheduled_arrival": "2026-11-01T12:00:00",
        }
    )
    second = client.post("/api/missions/", formation, format="json")
    assert second.status_code == 201, second.content

    mission_first = Mission.objects.get(pk=first.json()["id"])
    mission_first.status = "active"
    mission_first.save(update_fields=["status"])
    arrival = client.post(
        f"/api/missions/{mission_first.id}/air-arrival/",
        {"at": "2026-11-01T07:00:00", "ready": True},
        format="json",
    )
    assert arrival.status_code == 200, arrival.content

    mission_second = Mission.objects.get(pk=second.json()["id"])
    blockers = approvals.dependency_blockers(mission_second)
    assert [blocker["code"] for blocker in blockers] == ["dependency_buffer"]


def test_a_mission_cannot_depend_on_itself(as_user, planner, unit):
    client = as_user(planner)
    created = client.post("/api/missions/", air_payload(unit), format="json")
    assert created.status_code == 201, created.content
    response = client.patch(
        f"/api/missions/{created.json()['id']}/",
        {"depends_on": created.json()["id"]},
        format="json",
    )
    assert response.status_code == 400
    assert "depends_on" in response.json()["errors"]

"""What an assessment takes from its mission, and the weather over its route.

The mission details card copies the vehicles and itinerary the mission planned,
and the conditions ask a forecast provider about those places and hours. These
tests pin the copying, the deterministic mapping of a forecast onto the MRM's own
weather choices, and the endpoint that exposes both - using recorded forecast
payloads, never the network.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from apps.command.models import UnitMapMarker
from apps.legacy.models import HazardRegistry, MRMForm
from apps.ops import mrm_editor, weather
from apps.ops.models import (
    AirMissionDetail,
    Mission,
    RiskAssessment,
    TripItinerary,
    Vehicle,
    VehicleTrip,
    VehicleTripVehicle,
)

pytestmark = pytest.mark.django_db


def make_form(unit, creator) -> MRMForm:
    return MRMForm.objects.create(
        mrm_number=f"MRM-{uuid.uuid4().hex[:6]}",
        activity_mission="Convoy escort",
        created_by=creator,
        safety_objectives="Safe movement",
        status="draft",
        review_status="pending",
        original_unit_name=unit.name,
        original_unit_id=unit.id,
    )


def link_mission(form, unit, creator, *, start=None, end=None):
    mission = Mission.objects.create(
        mission_number=f"MSN-{uuid.uuid4().hex[:6]}",
        category="ground",
        title="Convoy escort",
        unit=unit,
        created_by=creator,
        start_at=start,
        end_at=end,
        location_name="Camp Aguinaldo",
        latitude=14.6091,
        longitude=121.0622,
    )
    RiskAssessment.objects.create(
        mission=mission, mrm_form=form, evaluator=creator, status="draft"
    )
    return mission


def add_trip(mission, unit, creator, stops):
    vehicle = Vehicle.objects.create(
        registration_number=f"PMV-{uuid.uuid4().hex[:4]}",
        vehicle_type="Truck",
        make_model="KM450",
        unit=unit,
        capacity=12,
    )
    trip = VehicleTrip.objects.create(
        trip_number=f"TRIP-{uuid.uuid4().hex[:6]}",
        title="Convoy escort",
        mission=mission,
        status="planned",
        departure_at=dt.datetime(2026, 11, 2, 5, 0),
        return_at=dt.datetime(2026, 11, 2, 17, 0),
        created_by=creator,
    )
    VehicleTripVehicle.objects.create(trip=trip, vehicle=vehicle)
    for sequence, (name, latitude, longitude) in enumerate(stops, start=1):
        TripItinerary.objects.create(
            trip=trip,
            sequence=sequence,
            location_name=name,
            latitude=latitude,
            longitude=longitude,
        )
    return vehicle, trip


# ---------------------------------------------------------------------------
# Mission details copied into the assessment
# ---------------------------------------------------------------------------


def test_mission_context_copies_vehicles_and_the_combined_itinerary(
    db, unit, creator
):
    form = make_form(unit, creator)
    mission = link_mission(form, unit, creator)
    vehicle, _trip = add_trip(
        mission,
        unit,
        creator,
        [
            ("Camp Aguinaldo", 14.6091, 121.0622),
            ("Checkpoint 2", 14.68, 121.12),
            ("Forward Base", 14.75, 121.2),
        ],
    )

    context = mrm_editor.mission_context(form)

    assert context["mission_id"] == mission.id
    # The model and registration both identify the vehicle that will be used.
    assert context["aircraft_vehicle"] == f"KM450 Nr {vehicle.registration_number}"
    assert context["routes"][0]["vehicles"] == [f"KM450 Nr {vehicle.registration_number}"]
    assert context["itinerary_text"] == "Camp Aguinaldo → Checkpoint 2 → Forward Base"
    # The pinned mission location and every stop are forecast targets.
    assert {"name", "latitude", "longitude"} <= set(context["forecast_points"][0])
    assert len(context["forecast_points"]) == 3
    assert context["window"]["start"].startswith("2026-11-02T05:00")
    assert context["window"]["end"].startswith("2026-11-02T17:00")


def test_air_mission_copies_its_aircraft_and_airfield_itinerary(db, unit, creator):
    form = make_form(unit, creator)
    mission = link_mission(form, unit, creator)
    mission.category = "air"
    mission.save(update_fields=["category"])
    AirMissionDetail.objects.create(
        mission=mission,
        aircraft_type="C-130",
        aircraft_tail_number="12345567",
        departure_location="Villamor Air Base",
        destination_location="Bancasi Airport",
    )

    context = mrm_editor.mission_context(form)

    assert context["aircraft_vehicle"] == "C-130 Nr 12345567"
    assert context["itinerary_text"] == "Villamor Air Base → Bancasi Airport"


def test_mission_context_is_empty_without_a_linked_mission(db, unit, creator):
    form = make_form(unit, creator)
    context = mrm_editor.mission_context(form)

    assert context["mission_id"] is None
    assert context["itinerary_text"] == ""
    assert context["aircraft_vehicle"] == ""
    assert context["forecast_points"] == []


def test_mission_context_lists_the_missions_own_personnel(db, unit, creator):
    from apps.ops.models import MissionPersonnel

    form = make_form(unit, creator)
    mission = link_mission(form, unit, creator)
    MissionPersonnel.objects.create(mission=mission, user=creator, role="Team Leader")

    context = mrm_editor.mission_context(form)

    assert len(context["personnel"]) == 1
    row = context["personnel"][0]
    assert row["user_id"] == creator.id
    assert row["mission_role"] == "Team Leader"
    assert row["full_name"]


def test_payload_carries_the_mission_for_the_editor(as_user, unit, creator):
    form = make_form(unit, creator)
    mission = link_mission(form, unit, creator)
    add_trip(mission, unit, creator, [("Camp Aguinaldo", 14.6091, 121.0622)])

    response = as_user(creator).get(f"/api/risk-register/{form.id}/details/")

    assert response.status_code == 200
    body = response.json()
    assert body["mission"]["mission_id"] == mission.id
    assert body["mission"]["itinerary_text"] == "Camp Aguinaldo"


# ---------------------------------------------------------------------------
# Hazard activity codes come from the unit's registry
# ---------------------------------------------------------------------------


def test_activity_codes_are_the_units_own_registry(db, as_user, unit, other_unit, creator):
    for code, description in (("1.1", "Terrain"), ("1.1", "Weather"), ("4.2", "Traffic")):
        HazardRegistry.objects.create(
            unit=unit.name,
            unit_id=unit,
            activity_code=code,
            hazard_description=description,
            before_likelihood=4,
            before_severity="B",
            before_risk_rating="4B",
            mitigations="Brief",
            after_likelihood=2,
            after_severity="D",
            after_risk_rating="2D",
            created_by=creator,
        )
    HazardRegistry.objects.create(
        unit=other_unit.name,
        unit_id=other_unit,
        activity_code="9.9",
        hazard_description="Someone else's hazard",
        before_likelihood=5,
        before_severity="A",
        before_risk_rating="5A",
        mitigations="—",
        after_likelihood=5,
        after_severity="A",
        after_risk_rating="5A",
        created_by=creator,
    )

    response = as_user(creator).get("/api/risk-register/hazard-codes/")

    assert response.status_code == 200
    results = response.json()["results"]
    rows = {row["activity_code"]: row["hazard_count"] for row in results}
    assert rows == {"1.1": 2, "4.2": 1}
    # The code the unit assesses most often leads the picker.
    assert [row["activity_code"] for row in results] == ["1.1", "4.2"]


# ---------------------------------------------------------------------------
# Forecast -> the MRM's own weather choices
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,precipitation,temperature,wind,expected",
    [
        (0, 0, 30, 5, weather.CLEAR),
        (3, 0, 30, 5, weather.CLOUDY),
        (61, 1.2, 28, 5, weather.RAINY),
        (95, 0, 28, 5, weather.RAINY),
        (45, 0, 28, 5, weather.COLD_FOGGY),
        (0, 0, 16, 5, weather.COLD_FOGGY),
        (0, 0, 31, 35, weather.WINDY),
        (0, 0, 35, 5, weather.HOT),
    ],
)
def test_forecast_classification(code, precipitation, temperature, wind, expected):
    assert weather.classify(code, precipitation, temperature, wind) == expected


def test_forecast_reports_the_worst_condition_over_the_route(monkeypatch):
    base = dt.datetime.now().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    hourly = {
        "time": [(base + dt.timedelta(hours=offset)).strftime("%Y-%m-%dT%H:%M") for offset in range(3)],
        "weather_code": [0, 63, 3],
        "precipitation": [0, 4.1, 0],
        "temperature_2m": [29, 27, 28],
        "wind_speed_10m": [6, 9, 8],
    }
    monkeypatch.setattr(weather, "_http_get_json", lambda url, timeout=8: {"hourly": hourly})

    result = weather.forecast_for(
        [{"name": "Camp Aguinaldo", "latitude": 14.6091, "longitude": 121.0622}],
        base,
        base + dt.timedelta(hours=6),
    )

    assert result["available"] is True
    assert result["weather"] == weather.RAINY
    assert result["hours"] == 3
    assert "Open-Meteo" in result["summary"]


def test_forecast_refuses_without_coordinates():
    with pytest.raises(weather.WeatherError):
        weather.forecast_for([], None, None)


def test_forecast_endpoint_uses_the_units_location_for_a_manual_mrm(
    monkeypatch, as_user, unit, creator
):
    form = make_form(unit, creator)
    UnitMapMarker.objects.create(
        unit=unit,
        latitude=14.6091,
        longitude=121.0622,
        place_name="Camp Aguinaldo",
    )
    moment = dt.datetime.now().replace(minute=0, second=0, microsecond=0) + dt.timedelta(
        hours=1
    )
    hourly = {
        "time": [moment.strftime("%Y-%m-%dT%H:%M")],
        "weather_code": [3],
        "precipitation": [0],
        "temperature_2m": [28],
        "wind_speed_10m": [7],
    }
    requested = {}

    def fake_get(url, timeout=8):
        requested["url"] = url
        return {"hourly": hourly}

    monkeypatch.setattr(weather, "_http_get_json", fake_get)

    response = as_user(creator).get(f"/api/risk-register/{form.id}/forecast/")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["weather"] == weather.CLOUDY
    assert body["points"] == 1
    assert body["places"] == ["Camp Aguinaldo"]
    assert "Camp Aguinaldo" in body["summary"]
    assert "latitude=14.60910" in requested["url"]
    assert "longitude=121.06220" in requested["url"]


def test_forecast_without_a_mission_or_marker_says_what_to_set(
    as_user, unit, creator
):
    form = make_form(unit, creator)

    response = as_user(creator).get(f"/api/risk-register/{form.id}/forecast/")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "Operational Mapping" in body["detail"]


def test_forecast_endpoint_sets_the_weather_from_the_forecast(
    monkeypatch, as_user, unit, creator
):
    form = make_form(unit, creator)
    mission = link_mission(
        form,
        unit,
        creator,
        start=dt.datetime.now() + dt.timedelta(days=1),
        end=dt.datetime.now() + dt.timedelta(days=1, hours=6),
    )
    add_trip(mission, unit, creator, [("Camp Aguinaldo", 14.6091, 121.0622)])

    hourly = {
        "time": [(dt.datetime.now() + dt.timedelta(days=1, hours=2)).strftime("%Y-%m-%dT%H:%M")],
        "weather_code": [3],
        "precipitation": [0],
        "temperature_2m": [28],
        "wind_speed_10m": [7],
    }
    monkeypatch.setattr(weather, "_http_get_json", lambda url, timeout=8: {"hourly": hourly})

    response = as_user(creator).get(f"/api/risk-register/{form.id}/forecast/")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["weather"] == weather.CLOUDY
    assert body["points"] == 1

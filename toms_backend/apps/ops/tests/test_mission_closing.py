"""Ending a mission from the list: when the action is offered, and why.

The Operations Center may close a mission in progress once the plan itself says
the work is over - its estimated end has passed, or every vehicle trip it
planned is completed. The API reports that readiness beside the mission so the
list and the endpoint can never disagree about it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.core import timeutils
from apps.ops.models import Mission, Vehicle, VehicleRun, VehicleTrip

pytestmark = pytest.mark.django_db


def make_mission(unit, user, **overrides):
    data = {
        "mission_number": "TOWEMS-GO-CLOSE-001",
        "category": Mission.CATEGORY_GROUND,
        "title": "Convoy to North Ridge",
        "unit": unit,
        "created_by": user,
        "status": "active",
        "start_at": timeutils.now() - dt.timedelta(hours=6),
        "end_at": timeutils.now() - dt.timedelta(hours=2),
    }
    data.update(overrides)
    return Mission.objects.create(**data)


def make_trip(mission, user, status="planned", number="TRIP-CLOSE-001"):
    return VehicleTrip.objects.create(
        trip_number=number,
        title="Convoy move",
        mission=mission,
        created_by=user,
        status=status,
    )


def closing_of(client, mission):
    response = client.get(f"/api/missions/{mission.id}/")
    assert response.status_code == 200, response.content
    return response.json()["closing"]


def test_a_mission_past_its_planned_end_can_be_ended(as_user, planner, unit):
    mission = make_mission(unit, planner)

    closing = closing_of(as_user(planner), mission)

    assert closing["available"] is True
    assert closing["reasons"] == ["planned_end_passed"]
    assert "estimated end" in closing["reason"].lower()
    assert closing["open_runs"] == 0


def test_a_mission_with_every_trip_completed_can_be_ended(as_user, planner, unit):
    """The plan is finished even if the clock has not run out."""
    mission = make_mission(unit, planner, end_at=timeutils.now() + dt.timedelta(hours=4))
    make_trip(mission, planner, status="completed")

    closing = closing_of(as_user(planner), mission)

    assert closing["available"] is True
    assert closing["reasons"] == ["trips_completed"]
    assert closing["reason"] == "The vehicle trip is completed."


def test_a_mission_still_running_is_not_endable(as_user, planner, unit):
    mission = make_mission(unit, planner, end_at=timeutils.now() + dt.timedelta(hours=4))
    make_trip(mission, planner, status="in_progress")

    closing = closing_of(as_user(planner), mission)

    assert closing["available"] is False
    assert closing["reasons"] == []
    assert closing["reason"] == ""


def test_a_cancelled_trip_does_not_make_an_otherwise_running_mission_endable(
    as_user, planner, unit
):
    mission = make_mission(unit, planner, end_at=timeutils.now() + dt.timedelta(hours=4))
    make_trip(mission, planner, status="in_progress", number="TRIP-LIVE")
    make_trip(mission, planner, status="cancelled", number="TRIP-DROPPED")

    assert closing_of(as_user(planner), mission)["available"] is False


def test_a_closed_mission_is_not_endable(as_user, planner, unit):
    for status in ("completed", "cancelled", "planned"):
        mission = make_mission(
            unit,
            planner,
            status=status,
            mission_number=f"TOWEMS-GO-{status.upper()}",
        )
        assert closing_of(as_user(planner), mission)["available"] is False


def test_the_confirmation_knows_which_runs_it_would_cancel(as_user, planner, unit):
    """A run still open is cancelled by closing, so the count is reported."""
    mission = make_mission(unit, planner)
    trip = make_trip(mission, planner, status="in_progress")
    vehicle = Vehicle.objects.create(
        registration_number="KM450-CLOSE-1", vehicle_type="KM450", unit=unit
    )
    VehicleRun.objects.create(
        mission=mission,
        trip=trip,
        vehicle=vehicle,
        driver=planner,
        status=VehicleRun.STATUS_ACTIVE,
        etd=mission.start_at,
    )

    closing = closing_of(as_user(planner), mission)

    assert closing["available"] is True
    assert closing["open_runs"] == 1

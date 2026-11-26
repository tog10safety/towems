"""The driver's phone keeps reporting after the browser session expires.

A Django session lives an hour; a convoy does not. So the same narrow token the
alert watcher uses may also post the driver's own breadcrumbs - and only those:
the run has to be assigned to that account and still open, and the write reaches
no other endpoint. These tests pin both the capability and the fence around it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.core.factories import make_user
from apps.ops.models import (
    Mission,
    TrackingPing,
    Vehicle,
    VehicleRun,
    VehicleTrip,
    VehicleTripVehicle,
)

pytestmark = pytest.mark.django_db


def _sign_in(client, user, password=None):
    from apps.core.factories import PASSWORD

    client.get("/api/auth/csrf/")
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    return client.post(
        "/api/auth/login/",
        {"username": user.username, "password": password or PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )


def _alert_client(raw: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Device {raw}")
    return client


def _token_for(client, user) -> str:
    assert _sign_in(client, user).status_code == 200
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    response = client.post(
        "/api/auth/devices/token/",
        {"device_id": "install-track-1", "label": "Android phone"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 201, response.content
    return response.json()["token"]


@pytest.fixture
def driver(db, unit):
    return make_user(unit=unit, authority="Creator")


def _mission_with_run(planner, unit, driver, *, number="TOWEMS-GO-TOK-001"):
    mission = Mission.objects.create(
        mission_number=number,
        category=Mission.CATEGORY_GROUND,
        title="Convoy to North Ridge",
        unit=unit,
        created_by=planner,
        status="active",
        start_at=dt.datetime(2026, 11, 1, 6, 0),
        end_at=dt.datetime(2026, 11, 1, 18, 0),
    )
    tail = number.rsplit("-", 1)[-1]
    vehicle = Vehicle.objects.create(
        registration_number=f"KM450-765{tail}",
        vehicle_type="KM450",
        unit=unit,
        capacity=12,
    )
    trip = VehicleTrip.objects.create(
        trip_number=f"TRIP-TOK-{tail}",
        title="Convoy move",
        mission=mission,
        created_by=planner,
        status="planned",
        departure_at=dt.datetime(2026, 11, 1, 6, 0),
        return_at=dt.datetime(2026, 11, 1, 17, 0),
    )
    VehicleTripVehicle.objects.create(trip=trip, vehicle=vehicle, driver=driver)
    run = VehicleRun.objects.create(
        mission=mission,
        trip=trip,
        vehicle=vehicle,
        driver=driver,
        status=VehicleRun.STATUS_ACTIVE,
    )
    return mission, run


@pytest.fixture
def open_run(db, planner, unit, driver):
    return _mission_with_run(planner, unit, driver)


def test_the_token_is_told_which_run_it_should_record(open_run, driver):
    _mission, run = open_run
    alert = _alert_client(_token_for(APIClient(), driver))

    body = alert.get("/api/auth/devices/tracking/").json()
    assert body["run"] == run.id
    assert body["mission"] == run.mission_id


def test_the_token_posts_breadcrumbs_with_no_session_at_all(open_run, driver):
    _mission, run = open_run
    alert = _alert_client(_token_for(APIClient(), driver))

    response = alert.post(
        "/api/auth/devices/tracking/",
        {
            "run": run.id,
            "pings": [
                {
                    "latitude": 14.61,
                    "longitude": 121.07,
                    "accuracy_m": 9,
                    "recorded_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=20))
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
            ],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["accepted"] == 1
    assert TrackingPing.objects.filter(run=run).count() == 1

    run.refresh_from_db()
    assert run.last_latitude == pytest.approx(14.61)


def test_a_token_cannot_report_a_run_it_does_not_drive(open_run, unit):
    _mission, run = open_run
    stranger = make_user(unit=unit, authority="Creator")
    alert = _alert_client(_token_for(APIClient(), stranger))

    # Somebody else's run is not even confirmed to exist for this token.
    response = alert.post(
        "/api/auth/devices/tracking/",
        {
            "run": run.id,
            "pings": [{"latitude": 14.6, "longitude": 121.0}],
        },
        format="json",
    )
    assert response.status_code in (403, 400)
    assert TrackingPing.objects.filter(run=run).count() == 0


def test_the_token_still_cannot_write_elsewhere(open_run, driver):
    mission, run = open_run
    alert = _alert_client(_token_for(APIClient(), driver))

    # The web endpoint keeps its session-only authentication.
    assert (
        alert.post(
            f"/api/missions/{mission.id}/tracking/ping/",
            {"run": run.id, "pings": [{"latitude": 14.6, "longitude": 121.0}]},
            format="json",
        ).status_code
        in (401, 403)
    )
    assert alert.post("/api/auth/logout/", {}, format="json").status_code in (401, 403)


def test_a_signed_out_phone_reports_nothing(open_run):
    _mission, run = open_run

    anonymous = APIClient()
    assert anonymous.get("/api/auth/devices/tracking/").status_code in (401, 403)
    assert (
        anonymous.post(
            "/api/auth/devices/tracking/",
            {"run": run.id, "pings": [{"latitude": 14.6, "longitude": 121.0}]},
            format="json",
        ).status_code
        in (401, 403)
    )
    assert TrackingPing.objects.count() == 0

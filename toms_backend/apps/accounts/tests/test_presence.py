"""Presence: who the dashboard rail may show as online.

Presence is derived from the last authenticated request, so these tests exercise
the middleware (a session request stamps ``last_seen``), the throttle, and the
scope of the list. Nothing here depends on the browser sending a heartbeat.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from apps.accounts.models import UserProfile
from apps.accounts.presence import ONLINE_WINDOW_MINUTES, _last_stamp, stamp_presence
from apps.core import timeutils
from apps.core.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_throttle():
    """The per-process throttle must not leak between tests."""
    _last_stamp.clear()
    yield
    _last_stamp.clear()


def test_a_session_request_stamps_last_seen(login, api, creator):
    assert login(creator).status_code == 200

    # The login request itself is anonymous until the view runs, so the stamp
    # comes from the next request - exactly as it would in a browser.
    assert api.get("/api/auth/me/").status_code == 200
    assert UserProfile.objects.get(user_id=creator.id).last_seen is not None


def test_the_stamp_is_throttled_to_once_a_minute(login, api, creator):
    login(creator)
    api.get("/api/auth/me/")
    first = UserProfile.objects.get(user_id=creator.id).last_seen

    api.get("/api/auth/me/")
    api.get("/api/dashboard/")

    assert UserProfile.objects.get(user_id=creator.id).last_seen == first


def test_the_throttle_is_per_account(login, api, creator, safety):
    login(creator)
    api.get("/api/auth/me/")
    assert UserProfile.objects.get(user_id=creator.id).last_seen is not None

    # A different account is stamped on its own first request.
    from django.test import Client

    other = Client()
    other.force_login(safety)
    other.get("/api/auth/me/")
    assert UserProfile.objects.get(user_id=safety.id).last_seen is not None


def test_online_lists_people_seen_inside_the_window(as_user, creator, safety):
    UserProfile.objects.create(user_id=creator.id, last_seen=timeutils.now())
    UserProfile.objects.create(
        user_id=safety.id,
        last_seen=timeutils.now() - timedelta(minutes=ONLINE_WINDOW_MINUTES, seconds=30),
    )

    body = as_user(creator).get("/api/presence/").json()

    serials = [row["serial_number"] for row in body["users"]]
    assert serials == [creator.serial_number]
    assert body["count"] == 1
    assert body["window_minutes"] == ONLINE_WINDOW_MINUTES


def test_the_rail_identifies_people_by_serial_number_and_picture(as_user, creator):
    UserProfile.objects.create(
        user_id=creator.id, last_seen=timeutils.now(), avatar_filename="avatar_1_a.jpg"
    )

    row = as_user(creator).get("/api/presence/").json()["users"][0]

    assert row["serial_number"] == creator.serial_number
    assert row["avatar_url"] == f"/api/personnel/{creator.id}/avatar/?v=avatar_1_a.jpg"
    assert row["is_self"] is True
    # Name and designation travel for the tooltip only; rank is its own field.
    assert row["full_name"] == creator.get_full_name()
    assert row["rank"] == creator.rank


def test_a_person_without_a_picture_has_no_avatar_url(as_user, creator):
    UserProfile.objects.create(user_id=creator.id, last_seen=timeutils.now())

    assert as_user(creator).get("/api/presence/").json()["users"][0]["avatar_url"] == ""


def test_online_is_scoped_to_the_callers_units(as_user, creator, other_unit):
    stranger = make_user(unit=other_unit, authority="Creator")
    UserProfile.objects.create(user_id=creator.id, last_seen=timeutils.now())
    UserProfile.objects.create(user_id=stranger.id, last_seen=timeutils.now())

    rows = as_user(creator).get("/api/presence/").json()["users"]

    assert [row["serial_number"] for row in rows] == [creator.serial_number]


def test_subordinates_appear_for_their_command(as_user, unit, other_unit, creator):
    """A child unit's people are inside the parent's scope."""
    from apps.core.factories import make_unit

    child = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    subordinate = make_user(unit=child, authority="Creator")
    elsewhere = make_user(unit=other_unit, authority="Creator")
    UserProfile.objects.create(user_id=subordinate.id, last_seen=timeutils.now())
    UserProfile.objects.create(user_id=elsewhere.id, last_seen=timeutils.now())

    body = as_user(creator).get("/api/presence/").json()

    serials = [row["serial_number"] for row in body["users"]]
    assert subordinate.serial_number in serials
    # The caller is stamped by the request itself, so they are online too.
    assert creator.serial_number in serials
    assert elsewhere.serial_number not in serials


def test_online_requires_authentication(api):
    assert api.get("/api/presence/").status_code in (401, 403)


def test_stamping_is_safe_for_a_user_with_no_profile_row(creator):
    """A first-ever request creates the side row instead of failing."""
    assert not UserProfile.objects.filter(user_id=creator.id).exists()

    stamp_presence(creator)

    assert UserProfile.objects.get(user_id=creator.id).last_seen is not None


def test_the_endpoint_stamps_the_caller(as_user, creator):
    """DRF-only callers never pass the session middleware, so the view stamps."""
    assert as_user(creator).get("/api/presence/").status_code == 200
    assert UserProfile.objects.get(user_id=creator.id).last_seen is not None

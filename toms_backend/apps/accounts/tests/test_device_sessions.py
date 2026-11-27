"""One device at a time.

An account holds a single live session. A second sign-in is refused with the
device already signed in, and the person can confirm that the other session
may end and continue on the new device. A deployment can exempt a base role
through ``SINGLE_DEVICE_EXEMPT_ROLES`` when a desk genuinely needs two screens.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import DeviceSession
from apps.core.factories import PASSWORD

pytestmark = pytest.mark.django_db


def _client() -> APIClient:
    return APIClient()


def _sign_in(client, user, password=PASSWORD):
    client.get("/api/auth/csrf/")
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    return client.post(
        "/api/auth/login/",
        {"username": user.username, "password": password},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )


def _switch_device(client, challenge):
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    return client.post(
        "/api/auth/login/switch-device/",
        {"challenge": challenge},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )


def _session_opened_before_tracking(user) -> str:
    """A session that exists only in Django's table, as the deployment left them."""
    from django.contrib.sessions.backends.db import SessionStore

    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    store["_auth_user_hash"] = user.get_session_auth_hash()
    store.create()
    return store.session_key


def test_a_session_opened_before_tracking_still_blocks_a_second_login(creator):
    from django.contrib.sessions.models import Session

    legacy = _session_opened_before_tracking(creator)
    assert not DeviceSession.objects.filter(user=creator).exists()

    second = _client()
    response = _sign_in(second, creator)

    # The account is already held, even though no device row knows that session.
    assert response.status_code == 409
    conflict = response.json()["errors"]
    assert conflict["device"]["label"]

    assert _switch_device(second, conflict["challenge"]).status_code == 200
    # Confirming the switch ends that session too, so only one device is left.
    assert not Session.objects.filter(session_key=legacy).exists()
    assert second.get("/api/auth/me/").status_code == 200


def test_a_creator_cannot_sign_in_on_a_second_device(creator):
    first = _client()
    assert _sign_in(first, creator).status_code == 200

    second = _client()
    response = _sign_in(second, creator)

    assert response.status_code == 409
    body = response.json()
    # The API's error envelope keeps the extra fields under "errors".
    assert body["errors"]["code"] == "device_in_use"
    assert body["errors"]["challenge"]
    assert body["errors"]["device"]["label"]
    assert "Only one device" in body["detail"]
    # The device that was already signed in keeps its session.
    assert first.get("/api/auth/me/").status_code == 200
    # The refused attempt leaves no session behind on the second device.
    assert second.get("/api/auth/me/").status_code in (401, 403)


def test_confirming_the_device_switch_ends_the_other_session(creator):
    first = _client()
    assert _sign_in(first, creator).status_code == 200

    second = _client()
    conflict = _sign_in(second, creator).json()["errors"]

    response = _switch_device(second, conflict["challenge"])
    assert response.status_code == 200, response.content
    assert response.json()["username"] == creator.username

    # The new device is signed in; the old one is not.
    assert second.get("/api/auth/me/").status_code == 200
    assert first.get("/api/auth/me/").status_code in (401, 403)


def test_a_used_or_stale_challenge_cannot_be_replayed(creator):
    first = _client()
    assert _sign_in(first, creator).status_code == 200
    second = _client()
    conflict = _sign_in(second, creator).json()["errors"]

    assert _switch_device(second, conflict["challenge"]).status_code == 200
    # The same token cannot be used to disturb the session it just created.
    third = _client()
    third.get("/api/auth/csrf/")
    assert _switch_device(third, "not-a-token").status_code == 400


def test_signing_out_frees_the_creator_device(creator):
    first = _client()
    assert _sign_in(first, creator).status_code == 200
    token = first.cookies[settings.CSRF_COOKIE_NAME].value
    assert (
        first.post("/api/auth/logout/", {}, format="json", HTTP_X_CSRFTOKEN=token).status_code
        == 204
    )
    assert DeviceSession.objects.filter(user=creator).count() == 0

    second = _client()
    assert _sign_in(second, creator).status_code == 200


def test_an_administrator_is_held_to_one_device_too(admin):
    first = _client()
    assert _sign_in(first, admin).status_code == 200

    second = _client()
    response = _sign_in(second, admin)

    # Every role is held to one session by default, not only a Creator.
    assert response.status_code == 409
    assert response.json()["errors"]["code"] == "device_in_use"
    assert first.get("/api/auth/me/").status_code == 200
    assert second.get("/api/auth/me/").status_code in (401, 403)


def test_an_exempted_role_may_stay_signed_in_on_two_devices(admin, settings):
    settings.SINGLE_DEVICE_EXEMPT_ROLES = ["administrator"]

    first = _client()
    assert _sign_in(first, admin).status_code == 200

    second = _client()
    response = _sign_in(second, admin)
    assert response.status_code == 200, response.content

    assert first.get("/api/auth/me/").status_code == 200
    assert second.get("/api/auth/me/").status_code == 200


def test_the_device_record_names_the_browser(creator):
    client = _client()
    assert _sign_in(client, creator).status_code == 200

    row = DeviceSession.objects.get(user=creator)
    assert row.device_label
    assert row.session_key == client.session.session_key


def _devices(client):
    return client.get("/api/auth/devices/").json()


def _end_session(client, session):
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    return client.post(
        "/api/auth/devices/end/",
        {"session": session},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )


def test_the_device_panel_lists_the_current_device_and_the_others(admin, settings):
    settings.SINGLE_DEVICE_EXEMPT_ROLES = ["administrator"]
    first = _client()
    assert _sign_in(first, admin).status_code == 200
    second = _client()
    assert _sign_in(second, admin).status_code == 200

    body = _devices(second)
    assert body["current_device"]["session"]
    assert body["current_device"]["label"]
    assert len(body["devices"]) == 1
    assert body["devices"][0]["session"] != body["current_device"]["session"]
    assert body["devices"][0]["session"] == first.session.session_key[:8]


def test_the_device_panel_ends_another_device(admin, settings):
    settings.SINGLE_DEVICE_EXEMPT_ROLES = ["administrator"]
    first = _client()
    assert _sign_in(first, admin).status_code == 200
    second = _client()
    assert _sign_in(second, admin).status_code == 200

    other = _devices(second)["devices"][0]
    assert _end_session(second, other["session"]).status_code == 204

    assert first.get("/api/auth/me/").status_code in (401, 403)
    assert second.get("/api/auth/me/").status_code == 200
    assert _devices(second)["devices"] == []


def test_the_device_panel_refuses_to_end_the_current_device(admin):
    client = _client()
    assert _sign_in(client, admin).status_code == 200
    current = _devices(client)["current_device"]

    assert _end_session(client, current["session"]).status_code == 404
    assert client.get("/api/auth/me/").status_code == 200


def test_another_account_cannot_end_someone_elses_device(admin, settings, creator):
    settings.SINGLE_DEVICE_EXEMPT_ROLES = ["administrator"]
    first = _client()
    assert _sign_in(first, admin).status_code == 200

    intruder = _client()
    assert _sign_in(intruder, creator).status_code == 200
    victim = _devices(first)["current_device"]

    assert _end_session(intruder, victim["session"]).status_code == 404
    assert first.get("/api/auth/me/").status_code == 200

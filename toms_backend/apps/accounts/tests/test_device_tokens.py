"""The phone's alert watcher must outlive the one-hour browser session.

The device token is the narrow credential that makes that possible: minted
from a live, fully verified session, read-only, bound to one install, revoked
with that session, and expiring on its own. These tests pin each of those
properties, because the whole point is that the web login itself is unchanged.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import device_tokens
from apps.accounts.models import DeviceToken
from apps.core.factories import PASSWORD

pytestmark = pytest.mark.django_db


def _sign_in(client, user, password=PASSWORD):
    client.get("/api/auth/csrf/")
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    return client.post(
        "/api/auth/login/",
        {"username": user.username, "password": password},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )


def _mint(client, device_id="install-0001"):
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    return client.post(
        "/api/auth/devices/token/",
        {"device_id": device_id, "label": "Android phone"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )


def _alert_client(raw: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Device {raw}")
    return client


def _token_for(client, user) -> str:
    assert _sign_in(client, user).status_code == 200
    response = _mint(client)
    assert response.status_code == 201, response.content
    return response.json()["token"]


def test_a_live_session_mints_a_read_only_token(creator):
    client = APIClient()
    raw = _token_for(client, creator)

    row = DeviceToken.objects.get(user=creator)
    assert raw.startswith(device_tokens.TOKEN_PREFIX)
    # Only the digest is stored; the raw value is never persisted.
    assert row.token_hash != raw
    assert row.token_hash == device_tokens._digest(raw)
    assert row.session_key == client.session.session_key
    assert row.expires_at > timezone.now()
    assert row.expires_at <= timezone.now() + dt.timedelta(
        days=settings.DEVICE_TOKEN_AGE_DAYS + 1
    )


def test_the_token_reads_the_pulse_and_the_message_summary(creator):
    raw = _token_for(APIClient(), creator)
    alert = _alert_client(raw)

    assert alert.get("/api/notifications/pulse/?since=0").status_code == 200
    assert alert.get("/api/messaging/summary/").status_code == 200


def test_the_token_cannot_reach_the_rest_of_the_api(creator):
    raw = _token_for(APIClient(), creator)
    alert = _alert_client(raw)

    assert alert.get("/api/auth/me/").status_code in (401, 403)
    assert alert.get("/api/notifications/").status_code in (401, 403)
    assert alert.post("/api/auth/logout/", {}, format="json").status_code in (401, 403)
    # A token can never mint another token.
    assert alert.post(
        "/api/auth/devices/token/", {"device_id": "install-0002"}, format="json"
    ).status_code in (401, 403)


def test_the_token_still_works_after_the_browser_session_expires(creator):
    client = APIClient()
    raw = _token_for(client, creator)
    assert client.get("/api/auth/me/").status_code == 200

    # The deployment's one-hour session is gone; the phone's credential is not.
    from django.contrib.sessions.models import Session

    Session.objects.all().delete()
    assert client.get("/api/auth/me/").status_code in (401, 403)

    alert = _alert_client(raw)
    assert alert.get("/api/notifications/pulse/?since=0").status_code == 200
    assert alert.get("/api/messaging/summary/").status_code == 200


def test_signing_out_revokes_the_token(creator):
    client = APIClient()
    raw = _token_for(client, creator)

    csrf = client.cookies[settings.CSRF_COOKIE_NAME].value
    assert (
        client.post(
            "/api/auth/logout/", {}, format="json", HTTP_X_CSRFTOKEN=csrf
        ).status_code
        == 204
    )
    assert _alert_client(raw).get("/api/notifications/pulse/?since=0").status_code in (
        401,
        403,
    )


def test_ending_the_other_session_revokes_its_token(creator):
    first = APIClient()
    raw = _token_for(first, creator)

    second = APIClient()
    conflict = _sign_in(second, creator).json()["errors"]
    csrf = second.cookies[settings.CSRF_COOKIE_NAME].value
    assert (
        second.post(
            "/api/auth/login/switch-device/",
            {"challenge": conflict["challenge"]},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        ).status_code
        == 200
    )
    assert _alert_client(raw).get("/api/notifications/pulse/?since=0").status_code in (
        401,
        403,
    )


def test_a_password_change_revokes_every_token(creator):
    client = APIClient()
    raw = _token_for(client, creator)

    csrf = client.cookies[settings.CSRF_COOKIE_NAME].value
    response = client.post(
        "/api/auth/password/",
        {"current_password": PASSWORD, "new_password": "Str0ng!Passw0rd"},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 200, response.content
    assert _alert_client(raw).get("/api/notifications/pulse/?since=0").status_code in (
        401,
        403,
    )


def test_an_expired_token_is_refused(creator):
    raw = _token_for(APIClient(), creator)
    DeviceToken.objects.filter(user=creator).update(
        expires_at=timezone.now() - dt.timedelta(seconds=1)
    )
    assert _alert_client(raw).get("/api/notifications/pulse/?since=0").status_code in (
        401,
        403,
    )


def test_a_deactivated_account_loses_its_alerts(creator):
    raw = _token_for(APIClient(), creator)
    creator.is_active = False
    creator.save(update_fields=["is_active"])

    assert _alert_client(raw).get("/api/notifications/pulse/?since=0").status_code in (
        401,
        403,
    )


def test_minting_again_rotates_the_install_token(creator):
    client = APIClient()
    assert _sign_in(client, creator).status_code == 200
    first = _mint(client).json()["token"]
    second = _mint(client).json()["token"]

    assert first != second
    assert DeviceToken.objects.filter(user=creator, device_id="install-0001").count() == 1
    assert _alert_client(first).get("/api/notifications/pulse/?since=0").status_code in (
        401,
        403,
    )
    assert _alert_client(second).get("/api/notifications/pulse/?since=0").status_code == 200


def test_minting_needs_a_session(api):
    response = api.post(
        "/api/auth/devices/token/", {"device_id": "install-0001"}, format="json"
    )
    assert response.status_code in (401, 403)
    assert not DeviceToken.objects.exists()


def test_minting_a_token_needs_csrf(creator):
    strict = APIClient(enforce_csrf_checks=True)
    assert strict.get("/api/auth/csrf/").status_code == 200
    csrf = strict.cookies[settings.CSRF_COOKIE_NAME].value
    assert (
        strict.post(
            "/api/auth/login/",
            {"username": creator.username, "password": PASSWORD},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        ).status_code
        == 200
    )

    # Same session, but no CSRF token: a cross-site page cannot mint one.
    response = strict.post(
        "/api/auth/devices/token/", {"device_id": "install-0001"}, format="json"
    )
    assert response.status_code == 403
    assert not DeviceToken.objects.exists()

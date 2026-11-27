"""Phone push: the notification hook, the FCM payload and token registration.

The point of push is that a phone with TOWEMS closed still hears about a
message. These tests pin the pieces the server owns: one message per created
notification, a data-only payload the app can turn into its audible channel,
and a registration token stored against the install's own row.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import DeviceToken
from apps.core.factories import PASSWORD
from apps.ops import push
from apps.ops.notifications import notify

pytestmark = pytest.mark.django_db


class _Reply:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def _sign_in(client, user):
    client.get("/api/auth/csrf/")
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    return client.post(
        "/api/auth/login/",
        {"username": user.username, "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )


def _device_token(client, user, device_id="install-0001"):
    assert _sign_in(client, user).status_code == 200
    csrf = client.cookies[settings.CSRF_COOKIE_NAME].value
    response = client.post(
        "/api/auth/devices/token/",
        {"device_id": device_id},
        format="json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert response.status_code == 201, response.content
    return response.json()["token"]


def test_a_new_notification_is_queued_for_push(creator, django_capture_on_commit_callbacks):
    queued = []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(push, "configured", lambda: True)
        patch.setattr(push, "queue_notification", lambda **kwargs: queued.append(kwargs))
        with django_capture_on_commit_callbacks(execute=True):
            notify(
                creator,
                title="New message from Juan",
                message="Open Messages to read it.",
                category="message",
                priority="high",
                action_url="/messages?conversation=7",
            )

    assert len(queued) == 1
    assert queued[0]["recipient_id"] == creator.id
    assert queued[0]["category"] == "message"
    assert queued[0]["path"] == "/messages?conversation=7"


def test_nothing_is_queued_without_firebase_configuration(
    creator, django_capture_on_commit_callbacks
):
    with override_settings(FCM_PROJECT_ID="", FCM_CREDENTIALS_FILE=""):
        assert push.configured() is False
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            notify(creator, title="Routine")

    assert callbacks == []


def test_the_payload_is_data_only_and_never_chat_text():
    """The phone draws the notification; FCM must not draw one of its own."""
    message = push._message(
        "phone-token",
        title="New message from Juan",
        body="Open Messages to read it.",
        path="/messages?conversation=7",
        count=4,
        kind="message",
    )["message"]

    assert "notification" not in message
    assert message["android"]["priority"] == "high"
    assert message["data"]["kind"] == "message"
    assert message["data"]["count"] == "4"
    assert message["data"]["title"] == "New message from Juan"
    assert message["data"]["path"] == "/messages?conversation=7"
    # The body is the server's pointer, not the conversation itself.
    assert message["data"]["body"] == "Open Messages to read it."


def test_a_phone_registers_its_fcm_token_with_its_alert_token(creator):
    raw = _device_token(APIClient(), creator)
    phone = APIClient()
    phone.credentials(HTTP_AUTHORIZATION=f"Device {raw}")

    response = phone.post(
        "/api/auth/devices/push/", {"fcm_token": "fcm-abc-123"}, format="json"
    )

    assert response.status_code == 200, response.content
    assert response.json()["registered"] is True
    assert DeviceToken.objects.get(user=creator).fcm_token == "fcm-abc-123"

    # An empty token unregisters the install (FCM token revoked by the user).
    again = phone.post("/api/auth/devices/push/", {"fcm_token": ""}, format="json")
    assert again.status_code == 200
    assert DeviceToken.objects.get(user=creator).fcm_token == ""


def test_a_session_cannot_register_a_token_against_another_install(
    as_user, creator, evaluator
):
    """With only a browser session there is no install named by the credential,
    so the body's device_id is looked up inside this account - never another's."""
    other = DeviceToken.objects.create(
        user=evaluator,
        device_id="install-9999",
        token_hash="x" * 64,
        expires_at=timezone.now() + dt.timedelta(days=30),
    )

    response = as_user(creator).post(
        "/api/auth/devices/push/",
        {"fcm_token": "fcm-abc-123", "device_id": other.device_id},
        format="json",
    )

    assert response.status_code == 400
    other.refresh_from_db()
    assert other.fcm_token == ""


def test_an_alert_token_registers_only_its_own_row(creator, evaluator):
    """A token copied from another phone cannot attach a push token to it."""
    raw = _device_token(APIClient(), creator)
    other = DeviceToken.objects.create(
        user=evaluator,
        device_id="install-9999",
        token_hash="z" * 64,
        expires_at=timezone.now() + dt.timedelta(days=30),
    )
    phone = APIClient()
    phone.credentials(HTTP_AUTHORIZATION=f"Device {raw}")

    response = phone.post(
        "/api/auth/devices/push/",
        {"fcm_token": "fcm-abc-123", "device_id": other.device_id},
        format="json",
    )

    assert response.status_code == 200
    assert DeviceToken.objects.get(user=creator).fcm_token == "fcm-abc-123"
    other.refresh_from_db()
    assert other.fcm_token == ""


def test_a_dead_fcm_token_is_cleared(creator, tmp_path, monkeypatch):
    key = tmp_path / "service-account.json"
    key.write_text("{}", encoding="utf-8")
    row = DeviceToken.objects.create(
        user=creator,
        device_id="install-0001",
        token_hash="y" * 64,
        fcm_token="fcm-gone",
        expires_at=timezone.now() + dt.timedelta(days=30),
    )

    monkeypatch.setattr(push, "configured", lambda: True)
    monkeypatch.setattr(push, "project_id", lambda: "towems-test")
    monkeypatch.setattr(push, "_session", lambda: None)
    monkeypatch.setattr(push, "_badge_count", lambda user_id: 3)
    monkeypatch.setattr(
        push,
        "_post",
        lambda session, url, payload: _Reply(
            404, {"error": {"details": [{"errorCode": "UNREGISTERED"}]}}
        ),
    )

    assert push.send_to_user(creator.id, title="Alert", body="", path="/") == 0
    row.refresh_from_db()
    assert row.fcm_token == ""

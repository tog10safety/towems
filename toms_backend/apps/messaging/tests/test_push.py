"""Closed-tab browser push: subscriptions, dispatch rules, cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pywebpush import WebPushException

from apps.core.factories import make_user
from apps.messaging import push, services
from apps.messaging.models import PushSubscription

pytestmark = pytest.mark.django_db

ENDPOINT = "https://fcm.googleapis.com/wp/abc123"


def subscription_payload(endpoint=ENDPOINT, p256dh="browser-public-key"):
    return {
        "endpoint": endpoint,
        "keys": {"p256dh": p256dh, "auth": "browser-auth-secret"},
    }


def open_conversation(as_user, actor, other):
    response = as_user(actor).post(
        "/api/messaging/conversations/", {"user_id": other.id}, format="json"
    )
    assert response.status_code in (200, 201), response.content
    return response.json()["id"]


def send_message(client, conversation, body="Hello"):
    return client.post(
        f"/api/messaging/conversations/{conversation}/messages/",
        {"body": body},
        format="json",
    )


def test_a_browser_subscribes_once_and_keys_refresh(unit, as_user, monkeypatch):
    person = make_user(unit=unit)
    client = as_user(person)
    monkeypatch.setattr(push, "configured", lambda: True)

    first = client.post(
        "/api/messaging/push/subscribe/", subscription_payload(), format="json"
    )
    assert first.status_code == 201, first.content
    assert PushSubscription.objects.get(user=person).p256dh == "browser-public-key"

    second = client.post(
        "/api/messaging/push/subscribe/",
        subscription_payload(p256dh="rotated-key"),
        format="json",
    )
    assert second.status_code == 200, second.content
    assert PushSubscription.objects.count() == 1
    assert PushSubscription.objects.get().p256dh == "rotated-key"


def test_an_endpoint_belongs_to_the_last_person_signed_in_there(unit, as_user, monkeypatch):
    first = make_user(unit=unit)
    second = make_user(unit=unit)
    monkeypatch.setattr(push, "configured", lambda: True)

    as_user(first).post(
        "/api/messaging/push/subscribe/", subscription_payload(), format="json"
    )
    as_user(second).post(
        "/api/messaging/push/subscribe/", subscription_payload(), format="json"
    )

    assert PushSubscription.objects.count() == 1
    assert PushSubscription.objects.get().user_id == second.id


def test_unsubscribing_never_touches_another_accounts_browser(unit, as_user, monkeypatch):
    person = make_user(unit=unit)
    other = make_user(unit=unit)
    monkeypatch.setattr(push, "configured", lambda: True)

    own = PushSubscription.objects.create(
        user=person, endpoint=ENDPOINT, p256dh="a", auth="b"
    )
    foreign = PushSubscription.objects.create(
        user=other,
        endpoint="https://updates.push.services.mozilla.com/wpush/v2/other",
        p256dh="c",
        auth="d",
    )

    client = as_user(person)
    # Naming somebody else's endpoint must not remove their browser.
    response = client.post(
        "/api/messaging/push/unsubscribe/",
        {"endpoint": foreign.endpoint},
        format="json",
    )
    assert response.status_code == 204
    assert PushSubscription.objects.filter(pk=foreign.pk).exists()
    assert PushSubscription.objects.filter(pk=own.pk).exists()

    response = client.post(
        "/api/messaging/push/unsubscribe/",
        {"endpoint": own.endpoint},
        format="json",
    )
    assert response.status_code == 204
    assert not PushSubscription.objects.filter(pk=own.pk).exists()
    assert PushSubscription.objects.filter(pk=foreign.pk).exists()


def test_config_is_disabled_until_the_deployment_has_keys(as_user, unit, monkeypatch):
    person = make_user(unit=unit)
    monkeypatch.setattr(push, "configured", lambda: False)
    monkeypatch.setattr(push, "public_key", lambda: "")

    body = as_user(person).get("/api/messaging/push/config/").json()

    assert body == {"enabled": False, "public_key": ""}


def test_subscribing_is_refused_without_server_keys(unit, as_user, monkeypatch):
    person = make_user(unit=unit)
    monkeypatch.setattr(push, "configured", lambda: False)

    response = as_user(person).post(
        "/api/messaging/push/subscribe/", subscription_payload(), format="json"
    )

    assert response.status_code == 503
    assert PushSubscription.objects.count() == 0


def test_a_forged_endpoint_is_refused(unit, as_user, monkeypatch):
    person = make_user(unit=unit)
    monkeypatch.setattr(push, "configured", lambda: True)
    client = as_user(person)

    for endpoint in (
        "http://fcm.googleapis.com/wp/plain-http",
        "https://127.0.0.1/internal",
        "https://internal.example.test/collect",
    ):
        response = client.post(
            "/api/messaging/push/subscribe/",
            subscription_payload(endpoint=endpoint),
            format="json",
        )
        assert response.status_code == 400, endpoint
    assert PushSubscription.objects.count() == 0


def test_an_offline_recipient_gets_one_push_after_the_message_commits(
    unit, as_user, monkeypatch, django_capture_on_commit_callbacks
):
    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    conversation = open_conversation(as_user, sender, recipient)
    monkeypatch.setattr(push, "configured", lambda: True)
    monkeypatch.setattr(services, "user_is_online", lambda user: False)

    with patch.object(push, "queue_message_push") as queued:
        with django_capture_on_commit_callbacks(execute=True):
            sent = send_message(as_user(sender), conversation)

    assert sent.status_code == 201, sent.content
    queued.assert_called_once()
    user_id, sender_name, conversation_id, message_id = queued.call_args.args
    assert user_id == recipient.id
    assert sender_name  # who wrote, but never what they wrote
    assert conversation_id == conversation
    assert message_id == sent.json()["id"]


def test_a_recipient_already_inside_towems_is_not_pushed(
    unit, as_user, monkeypatch, django_capture_on_commit_callbacks
):
    sender = make_user(unit=unit)
    recipient = make_user(unit=unit)
    conversation = open_conversation(as_user, sender, recipient)
    monkeypatch.setattr(push, "configured", lambda: True)
    monkeypatch.setattr(services, "user_is_online", lambda user: True)

    with patch.object(push, "queue_message_push") as queued:
        with django_capture_on_commit_callbacks(execute=True):
            assert send_message(as_user(sender), conversation).status_code == 201

    queued.assert_not_called()


def test_a_gone_endpoint_is_deleted(unit, monkeypatch):
    person = make_user(unit=unit)
    # The deployment key is not part of the test settings; _send only needs a
    # truthy signer because the HTTP call itself is patched below.
    monkeypatch.setattr(push, "_vapid", lambda: object())
    row = PushSubscription.objects.create(
        user=person, endpoint=ENDPOINT, p256dh="a", auth="b"
    )
    gone = WebPushException("gone", response=SimpleNamespace(status_code=410))

    with patch("pywebpush.webpush", side_effect=gone):
        delivered = push.send_to_user(person.id, "{}")

    assert delivered == 0
    assert not PushSubscription.objects.filter(pk=row.pk).exists()


def test_a_delivered_push_is_stamped(unit, monkeypatch):
    person = make_user(unit=unit)
    monkeypatch.setattr(push, "_vapid", lambda: object())
    row = PushSubscription.objects.create(
        user=person, endpoint=ENDPOINT, p256dh="a", auth="b"
    )

    with patch("pywebpush.webpush", return_value=SimpleNamespace(status_code=201)):
        delivered = push.send_to_user(person.id, "{}")

    assert delivered == 1
    row.refresh_from_db()
    assert row.last_success_at is not None
    assert row.failure_count == 0

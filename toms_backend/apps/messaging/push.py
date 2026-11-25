"""Browser push alerts for new private messages.

Closed-tab delivery uses the standard Web Push protocol: the browser hands the
server an endpoint plus its two encryption keys, and the server signs the
request with the deployment's VAPID key pair. No third-party messaging service
is involved, and the payload never contains the message text - a locked phone
learns who wrote, not what.

Delivery is best-effort and deliberately detached from the request that stored
the message: a slow or dead push service must not delay or fail sending. The
worker thread is only started for people presence says are not already inside
TOWEMS, where the in-app bell covers the same event.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from functools import lru_cache

from django.conf import settings
from django.db import close_old_connections
from django.db.models import F

from apps.core import timeutils

logger = logging.getLogger(__name__)

#: Push services occasionally answer slowly; the message is already stored, so
#: waiting longer than this only ties up a worker thread.
PUSH_TIMEOUT_SECONDS = 10


def public_key() -> str:
    return (getattr(settings, "VAPID_PUBLIC_KEY", "") or "").strip()


def private_key() -> str:
    """The VAPID private key as PEM.

    Deployments store it base64-wrapped so the whole PEM fits on one environment
    line; a raw PEM supplied by hand is accepted as well.
    """
    value = (getattr(settings, "VAPID_PRIVATE_KEY", "") or "").strip()
    if not value or "BEGIN" in value:
        return value
    try:
        return base64.b64decode(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        logger.warning("VAPID_PRIVATE_KEY is neither PEM nor base64-wrapped PEM.")
        return ""


@lru_cache(maxsize=1)
def _vapid():
    """The signing key, parsed once per worker process.

    pywebpush accepts a ``Vapid02`` instance; handing it a PEM string made
    py-vapid decode it as base64 DER and fail, so the parsing happens here.
    """
    from py_vapid import Vapid02

    pem = private_key()
    if not pem:
        return None
    try:
        return Vapid02.from_pem(pem.encode("utf-8"))
    except Exception:  # pragma: no cover - a malformed deployment secret
        logger.warning("VAPID private key could not be parsed.", exc_info=True)
        return None


def configured() -> bool:
    return bool(public_key() and _vapid() is not None)


def _payload(recipient_id, sender_name, conversation_id, message_id) -> str:
    """What the service worker displays. Never the message body itself."""
    return json.dumps(
        {
            "title": f"New message from {sender_name}"[:120],
            "body": "Open TOWEMS to read it.",
            "url": f"/messages?conversation={conversation_id}",
            "tag": f"towems-conversation-{conversation_id}",
            "conversation_id": conversation_id,
            "message_id": message_id,
            "recipient_id": recipient_id,
        }
    )


def _send(subscription, payload: str):
    from pywebpush import webpush

    vapid = _vapid()
    if vapid is None:
        raise RuntimeError("VAPID private key is not usable.")
    return webpush(
        subscription_info=subscription.as_subscription_info(),
        data=payload,
        vapid_private_key=vapid,
        vapid_claims={"sub": getattr(settings, "VAPID_SUBJECT", "mailto:no-reply@localhost")},
        ttl=int(getattr(settings, "MESSAGING_PUSH_TTL", 86400)),
        timeout=PUSH_TIMEOUT_SECONDS,
    )


def send_to_user(user_id, payload: str) -> int:
    """Deliver one payload to every live browser of ``user_id``.

    Returns the number of browsers the push service accepted. A 404/410 means
    that browser is gone (uninstalled, permission revoked, endpoint expired),
    so the row is deleted; other failures are counted and retried on the next
    message rather than deleting a subscription that may still work.
    """
    from apps.messaging.models import PushSubscription

    try:
        from pywebpush import WebPushException
    except ImportError:  # pragma: no cover - deployment without the dependency
        logger.warning("pywebpush is not installed; browser push is disabled.")
        return 0

    delivered = 0
    for subscription in list(PushSubscription.objects.filter(user_id=user_id)):
        try:
            _send(subscription, payload)
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                subscription.delete()
            else:
                PushSubscription.objects.filter(pk=subscription.pk).update(
                    failure_count=F("failure_count") + 1
                )
                logger.warning(
                    "Push failed for subscription %s (status %s).",
                    subscription.pk,
                    status,
                )
        except Exception:  # pragma: no cover - network/protocol failures
            PushSubscription.objects.filter(pk=subscription.pk).update(
                failure_count=F("failure_count") + 1
            )
            logger.warning(
                "Push failed for subscription %s.", subscription.pk, exc_info=True
            )
        else:
            delivered += 1
            PushSubscription.objects.filter(pk=subscription.pk).update(
                last_success_at=timeutils.now(), failure_count=0
            )
    return delivered


def _deliver(user_id, payload: str) -> None:
    # A worker thread gets its own database connection; close it on both ends
    # so gunicorn does not accumulate idle connections.
    close_old_connections()
    try:
        send_to_user(user_id, payload)
    except Exception:  # pragma: no cover - the thread must never crash the app
        logger.warning("Push delivery crashed for user %s.", user_id, exc_info=True)
    finally:
        close_old_connections()


def queue_message_push(user_id, sender_name, conversation_id, message_id) -> bool:
    """Start a detached delivery. Safe to call when push is not configured."""
    if not configured():
        return False
    payload = _payload(user_id, sender_name, conversation_id, message_id)
    threading.Thread(
        target=_deliver,
        args=(user_id, payload),
        name=f"messaging-push-{conversation_id}",
        daemon=True,
    ).start()
    return True

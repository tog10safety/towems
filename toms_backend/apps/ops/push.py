"""Phone push through Firebase Cloud Messaging.

The Android shell used to learn about a notification only by polling from a
foreground service. That works while the service lives, but Android caps a
long-running service and OEM battery savers end it anyway, so a phone in a
pocket went quiet until the app was opened - exactly the failure a phone alert
must not have.

FCM is the transport the chat apps use: Google keeps one connection per device
and wakes the app when there is something for it, so delivery no longer depends
on a service the phone is allowed to keep running. It is free for unlimited
messages; a Firebase project and one service-account key are the whole setup.

The server sends *data-only* messages on purpose. The app builds the
notification itself, so it lands on the audible `towems_*_v2` channel with the
launcher badge number and the deep link, and the same code path works whether
the app is open, backgrounded or swiped away. Nothing from a conversation is
ever put in the payload: a message alert says only that something is waiting.

Delivery is best-effort and detached from the request that stored the
notification, the same way browser push works: a slow or dead push service must
never delay or fail the action that created the notice.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)

#: The one scope an FCM sender needs.
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"

#: FCM accepts requests quickly, but a notice to a whole unit must not start one
#: thread per recipient. Four workers drain a few hundred sends in seconds.
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="towems-push")

PUSH_TIMEOUT_SECONDS = 15

#: A phone that has not opened TOWEMS for this long is not worth waking: the
#: install's alert token expires on its own well before this matters, but the
#: filter keeps the query honest if the age is ever raised.
TOKEN_STALE_SECONDS = 60 * 60 * 24 * 30


def project_id() -> str:
    return (getattr(settings, "FCM_PROJECT_ID", "") or "").strip()


def credentials_file() -> str:
    return (getattr(settings, "FCM_CREDENTIALS_FILE", "") or "").strip()


def configured() -> bool:
    """True when this deployment can actually send a push.

    With either setting missing the server behaves exactly as before: the
    watcher still polls, and nothing tries to reach Google.
    """
    path = credentials_file()
    return bool(project_id()) and bool(path) and os.path.isfile(path)


@lru_cache(maxsize=1)
def _credentials():
    """The service-account credentials, parsed once per worker process."""
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(
        credentials_file(), scopes=[SCOPE]
    )


def _session():
    from google.auth.transport.requests import AuthorizedSession

    # AuthorizedSession refreshes the OAuth token by itself; the credentials
    # object is cached, the session is cheap.
    return AuthorizedSession(_credentials())


def _tokens_for(user_id: int) -> list[str]:
    """Every live FCM token this account has registered."""
    from apps.accounts.models import DeviceToken

    rows = (
        DeviceToken.objects.filter(
            user_id=user_id,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .exclude(fcm_token="")
        .values_list("fcm_token", flat=True)
    )
    # One phone can have been registered twice while its token rotated.
    return list(dict.fromkeys(token for token in rows if token))


def _message(token: str, *, title: str, body: str, path: str, count: int, kind: str) -> dict:
    """One FCM message. Data-only: the app owns the notification itself."""
    return {
        "message": {
            "token": token,
            "data": {
                "title": (title or "TOWEMS")[:160],
                "body": (body or "")[:400],
                "path": (path or "/notifications")[:300],
                "count": str(max(count, 0)),
                # ``message`` and ``urgent`` pick the channel and category on
                # the phone; anything else is a routine operational alert.
                "kind": kind,
            },
            "android": {
                # High priority is what wakes a backgrounded or swiped-away app;
                # normal priority may be batched until the phone next wakes.
                "priority": "high",
                "ttl": "86400s",
            },
        }
    }


def _post(session, url: str, payload: dict):
    return session.post(url, json=payload, timeout=PUSH_TIMEOUT_SECONDS)


def _gone(response) -> bool:
    """Whether FCM says this token belongs to an install that no longer exists."""
    if getattr(response, "status_code", 0) == 404:
        return True
    try:
        error = (response.json() or {}).get("error") or {}
    except (ValueError, json.JSONDecodeError):
        return False
    for detail in error.get("details") or []:
        if detail.get("errorCode") in ("UNREGISTERED", "SENDER_ID_MISMATCH"):
            return True
    return False


def _badge_count(user_id: int) -> int:
    """What the launcher icon should show: notices and unread chat messages."""
    from apps.ops.models import Notification

    unread = Notification.objects.filter(recipient_id=user_id, is_read=False).count()
    try:
        from apps.accounts.models import User
        from apps.messaging.services import inbox_unread_total

        user = User.objects.filter(pk=user_id).first()
        if user is not None:
            unread = max(unread, inbox_unread_total(user))
    except Exception:  # pragma: no cover - messaging is optional while migrating
        logger.debug("unread message count unavailable for %s", user_id, exc_info=True)
    return unread


def send_to_user(
    user_id: int,
    *,
    title: str,
    body: str,
    path: str,
    kind: str = "alert",
    count: int | None = None,
) -> int:
    """Deliver one alert to every phone of ``user_id``; returns how many woke.

    A token FCM reports as unregistered is cleared from the row, so it is not
    retried on every later alert. Other failures are logged and left alone:
    a network blip should not unregister a working phone.
    """
    from apps.accounts.models import DeviceToken

    if not configured():
        return 0
    tokens = _tokens_for(user_id)
    if not tokens:
        return 0

    if count is None:
        count = _badge_count(user_id)
    url = SEND_URL.format(project=project_id())
    session = _session()

    delivered = 0
    for token in tokens:
        try:
            response = _post(
                session,
                url,
                _message(token, title=title, body=body, path=path, count=count, kind=kind),
            )
        except Exception:
            logger.warning("FCM send failed for user %s", user_id, exc_info=True)
            continue

        status = getattr(response, "status_code", 0)
        if status == 200:
            delivered += 1
        elif _gone(response):
            DeviceToken.objects.filter(fcm_token=token).update(fcm_token="")
            logger.info("dropped an FCM token the service says is gone")
        else:
            logger.warning("FCM answered %s for user %s", status, user_id)
    return delivered


def _deliver(
    user_id: int,
    title: str,
    body: str,
    path: str,
    kind: str,
    count: int | None,
) -> None:
    # A worker thread gets its own database connection; close it on both ends
    # so gunicorn does not accumulate idle connections.
    close_old_connections()
    try:
        send_to_user(
            user_id, title=title, body=body, path=path, kind=kind, count=count
        )
    except Exception:  # pragma: no cover - the thread must never crash the app
        logger.warning("push delivery crashed for user %s", user_id, exc_info=True)
    finally:
        close_old_connections()


def queue_notification(
    *,
    recipient_id: int,
    title: str,
    body: str = "",
    path: str = "",
    priority: str = "normal",
    category: str = "",
    count: int | None = None,
) -> bool:
    """Start a detached delivery for one new notification. Safe to call always."""
    if not configured() or not recipient_id:
        return False
    kind = "message" if category == "message" else (
        "urgent" if priority == "urgent" else "alert"
    )
    _pool.submit(
        _deliver,
        int(recipient_id),
        title,
        body or "",
        path or ("/messages" if category == "message" else "/notifications"),
        kind,
        count,
    )
    return True

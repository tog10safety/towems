"""Presence: who has TOWEMS open right now.

The legacy ``users`` table carries no session bookkeeping and TOWEMS keeps its
session in the browser, so there is nothing to list. Presence is therefore
stamped from the request itself: every authenticated request refreshes
``UserProfile.last_seen``, at most once a minute per account so a page full of
polls does not become a write per request.

"Online" means "seen in the last :data:`ONLINE_WINDOW_MINUTES` minutes". A closed
browser or an expired session needs no cleanup: the stamp ages out on its own.

The list is unit-scoped like the rest of the personnel data - a caller sees the
people whose records they may already open - and it is deliberately thin:
serial number and picture are what the top-bar rail shows, with rank, name and
designation available for a tooltip.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import timeutils
from apps.core.scoping import accessible_unit_ids

logger = logging.getLogger(__name__)

#: How often one account's stamp is written at most.
WRITE_INTERVAL_SECONDS = 60
#: How long after the last request someone still counts as online.
ONLINE_WINDOW_MINUTES = 5
#: Most people the rail lists; the count still reports the whole set.
MAX_ONLINE_ROWS = 40

#: Per-process throttle: a module-level map keeps the middleware query-free on
#: the requests that fall inside the interval. Each worker keeps its own map,
#: which only means a stamp can be written a little more often than once a
#: minute - never less.
_last_stamp: dict[int, float] = {}


def stamp_presence(user) -> None:
    """Record that ``user`` just used the system, if the interval has passed."""
    try:
        user_id = int(user.pk)
    except (TypeError, ValueError):
        return

    now = time.monotonic()
    last = _last_stamp.get(user_id)
    if last is not None and now - last < WRITE_INTERVAL_SECONDS:
        return
    _last_stamp[user_id] = now

    from apps.accounts.models import UserProfile

    try:
        UserProfile.objects.update_or_create(
            user_id=user_id, defaults={"last_seen": timeutils.now()}
        )
    except Exception:  # presence must never fail a request
        _last_stamp.pop(user_id, None)
        logger.warning("Could not record presence for user %s", user_id, exc_info=True)


class PresenceMiddleware:
    """Stamp the signed-in account's last activity.

    Placed after ``AuthenticationMiddleware`` because it needs the session user;
    an anonymous request is skipped, and DRF-only authentication (token clients)
    is left to the view, which stamps through :func:`stamp_presence`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            stamp_presence(user)
        return self.get_response(request)


def _avatar_url(user) -> str:
    """The person's picture, or "" when none is uploaded.

    Another person's picture is served by the personnel avatar endpoint, which
    applies the same unit scope as this list. The filename is a version suffix so
    a replaced picture is fetched immediately.
    """
    try:
        profile = user.profile
    except Exception:  # no profile row yet
        return ""
    filename = getattr(profile, "avatar_filename", "") or ""
    if not filename:
        return ""
    return f"/api/personnel/{user.id}/avatar/?v={filename}"


def online_payload(user) -> dict:
    """Everyone seen inside the window, most recent first, scoped to ``user``."""
    from apps.accounts.models import User

    since = timeutils.now() - timedelta(minutes=ONLINE_WINDOW_MINUTES)
    allowed = accessible_unit_ids(user)
    queryset = User.objects.filter(is_active=True, profile__last_seen__gte=since)
    if allowed:
        queryset = queryset.filter(Q(unit_id__in=allowed) | Q(unit_id__isnull=True))
    else:
        # No unit information means no presence list, never everyone's.
        queryset = queryset.none()

    # Rows are capped at MAX_ONLINE_ROWS and already carry their profile and
    # unit through select_related, so no row below costs another query.
    rows = list(
        queryset.select_related("profile", "unit").order_by("-profile__last_seen")[
            :MAX_ONLINE_ROWS
        ]
    )

    return {
        "window_minutes": ONLINE_WINDOW_MINUTES,
        "count": len(rows),
        "users": [
            {
                "id": row.id,
                # The rail identifies people by serial number, the stable
                # operational identifier - never by username or email.
                "serial_number": row.serial_number,
                "rank": row.rank or "",
                "full_name": row.get_full_name(),
                "designation": row.designation or "",
                "unit_name": row.unit.name if row.unit_id else "",
                "unit_acronym": row.unit.acronym if row.unit_id else "",
                "avatar_url": _avatar_url(row),
                # The filter guarantees the profile row exists, and the stamp is
                # what put this person on the list.
                "last_seen": row.profile.last_seen.isoformat(),
                "is_self": row.id == getattr(user, "id", None),
            }
            for row in rows
        ],
    }


class OnlineNowView(APIView):
    """Who is online, for the top-bar rail."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Token/DRF-authenticated callers do not pass through the session
        # middleware, so the first call also marks the caller as present.
        stamp_presence(request.user)
        return Response(online_payload(request.user))

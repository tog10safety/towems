"""Planned road routes for vehicle missions (spec §5.3).

A vehicle route is calculated by a road-network routing engine, never by
joining stops with a straight line. This module is the only place that talks to
that engine, so the provider can be swapped or self-hosted without touching the
mission planner.

Configuration
-------------
``TOMS_ROUTING_BASE_URL``
    Base URL of an OSRM-compatible routing service. Defaults to the OSRM demo
    server. Point it at an internal OSRM instance when operational coordinates
    must not leave your network.
``TOMS_ROUTING_ENABLED``
    Set to ``0`` to disable road routing entirely. Every attempt then fails with
    an explicit message instead of falling back to aerial distance.

Nothing here fabricates a route. If the engine is unreachable or cannot route a
leg, :class:`RoutingError` is raised and the caller surfaces the reason.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://router.project-osrm.org"
REQUEST_TIMEOUT = 12


class RoutingError(Exception):
    """Road routing could not be completed; the message is user-facing."""


def enabled() -> bool:
    return os.environ.get("TOMS_ROUTING_ENABLED", "1") not in {"0", "false", "False"}


def base_url() -> str:
    return (
        os.environ.get("TOMS_ROUTING_BASE_URL") or DEFAULT_BASE_URL
    ).rstrip("/")


def provider_name() -> str:
    host = urllib.parse.urlsplit(base_url()).netloc or base_url()
    return f"OSRM · {host}"


def _http_get_json(url: str, timeout: int = REQUEST_TIMEOUT) -> dict:
    """Kept separate so tests can replace it without network access."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "TOWEMS/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RoutingError(
            f"The routing service rejected the request (HTTP {exc.code})."
        ) from exc
    except urllib.error.URLError as exc:
        raise RoutingError(
            "The routing service could not be reached. Check the routing "
            f"configuration or try again later. ({exc.reason})"
        ) from exc
    except TimeoutError as exc:
        raise RoutingError("The routing service timed out. Try again.") from exc

    try:
        return json.loads(body)
    except ValueError as exc:
        raise RoutingError("The routing service returned an unreadable response.") from exc


def _clean_stops(stops):
    cleaned = []
    for index, stop in enumerate(stops, start=1):
        try:
            lat = float(stop["latitude"])
            lon = float(stop["longitude"])
        except (KeyError, TypeError, ValueError):
            raise RoutingError(
                f"Stop {index} has no usable coordinates. Set a point on the map "
                "or enter the latitude and longitude."
            ) from None
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise RoutingError(
                f"Stop {index} has coordinates outside the valid range."
            )
        cleaned.append(
            {
                "index": index,
                "label": stop.get("label") or stop.get("location_name") or f"Stop {index}",
                "latitude": lat,
                "longitude": lon,
            }
        )
    return cleaned


def route(stops) -> dict:
    """Calculate a driving route through ``stops`` in the given order.

    ``stops`` is an ordered list of ``{"latitude", "longitude", "label"?}``.
    Returns geometry, per-leg details and totals. Raises :class:`RoutingError`
    with a readable reason when the road route cannot be produced.
    """
    cleaned = _clean_stops(stops or [])
    if len(cleaned) < 2:
        raise RoutingError(
            "At least two itinerary stops with coordinates are needed to calculate "
            "a driving route."
        )
    if not enabled():
        raise RoutingError(
            "Road routing is switched off on this server. Ask an administrator to "
            "configure a routing service."
        )

    coords = ";".join(f"{stop['longitude']},{stop['latitude']}" for stop in cleaned)
    query = urllib.parse.urlencode(
        {"overview": "full", "geometries": "geojson", "steps": "false"}
    )
    url = f"{base_url()}/route/v1/driving/{coords}?{query}"

    payload = _http_get_json(url)
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError(
            "No road route could be found between these stops. "
            f"{payload.get('message') or 'Check the stop order and coordinates.'}"
        )

    best = payload["routes"][0]
    geometry = best.get("geometry") or {}
    if not geometry.get("coordinates"):
        raise RoutingError("The routing service returned an empty route geometry.")

    raw_legs = best.get("legs") or []
    legs = []
    for index, leg in enumerate(raw_legs):
        legs.append(
            {
                "index": index + 1,
                "from": cleaned[index]["label"] if index < len(cleaned) else "",
                "to": cleaned[index + 1]["label"] if index + 1 < len(cleaned) else "",
                "distance_m": round(float(leg.get("distance") or 0.0), 1),
                "duration_s": round(float(leg.get("duration") or 0.0), 1),
            }
        )

    if len(legs) != len(cleaned) - 1:
        raise RoutingError(
            "The routing service returned an incomplete route. Every leg between "
            "consecutive stops must be routable; nothing was substituted."
        )

    return {
        "provider": provider_name(),
        "geometry": geometry,
        "legs": legs,
        "distance_m": round(float(best.get("distance") or 0.0), 1),
        "duration_s": round(float(best.get("duration") or 0.0), 1),
        "stops": cleaned,
        "planned": True,
    }

"""Place search for the map picker.

Operators search for the thing they are going to, not only for a province:
"SM City Butuan", "Camp Evangelista", "Butuan Airport". That needs a place
index, which TOWEMS does not carry, so this module asks an external provider and
normalises the answer into one small shape the frontend can pin:

    {"label", "detail", "latitude", "longitude", "source", "provider_id"}

Two providers are supported, chosen by configuration:

* **Google Places** when ``TOMS_GOOGLE_MAPS_API_KEY`` is set - the key is sent
  in the request header from the server and is never exposed to the browser.
* **OpenStreetMap Nominatim** otherwise, so place search works with no account,
  no key and no billing.

``TOMS_GEOCODER`` may force ``google`` or ``nominatim``, or be set to ``off`` to
disable external lookups entirely (air-gapped deployments). Nothing here is
allowed to break the map: any network/HTTP failure returns an empty result set
with a note, and the caller keeps its offline PSGC and TOWEMS-record search.

The free Nominatim service asks for at most one request per second and a
descriptive User-Agent, so responses are cached briefly and lookups are only
made for queries of at least three characters.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache

MIN_QUERY_LENGTH = 3
DEFAULT_LIMIT = 8

GOOGLE_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_GEOCODE_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"
NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_ENDPOINT = "https://nominatim.openstreetmap.org/reverse"

#: Only what the picker needs; keeps the Google response (and its billing tier)
#: as small as possible.
GOOGLE_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
    ]
)


def provider() -> str:
    """The provider that will be used for this deployment."""
    configured = (getattr(settings, "GEOCODER_PROVIDER", "auto") or "auto").lower()
    key = getattr(settings, "GOOGLE_MAPS_API_KEY", "") or ""
    if configured == "off":
        return "off"
    if configured == "google":
        return "google" if key else "off"
    if configured == "nominatim":
        return "nominatim"
    return "google" if key else "nominatim"


def _timeout() -> float:
    try:
        return float(getattr(settings, "GEOCODER_TIMEOUT", 8) or 8)
    except (TypeError, ValueError):  # pragma: no cover - misconfiguration
        return 8.0


def _user_agent() -> str:
    return (
        getattr(settings, "GEOCODER_USER_AGENT", "")
        or "TOWEMS-TacticalOps/1.0 (mission planning map picker)"
    )


def _cache_seconds() -> int:
    try:
        return int(getattr(settings, "GEOCODER_CACHE_SECONDS", 600) or 0)
    except (TypeError, ValueError):  # pragma: no cover - misconfiguration
        return 600


def _result(
    *,
    label: str,
    detail: str,
    latitude,
    longitude,
    source: str,
    provider_id: str = "",
) -> dict | None:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    label = (label or detail or "").strip()
    if not label:
        return None
    return {
        "label": label[:200],
        "detail": (detail or label)[:300],
        "latitude": round(lat, 6),
        "longitude": round(lon, 6),
        "source": source,
        "provider_id": str(provider_id or ""),
    }


def _google_search(query: str, limit: int) -> list[dict]:
    """Google Places (New) text search, biased to the Philippines."""
    body = {
        "textQuery": query,
        "languageCode": "en",
        "regionCode": "PH",
        "pageSize": max(1, min(limit, 20)),
        # Bias - not restrict - so an exact match outside the box still appears.
        "locationBias": {
            "rectangle": {
                "low": {"latitude": 4.5, "longitude": 114.0},
                "high": {"latitude": 21.5, "longitude": 127.0},
            }
        },
    }
    request = urllib.request.Request(
        GOOGLE_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            # The key stays on the server; it is never sent to the browser.
            "X-Goog-Api-Key": getattr(settings, "GOOGLE_MAPS_API_KEY", "") or "",
            "X-Goog-FieldMask": GOOGLE_FIELD_MASK,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_timeout()) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")

    results = []
    for place in payload.get("places", []) or []:
        location = place.get("location") or {}
        name = (place.get("displayName") or {}).get("text") or ""
        row = _result(
            label=name,
            detail=place.get("formattedAddress") or name,
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            source="Google",
            provider_id=place.get("id") or "",
        )
        if row:
            results.append(row)
    return results


def _nominatim_search(query: str, limit: int) -> list[dict]:
    """OpenStreetMap Nominatim search, restricted to the Philippines."""
    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "countrycodes": "ph",
            "limit": max(1, min(limit, 20)),
            "addressdetails": 1,
            "accept-language": "en",
        }
    )
    request = urllib.request.Request(
        f"{NOMINATIM_ENDPOINT}?{params}",
        headers={"User-Agent": _user_agent(), "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_timeout()) as response:
        payload = json.loads(response.read().decode("utf-8") or "[]")

    results = []
    for row in payload if isinstance(payload, list) else []:
        display = row.get("display_name") or ""
        name = row.get("name") or (display.split(",")[0].strip() if display else "")
        result = _result(
            label=name,
            detail=display,
            latitude=row.get("lat"),
            longitude=row.get("lon"),
            source="OpenStreetMap",
            provider_id=row.get("place_id") or "",
        )
        if result:
            results.append(result)
    return results


def _clean(value) -> str:
    return value.strip()[:200] if isinstance(value, str) else ""


def _reverse_result(
    *,
    barangay: str = "",
    establishment: str = "",
    city: str = "",
    province: str = "",
    source: str = "",
    note: str = "",
) -> dict:
    """One small shape for a pin's address detail; never raises."""
    return {
        "available": bool(barangay or establishment or city or province),
        "barangay": _clean(barangay),
        "establishment": _clean(establishment),
        "city": _clean(city),
        "province": _clean(province),
        "source": source,
        "note": note,
    }


def _nominatim_reverse(latitude: float, longitude: float) -> dict:
    """Nearest address for a pin, restricted to the Philippines."""
    params = urllib.parse.urlencode(
        {
            "lat": f"{latitude:.6f}",
            "lon": f"{longitude:.6f}",
            "format": "jsonv2",
            "addressdetails": 1,
            "zoom": 18,
            "accept-language": "en",
        }
    )
    request = urllib.request.Request(
        f"{NOMINATIM_REVERSE_ENDPOINT}?{params}",
        headers={"User-Agent": _user_agent(), "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_timeout()) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")

    address = payload.get("address") or {}
    barangay = (
        address.get("barangay")
        or address.get("neighbourhood")
        or address.get("suburb")
        or address.get("quarter")
        or address.get("village")
        or address.get("hamlet")
        or ""
    )
    city = (
        address.get("city")
        or address.get("town")
        or address.get("municipality")
        or address.get("city_district")
        or ""
    )
    province = (
        address.get("province")
        or address.get("state")
        or address.get("region")
        or address.get("state_district")
        or ""
    )
    # A rural pin often comes back named after the province or municipality it
    # is in. That is an administrative name, not the establishment at the pin,
    # so it must not be reported as one.
    feature = _clean(payload.get("name"))
    # A street is an address line, not the establishment at the pin.
    road_types = {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
        "residential",
        "service",
        "living_street",
        "pedestrian",
        "footway",
        "path",
        "cycleway",
        "track",
        "road",
    }
    if _clean(payload.get("category")).casefold() == "highway" or _clean(
        payload.get("type")
    ).casefold() in road_types:
        feature = ""
    administrative = {
        _clean(value).casefold()
        for value in (
            city,
            province,
            address.get("state"),
            address.get("region"),
            address.get("county"),
        )
        if _clean(value)
    }
    if feature.casefold() in administrative:
        feature = ""
    establishment = (
        feature
        or address.get("amenity")
        or address.get("shop")
        or address.get("building")
        or address.get("tourism")
        or address.get("office")
        or ""
    )
    return _reverse_result(
        barangay=barangay,
        establishment=establishment,
        city=city,
        province=province,
        source="OpenStreetMap",
    )


def _google_reverse(latitude: float, longitude: float) -> dict:
    """Reverse geocode through Google, using the same server-side key."""
    params = urllib.parse.urlencode(
        {
            "latlng": f"{latitude:.6f},{longitude:.6f}",
            "key": getattr(settings, "GOOGLE_MAPS_API_KEY", "") or "",
            "language": "en",
            "region": "ph",
        }
    )
    request = urllib.request.Request(
        f"{GOOGLE_GEOCODE_ENDPOINT}?{params}", headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=_timeout()) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")

    rows = payload.get("results") or []
    if not rows:
        return _reverse_result(source="Google", note="No address was found for that point.")

    components = rows[0].get("address_components") or []

    def component(*types) -> str:
        for part in components:
            if set(types) & set(part.get("types") or []):
                return part.get("long_name") or ""
        return ""

    return _reverse_result(
        barangay=component(
            "sublocality_level_1", "sublocality", "neighborhood", "administrative_area_level_3"
        ),
        establishment=component("premise", "point_of_interest", "establishment"),
        city=component("locality", "administrative_area_level_2"),
        province=component("administrative_area_level_1"),
        source="Google",
    )


def reverse(latitude, longitude) -> dict:
    """Name the point: nearest barangay or establishment, its city and province.

    Best-effort and never fatal: when the geocoder is switched off or
    unreachable the result says so in ``note`` and the caller falls back to the
    PSGC boundaries it already has.
    """
    chosen = provider()
    if chosen == "off":
        return _reverse_result(
            note="The barangay/establishment lookup is switched off for this deployment."
        )
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return _reverse_result(note="Numeric coordinates are required.")

    digest = hashlib.sha1(f"{lat:.5f},{lon:.5f}".encode("utf-8")).hexdigest()
    cache_key = f"toms:places:reverse:{chosen}:{digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        detail = (
            _google_reverse(lat, lon) if chosen == "google" else _nominatim_reverse(lat, lon)
        )
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        detail = _reverse_result(
            note="Barangay/establishment lookup is unavailable right now."
        )
        detail["status"] = exc.code
    except (urllib.error.URLError, TimeoutError, ValueError):  # pragma: no cover
        detail = _reverse_result(
            note="The barangay/establishment lookup service could not be reached."
        )

    seconds = _cache_seconds()
    if seconds and not detail.get("status"):
        cache.set(cache_key, detail, seconds)
    return detail


def search(query: str, *, limit: int = DEFAULT_LIMIT) -> dict:
    """Search places for ``query``.

    Always returns a dict - ``{"provider", "results", "note"}`` - so the caller
    can render something useful even when the provider is disabled, unreachable
    or rate limited.
    """
    text = (query or "").strip()
    chosen = provider()
    if chosen == "off":
        return {
            "provider": "off",
            "results": [],
            "note": "External place search is switched off for this deployment.",
        }
    if len(text) < MIN_QUERY_LENGTH:
        return {"provider": chosen, "results": [], "note": ""}

    # Hashed so the key is safe for any cache backend (no spaces/punctuation).
    digest = hashlib.sha1(text.lower().encode("utf-8")).hexdigest()
    cache_key = f"toms:places:{chosen}:{limit}:{digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = {"provider": chosen, "results": [], "note": ""}
    try:
        if chosen == "google":
            payload["results"] = _google_search(text, limit)
        else:
            payload["results"] = _nominatim_search(text, limit)
        if not payload["results"]:
            payload["note"] = "No matching place found."
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        payload["note"] = (
            "Place search is unavailable right now. Boundary and TOWEMS-record search still work."
        )
        payload["status"] = exc.code
    except (urllib.error.URLError, TimeoutError, ValueError):  # pragma: no cover
        payload["note"] = (
            "The place search service could not be reached. Boundary and TOWEMS-record search still work."
        )

    seconds = _cache_seconds()
    if seconds and not payload.get("status"):
        cache.set(cache_key, payload, seconds)
    return payload

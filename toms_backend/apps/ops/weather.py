"""Forecast weather for a planned mission (assumptions section of the MRM).

The assessment asks what the weather will be like over the hours and places the
mission actually covers, so this module asks a forecast provider for that window
instead of making the planner guess.

Configuration
-------------
``TOMS_WEATHER_BASE_URL``
    Open-Meteo-compatible forecast endpoint. Defaults to the public Open-Meteo
    API, which needs no account or key and covers the whole world. Point it at
    an internal or commercial service (AccuWeather, Windy, ...) if operational
    locations must not leave your network.
``TOMS_WEATHER_ENABLED``
    Set to ``0`` to disable the lookup. The MRM then keeps the manual weather
    choice, and the editor says why no forecast was offered.

Nothing here invents a forecast: when the request fails or the window is beyond
the provider's horizon, :class:`WeatherError` is raised with a readable reason
and the planner simply picks the condition by hand.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 8
#: Open-Meteo publishes a 16-day forecast; beyond this it has nothing useful.
MAX_FORECAST_DAYS = 15
#: One forecast request per place, capped so a long route cannot fan out.
MAX_POINTS = 4
#: Hours sampled per place when the window is long.
MAX_HOURS_PER_POINT = 72

#: The MRM's own weather choices, in the legacy wording.
CLEAR = "Clear skies"
CLOUDY = "Overcast / Cloudy"
RAINY = "Rainy / Wet"
WINDY = "Windy"
HOT = "Hot / Humid"
COLD_FOGGY = "Cold / Foggy"


class WeatherError(Exception):
    """The forecast could not be produced; the message is user-facing."""


def enabled() -> bool:
    return os.environ.get("TOMS_WEATHER_ENABLED", "1") not in {"0", "false", "False"}


def base_url() -> str:
    return (os.environ.get("TOMS_WEATHER_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def provider_name() -> str:
    host = urllib.parse.urlsplit(base_url()).netloc or base_url()
    if "open-meteo" in host:
        return "Open-Meteo"
    return host


def _http_get_json(url: str, timeout: int = REQUEST_TIMEOUT) -> dict:
    """Kept separate so tests can replace it without network access."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "TOWEMS/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise WeatherError(
            f"The forecast service rejected the request (HTTP {exc.code})."
        ) from exc
    except urllib.error.URLError as exc:
        raise WeatherError(
            f"The forecast service could not be reached. ({exc.reason})"
        ) from exc
    except TimeoutError as exc:
        raise WeatherError("The forecast service did not answer in time.") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise WeatherError("The forecast service returned an unreadable answer.") from exc


def classify(weather_code, precipitation, temperature, wind_speed) -> str:
    """Map one hourly sample onto the MRM's own weather choices.

    Order matters: a thunderstorm is "Rainy / Wet" even when it is also windy,
    and fog counts before a mild breeze.
    """
    code = int(weather_code) if weather_code is not None else 0
    rain = precipitation is not None and float(precipitation) > 0.1
    if 51 <= code <= 67 or 80 <= code <= 82 or 95 <= code <= 99 or rain:
        return RAINY
    if code in {45, 48}:
        return COLD_FOGGY
    if temperature is not None and float(temperature) <= 18:
        return COLD_FOGGY
    if wind_speed is not None and float(wind_speed) >= 30:
        return WINDY
    if temperature is not None and float(temperature) >= 33:
        return HOT
    if code in {2, 3}:
        return CLOUDY
    return CLEAR


#: When the samples disagree, the assessment cares about the worst of them.
SEVERITY_ORDER = {RAINY: 5, COLD_FOGGY: 4, WINDY: 3, HOT: 2, CLOUDY: 1, CLEAR: 0}


def _window(start_at, end_at):
    """The hours the mission covers, kept inside what a forecast can answer."""
    now = dt.datetime.now()
    start = start_at or now
    end = end_at or (start + dt.timedelta(hours=6))
    if end < start:
        end = start
    if start < now:
        start = now
    horizon = now + dt.timedelta(days=MAX_FORECAST_DAYS)
    if start > horizon:
        raise WeatherError(
            "That mission is more than "
            f"{MAX_FORECAST_DAYS} days away; forecasts do not reach it yet. "
            "Choose the expected weather by hand for now."
        )
    if end > horizon:
        end = horizon
    if end - start > dt.timedelta(hours=MAX_HOURS_PER_POINT):
        end = start + dt.timedelta(hours=MAX_HOURS_PER_POINT)
    return start, end


def _samples_for_point(point, start, end):
    params = {
        "latitude": f"{float(point['latitude']):.5f}",
        "longitude": f"{float(point['longitude']):.5f}",
        "hourly": "weather_code,precipitation,temperature_2m,wind_speed_10m",
        "timezone": "Asia/Manila",
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
    }
    payload = _http_get_json(f"{base_url()}?{urllib.parse.urlencode(params)}")
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    codes = hourly.get("weather_code") or []
    rain = hourly.get("precipitation") or []
    temps = hourly.get("temperature_2m") or []
    winds = hourly.get("wind_speed_10m") or []

    samples = []
    for index, stamp in enumerate(times):
        try:
            moment = dt.datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if moment < start - dt.timedelta(hours=1) or moment > end + dt.timedelta(hours=1):
            continue
        category = classify(
            codes[index] if index < len(codes) else 0,
            rain[index] if index < len(rain) else 0,
            temps[index] if index < len(temps) else None,
            winds[index] if index < len(winds) else None,
        )
        samples.append((moment, category))
    return samples


def forecast_for(points, start_at=None, end_at=None) -> dict:
    """Forecast the weather over the mission's places and hours."""
    if not enabled():
        raise WeatherError(
            "Forecast lookup is switched off for this deployment "
            "(TOMS_WEATHER_ENABLED=0). Choose the weather by hand."
        )

    usable = [
        point
        for point in (points or [])
        if point.get("latitude") is not None and point.get("longitude") is not None
    ][:MAX_POINTS]
    if not usable:
        raise WeatherError(
            "This mission has no pinned location or itinerary coordinates, so there "
            "is nowhere to forecast for."
        )

    start, end = _window(start_at, end_at)

    counts = {}
    places = []
    for point in usable:
        samples = _samples_for_point(point, start, end)
        if not samples:
            continue
        places.append(point.get("name") or "unnamed point")
        for _moment, category in samples:
            counts[category] = counts.get(category, 0) + 1

    if not counts:
        raise WeatherError(
            "The forecast service had no hourly data for this window."
        )

    dominant = max(counts, key=lambda key: (SEVERITY_ORDER.get(key, 0), counts[key]))
    total = sum(counts.values())
    when = f"{start:%d %b %H%M}H to {end:%d %b %H%M}H (PST)"
    where = ", ".join(dict.fromkeys(places))
    place_count = len(dict.fromkeys(places))
    return {
        "available": True,
        "weather": dominant,
        "observed": counts,
        "hours": total,
        "points": len(places),
        "places": list(dict.fromkeys(places)),
        "source": provider_name(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "summary": (
            f"{dominant} in {counts[dominant]} of {total} forecast hours "
            f"across {place_count} location(s), {when}, at {where} "
            f"({provider_name()})."
        ),
    }

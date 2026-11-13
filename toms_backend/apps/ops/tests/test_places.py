"""Place search for the map picker (Google Places / OpenStreetMap Nominatim).

These tests never touch the network: the provider calls are exercised through a
stub opener, so the mapping, the provider choice and the failure behaviour are
pinned without an API key or an internet connection.
"""

from __future__ import annotations

import json

import pytest
from django.test import override_settings

from apps.ops import places

pytestmark = pytest.mark.django_db


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def patch_opener(monkeypatch, payload, captured=None):
    def fake_urlopen(request, timeout=None):
        if captured is not None:
            captured.append(request)
        return FakeResponse(payload)

    monkeypatch.setattr(places.urllib.request, "urlopen", fake_urlopen)


def clear_cache():
    from django.core.cache import cache

    cache.clear()


# ---------------------------------------------------------------------------
# provider selection
# ---------------------------------------------------------------------------


def test_no_key_means_nominatim():
    with override_settings(GEOCODER_PROVIDER="auto", GOOGLE_MAPS_API_KEY=""):
        assert places.provider() == "nominatim"


def test_a_google_key_selects_google():
    with override_settings(GEOCODER_PROVIDER="auto", GOOGLE_MAPS_API_KEY="test-key"):
        assert places.provider() == "google"


def test_the_provider_can_be_switched_off():
    with override_settings(GEOCODER_PROVIDER="off", GOOGLE_MAPS_API_KEY="test-key"):
        assert places.provider() == "off"


@override_settings(GEOCODER_PROVIDER="off")
def test_switched_off_returns_a_note_instead_of_calling_out():
    clear_cache()
    payload = places.search("SM City Butuan")
    assert payload["provider"] == "off"
    assert payload["results"] == []
    assert "switched off" in payload["note"]


def test_short_queries_are_not_sent_anywhere(monkeypatch):
    clear_cache()
    calls = []

    def never(_request, timeout=None):  # pragma: no cover - must not run
        calls.append(True)
        raise AssertionError("a two-character query must not reach the provider")

    monkeypatch.setattr(places.urllib.request, "urlopen", never)
    with override_settings(GEOCODER_PROVIDER="nominatim"):
        payload = places.search("SM")
    assert payload["results"] == []
    assert calls == []


# ---------------------------------------------------------------------------
# Nominatim (no key required)
# ---------------------------------------------------------------------------


@override_settings(GEOCODER_PROVIDER="nominatim")
def test_nominatim_results_are_normalised(monkeypatch):
    clear_cache()
    patch_opener(
        monkeypatch,
        [
            {
                "place_id": 260266010,
                "lat": "8.9454876",
                "lon": "125.5337290",
                "name": "SM City Butuan",
                "display_name": "SM City Butuan, Jose C. Aquino Avenue, Butuan, Caraga, Philippines",
            }
        ],
    )

    payload = places.search("SM City Butuan")
    assert payload["provider"] == "nominatim"
    assert len(payload["results"]) == 1
    row = payload["results"][0]
    assert row["label"] == "SM City Butuan"
    assert row["detail"].startswith("SM City Butuan, Jose C. Aquino")
    assert row["latitude"] == pytest.approx(8.945488, abs=1e-6)
    assert row["longitude"] == pytest.approx(125.533729, abs=1e-6)
    assert row["source"] == "OpenStreetMap"


@override_settings(GEOCODER_PROVIDER="nominatim")
def test_nominatim_request_is_restricted_to_the_philippines(monkeypatch):
    clear_cache()
    captured = []
    patch_opener(monkeypatch, [])
    patch_opener(monkeypatch, [], captured=captured)

    places.search("Camp Evangelista")
    url = captured[0].full_url
    assert "countrycodes=ph" in url
    assert "format=jsonv2" in url
    assert "Camp+Evangelista" in url or "Camp%20Evangelista" in url


def test_a_broken_result_is_skipped(monkeypatch):
    clear_cache()
    patch_opener(
        monkeypatch,
        [
            {"lat": "not-a-number", "lon": "125.5", "name": "Broken"},
            {"lat": "8.5", "lon": "125.6", "name": "Good"},
        ],
    )
    with override_settings(GEOCODER_PROVIDER="nominatim"):
        payload = places.search("Butuan")
    assert [row["label"] for row in payload["results"]] == ["Good"]


# ---------------------------------------------------------------------------
# Google Places
# ---------------------------------------------------------------------------


@override_settings(GEOCODER_PROVIDER="google", GOOGLE_MAPS_API_KEY="test-key")
def test_google_results_are_normalised(monkeypatch):
    clear_cache()
    captured = []
    patch_opener(
        monkeypatch,
        {
            "places": [
                {
                    "id": "ChIJ-test",
                    "displayName": {"text": "SM City Butuan"},
                    "formattedAddress": "Jose C. Aquino Ave, Butuan City, Philippines",
                    "location": {"latitude": 8.9455, "longitude": 125.5337},
                }
            ]
        },
        captured=captured,
    )

    payload = places.search("SM City Butuan")
    row = payload["results"][0]
    assert row["label"] == "SM City Butuan"
    assert row["latitude"] == pytest.approx(8.9455)
    assert row["source"] == "Google"

    request = captured[0]
    # The key is a header, never a query parameter or part of the URL.
    assert request.get_header("X-goog-api-key") == "test-key"
    assert "test-key" not in request.full_url


def test_a_provider_failure_degrades_to_a_note(monkeypatch):
    clear_cache()
    import urllib.error

    def boom(_request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(places.urllib.request, "urlopen", boom)
    with override_settings(GEOCODER_PROVIDER="nominatim"):
        payload = places.search("Butuan")
    assert payload["results"] == []
    assert "could not be reached" in payload["note"]


# ---------------------------------------------------------------------------
# endpoint
# ---------------------------------------------------------------------------


@override_settings(GEOCODER_PROVIDER="off")
def test_endpoint_requires_authentication(api):
    assert api.get("/api/gis/places/?q=Butuan").status_code in (401, 403)


@override_settings(GEOCODER_PROVIDER="off")
def test_endpoint_reports_the_provider(as_user, creator):
    response = as_user(creator).get("/api/gis/places/?q=Butuan")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["provider"] == "off"
    assert body["count"] == 0
    assert "note" in body


@override_settings(GEOCODER_PROVIDER="nominatim")
def test_endpoint_returns_pinnable_results(as_user, creator, monkeypatch):
    clear_cache()
    patch_opener(
        monkeypatch,
        [{"lat": "8.9455", "lon": "125.5337", "name": "SM City Butuan", "display_name": "SM City Butuan, Butuan"}],
    )
    body = as_user(creator).get("/api/gis/places/?q=SM%20City%20Butuan").json()
    assert body["count"] == 1
    row = body["results"][0]
    assert {"label", "detail", "latitude", "longitude", "source"} <= set(row)

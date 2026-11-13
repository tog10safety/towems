"""Naming a map pin for the location picker.

The administrative area answers "which province, and whose AOR?" and the
location answers "which barangay or establishment is this?" from PSGC
boundaries plus a best-effort geocoder that must never break the picker.
"""

from __future__ import annotations

import json

import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.ops import places
from apps.ops.models import AdministrativeArea, AreaOfResponsibility

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_place_cache():
    cache.clear()
    yield
    cache.clear()


class _Response:
    """Just enough of an HTTP response for ``places`` to read."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def square(lon: float = 121.0, lat: float = 14.6, size: float = 0.5) -> dict:
    west, east = lon - size, lon + size
    south, north = lat - size, lat + size
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


def make_area(psgc, name, level, *, lon=121.0, lat=14.6, size=0.5, area=100.0, **extra):
    region_name = extra.pop("region_name", "National Capital Region")
    return AdministrativeArea.objects.create(
        psgc=psgc,
        name=name,
        level=level,
        geometry=square(lon, lat, size),
        min_lon=lon - size,
        max_lon=lon + size,
        min_lat=lat - size,
        max_lat=lat + size,
        centroid_lon=lon,
        centroid_lat=lat,
        area_km2=area,
        region_name=region_name,
        **extra,
    )


def make_aor(unit, *, code="AOR-MM", name="Metro Manila AOR", lon=121.0, lat=14.6, size=0.4):
    return AreaOfResponsibility.objects.create(
        name=name,
        code=code,
        unit=unit,
        geometry=square(lon, lat, size),
        min_lon=lon - size,
        max_lon=lon + size,
        min_lat=lat - size,
        max_lat=lat + size,
        centroid_lon=lon,
        centroid_lat=lat,
    )


@override_settings(GEOCODER_PROVIDER="off")
def test_pin_names_the_province_and_the_owning_units_aor(as_user, creator, unit):
    make_area(501, "Metro Manila", "province", size=0.6, area=619.57)
    make_area(137401, "Quezon City", "city", size=0.2, area=166.0, parent_name="Metro Manila")
    make_aor(unit)

    body = as_user(creator).get("/api/gis/pin/?lat=14.6&lon=121.0").json()

    assert body["administrative_area"]["province"] == "Metro Manila"
    assert body["administrative_area"]["city"] == "Quezon City"
    assert body["administrative_area"]["aor"]["unit_acronym"] == unit.acronym
    assert body["administrative_area"]["label"] == f"Metro Manila - {unit.acronym} AOR"
    # The geocoder is off, so the pin is still named from the boundaries.
    assert body["location"]["label"] == "Quezon City, Metro Manila"
    assert "switched off" in body["note"]


@override_settings(GEOCODER_PROVIDER="off")
def test_pin_says_when_no_aor_covers_the_point(as_user, creator):
    make_area(501, "Metro Manila", "province", size=0.6)

    body = as_user(creator).get("/api/gis/pin/?lat=14.6&lon=121.0").json()

    assert body["administrative_area"]["aor"] is None
    assert body["administrative_area"]["label"] == "Metro Manila - No AOR assigned"


@override_settings(GEOCODER_PROVIDER="nominatim")
def test_reverse_names_the_barangay_and_establishment(monkeypatch):
    monkeypatch.setattr(
        places.urllib.request,
        "urlopen",
        lambda request, timeout=None: _Response(
            {
                "name": "Santa Teresita Barangay Hall",
                "address": {
                    "suburb": "Santa Teresita",
                    "city": "Bayugan",
                    "province": "Agusan del Sur",
                },
            }
        ),
    )

    detail = places.reverse(8.7, 125.75)

    assert detail["barangay"] == "Santa Teresita"
    assert detail["establishment"] == "Santa Teresita Barangay Hall"
    assert detail["city"] == "Bayugan"
    assert detail["province"] == "Agusan del Sur"
    assert detail["source"] == "OpenStreetMap"


@override_settings(GEOCODER_PROVIDER="nominatim")
def test_a_street_name_is_not_reported_as_the_establishment(monkeypatch):
    monkeypatch.setattr(
        places.urllib.request,
        "urlopen",
        lambda request, timeout=None: _Response(
            {
                "name": "Colonel Rodriguez Street",
                "category": "highway",
                "type": "residential",
                "address": {
                    "suburb": "Zone 2",
                    "city": "Cagayan de Oro",
                    "province": "Misamis Oriental",
                },
            }
        ),
    )

    detail = places.reverse(8.49, 124.62)

    assert detail["establishment"] == ""
    assert detail["barangay"] == "Zone 2"


@override_settings(GEOCODER_PROVIDER="nominatim")
def test_a_boundary_name_is_not_mistaken_for_an_establishment(monkeypatch):
    monkeypatch.setattr(
        places.urllib.request,
        "urlopen",
        lambda request, timeout=None: _Response(
            {"name": "Misamis Oriental", "address": {"state": "Misamis Oriental"}}
        ),
    )

    detail = places.reverse(8.66, 125.06)

    assert detail["establishment"] == ""
    assert detail["province"] == "Misamis Oriental"


@override_settings(GEOCODER_PROVIDER="nominatim")
def test_pin_uses_the_geocoder_for_the_exact_location(monkeypatch, as_user, creator, unit):
    make_area(501, "Agusan del Sur", "province", lon=125.75, lat=8.7, size=0.6)
    make_area(
        160201000, "Bayugan", "city", lon=125.75, lat=8.7, size=0.2, parent_name="Agusan del Sur"
    )
    make_aor(unit, code="AOR-ADS", name="Agusan del Sur AOR", lon=125.75, lat=8.7)
    monkeypatch.setattr(
        places.urllib.request,
        "urlopen",
        lambda request, timeout=None: _Response(
            {
                "name": "Santa Teresita Barangay Hall",
                "address": {
                    "suburb": "Santa Teresita",
                    "city": "Bayugan",
                    "province": "Agusan del Sur",
                },
            }
        ),
    )

    body = as_user(creator).get("/api/gis/pin/?lat=8.7&lon=125.75").json()

    assert body["administrative_area"]["label"] == f"Agusan del Sur - {unit.acronym} AOR"
    assert body["location"]["barangay"] == "Santa Teresita"
    assert (
        body["location"]["label"]
        == "Santa Teresita Barangay Hall, Santa Teresita, Bayugan, Agusan del Sur"
    )
    assert body["provider"] == "OpenStreetMap"


@override_settings(GEOCODER_PROVIDER="nominatim")
def test_the_region_name_is_not_used_where_psgc_knows_the_province(
    monkeypatch, as_user, creator
):
    make_area(501, "Misamis Oriental", "province", lon=124.62, lat=8.49, size=0.6)
    make_area(
        104305000,
        "Cagayan de Oro",
        "city",
        lon=124.62,
        lat=8.49,
        size=0.2,
        parent_name="Misamis Oriental",
        region_name="Region X (Northern Mindanao)",
    )
    monkeypatch.setattr(
        places.urllib.request,
        "urlopen",
        lambda request, timeout=None: _Response(
            {
                "name": "Camp Evangelista",
                "address": {"city": "Cagayan de Oro", "state": "Northern Mindanao"},
            }
        ),
    )

    body = as_user(creator).get("/api/gis/pin/?lat=8.49&lon=124.62").json()

    assert body["administrative_area"]["label"] == "Misamis Oriental - No AOR assigned"
    assert body["location"]["province"] == "Misamis Oriental"
    assert body["location"]["label"] == (
        "Camp Evangelista, Cagayan de Oro, Misamis Oriental"
    )


def test_pin_rejects_bad_coordinates(as_user, creator):
    client = as_user(creator)
    assert client.get("/api/gis/pin/?lat=abc&lon=1").status_code == 400
    assert client.get("/api/gis/pin/?lat=200&lon=1").status_code == 400


def test_pin_requires_authentication(api):
    assert api.get("/api/gis/pin/?lat=8.7&lon=125.75").status_code in (401, 403)

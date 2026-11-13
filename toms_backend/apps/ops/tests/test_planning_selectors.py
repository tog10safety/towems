"""Planning selectors: unit scope and the map location lookup.

The mission form must never offer a unit, office or location outside what the
operator is authorised to use, so both selectors are tested against the server
rather than trusted in the browser.
"""

from __future__ import annotations

import pytest

from apps.core.factories import make_unit
from apps.ops.models import AdministrativeArea

pytestmark = pytest.mark.django_db


def square(lon: float, lat: float, size: float = 0.5) -> dict:
    """A small closed square around a point (WGS84 GeoJSON)."""
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


def make_area(psgc, name, level, lon=121.0, lat=14.6, size=0.5, area=100.0, **extra):
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
        region_name="National Capital Region",
        **extra,
    )


# ---------------------------------------------------------------------------
# Unit selector
# ---------------------------------------------------------------------------


def test_unit_selector_lists_only_the_users_unit_and_subordinates(
    as_user, planner, unit, other_unit
):
    child = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)

    body = as_user(planner).get("/api/units/?scope=mine&is_active=true&page_size=200").json()
    ids = {row["id"] for row in body["results"]}

    assert unit.id in ids
    assert child.id in ids
    assert other_unit.id not in ids


def test_unit_selector_never_widens_with_a_unit_parameter(as_user, planner, unit, other_unit):
    body = as_user(planner).get(f"/api/units/?scope=mine&parent={other_unit.id}").json()
    assert unit.id not in {row["id"] for row in body["results"]}


def test_unit_selector_default_stays_a_reference_list(as_user, planner, unit, other_unit):
    ids = {row["id"] for row in as_user(planner).get("/api/units/").json()["results"]}
    assert other_unit.id in ids


def test_unit_selector_requires_authentication(api):
    assert api.get("/api/units/?scope=mine").status_code in (401, 403)


# ---------------------------------------------------------------------------
# Location lookup
# ---------------------------------------------------------------------------


def test_location_lookup_finds_the_containing_areas(as_user, planner):
    make_area(501, "Metro Manila", "province", size=0.6, area=619.57)
    make_area(137401, "Quezon City", "city", size=0.2, area=166.0)

    body = as_user(planner).get("/api/admin-areas/at/?lat=14.6&lon=121.0").json()

    assert body["count"] == 2
    # Smallest containing area first: the city, then its province.
    assert body["results"][0]["name"] == "Quezon City"
    assert body["results"][0]["level"] == "city"
    assert body["results"][1]["name"] == "Metro Manila"
    assert body["results"][0]["region_name"] == "National Capital Region"


def test_location_lookup_returns_nothing_outside_every_boundary(as_user, planner):
    make_area(501, "Metro Manila", "province", size=0.2)
    body = as_user(planner).get("/api/admin-areas/at/?lat=7.1&lon=125.6").json()
    assert body["count"] == 0


def test_location_lookup_rejects_bad_coordinates(as_user, planner):
    assert as_user(planner).get("/api/admin-areas/at/?lat=abc&lon=1").status_code == 400
    assert as_user(planner).get("/api/admin-areas/at/?lat=200&lon=1").status_code == 400

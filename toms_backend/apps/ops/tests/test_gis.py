"""GIS: MGRS conversion, AOR geometry validation, spatial queries."""

from __future__ import annotations

import pytest

from apps.core.mgrs import MGRSError, from_mgrs, to_mgrs
from apps.ops.models import AreaOfResponsibility

pytestmark = pytest.mark.django_db

MANILA = (14.5995, 120.9842)
EIFFEL = (48.8584, 2.2945)

POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [
            [120.90, 14.50],
            [121.10, 14.50],
            [121.10, 14.70],
            [120.90, 14.70],
            [120.90, 14.50],
        ]
    ],
}


# -- MGRS unit-level ------------------------------------------------------


def test_known_mgrs_reference_points():
    # Independent reference values for these landmarks.
    assert to_mgrs(*EIFFEL, 5).startswith("31UDQ")
    assert to_mgrs(21.3069, -157.8583, 5).startswith("04QFJ")


def test_mgrs_round_trip_is_sub_metre():
    for lat, lon in [MANILA, (10.3157, 123.8854), (7.1907, 125.4553), EIFFEL]:
        mgrs_value = to_mgrs(lat, lon, 5)
        back_lat, back_lon = from_mgrs(mgrs_value)
        assert abs(back_lat - lat) < 1e-5
        assert abs(back_lon - lon) < 1e-5


def test_invalid_mgrs_raises():
    # Note: "31UDQ12" is a VALID MGRS (1 digit per axis = 10 km precision),
    # so odd digit counts are used here instead.
    for value in ["", "ZZ", "31UDQ1", "99UDQ1234567890", "31IDQ1234567890"]:
        with pytest.raises(MGRSError):
            from_mgrs(value)


# -- MGRS API -------------------------------------------------------------


def test_convert_latlon_to_mgrs(as_user, creator):
    response = as_user(creator).post(
        "/api/gis/mgrs/", {"latitude": EIFFEL[0], "longitude": EIFFEL[1]}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.json()["mgrs"].startswith("31UDQ")


def test_convert_mgrs_to_latlon(as_user, creator):
    response = as_user(creator).post(
        "/api/gis/mgrs/", {"mgrs": "31UDQ4825211954"}, format="json"
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert abs(body["latitude"] - EIFFEL[0]) < 1e-4
    assert abs(body["longitude"] - EIFFEL[1]) < 1e-4


def test_convert_rejects_garbage(as_user, creator):
    response = as_user(creator).post("/api/gis/mgrs/", {"mgrs": "nonsense"}, format="json")
    assert response.status_code == 400


# -- AOR CRUD -------------------------------------------------------------


def test_create_aor_computes_bbox_and_centroid(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/aor/",
        {"name": "North AOR", "code": "AOR-N", "unit": unit.id, "geometry": POLYGON},
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["min_lon"] == pytest.approx(120.90)
    assert body["max_lat"] == pytest.approx(14.70)
    assert body["centroid_lon"] == pytest.approx(121.0, abs=1e-6)
    assert body["centroid_lat"] == pytest.approx(14.6, abs=1e-6)


def test_aor_rejects_degenerate_polygon(as_user, admin):
    bad = {"type": "Polygon", "coordinates": [[[120.9, 14.5], [121.0, 14.6]]]}
    response = as_user(admin).post(
        "/api/aor/", {"name": "Bad", "code": "BAD", "geometry": bad}, format="json"
    )
    assert response.status_code == 400


def test_aor_rejects_out_of_range_coordinates(as_user, admin):
    bad = {
        "type": "Polygon",
        "coordinates": [
            [[200.0, 14.5], [201.0, 14.5], [201.0, 14.7], [200.0, 14.5]]
        ],
    }
    response = as_user(admin).post(
        "/api/aor/", {"name": "Bad", "code": "BAD2", "geometry": bad}, format="json"
    )
    assert response.status_code == 400


def test_aor_rejects_unsupported_geometry(as_user, admin):
    response = as_user(admin).post(
        "/api/aor/",
        {"name": "Point", "code": "PT", "geometry": {"type": "Point", "coordinates": [120, 14]}},
        format="json",
    )
    assert response.status_code == 400


def test_open_polygon_is_closed_automatically(as_user, admin):
    open_ring = {
        "type": "Polygon",
        "coordinates": [[[120.90, 14.50], [121.10, 14.50], [121.10, 14.70], [120.90, 14.70]]],
    }
    response = as_user(admin).post(
        "/api/aor/", {"name": "Open", "code": "OPEN", "geometry": open_ring}, format="json"
    )
    assert response.status_code == 201
    ring = response.json()["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]


def test_creator_cannot_create_aor(as_user, creator, unit):
    response = as_user(creator).post(
        "/api/aor/",
        {"name": "Nope", "code": "NO", "unit": unit.id, "geometry": POLYGON},
        format="json",
    )
    assert response.status_code == 403


# -- Spatial queries ------------------------------------------------------


@pytest.fixture
def aor(db, safety, unit):
    return AreaOfResponsibility.objects.create(
        name="North AOR",
        code="AOR-N",
        unit=unit,
        geometry=POLYGON,
        created_by=safety,
        centroid_lon=121.0,
        centroid_lat=14.6,
        min_lon=120.9,
        min_lat=14.5,
        max_lon=121.1,
        max_lat=14.7,
    )


def test_at_point_returns_containing_aor(as_user, creator, aor):
    client = as_user(creator)
    inside = client.get("/api/aor/at-point/?latitude=14.60&longitude=121.00")
    outside = client.get("/api/aor/at-point/?latitude=10.00&longitude=121.00")
    assert inside.json()["count"] == 1
    assert outside.json()["count"] == 0


def test_contains_action(as_user, creator, aor):
    client = as_user(creator)
    response = client.post(
        f"/api/aor/{aor.id}/contains/",
        {"latitude": 14.60, "longitude": 121.00},
        format="json",
    )
    assert response.json()["contains_point"] is True

    response = client.post(
        f"/api/aor/{aor.id}/contains/",
        {"latitude": 1.0, "longitude": 1.0},
        format="json",
    )
    assert response.json()["contains_point"] is False


def test_at_point_requires_parameters(as_user, creator):
    response = as_user(creator).get("/api/aor/at-point/")
    assert response.status_code == 400


def test_location_search_uses_known_data(as_user, creator, aor):
    response = as_user(creator).get("/api/gis/search/?q=North")
    assert response.status_code == 200
    assert response.json()["count"] >= 1

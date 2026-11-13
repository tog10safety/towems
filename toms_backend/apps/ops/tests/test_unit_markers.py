"""Unit map markers: coordinate, place name and the seal used as the pin.

Covers the additive `command_unit_map_marker` table (the legacy `units` table is
never altered), the Administrator-only write rule, MGRS/coordinate handling, the
seal upload/serve/remove cycle, and their appearance on the operational picture.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.command.models import UnitMapMarker
from apps.core.factories import make_unit, make_user

pytestmark = pytest.mark.django_db


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def set_marker(client, unit, **payload):
    return client.patch(f"/api/units/{unit.id}/marker/", payload, format="json")


def test_an_administrator_pins_a_unit_without_touching_the_legacy_table(as_user, admin, unit):
    response = set_marker(
        as_user(admin),
        unit,
        latitude=8.9455,
        longitude=125.5337,
        place_name="Camp Evangelista",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["has_marker"] is True
    assert body["place_name"] == "Camp Evangelista"
    assert body["mgrs"]  # derived from the coordinates
    assert body["latitude"] == pytest.approx(8.9455, abs=1e-4)

    marker = UnitMapMarker.objects.get(unit_id=unit.id)
    assert marker.mgrs == body["mgrs"]
    # The legacy unit row is untouched: no coordinate columns were added there.
    unit.refresh_from_db()
    assert unit.acronym == "ALPHA"


def test_an_mgrs_grid_alone_pins_the_unit(as_user, admin, unit):
    as_user(admin).patch(
        f"/api/units/{unit.id}/marker/",
        {"mgrs": "51PTS8284814992"},
        format="json",
    )
    marker = UnitMapMarker.objects.get(unit_id=unit.id)
    assert marker.latitude is not None and marker.longitude is not None
    assert marker.mgrs == "51PTS8284814992"


def test_half_a_coordinate_is_refused(as_user, admin, unit):
    response = as_user(admin).patch(
        f"/api/units/{unit.id}/marker/", {"latitude": 8.9}, format="json"
    )
    assert response.status_code == 400, response.content
    assert "latitude" in response.json()["errors"]
    assert not UnitMapMarker.objects.exists()


def test_only_an_administrator_can_pin_a_unit(as_user, creator, commander, unit, other_unit):
    for user in (creator, commander):
        response = set_marker(as_user(user), unit, latitude=1, longitude=2)
        assert response.status_code == 403, (user.username, response.content)
    assert not UnitMapMarker.objects.exists()


def test_any_authenticated_user_can_read_a_units_pin(as_user, creator, admin, unit):
    set_marker(as_user(admin), unit, latitude=8.9455, longitude=125.5337)
    response = as_user(creator).get(f"/api/units/{unit.id}/marker/")
    assert response.status_code == 200, response.content
    assert response.json()["has_marker"] is True


def test_the_marker_list_shows_every_plotted_unit(as_user, creator, admin, unit, other_unit):
    """Unit locations are reference data, not a unit-scoped operational record.

    A TOG operator must see the neighbouring units an Administrator plotted,
    even though those units sit outside the operator's own chain.
    """
    set_marker(as_user(admin), unit, latitude=8.9, longitude=125.5)
    set_marker(as_user(admin), other_unit, latitude=10.3, longitude=123.9)

    rows = as_user(creator).get("/api/units/markers/").json()["results"]
    assert {row["unit_id"] for row in rows} == {unit.id, other_unit.id}

    everything = as_user(admin).get("/api/units/markers/").json()["results"]
    assert {row["unit_id"] for row in everything} == {unit.id, other_unit.id}


def test_the_unit_list_carries_the_pin_fields(as_user, admin, unit):
    set_marker(as_user(admin), unit, latitude=8.9455, longitude=125.5337)
    rows = as_user(admin).get("/api/units/?page_size=200").json()["results"]
    row = next(item for item in rows if item["id"] == unit.id)
    assert row["map_latitude"] == pytest.approx(8.9455, abs=1e-4)
    assert row["map_longitude"] == pytest.approx(125.5337, abs=1e-4)
    assert row["map_mgrs"]


def test_the_seal_upload_serve_and_remove_cycle(as_user, admin, unit):
    client = as_user(admin)
    upload = client.post(
        f"/api/units/{unit.id}/marker-logo/",
        {"logo": SimpleUploadedFile("seal.png", PNG_BYTES, content_type="image/png")},
        format="multipart",
    )
    assert upload.status_code == 200, upload.content
    body = upload.json()
    assert body["has_logo"] is True
    assert body["logo_url"].startswith(f"/api/units/{unit.id}/marker-logo/")

    served = client.get(f"/api/units/{unit.id}/marker-logo/")
    assert served.status_code == 200
    assert served["Content-Type"].startswith("image/")

    removed = client.delete(f"/api/units/{unit.id}/marker-logo/")
    assert removed.status_code == 200, removed.content
    assert removed.json()["has_logo"] is False
    assert client.get(f"/api/units/{unit.id}/marker-logo/").status_code == 404


def test_a_non_image_upload_is_refused(as_user, admin, unit):
    response = as_user(admin).post(
        f"/api/units/{unit.id}/marker-logo/",
        {"logo": SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain")},
        format="multipart",
    )
    assert response.status_code == 400, response.content
    assert "logo" in response.json()["errors"]


def test_only_an_administrator_can_upload_a_seal(as_user, creator, unit):
    response = as_user(creator).post(
        f"/api/units/{unit.id}/marker-logo/",
        {"logo": SimpleUploadedFile("seal.png", PNG_BYTES, content_type="image/png")},
        format="multipart",
    )
    assert response.status_code == 403


def test_the_operational_picture_draws_unit_pins(as_user, admin, creator, unit):
    client = as_user(admin)
    set_marker(client, unit, latitude=8.9455, longitude=125.5337)
    client.post(
        f"/api/units/{unit.id}/marker-logo/",
        {"logo": SimpleUploadedFile("seal.png", PNG_BYTES, content_type="image/png")},
        format="multipart",
    )

    body = as_user(creator).get("/api/gis/operational-picture/?layers=units").json()
    assert body["counts"]["units"] == 1
    row = body["units"][0]
    assert row["unit_id"] == unit.id
    assert row["latitude"] == pytest.approx(8.9455, abs=1e-4)
    assert row["logo_url"]

    # Without the layer the payload stays lean.
    lean = as_user(creator).get("/api/gis/operational-picture/?layers=aor").json()
    assert lean["units"] == []


def test_an_unrelated_units_pin_stays_out_of_scope(as_user, creator, admin, unit, other_unit):
    set_marker(as_user(admin), other_unit, latitude=10.3, longitude=123.9)
    body = as_user(creator).get("/api/gis/operational-picture/?layers=units").json()
    assert body["units"] == []

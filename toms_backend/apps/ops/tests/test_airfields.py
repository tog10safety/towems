"""Airports and air strips: the reference register behind the mapping layer."""

from __future__ import annotations

import pytest

from apps.ops.models import Airfield

pytestmark = pytest.mark.django_db


def test_the_register_is_seeded_with_airports_and_strips():
    codes = set(Airfield.objects.values_list("code", flat=True))
    assert {"RPLL", "RPLC", "RPVM", "RPMD", "RPMY"} <= codes
    assert Airfield.objects.filter(kind="airstrip").exists()


def test_the_register_carries_the_fields_the_map_shows():
    row = Airfield.objects.get(code="RPLL")
    assert row.name
    assert row.municipality
    assert row.latitude and row.longitude
    assert row.mgrs
    assert row.runway_length_m > 0
    assert row.runway_width_m > 0
    assert row.runway_orientation


def test_every_signed_in_account_reads_the_register(as_user, creator):
    response = as_user(creator).get("/api/airfields/?page_size=500")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["count"] >= 40
    row = next(item for item in body["results"] if item["code"] == "RPMY")
    assert row["name"] == "Laguindingan Airport"
    assert row["kind"] == "airport"
    assert row["location"]
    assert row["grid_coordinate"]


def test_the_register_can_be_filtered_to_air_strips(as_user, creator):
    response = as_user(creator).get("/api/airfields/?kind=airstrip&page_size=500")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["count"] >= 1
    assert all(item["kind"] == "airstrip" for item in body["results"])


def test_the_register_is_searchable_by_code_and_name(as_user, creator):
    by_code = as_user(creator).get("/api/airfields/?search=RPLL").json()
    assert any(item["code"] == "RPLL" for item in by_code["results"])

    by_name = as_user(creator).get("/api/airfields/?search=Laguindingan").json()
    assert any(item["code"] == "RPMY" for item in by_name["results"])

"""Air assets: the Administrator's aircraft register and its read rules."""

from __future__ import annotations

import pytest

from apps.ops.models import AirAsset

pytestmark = pytest.mark.django_db


def payload(opcon_unit, tail="001", **overrides):
    data = {
        "aircraft_type": "fixed_wing",
        "categories": ["attack_cas", "light_attack"],
        "model": "FA-50PH",
        "tail_number": tail,
        "callsign": "EAGLE 01",
        "cruising_speed_knots": 160,
        "current_location": "HTOG10, Bancasi, Butuan City, 51PYK7316589902",
        "assigned_unit": "15sw",
        "opcon_unit": opcon_unit.id if opcon_unit else None,
        "operational_status": "available",
        "firepower_capabilities": ["multi_role", "air_to_ground"],
        "munitions_categories": ["guided", "gun_ammunition"],
    }
    data.update(overrides)
    return data


def make_asset(opcon_unit, tail="001", **overrides):
    data = payload(opcon_unit, tail, **overrides)
    data.pop("opcon_unit", None)
    return AirAsset.objects.create(opcon_unit=opcon_unit, **data)


def test_administrator_records_an_air_asset(as_user, admin, unit):
    response = as_user(admin).post(
        "/api/inventory/air-assets/", payload(unit), format="json"
    )
    assert response.status_code == 201, response.content
    body = response.json()

    assert body["aircraft_type_label"] == "Fixed-Wing Aircraft"
    assert body["categories"] == ["attack_cas", "light_attack"]
    assert body["category_labels"] == [
        "Attack / Close Air Support",
        "Light Attack",
    ]
    assert body["tail_number"] == "001"
    assert body["cruising_speed_knots"] == 160
    assert body["current_location"] == "HTOG10, Bancasi, Butuan City, 51PYK7316589902"
    assert body["assigned_unit"] == "15sw"
    assert body["assigned_unit_label"] == "15th Strike Wing"
    assert body["opcon_unit"] == unit.id
    assert body["opcon_unit_acronym"] == "ALPHA"
    assert body["operational_status_label"] == "Available"
    assert body["firepower_capabilities"] == ["multi_role", "air_to_ground"]
    assert body["firepower_capability_labels"] == [
        "Multi-Role Capability",
        "Air-to-Ground Capability",
    ]
    assert body["munitions_categories"] == ["guided", "gun_ammunition"]
    assert body["munitions_category_labels"] == [
        "Guided Munitions",
        "Gun Ammunition",
    ]
    assert "classification" not in body

    row = AirAsset.objects.get(tail_number="001")
    assert row.model == "FA-50PH"
    assert row.current_location == "HTOG10, Bancasi, Butuan City, 51PYK7316589902"
    assert row.assigned_unit == "15sw"
    assert row.opcon_unit_id == unit.id
    assert not hasattr(row, "classification")


def test_current_location_records_operational_mapping_details(as_user, admin, unit):
    client = as_user(admin)
    location = "HTOG10, Bancasi, Butuan City, 51PYK7316589902"

    recorded = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "002", current_location=location),
        format="json",
    )
    assert recorded.status_code == 201, recorded.content
    body = recorded.json()
    assert body["current_location"] == location
    assert body["map_location"] == {
        "mgrs": "51PYK7316589902",
        "latitude": pytest.approx(8.946891, abs=1e-6),
        "longitude": pytest.approx(125.484154, abs=1e-6),
    }
    assert AirAsset.objects.get(tail_number="002").current_location == location

    blank = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "003", current_location=""),
        format="json",
    )
    assert blank.status_code == 201, blank.content
    assert blank.json()["current_location"] == ""
    assert blank.json()["map_location"] is None


def test_administrator_can_edit_and_delete_an_air_asset(as_user, admin, unit):
    client = as_user(admin)
    created = client.post(
        "/api/inventory/air-assets/", payload(unit), format="json"
    ).json()

    edited = client.patch(
        f"/api/inventory/air-assets/{created['id']}/",
        {"operational_status": "grounded", "callsign": "EAGLE 02"},
        format="json",
    )
    assert edited.status_code == 200, edited.content
    assert edited.json()["operational_status"] == "grounded"
    assert edited.json()["operational_status_label"] == "Grounded"

    assert client.delete(f"/api/inventory/air-assets/{created['id']}/").status_code == 204
    assert not AirAsset.objects.filter(pk=created["id"]).exists()


def test_non_administrators_can_read_but_never_change_the_register(
    as_user, logistics, unit
):
    asset = make_asset(unit)

    client = as_user(logistics)
    listed = client.get("/api/inventory/air-assets/")
    assert listed.status_code == 200, listed.content
    assert [row["tail_number"] for row in listed.json()["results"]] == ["001"]

    assert (
        client.post(
            "/api/inventory/air-assets/", payload(unit, "002"), format="json"
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/inventory/air-assets/{asset.id}/",
            {"operational_status": "maintenance"},
            format="json",
        ).status_code
        == 403
    )
    assert client.delete(f"/api/inventory/air-assets/{asset.id}/").status_code == 403

    asset.refresh_from_db()
    assert asset.operational_status == "available"
    assert AirAsset.objects.count() == 1


def test_a_creator_without_resources_cannot_open_the_register(as_user, creator, unit):
    make_asset(unit)
    response = as_user(creator).get("/api/inventory/air-assets/")
    assert response.status_code == 403


def test_air_assets_are_visible_to_the_opcon_unit_only(
    as_user, admin, logistics, unit, other_unit
):
    make_asset(unit, "001")
    make_asset(other_unit, "002")
    make_asset(None, "003")

    own = as_user(logistics).get("/api/inventory/air-assets/").json()
    assert [row["tail_number"] for row in own["results"]] == ["001"]

    every = as_user(admin).get("/api/inventory/air-assets/").json()
    assert {row["tail_number"] for row in every["results"]} == {"001", "002", "003"}


def test_assigned_unit_is_required_and_comes_from_the_air_units(as_user, admin, unit):
    client = as_user(admin)

    missing = payload(unit, "002")
    missing.pop("assigned_unit")
    required = client.post(
        "/api/inventory/air-assets/", missing, format="json"
    )
    assert required.status_code == 400
    assert "assigned_unit" in required.json()["errors"]

    unknown = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "003", assigned_unit="not_an_air_unit"),
        format="json",
    )
    assert unknown.status_code == 400
    assert "assigned_unit" in unknown.json()["errors"]
    assert AirAsset.objects.count() == 0


def test_validation_rejects_unknown_or_empty_categories(as_user, admin, unit):
    client = as_user(admin)

    unknown = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "002", categories=["not_a_real_category"]),
        format="json",
    )
    assert unknown.status_code == 400
    assert "categories" in unknown.json()["errors"]

    empty = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "003", categories=[]),
        format="json",
    )
    assert empty.status_code == 400
    assert "categories" in empty.json()["errors"]
    assert AirAsset.objects.count() == 0


def test_capability_and_munitions_selections_are_validated_lists(as_user, admin, unit):
    client = as_user(admin)

    recorded = client.post(
        "/api/inventory/air-assets/",
        payload(
            unit,
            "002",
            firepower_capabilities=["multi_role", "air_to_ground", "multi_role"],
            munitions_categories=["guided", "gun_ammunition"],
        ),
        format="json",
    )
    assert recorded.status_code == 201, recorded.content
    body = recorded.json()
    assert body["firepower_capabilities"] == ["multi_role", "air_to_ground"]
    assert body["munitions_categories"] == ["guided", "gun_ammunition"]

    no_selections = client.post(
        "/api/inventory/air-assets/",
        payload(
            unit,
            "003",
            firepower_capabilities=[],
            munitions_categories=[],
        ),
        format="json",
    )
    assert no_selections.status_code == 201, no_selections.content
    assert no_selections.json()["firepower_capability_labels"] == []
    assert no_selections.json()["munitions_category_labels"] == []

    for field in ("firepower_capabilities", "munitions_categories"):
        unknown = client.post(
            "/api/inventory/air-assets/",
            payload(unit, "004", **{field: ["not_a_real_choice"]}),
            format="json",
        )
        assert unknown.status_code == 400
        assert field in unknown.json()["errors"]

    scalar = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "005", firepower_capabilities="multi_role"),
        format="json",
    )
    assert scalar.status_code == 400
    assert "firepower_capabilities" in scalar.json()["errors"]


def test_tail_number_is_unique_only_for_the_same_type_and_model(as_user, admin, unit):
    client = as_user(admin)
    first = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "001", aircraft_type="fixed_wing", model="FA-50PH"),
        format="json",
    )
    assert first.status_code == 201, first.content

    different_type = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "001", aircraft_type="rotary_wing", model="FA-50PH"),
        format="json",
    )
    assert different_type.status_code == 201, different_type.content

    different_model = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "001", aircraft_type="fixed_wing", model="T-50"),
        format="json",
    )
    assert different_model.status_code == 201, different_model.content

    duplicate = client.post(
        "/api/inventory/air-assets/",
        payload(unit, "001", aircraft_type="fixed_wing", model="FA-50PH"),
        format="json",
    )
    assert duplicate.status_code == 400
    assert "tail_number" in duplicate.json()["errors"]

    edit_into_duplicate = client.patch(
        f"/api/inventory/air-assets/{different_model.json()['id']}/",
        {"model": "FA-50PH"},
        format="json",
    )
    assert edit_into_duplicate.status_code == 400
    assert "tail_number" in edit_into_duplicate.json()["errors"]
    assert AirAsset.objects.count() == 3


def test_the_register_can_be_filtered_and_searched(as_user, admin, unit):
    client = as_user(admin)
    client.post(
        "/api/inventory/air-assets/",
        payload(unit, "001", model="FA-50PH", aircraft_type="fixed_wing"),
        format="json",
    )
    client.post(
        "/api/inventory/air-assets/",
        payload(
            unit,
            "002",
            model="ScanEagle",
            aircraft_type="uav",
            callsign="RAVEN 02",
            assigned_unit="5fw",
            operational_status="maintenance",
        ),
        format="json",
    )

    by_type = client.get("/api/inventory/air-assets/?aircraft_type=uav").json()
    assert [row["tail_number"] for row in by_type["results"]] == ["002"]

    by_model = client.get("/api/inventory/air-assets/?model=ScanEagle").json()
    assert [row["tail_number"] for row in by_model["results"]] == ["002"]

    by_type_and_model = client.get(
        "/api/inventory/air-assets/?aircraft_type=fixed_wing&model=ScanEagle"
    ).json()
    assert by_type_and_model["results"] == []

    by_status = client.get(
        "/api/inventory/air-assets/?operational_status=maintenance"
    ).json()
    assert [row["tail_number"] for row in by_status["results"]] == ["002"]

    by_unit = client.get("/api/inventory/air-assets/?assigned_unit=5fw").json()
    assert [row["tail_number"] for row in by_unit["results"]] == ["002"]

    by_search = client.get("/api/inventory/air-assets/?search=RAVEN").json()
    assert [row["tail_number"] for row in by_search["results"]] == ["002"]

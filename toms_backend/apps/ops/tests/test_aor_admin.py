"""Administrator-only AOR management with real administrative boundaries.

Covers spec §1-§5: search, actual polygons, conflict detection (identical,
partial, parent/child), transfers, atomicity, audit and the read-only scope
for non-Administrators.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.command.models import OfficeSection, SectionAssignment
from apps.core import geo
from apps.core.factories import make_unit, make_user
from apps.ops.aor import unit_color
from apps.ops.models import AdministrativeArea, AreaOfResponsibility, AuditLog

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def geo_box(geometry):
    box = geo.bounding_box(geometry)
    centre = geo.centroid(geometry)
    return {
        "min_lon": box["min_lon"],
        "min_lat": box["min_lat"],
        "max_lon": box["max_lon"],
        "max_lat": box["max_lat"],
        "centroid_lon": centre[0],
        "centroid_lat": centre[1],
    }


def rect(min_lon, min_lat, max_lon, max_lat):
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]
        ],
    }


def make_area(
    psgc,
    name,
    level,
    geometry,
    *,
    parent=None,
    parent_name="",
    region_name="Test Region",
):
    box = geo.bounding_box(geometry)
    centre = geo.centroid(geometry)
    return AdministrativeArea.objects.create(
        psgc=psgc,
        name=name,
        level=level,
        parent_psgc=getattr(parent, "psgc", None),
        parent_name=parent_name or (parent.name if parent else ""),
        region_name=region_name,
        geometry=geometry,
        search_text=f"{name.lower()} {level} {parent_name} {region_name} {psgc}",
        min_lon=box["min_lon"],
        min_lat=box["min_lat"],
        max_lon=box["max_lon"],
        max_lat=box["max_lat"],
        centroid_lon=centre[0],
        centroid_lat=centre[1],
    )


@pytest.fixture
def province():
    return make_area(
        401000000,
        "Batangas",
        AdministrativeArea.LEVEL_PROVINCE,
        rect(120.5, 13.5, 122.0, 14.5),
    )


@pytest.fixture
def city(province):
    return make_area(
        401005000,
        "Batangas City",
        AdministrativeArea.LEVEL_CITY,
        rect(121.0, 13.9, 121.2, 14.1),
        parent=province,
        parent_name="Batangas",
    )


@pytest.fixture
def far_area():
    return make_area(
        702200000,
        "Cebu",
        AdministrativeArea.LEVEL_PROVINCE,
        rect(123.5, 10.0, 124.5, 11.0),
    )


def assign(client, unit, psgcs, **extra):
    payload = {"unit": unit.id, "psgcs": psgcs, **extra}
    return client.post("/api/aor/assign/", payload, format="json")


# ---------------------------------------------------------------------------
# search & boundaries (spec §2/§3)
# ---------------------------------------------------------------------------


def test_search_is_case_insensitive_and_partial(as_user, admin, province, city):
    response = as_user(admin).get("/api/admin-areas/?q=batang")
    assert response.status_code == 200
    names = {row["name"] for row in response.json()["results"]}
    assert {"Batangas", "Batangas City"} <= names

    only_cities = as_user(admin).get("/api/admin-areas/?q=BATANG&level=city").json()
    assert [row["name"] for row in only_cities["results"]] == ["Batangas City"]
    assert only_cities["results"][0]["level_label"] == "City"


def test_search_distinguishes_same_named_places(as_user, admin, province):
    make_area(
        1804514000,
        "Isabela",
        AdministrativeArea.LEVEL_MUNICIPALITY,
        rect(123.0, 10.2, 123.1, 10.3),
        parent_name="Negros Occidental",
    )
    make_area(
        203100000,
        "Isabela",
        AdministrativeArea.LEVEL_PROVINCE,
        rect(121.5, 16.5, 122.5, 17.5),
    )
    rows = as_user(admin).get("/api/admin-areas/?q=isabela").json()["results"]
    assert len(rows) == 2
    assert {row["level"] for row in rows} == {"province", "municipality"}
    assert any(row["parent_name"] == "Negros Occidental" for row in rows)
    assert len({row["psgc"] for row in rows}) == 2


def test_retrieve_returns_the_actual_boundary(as_user, admin, province):
    row = as_user(admin).get(f"/api/admin-areas/{province.psgc}/").json()
    assert row["geometry"] == province.geometry
    assert row["bbox"][0][0] == pytest.approx(13.5)
    assert row["area_km2"] is None  # not invented when the source has no value


def test_boundary_layer_is_geojson_and_marks_assignments(
    as_user, admin, unit, province
):
    assign(as_user(admin), unit, [province.psgc])
    payload = as_user(admin).get("/api/admin-areas/boundaries/?level=province").json()
    assert payload["type"] == "FeatureCollection"
    feature = next(f for f in payload["features"] if f["properties"]["psgc"] == province.psgc)
    assert feature["properties"]["assigned_unit"] == unit.acronym
    assert feature["geometry"]["type"] == "Polygon"


# ---------------------------------------------------------------------------
# assignment, conflict detection, transfers (spec §4/§5)
# ---------------------------------------------------------------------------


def test_administrator_assignment_creates_aor_from_boundary(
    as_user, admin, unit, province
):
    response = assign(as_user(admin), unit, [province.psgc])
    assert response.status_code == 200, response.content
    body = response.json()
    assert len(body["assigned"]) == 1

    aor = AreaOfResponsibility.objects.get(admin_area=province)
    assert aor.unit_id == unit.id
    assert aor.geometry == province.geometry
    assert aor.code == f"PSGC-{province.psgc}"
    assert aor.min_lat == pytest.approx(13.5)
    assert aor.created_by_id == admin.id

    coverage = as_user(admin).get("/api/aor/coverage/").json()
    entry = next(row for row in coverage["results"] if row["unit_id"] == unit.id)
    assert [area["psgc"] for area in entry["areas"]] == [province.psgc]


def test_assignment_coexists_with_a_manually_drawn_aor(as_user, admin, unit, province):
    AreaOfResponsibility.objects.create(
        name="Legacy manual",
        code="LEGACY-1",
        unit=unit,
        geometry=rect(125.0, 9.0, 125.2, 9.2),
        **geo_box(rect(125.0, 9.0, 125.2, 9.2)),
    )
    response = assign(as_user(admin), unit, [province.psgc])
    assert response.status_code == 200, response.content
    coverage = as_user(admin).get("/api/aor/coverage/?unit=%d" % unit.id).json()
    entry = next(row for row in coverage["results"] if row["unit_id"] == unit.id)
    assert entry["aor_count"] == 2
    assert entry["coverage_km2"] >= 0


def test_assigning_the_same_area_twice_is_idempotent(as_user, admin, unit, province):
    client = as_user(admin)
    assign(client, unit, [province.psgc])
    second = assign(client, unit, [province.psgc])
    assert second.status_code == 200
    assert second.json()["unchanged"][0]["psgc"] == province.psgc
    assert AreaOfResponsibility.objects.filter(admin_area=province, is_active=True).count() == 1


def test_duplicate_assignment_to_another_unit_is_refused(
    as_user, admin, unit, other_unit, province
):
    client = as_user(admin)
    assign(client, unit, [province.psgc])

    response = assign(client, other_unit, [province.psgc])
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "overlap"
    assert body["conflicts"][0]["unit_id"] == unit.id

    assert AreaOfResponsibility.objects.get(admin_area=province).unit_id == unit.id


def test_transfer_needs_confirmation_and_is_audited(
    as_user, admin, unit, other_unit, province
):
    client = as_user(admin)
    assign(client, unit, [province.psgc])

    refused = assign(client, other_unit, [province.psgc], confirm_transfer=False)
    assert refused.status_code == 400

    moved = assign(client, other_unit, [province.psgc], confirm_transfer=True)
    assert moved.status_code == 200, moved.content
    body = moved.json()
    assert body["transferred"][0]["admin_psgc"] == province.psgc

    aor = AreaOfResponsibility.objects.get(admin_area=province)
    assert aor.unit_id == other_unit.id
    assert aor.is_active is True

    entry = AuditLog.objects.filter(action="aor_transfer").latest("id")
    assert entry.actor_id == admin.id
    assert entry.before["unit"] == unit.id
    assert entry.after["unit"] == other_unit.id
    assert str(province.psgc) in entry.note


def test_partial_geometric_overlap_between_units_is_refused(
    as_user, admin, unit, other_unit
):
    AreaOfResponsibility.objects.create(
        name="Western half",
        code="MANUAL-1",
        unit=unit,
        geometry=rect(120.0, 13.0, 121.0, 14.0),
        **{k: v for k, v in geo_box(rect(120.0, 13.0, 121.0, 14.0)).items()},
    )
    half = make_area(
        999000001,
        "Overlapping district",
        AdministrativeArea.LEVEL_PROVINCE,
        rect(120.5, 13.0, 121.5, 14.0),
    )

    response = assign(as_user(admin), other_unit, [half.psgc])
    assert response.status_code == 400
    conflict = response.json()["conflicts"][0]
    assert conflict["relation"] == "partial"
    assert conflict["overlap_km2"] > 0
    assert not AreaOfResponsibility.objects.filter(admin_area=half).exists()


def geo_box(geometry):
    box = geo.bounding_box(geometry)
    centre = geo.centroid(geometry)
    return {
        "min_lon": box["min_lon"],
        "min_lat": box["min_lat"],
        "max_lon": box["max_lon"],
        "max_lat": box["max_lat"],
        "centroid_lon": centre[0],
        "centroid_lat": centre[1],
    }


def test_city_inside_another_units_province_is_refused(
    as_user, admin, unit, other_unit, province, city
):
    client = as_user(admin)
    assign(client, unit, [province.psgc])

    response = assign(client, other_unit, [city.psgc])
    assert response.status_code == 400
    conflict = response.json()["conflicts"][0]
    assert conflict["relation"] in {"contained", "identical", "partial"}
    assert conflict["unit_id"] == unit.id


def test_city_can_be_added_to_the_same_units_coverage(
    as_user, admin, unit, province, city
):
    client = as_user(admin)
    assign(client, unit, [province.psgc])
    response = assign(client, unit, [city.psgc])
    assert response.status_code == 200, response.content
    assert AreaOfResponsibility.objects.filter(unit=unit, is_active=True).count() == 2


def test_a_failed_assignment_leaves_nothing_behind(
    as_user, admin, unit, other_unit, province, far_area, city
):
    client = as_user(admin)
    assign(client, unit, [province.psgc])

    # far_area is free, city overlaps the other unit's province: the whole save
    # must be rejected without a partial assignment of far_area.
    response = assign(client, other_unit, [far_area.psgc, city.psgc])
    assert response.status_code == 400
    assert not AreaOfResponsibility.objects.filter(admin_area=far_area).exists()


def test_archive_is_soft_and_audited(as_user, admin, unit, province):
    client = as_user(admin)
    assign(client, unit, [province.psgc])
    aor = AreaOfResponsibility.objects.get(admin_area=province)

    response = client.post(f"/api/aor/{aor.id}/archive/", {"reason": "Superseded"}, format="json")
    assert response.status_code == 200, response.content
    aor.refresh_from_db()
    assert aor.is_active is False
    entry = AuditLog.objects.get(action="aor_archive", entity_id=str(aor.id))
    assert "Superseded" in entry.note

    # Restoring returns it to coverage without recreating the record.
    restore = client.post(f"/api/aor/{aor.id}/restore/")
    assert restore.status_code == 200, restore.content
    aor.refresh_from_db()
    assert aor.is_active is True

    # And the same area can be assigned again afterwards (reactivated row).
    again = assign(client, unit, [province.psgc])
    assert again.status_code == 200
    assert AreaOfResponsibility.objects.filter(admin_area=province).count() == 1


def test_existing_overlaps_are_reported_but_not_changed(as_user, admin, unit, other_unit):
    first = AreaOfResponsibility.objects.create(
        name="AOR A", code="A", unit=unit, geometry=rect(120, 13, 121, 14), **geo_box(rect(120, 13, 121, 14))
    )
    second = AreaOfResponsibility.objects.create(
        name="AOR B",
        code="B",
        unit=other_unit,
        geometry=rect(120.5, 13.5, 121.5, 14.5),
        **geo_box(rect(120.5, 13.5, 121.5, 14.5)),
    )

    response = as_user(admin).get("/api/aor/overlaps/")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert {row["id"] for row in body["results"][0]["aors"]} == {first.id, second.id}
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_active and second.is_active


def test_manual_polygon_overlap_is_rejected(as_user, admin, unit, other_unit):
    AreaOfResponsibility.objects.create(
        name="Manual A", code="MA", unit=unit, geometry=rect(120, 13, 121, 14), **geo_box(rect(120, 13, 121, 14))
    )
    response = as_user(admin).post(
        "/api/aor/",
        {
            "name": "Manual B",
            "code": "MB",
            "unit": other_unit.id,
            "geometry": rect(120.2, 13.2, 120.8, 13.8),
        },
        format="json",
    )
    assert response.status_code == 400
    assert "geometry" in response.json()["errors"]


# ---------------------------------------------------------------------------
# permissions (spec §1/§6, test list 12-14)
# ---------------------------------------------------------------------------


def test_non_administrators_cannot_change_aors(as_user, creator, safety, unit, province):
    for user in (creator, safety):
        client = as_user(user)
        assert client.post("/api/aor/assign/", {"unit": unit.id, "psgs": []}, format="json").status_code in (400, 403)
        assert assign(client, unit, [province.psgc]).status_code == 403
        assert client.post(
            "/api/aor/",
            {"name": "Sneaky", "code": f"S-{user.id}", "unit": unit.id, "geometry": rect(1, 1, 2, 2)},
            format="json",
        ).status_code == 403
    assert not AreaOfResponsibility.objects.exists()


def test_a_mother_units_coverage_rolls_up_its_subordinate_units(
    as_user, admin, seat_admin, unit, province
):
    """The child's AOR is automatically the mother unit's AOR."""
    child = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    assign(as_user(admin), child, [province.psgc])

    body = as_user(admin).get("/api/aor/coverage/?geometry=1").json()
    by_id = {row["unit_id"]: row for row in body["results"]}

    mother = by_id[unit.id]
    # No duplicate rows: the mother holds nothing itself, yet counts the child's
    # area as its own coverage.
    assert mother["aor_count"] == 0
    assert mother["areas"] == []
    assert mother["inherited_count"] == 1
    assert mother["total_aor_count"] == 1
    assert mother["inherited_from"] == [child.acronym]
    assert mother["parent_unit_id"] is None
    assert mother["effective_geometry"]["type"] in {"Polygon", "MultiPolygon"}
    # The union really is the child's ground, not an empty placeholder.
    assert geo.planar_area_km2(mother["effective_geometry"]) > 1000

    # The child still reports the area as its own.
    assert by_id[child.id]["aor_count"] == 1
    assert by_id[child.id]["inherited_count"] == 0
    assert by_id[child.id]["parent_acronym"] == unit.acronym

    # Deleting the child's area removes it from the mother too, automatically.
    child_aor = AreaOfResponsibility.objects.get(admin_area=province)
    assert as_user(admin).delete(f"/api/aor/{child_aor.id}/").status_code == 204
    after = {row["unit_id"]: row for row in as_user(admin).get("/api/aor/coverage/").json()["results"]}
    assert unit.id not in after or after[unit.id]["inherited_count"] == 0


def test_the_roll_up_reaches_every_ancestor(as_user, admin):
    grandparent = make_unit(name="Group HQ", acronym="GHQ")
    parent = make_unit(name="Wing", acronym="WING", parent=grandparent)
    child = make_unit(name="Squadron", acronym="SQN", parent=parent)
    area = make_area(
        555000000,
        "Echo",
        AdministrativeArea.LEVEL_PROVINCE,
        rect(122.0, 9.0, 123.0, 10.0),
    )
    assign(as_user(admin), child, [area.psgc])

    by_id = {
        row["unit_id"]: row
        for row in as_user(admin).get("/api/aor/coverage/").json()["results"]
    }
    assert by_id[parent.id]["inherited_count"] == 1
    assert by_id[grandparent.id]["inherited_count"] == 1
    assert by_id[grandparent.id]["parent_unit_id"] is None


def test_neighbouring_boundaries_that_only_touch_are_not_conflicts(
    as_user, admin, unit, other_unit
):
    """Adjacent provinces may belong to different units.

    The PSGC boundaries are simplified: neighbours overlap by a few metres along
    the shared edge. That sliver must not count as a claim on the same ground -
    it made Davao Oriental / Davao de Oro / Davao del Norte impossible to add
    next to an existing Agusan / Bukidnon / Surigao AOR.
    """
    make_area(
        111000000,
        "Alpha",
        AdministrativeArea.LEVEL_PROVINCE,
        rect(120.0, 13.0, 122.0, 15.0),
    )
    make_area(
        222000000,
        "Beta",
        AdministrativeArea.LEVEL_PROVINCE,
        rect(121.999, 13.5, 123.0, 14.5),
    )
    client = as_user(admin)
    assert assign(client, other_unit, [111000000]).status_code == 200

    response = assign(client, unit, [222000000])
    assert response.status_code == 200, response.content
    assert AreaOfResponsibility.objects.filter(unit=unit, is_active=True).count() == 1


def test_a_meaningful_partial_overlap_is_still_refused(as_user, admin, unit, other_unit):
    make_area(
        333000000,
        "Gamma",
        AdministrativeArea.LEVEL_PROVINCE,
        rect(120.0, 10.0, 122.0, 12.0),
    )
    make_area(
        444000000,
        "Delta",
        AdministrativeArea.LEVEL_PROVINCE,
        rect(121.0, 10.5, 123.0, 12.5),
    )
    client = as_user(admin)
    assert assign(client, other_unit, [333000000]).status_code == 200

    response = assign(client, unit, [444000000])
    assert response.status_code == 400, response.content
    assert response.json()["code"] == "overlap"


def test_assigned_areas_take_the_units_colour(as_user, admin, unit, province):
    """Province/city selection colours coverage by unit for distinction."""
    client = as_user(admin)
    assign(client, unit, [province.psgc])
    aor = AreaOfResponsibility.objects.get(admin_area=province)
    assert aor.color == unit_color(unit)

    # A drawn boundary with no colour supplied follows the same rule.
    drawn = client.post(
        "/api/aor/",
        {
            "name": "Drawn area",
            "code": "DRAWN-COLOUR",
            "unit": unit.id,
            "geometry": rect(125.0, 9.0, 125.5, 9.4),
        },
        format="json",
    )
    assert drawn.status_code == 201, drawn.content
    assert drawn.json()["color"] == unit_color(unit)


def test_a_transfer_recolours_the_area_to_the_new_unit(as_user, admin, unit, other_unit, province):
    client = as_user(admin)
    assign(client, unit, [province.psgc])
    aor = AreaOfResponsibility.objects.get(admin_area=province)
    assert aor.color == unit_color(unit)
    assert unit_color(unit) != unit_color(other_unit)

    response = assign(client, other_unit, [province.psgc], confirm_transfer=True)
    assert response.status_code == 200, response.content
    aor.refresh_from_db()
    assert aor.unit_id == other_unit.id
    assert aor.color == unit_color(other_unit)


def test_every_unit_reports_its_aor_colour(as_user, admin, unit, other_unit):
    body = as_user(admin).get("/api/units/?page_size=200").json()
    colours = {row["id"]: row["aor_color"] for row in body["results"]}
    assert colours[unit.id] == unit_color(unit)
    assert colours[other_unit.id] == unit_color(other_unit)
    assert colours[unit.id] != colours[other_unit.id]


def test_delete_removes_the_record_and_keeps_a_full_snapshot(
    as_user, admin, seat_admin, unit, province
):
    client = as_user(admin)
    assign(client, unit, [province.psgc])
    aor = AreaOfResponsibility.objects.get(admin_area=province)

    response = client.delete(f"/api/aor/{aor.id}/")
    assert response.status_code == 204, response.content
    assert not AreaOfResponsibility.objects.filter(id=aor.id).exists()

    entry = AuditLog.objects.get(action="aor_delete", entity_id=str(aor.id))
    # The boundary itself is kept, so the record can be reconstructed.
    assert entry.before["geometry"]["type"] == "Polygon"
    assert entry.before["code"] == aor.code

    # The area is free again, so it can be assigned to any unit.
    assert assign(client, unit, [province.psgc]).status_code == 200


def test_a_manually_drawn_aor_can_be_deleted(as_user, admin, seat_admin, unit):
    """The reported case: a drawn AOR (no administrative area) must delete."""
    drawn = AreaOfResponsibility.objects.create(
        name="TOG10 AOR",
        code="TOG10-AO",
        unit=unit,
        geometry=rect(121.0, 14.0, 121.5, 14.4),
        **geo_box(rect(121.0, 14.0, 121.5, 14.4)),
    )
    section = as_user(admin)
    assert section.delete(f"/api/aor/{drawn.id}/").status_code == 204
    assert not AreaOfResponsibility.objects.filter(code="TOG10-AO").exists()


def test_operations_center_maintains_its_own_units_aor(as_user, unit):
    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    member = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(user=member, section=office, assignment_type="primary")

    aor = AreaOfResponsibility.objects.create(
        name="Drawn",
        code="DRAWN-1",
        unit=unit,
        geometry=rect(121.0, 14.0, 121.5, 14.4),
        **geo_box(rect(121.0, 14.0, 121.5, 14.4)),
    )
    client = as_user(member)

    # Descriptive edit is allowed...
    response = client.patch(
        f"/api/aor/{aor.id}/",
        {"name": "Drawn area", "color": "#123456"},
        format="json",
    )
    assert response.status_code == 200, response.content

    # ...but the coverage itself is not: unit and boundary are ignored.
    response = client.patch(
        f"/api/aor/{aor.id}/",
        {"unit": unit.id, "geometry": rect(100.0, 5.0, 100.5, 5.4)},
        format="json",
    )
    assert response.status_code == 200, response.content
    aor.refresh_from_db()
    assert aor.geometry == rect(121.0, 14.0, 121.5, 14.4)

    # Archive and restore are theirs; permanent delete is not.
    assert client.post(f"/api/aor/{aor.id}/archive/").status_code == 200
    assert client.post(f"/api/aor/{aor.id}/restore/").status_code == 200
    assert client.delete(f"/api/aor/{aor.id}/").status_code == 403
    assert AreaOfResponsibility.objects.filter(id=aor.id).exists()


def test_operations_center_cannot_maintain_another_units_aor(as_user, unit, other_unit):
    office = OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPSCTR",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )
    member = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(user=member, section=office, assignment_type="primary")

    foreign = AreaOfResponsibility.objects.create(
        name="Bravo area",
        code="BRAVO-AO",
        unit=other_unit,
        geometry=rect(121.0, 14.0, 121.5, 14.4),
        **geo_box(rect(121.0, 14.0, 121.5, 14.4)),
    )
    # Out of scope entirely: the record is not even visible.
    assert as_user(member).post(f"/api/aor/{foreign.id}/archive/").status_code == 404
    foreign.refresh_from_db()
    assert foreign.is_active is True


def test_restore_does_not_take_an_area_back_from_another_unit(
    as_user, admin, unit, other_unit, province
):
    """Assigning an archived area to another unit moves the row, not the history.

    The administrative-area row is reused (one row per PSGC area), so once
    another unit holds it, restoring the old row must not change ownership.
    """
    client = as_user(admin)
    assign(client, unit, [province.psgc])
    aor = AreaOfResponsibility.objects.get(admin_area=province)
    assert client.post(f"/api/aor/{aor.id}/archive/").status_code == 200
    assert assign(client, other_unit, [province.psgc]).status_code == 200

    assert client.post(f"/api/aor/{aor.id}/restore/").status_code == 200
    aor.refresh_from_db()
    assert aor.unit_id == other_unit.id
    assert AreaOfResponsibility.objects.filter(admin_area=province).count() == 1


def test_non_administrator_cannot_delete_or_reassign(
    as_user, admin, creator, unit, province
):
    assign(as_user(admin), unit, [province.psgc])
    aor = AreaOfResponsibility.objects.get(admin_area=province)

    client = as_user(creator)
    assert client.delete(f"/api/aor/{aor.id}/").status_code == 403
    assert client.post(f"/api/aor/{aor.id}/archive/").status_code == 403
    assert client.post(f"/api/aor/{aor.id}/remove/").status_code == 403
    assert client.patch(
        f"/api/aor/{aor.id}/", {"name": "Hijacked"}, format="json"
    ).status_code == 403
    aor.refresh_from_db()
    assert aor.is_active and aor.name == "Batangas"


def test_aor_list_stays_inside_the_unit_scope(
    as_user, admin, creator, unit, other_unit, province, far_area
):
    client = as_user(admin)
    assign(client, unit, [province.psgc])
    assign(client, other_unit, [far_area.psgc])

    visible = as_user(creator).get("/api/aor/?page_size=200").json()["results"]
    units = {row["unit"] for row in visible}
    assert other_unit.id not in units
    assert unit.id in units


def test_unauthenticated_requests_are_refused(api, province):
    assert api.get("/api/admin-areas/").status_code in (401, 403)
    assert api.get("/api/aor/").status_code in (401, 403)


# ---------------------------------------------------------------------------
# map configuration (spec §7/§8/§9)
# ---------------------------------------------------------------------------


def test_map_config_offers_licensed_providers_and_layers(as_user, admin, monkeypatch):
    monkeypatch.delenv("TOMS_INTERNAL_TILE_URL", raising=False)
    payload = as_user(admin).get("/api/map/config/").json()

    ids = {item["id"] for item in payload["basemaps"]}
    assert {"streets", "streets_dark", "satellite", "hybrid", "terrain"} <= ids
    # Every offered basemap must be keyless: CARTO retired its keyless tiles
    # and answers them with an "API Key Required" placeholder image.
    night = next(item for item in payload["basemaps"] if item["id"] == "streets_dark")
    assert night["available"] is True
    assert "cartocdn" not in night["url"]
    assert night["max_native_zoom"] <= night["max_zoom"]
    # No dead options: the unconfigured self-hosted provider is marked unavailable.
    internal = next(item for item in payload["basemaps"] if item["id"] == "internal")
    assert internal["available"] is False
    assert "TOMS_INTERNAL_TILE_URL" in internal["unavailable_reason"]

    assert payload["default"] == "streets"
    layers = {layer["id"] for layer in payload["layers"]}
    assert {"aor", "admin_boundaries", "missions", "vehicles", "hazards"} <= layers

    # No secrets or credentials are exposed through the config.
    text = str(payload).lower()
    for forbidden in ("api_key", "token", "secret", "password"):
        assert forbidden not in text


def test_internal_basemap_appears_only_when_configured(as_user, admin, monkeypatch):
    monkeypatch.setenv("TOMS_INTERNAL_TILE_URL", "https://tiles.internal/{z}/{x}/{y}.png")
    payload = as_user(admin).get("/api/map/config/").json()
    internal = next(item for item in payload["basemaps"] if item["id"] == "internal")
    assert internal["available"] is True


# ---------------------------------------------------------------------------
# bundled dataset (spec §11/§12)
# ---------------------------------------------------------------------------


def test_bundled_dataset_imports_cleanly_and_is_idempotent():
    call_command("import_admin_boundaries", verbosity=0)
    total = AdministrativeArea.objects.count()
    assert total == 1730
    assert AdministrativeArea.objects.filter(level="province").count() == 82
    assert AdministrativeArea.objects.filter(level="city").count() == 149
    assert AdministrativeArea.objects.filter(level="municipality").count() == 1493

    batangas = AdministrativeArea.objects.get(psgc=401000000)
    assert batangas.name == "Batangas"
    assert batangas.geometry["type"] in {"Polygon", "MultiPolygon"}
    # Lipa City lies inside Batangas: the stored polygons really are geographic.
    assert geo.point_in_geometry(121.16, 13.94, batangas.geometry)

    call_command("import_admin_boundaries", verbosity=0)
    assert AdministrativeArea.objects.count() == total

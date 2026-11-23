"""Inventory: atomic stock movements, validation and sensitive-data access."""

from __future__ import annotations

import pytest

from apps.ops.models import InventoryCategory, InventoryItem, InventoryTransaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def category(db):
    # Codes are test-local: the standard catalogue (MAT, EQP, AGE, FA, AMMO, ...)
    # is seeded by migration 0011 and must not be shadowed here.
    return InventoryCategory.objects.create(name="General Supplies", code="T-GEN", kind="materiel")


@pytest.fixture
def sensitive_category(db):
    return InventoryCategory.objects.create(
        name="Ammunition", code="T-AMMO", kind="ammunition", is_sensitive=True
    )


@pytest.fixture
def item(db, category, unit):
    return InventoryItem.objects.create(
        category=category,
        name="Field Rations",
        asset_tag="SUP-001",
        unit=unit,
        quantity_total=10,
        quantity_available=10,
    )


@pytest.fixture
def sensitive_item(db, sensitive_category, unit):
    return InventoryItem.objects.create(
        category=sensitive_category,
        name="5.56mm Ball",
        asset_tag="AMM-001",
        unit=unit,
        quantity_total=1000,
        quantity_available=1000,
        is_sensitive=True,
    )


def move(client, item, **payload):
    body = {"item": item.id}
    body.update(payload)
    return client.post(f"/api/inventory/items/{item.id}/move/", body, format="json")


def test_issue_stock_decrements_available(as_user, logistics, item):
    client = as_user(logistics)
    response = move(client, item, kind="issue", quantity=3)
    assert response.status_code == 201, response.content
    item.refresh_from_db()
    assert item.quantity_available == 7
    assert item.quantity_total == 10
    assert InventoryTransaction.objects.filter(item=item, kind="issue").count() == 1


def test_issue_insufficient_stock_is_rejected_and_leaves_state_unchanged(
    as_user, logistics, item
):
    client = as_user(logistics)
    response = move(client, item, kind="issue", quantity=99)
    assert response.status_code == 400
    assert "only 10 available" in response.json()["detail"]
    item.refresh_from_db()
    assert item.quantity_available == 10
    assert InventoryTransaction.objects.filter(item=item).count() == 0


def test_return_increases_available(as_user, logistics, item):
    client = as_user(logistics)
    assert move(client, item, kind="issue", quantity=4).status_code == 201
    assert move(client, item, kind="return", quantity=2).status_code == 201
    item.refresh_from_db()
    assert item.quantity_available == 8


def test_receipt_increases_total_and_available(as_user, logistics, item):
    client = as_user(logistics)
    assert move(client, item, kind="receipt", quantity=5).status_code == 201
    item.refresh_from_db()
    assert item.quantity_total == 15
    assert item.quantity_available == 15


def test_return_cannot_exceed_total(as_user, logistics, item):
    client = as_user(logistics)
    response = move(client, item, kind="return", quantity=1)
    assert response.status_code == 400


def test_transfer_requires_destination_unit(as_user, logistics, item):
    client = as_user(logistics)
    response = move(client, item, kind="transfer", quantity=1)
    assert response.status_code == 400


def test_transfer_moves_item_between_units(as_user, logistics, item, other_unit):
    client = as_user(logistics)
    response = move(client, item, kind="transfer", quantity=1, to_unit=other_unit.id)
    assert response.status_code == 201, response.content
    item.refresh_from_db()
    assert item.unit_id == other_unit.id


def test_maintenance_changes_serviceability(as_user, logistics, item):
    client = as_user(logistics)
    assert move(client, item, kind="maintenance", quantity=1).status_code == 201
    item.refresh_from_db()
    assert item.serviceability == "maintenance"


def test_creator_cannot_move_stock(as_user, creator, item):
    client = as_user(creator)
    response = move(client, item, kind="issue", quantity=1)
    assert response.status_code == 403
    item.refresh_from_db()
    assert item.quantity_available == 10


def test_sensitive_items_hidden_from_a_non_sensitive_resource_viewer(
    as_user, commander, item, sensitive_item
):
    response = as_user(commander).get("/api/inventory/items/")
    assert response.status_code == 200, response.content
    names = [row["name"] for row in response.json()["results"]]
    assert "Field Rations" in names
    assert "5.56mm Ball" not in names


def test_sensitive_items_visible_to_safety(as_user, safety, sensitive_item):
    response = as_user(safety).get("/api/inventory/items/")
    names = [row["name"] for row in response.json()["results"]]
    assert "5.56mm Ball" in names


def test_sensitive_item_detail_is_404_for_a_non_sensitive_resource_viewer(
    as_user, commander, sensitive_item
):
    response = as_user(commander).get(f"/api/inventory/items/{sensitive_item.id}/")
    assert response.status_code == 404


def test_quantity_cannot_go_negative_through_api(as_user, logistics, item):
    client = as_user(logistics)
    response = move(client, item, kind="issue", quantity=-5)
    assert response.status_code == 400


def test_transaction_ledger_is_read_only(as_user, logistics, item):
    client = as_user(logistics)
    move(client, item, kind="issue", quantity=1)
    txn = InventoryTransaction.objects.get(item=item)
    response = client.delete(f"/api/inventory/transactions/{txn.id}/")
    assert response.status_code == 405
    assert InventoryTransaction.objects.filter(pk=txn.id).exists()


def test_item_cannot_be_deleted_while_ledger_references_it(as_user, logistics, item):
    client = as_user(logistics)
    move(client, item, kind="issue", quantity=1)
    response = client.delete(f"/api/inventory/items/{item.id}/")
    assert response.status_code in (400, 409, 500)
    assert InventoryItem.objects.filter(pk=item.id).exists()


# ---------------------------------------------------------------------------
# The register belongs to the Logistics Officer
# ---------------------------------------------------------------------------


def test_safety_officer_reads_but_cannot_change_the_register(as_user, safety, item):
    client = as_user(safety)

    # Read access - controlled items included - is unchanged.
    assert client.get("/api/inventory/items/").status_code == 200

    # Every write is refused: stock movement, add, edit and delete.
    assert move(client, item, kind="issue", quantity=1).status_code == 403
    added = client.post(
        "/api/inventory/items/",
        {
            "name": "Field Bed",
            "category": item.category_id,
            "quantity_total": 2,
            "unit": item.unit_id,
        },
        format="json",
    )
    assert added.status_code == 403
    edited = client.patch(
        f"/api/inventory/items/{item.id}/", {"serviceability": "condemned"}, format="json"
    )
    assert edited.status_code == 403
    assert client.delete(f"/api/inventory/items/{item.id}/").status_code == 403

    item.refresh_from_db()
    assert item.quantity_available == 10
    assert item.serviceability == "serviceable"


def test_logistics_officer_adds_edits_and_removes_an_item(as_user, logistics, unit):
    client = as_user(logistics)
    category = InventoryCategory.objects.create(name="Tools", code="T-TLS", kind="materiel")

    created = client.post(
        "/api/inventory/items/",
        {
            "name": "Tool Kit",
            "category": category.id,
            "asset_tag": "TLS-001",
            "quantity_total": 4,
            "unit": unit.id,
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    item_id = created.json()["id"]

    edited = client.patch(
        f"/api/inventory/items/{item_id}/", {"serviceability": "maintenance"}, format="json"
    )
    assert edited.status_code == 200, edited.content
    assert InventoryItem.objects.get(pk=item_id).serviceability == "maintenance"

    assert client.delete(f"/api/inventory/items/{item_id}/").status_code == 204
    assert not InventoryItem.objects.filter(pk=item_id).exists()


def test_logistics_officer_cannot_move_stock_of_another_unit(as_user, logistics, other_unit):
    """The post is scoped to its own unit tree, like every other appointment."""
    client = as_user(logistics)
    foreign_category = InventoryCategory.objects.create(
        name="Rations", code="T-RTN", kind="materiel"
    )
    foreign_item = InventoryItem.objects.create(
        category=foreign_category,
        name="Foreign Rations",
        unit=other_unit,
        quantity_total=5,
        quantity_available=5,
    )
    assert move(client, foreign_item, kind="issue", quantity=1).status_code == 404
    foreign_item.refresh_from_db()
    assert foreign_item.quantity_available == 5


# ---------------------------------------------------------------------------
# Asset classes: materiel, equipment, AGE, firearms, ammunition, vehicles
# ---------------------------------------------------------------------------


def test_the_standard_asset_classes_are_seeded():
    """The catalogue ships with one category per class, so an item can be filed."""
    by_code = {row.code: row for row in InventoryCategory.objects.all()}
    assert {"MAT", "EQP", "AGE", "COMMS", "FA", "AMMO"} <= set(by_code)
    assert by_code["AGE"].kind == "age"
    assert by_code["AGE"].get_kind_display() == "Aerospace Ground Equipment (AGE)"
    # Firearms and ammunition are controlled by definition.
    assert by_code["FA"].is_sensitive is True
    assert by_code["AMMO"].is_sensitive is True
    assert by_code["EQP"].is_sensitive is False


def test_items_filter_by_asset_class(as_user, logistics, item):
    age = InventoryCategory.objects.get(code="AGE")
    InventoryItem.objects.create(
        category=age,
        name="Ground Power Unit 28V",
        unit=item.unit,
        quantity_total=2,
        quantity_available=2,
    )
    client = as_user(logistics)

    names = [row["name"] for row in client.get("/api/inventory/items/?kind=age").json()["results"]]
    assert names == ["Ground Power Unit 28V"]

    materiel = client.get("/api/inventory/items/?kind=materiel").json()["results"]
    assert [row["name"] for row in materiel] == ["Field Rations"]
    # The class is on every row, so the table can label it.
    assert materiel[0]["category_kind"] == "materiel"
    assert materiel[0]["category_kind_label"] == "Materiel"
    assert client.get("/api/inventory/items/?kind=firearms").json()["count"] == 0


def test_items_filter_by_a_section_that_spans_two_kinds(as_user, logistics, item):
    """The Armory tab asks for firearms and ammunition in one request."""
    InventoryItem.objects.create(
        category=InventoryCategory.objects.get(code="FA"),
        name="M4 Carbine",
        unit=item.unit,
        quantity_total=1,
        quantity_available=1,
    )
    InventoryItem.objects.create(
        category=InventoryCategory.objects.get(code="AMMO"),
        name="5.56 mm Ball",
        unit=item.unit,
        quantity_total=200,
        quantity_available=200,
    )
    InventoryItem.objects.create(
        category=InventoryCategory.objects.get(code="AGE"),
        name="Ground Power Unit 28V",
        unit=item.unit,
        quantity_total=1,
        quantity_available=1,
    )

    body = as_user(logistics).get("/api/inventory/items/?kind=firearms,ammunition").json()
    assert body["count"] == 2
    assert {row["category_kind"] for row in body["results"]} == {"firearms", "ammunition"}


def test_categories_filter_by_class_and_carry_a_label(as_user, logistics):
    body = as_user(logistics).get("/api/inventory/categories/?kind=age").json()
    assert body["count"] >= 1
    assert body["results"][0]["kind_label"] == "Aerospace Ground Equipment (AGE)"


def test_only_the_administrator_adds_an_asset_category(as_user, admin, logistics):
    """The catalogue is the System Administrator's; an officer requests a code."""
    payload = {"name": "AGE - Power Support", "code": "AGE-PWR", "kind": "age"}
    refused = as_user(logistics).post(
        "/api/inventory/categories/", payload, format="json"
    )
    assert refused.status_code == 403

    created = as_user(admin).post("/api/inventory/categories/", payload, format="json")
    assert created.status_code == 201, created.content
    assert created.json()["kind"] == "age"
    assert created.json()["kind_label"] == "Aerospace Ground Equipment (AGE)"


def test_a_controlled_category_makes_its_items_controlled(as_user, logistics):
    firearms = InventoryCategory.objects.get(code="FA")
    created = as_user(logistics).post(
        "/api/inventory/items/",
        {
            "name": "M4 Carbine",
            "category": firearms.id,
            "serial_number": "W123456",
            "quantity_total": 1,
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    assert created.json()["is_sensitive"] is True
    # The Logistics Officer now owns firearms/ammunition, so the register shows
    # them the controlled rows they maintain.
    listed = as_user(logistics).get("/api/inventory/items/?kind=firearms").json()["results"]
    assert [row["name"] for row in listed] == ["M4 Carbine"]


def test_a_non_sensitive_resource_viewer_cannot_see_controlled_items(as_user, commander):
    """Widening visibility to the Logistics Officer must not widen it for everyone."""
    InventoryItem.objects.create(
        category=InventoryCategory.objects.get(code="AMMO"),
        name="5.56mm Ball",
        quantity_total=100,
        quantity_available=100,
        is_sensitive=True,
    )
    body = as_user(commander).get("/api/inventory/items/?kind=ammunition").json()
    assert body["count"] == 0


def test_a_category_holding_items_cannot_be_deleted(as_user, admin, item):
    client = as_user(admin)
    response = client.delete(f"/api/inventory/categories/{item.category_id}/")
    assert response.status_code == 400
    assert "still holds" in response.json()["detail"]
    assert InventoryCategory.objects.filter(pk=item.category_id).exists()


def test_an_empty_category_can_be_deleted(as_user, admin):
    category = InventoryCategory.objects.create(name="Unused Class", code="UNU", kind="other")
    assert as_user(admin).delete(f"/api/inventory/categories/{category.id}/").status_code == 204
    assert not InventoryCategory.objects.filter(pk=category.id).exists()


def test_logistics_officer_records_items_only_in_their_own_unit_tree(
    as_user, logistics, category, unit, other_unit
):
    """Add is scoped to the caller's unit and its subordinates, like import."""
    from apps.core.factories import make_unit

    subordinate = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    client = as_user(logistics)

    def add(name, **extra):
        body = {"category": category.id, "name": name, "quantity_total": 1}
        body.update(extra)
        return client.post("/api/inventory/items/", body, format="json")

    assert add("Own stores", unit=unit.id).status_code == 201
    assert add("Detachment stores", unit=subordinate.id).status_code == 201

    foreign = add("Bravo stores", unit=other_unit.id)
    assert foreign.status_code == 400
    assert "subordinate" in str(foreign.json())

    # Omitting the unit files the item against the caller's own unit.
    defaulted = add("Unsited stores")
    assert defaulted.status_code == 201, defaulted.content
    assert defaulted.json()["unit"] == unit.id


# ---------------------------------------------------------------------------
# Jet A1 fuel: drums and bulk
# ---------------------------------------------------------------------------


@pytest.fixture
def fuel_category(db):
    # Seeded by migration 0043, like the standard catalogue in 0011.
    return InventoryCategory.objects.get(code="POL-01")


def test_the_pol_catalogue_is_seeded_with_its_codes(db):
    rows = list(
        InventoryCategory.objects.filter(code__startswith="POL-")
        .order_by("code")
        .values_list("code", "name", "kind")
    )
    assert rows == [
        ("POL-01", "Aviation Fuel", "fuel"),
        ("POL-02", "Automotive Fuel", "fuel"),
        ("POL-03", "Engine Lubricants", "fuel"),
        ("POL-04", "Hydraulic Fluids", "fuel"),
        ("POL-05", "Greases", "fuel"),
        ("POL-06", "Transmission and Gear Oils", "fuel"),
        ("POL-07", "Coolants", "fuel"),
        ("POL-08", "Special-Purpose Fluids", "fuel"),
        ("POL-09", "Cleaning and Solvents", "fuel"),
    ]


def test_each_pol_category_accepts_only_its_own_grades(as_user, logistics):
    """POL-01 is AVGas or Jet A1, POL-02 is diesel or gasoline, the rest none."""
    client = as_user(logistics)
    aviation = InventoryCategory.objects.get(code="POL-01")
    automotive = InventoryCategory.objects.get(code="POL-02")
    lubricants = InventoryCategory.objects.get(code="POL-03")

    def post(category, **extra):
        return client.post(
            "/api/inventory/items/",
            {
                "name": f"{category.code} stock",
                "category": category.id,
                "quantity_total": 10,
                "fuel_form": "bulk",
                **extra,
            },
            format="json",
        )

    assert post(aviation, fuel_grade="jet_a1").status_code == 201
    assert post(aviation, fuel_grade="avgas").status_code == 201
    assert post(aviation, fuel_grade="diesel").status_code == 400
    # Aviation fuel is always bought in a grade.
    assert post(aviation).status_code == 400

    assert post(automotive, fuel_grade="diesel").status_code == 201
    assert post(automotive, fuel_grade="gasoline").status_code == 201
    assert post(automotive, fuel_grade="jet_a1").status_code == 400

    # Lubricants are POL stock but have no grade; drums and bulk still apply.
    created = post(lubricants)
    assert created.status_code == 201, created.content
    assert created.json()["fuel_grade"] == ""
    assert created.json()["litres_available"] == 10
    assert post(lubricants, fuel_grade="diesel").status_code == 400


def test_pol_stock_records_batch_dates(as_user, logistics, fuel_category, category):
    """POL batches are dated; a materiel row cannot carry those dates."""
    client = as_user(logistics)
    payload = {
        "name": "Petron Corporation",
        "category": fuel_category.id,
        "quantity_total": 5000,
        "fuel_grade": "jet_a1",
        "fuel_form": "bulk",
        "date_issued": "2026-11-01",
        "testing_date": "2026-11-05",
        "expiry_date": "2027-11-01",
    }
    created = client.post("/api/inventory/items/", payload, format="json")
    assert created.status_code == 201, created.content
    assert created.json()["date_issued"] == "2026-11-01"
    assert created.json()["testing_date"] == "2026-11-05"
    assert created.json()["expiry_date"] == "2027-11-01"

    backwards = client.post(
        "/api/inventory/items/",
        {**payload, "name": "Backwards", "expiry_date": "2026-10-01"},
        format="json",
    )
    assert backwards.status_code == 400
    assert "expiry_date" in backwards.json()["errors"]

    materiel = client.post(
        "/api/inventory/items/",
        {
            "name": "Field Rations",
            "category": category.id,
            "quantity_total": 5,
            "date_issued": "2026-11-01",
        },
        format="json",
    )
    assert materiel.status_code == 400
    assert "date_issued" in materiel.json()["errors"]


def test_a_bulk_jet_a1_delivery_tops_up_the_same_tank(
    as_user, logistics, fuel_category
):
    """One bulk tank per airport: a delivery adds to the balance and the trail."""
    client = as_user(logistics)

    def deliver(location, quantity, da_number, grade="jet_a1"):
        return client.post(
            "/api/inventory/items/",
            {
                "name": "Petron Corporation",
                "category": fuel_category.id,
                "quantity_total": quantity,
                "unit_of_measure": "litre",
                "storage_location": location,
                "fuel_grade": grade,
                "fuel_form": "bulk",
                "da_number": da_number,
            },
            format="json",
        )

    first = deliver("Laguindingan Airport (RPMY)", 10000, "DA-0001")
    assert first.status_code == 201, first.content

    second = deliver("Laguindingan Airport (RPMY)", 5000, "DA-0002")
    assert second.status_code == 201, second.content
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["quantity_total"] == 15000
    assert second.json()["quantity_available"] == 15000
    assert second.json()["da_number"] == "DA-0002"

    # The trail is the deliveries: each bulk-in keeps its own DA number.
    ledger = client.get(
        f"/api/inventory/items/{first.json()['id']}/transactions/"
    ).json()
    assert [(row["reference"], row["quantity"]) for row in ledger] == [
        ("DA-0002", 5000),
        ("DA-0001", 10000),
    ]

    # The same grade at another airport is a different tank.
    elsewhere = deliver("Bacolod Airport (RPVB)", 2000, "DA-0003")
    assert elsewhere.json()["id"] != first.json()["id"]


def test_a_da_number_belongs_to_bulk_aviation_only(
    as_user, logistics, fuel_category, category
):
    client = as_user(logistics)
    drum = client.post(
        "/api/inventory/items/",
        {
            "name": "Jet A1 drums",
            "category": fuel_category.id,
            "quantity_total": 4,
            "fuel_grade": "jet_a1",
            "fuel_form": "drum",
            "drum_capacity_litres": 200,
            "da_number": "DA-0009",
        },
        format="json",
    )
    assert drum.status_code == 400
    assert "da_number" in drum.json()["errors"]

    materiel = client.post(
        "/api/inventory/items/",
        {
            "name": "Field Rations",
            "category": category.id,
            "quantity_total": 5,
            "da_number": "DA-0010",
        },
        format="json",
    )
    assert materiel.status_code == 400
    assert "da_number" in materiel.json()["errors"]


def test_each_section_reports_its_own_statistics(
    as_user, logistics, category, unit, fuel_category
):
    """A tab's cards count that tab's shelf, not the whole store."""
    InventoryItem.objects.create(
        category=category,
        name="Field Rations",
        unit=unit,
        quantity_total=10,
        quantity_available=10,
    )
    InventoryItem.objects.create(
        category=fuel_category,
        name="Jet A1 bulk tank",
        unit=unit,
        quantity_total=15000,
        quantity_available=15000,
        unit_of_measure="litre",
        fuel_grade="jet_a1",
        fuel_form="bulk",
    )
    client = as_user(logistics)

    pol = client.get("/api/inventory/stats/?kind=fuel").json()
    assert pol["items"] == 1
    assert pol["quantity_available"] == 15000
    assert [row["kind"] for row in pol["by_class"]] == ["fuel"]
    assert pol["fuel"]["items"] == 1

    armory = client.get("/api/inventory/stats/?kind=firearms,ammunition").json()
    assert armory["items"] == 0
    assert {row["kind"] for row in armory["by_class"]} == {"firearms", "ammunition"}


def test_jet_a1_is_monitored_in_drums_and_in_bulk(
    as_user, logistics, unit, fuel_category
):
    """Drums convert with their own capacity; bulk is already litres."""
    InventoryItem.objects.create(
        category=fuel_category,
        name="Jet A1 drums - POL store",
        unit=unit,
        storage_location="POL Store, Bay 1",
        quantity_total=50,
        quantity_available=40,
        unit_of_measure="drum",
        fuel_grade="jet_a1",
        fuel_form="drum",
        drum_capacity_litres=200,
    )
    InventoryItem.objects.create(
        category=fuel_category,
        name="Jet A1 bulk tank",
        unit=unit,
        storage_location="Bulk farm",
        quantity_total=15000,
        quantity_available=12000,
        unit_of_measure="litre",
        fuel_grade="jet_a1",
        fuel_form="bulk",
    )

    fuel = as_user(logistics).get("/api/inventory/stats/").json()["fuel"]
    assert fuel["items"] == 2
    assert fuel["litres_available"] == 8000 + 12000
    assert fuel["litres_total"] == 10000 + 15000

    by_form = {row["form"]: row for row in fuel["by_form"]}
    assert by_form["drum"]["units_available"] == 40
    assert by_form["drum"]["units_total"] == 50
    assert by_form["drum"]["litres_available"] == 8000
    assert by_form["drum"]["litres_total"] == 10000
    assert by_form["bulk"]["units_available"] == 12000
    assert by_form["bulk"]["litres_available"] == 12000

    rows = {row["name"]: row for row in fuel["rows"]}
    assert rows["Jet A1 drums - POL store"]["litres_available"] == 8000
    assert rows["Jet A1 drums - POL store"]["drum_capacity_litres"] == 200
    assert rows["Jet A1 drums - POL store"]["unit_acronym"] == unit.acronym
    assert rows["Jet A1 bulk tank"]["litres_available"] == 12000


def test_materiel_is_never_reported_as_fuel(as_user, logistics, item):
    """A litre figure is never inferred for a row that is not fuel."""
    fuel = as_user(logistics).get("/api/inventory/stats/").json()["fuel"]
    assert fuel["items"] == 0
    assert fuel["litres_available"] == 0
    # Both ways of holding fuel are always reported, including the empty one.
    assert [row["form"] for row in fuel["by_form"]] == ["drum", "bulk"]


def test_fuel_items_are_filtered_by_the_fuel_class(
    as_user, logistics, category, unit, fuel_category
):
    InventoryItem.objects.create(
        category=fuel_category,
        name="Jet A1 bulk tank",
        unit=unit,
        quantity_total=1000,
        quantity_available=1000,
        unit_of_measure="litre",
        fuel_grade="jet_a1",
        fuel_form="bulk",
    )
    InventoryItem.objects.create(
        category=category, name="Field Rations", unit=unit, quantity_total=1
    )
    client = as_user(logistics)
    names = [
        row["name"]
        for row in client.get("/api/inventory/items/?kind=fuel").json()["results"]
    ]
    assert names == ["Jet A1 bulk tank"]
    listed = client.get("/api/inventory/items/?kind=fuel").json()["results"][0]
    assert listed["litres_available"] == 1000
    assert listed["fuel_grade_label"] == "Jet A1"
    assert listed["fuel_form_label"] == "Bulk tank (litres)"


def test_a_fuel_item_must_say_drums_or_bulk_and_its_drum_capacity(
    as_user, logistics, fuel_category
):
    client = as_user(logistics)
    base = {"name": "Jet A1 drums", "category": fuel_category.id, "quantity_total": 10}

    no_form = client.post(
        "/api/inventory/items/", {**base, "fuel_grade": "jet_a1"}, format="json"
    )
    assert no_form.status_code == 400
    assert "fuel_form" in no_form.json()["errors"]

    no_capacity = client.post(
        "/api/inventory/items/",
        {**base, "fuel_grade": "jet_a1", "fuel_form": "drum"},
        format="json",
    )
    assert no_capacity.status_code == 400
    assert "drum_capacity_litres" in no_capacity.json()["errors"]

    created = client.post(
        "/api/inventory/items/",
        {
            **base,
            "fuel_grade": "jet_a1",
            "fuel_form": "drum",
            "drum_capacity_litres": 200,
            "unit_of_measure": "drum",
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    assert created.json()["litres_available"] == 2000


def test_a_drum_store_defaults_to_the_units_pol_dump(
    as_user, logistics, fuel_category, unit
):
    """Drums are held at the officer's own unit, at its POL dump."""
    response = as_user(logistics).post(
        "/api/inventory/items/",
        {
            "name": "Jet A1 drums",
            "category": fuel_category.id,
            "quantity_total": 10,
            "unit_of_measure": "drum",
            "fuel_grade": "jet_a1",
            "fuel_form": "drum",
            "drum_capacity_litres": 200,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["storage_location"] == f"{unit.acronym} POL Dump"
    # No unit was posted: the stock is filed against the officer's own unit.
    assert response.json()["unit"] == unit.id


def _aor_polygon(*, lon: float = 125.0, lat: float = 8.5, size: float = 0.4) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - size, lat - size],
                [lon + size, lat - size],
                [lon + size, lat + size],
                [lon - size, lat + size],
                [lon - size, lat - size],
            ]
        ],
    }


def test_fuel_locations_are_the_airports_inside_the_unit_aor(
    as_user, logistics, unit
):
    """Bulk fuel is sited on an airport, and only inside the officer's AOR."""
    from apps.ops.models import Airfield, AreaOfResponsibility

    Airfield.objects.create(
        code="TINS", name="Inside Field", latitude=8.5, longitude=125.0
    )
    Airfield.objects.create(
        code="TOUT", name="Outside Field", latitude=14.6, longitude=121.0
    )
    AreaOfResponsibility.objects.create(
        name="Unit AOR", code="T-AOR", unit=unit, is_active=True, geometry=_aor_polygon()
    )

    body = as_user(logistics).get("/api/inventory/fuel-locations/").json()
    assert body["aor_scoped"] is True
    assert body["unit"] == unit.acronym
    codes = {row["code"] for row in body["results"]}
    # The seeded register is global, so the AOR may hold more than one field:
    # what matters is that the one inside is offered and the one outside is not.
    assert "TINS" in codes
    assert "TOUT" not in codes
    inside = next(row for row in body["results"] if row["code"] == "TINS")
    assert inside["display"] == "Inside Field (TINS)"

    # Search narrows the list; it never widens the AOR scope.
    narrowed = as_user(logistics).get(
        "/api/inventory/fuel-locations/?search=Outside"
    ).json()
    assert narrowed["count"] == 0


def test_fuel_locations_fall_back_to_every_airport_without_an_aor(
    as_user, logistics
):
    """A unit with no AOR is not blocked from recording bulk stock."""
    body = as_user(logistics).get("/api/inventory/fuel-locations/").json()
    assert body["aor_scoped"] is False
    assert body["count"] >= 40


def test_a_fuel_issue_reaches_the_litre_figure(as_user, logistics, fuel_category, unit):
    """The existing ledger is the fuel ledger: issuing drums lowers litres."""
    item = InventoryItem.objects.create(
        category=fuel_category,
        name="Jet A1 drums",
        unit=unit,
        quantity_total=10,
        quantity_available=10,
        unit_of_measure="drum",
        fuel_grade="jet_a1",
        fuel_form="drum",
        drum_capacity_litres=200,
    )
    client = as_user(logistics)
    assert move(client, item, kind="issue", quantity=3).status_code == 201

    fuel = client.get("/api/inventory/stats/").json()["fuel"]
    assert fuel["litres_available"] == 7 * 200
    assert fuel["movements_30d"] == 1
    assert fuel["rows"][0]["movements_30d"] == 1


def test_transfer_destinations_follow_the_mother_wing_aor(as_user, logistics, unit):
    """Drums may only be sent somewhere the Wing's AOR covers.

    The boundary belongs to the Wing, not the officer's own group: a field in a
    subordinate group's area is inside the Wing's coverage, one in another
    Wing's area is not.
    """
    from apps.command.models import UnitMapMarker
    from apps.core.factories import make_unit
    from apps.ops.models import Airfield, AreaOfResponsibility, OperationalMapFeature

    wing = make_unit(name="Test Wing", acronym="TWING", category="Wing")
    unit.parent = wing
    unit.save(update_fields=["parent"])
    AreaOfResponsibility.objects.create(
        name="Group AOR", code="T-AORW", unit=unit, is_active=True, geometry=_aor_polygon()
    )
    Airfield.objects.create(
        code="WIN", name="Inside Field", latitude=8.5, longitude=125.0
    )
    Airfield.objects.create(
        code="WOUT", name="Outside Field", latitude=14.6, longitude=121.0
    )
    OperationalMapFeature.objects.create(
        name="Friendly Inside",
        feature_type="friendly_troop",
        geometry={"type": "Point", "coordinates": [125.0, 8.5]},
    )
    OperationalMapFeature.objects.create(
        name="Friendly Outside",
        feature_type="friendly_troop",
        geometry={"type": "Point", "coordinates": [121.0, 14.6]},
    )
    UnitMapMarker.objects.create(unit=wing, latitude=8.5, longitude=125.0)

    body = as_user(logistics).get("/api/inventory/fuel/destinations/").json()
    assert body["aor_scoped"] is True
    assert body["mother_unit"] == "TWING"
    labels = {(row["type"], row["label"]) for row in body["results"]}
    assert ("Airport", "Inside Field (WIN)") in labels
    assert ("Friendly troops", "Friendly Inside") in labels
    assert ("Unit", "Test Wing") in labels
    # The seeded airfield register is global, so only the planted outside rows
    # matter here: nothing outside the polygon may be offered.
    assert not any("Outside Field" in label for _, label in labels)
    assert not any("Friendly Outside" in label for _, label in labels)
    assert all(row["latitude"] is not None for row in body["results"])


def test_transfer_destinations_fall_back_without_a_wing_aor(as_user, logistics, unit):
    """A Wing with no AOR recorded does not block a transfer."""
    from apps.core.factories import make_unit

    wing = make_unit(name="Bare Wing", acronym="BWING", category="Wing")
    unit.parent = wing
    unit.save(update_fields=["parent"])

    body = as_user(logistics).get("/api/inventory/fuel/destinations/").json()
    assert body["aor_scoped"] is False
    assert body["mother_unit"] == "BWING"
    assert body["count"] > 0


def _wing_guard(unit, *, acronym: str = "GWING"):
    """A Wing over the officer's unit, with one field inside and one outside."""
    from apps.core.factories import make_unit
    from apps.ops.models import Airfield, AreaOfResponsibility

    wing = make_unit(name=f"{acronym} Wing", acronym=acronym, category="Wing")
    unit.parent = wing
    unit.save(update_fields=["parent"])
    AreaOfResponsibility.objects.create(
        name=f"{acronym} AOR",
        code=f"{acronym}-AOR",
        unit=unit,
        is_active=True,
        geometry=_aor_polygon(),
    )
    Airfield.objects.create(
        code="GIN", name="Inside Field", latitude=8.5, longitude=125.0
    )
    Airfield.objects.create(
        code="GOUT", name="Outside Field", latitude=14.6, longitude=121.0
    )
    return wing


def _drum_source(fuel_category, unit, drums: int = 10):
    return InventoryItem.objects.create(
        category=fuel_category,
        name="Jet A1 drums",
        unit=unit,
        storage_location=f"{unit.acronym} POL Dump",
        quantity_total=drums,
        quantity_available=drums,
        unit_of_measure="drum",
        fuel_grade="jet_a1",
        fuel_form="drum",
        drum_capacity_litres=200,
    )


def test_transfer_rejects_a_destination_outside_the_wing_aor(
    as_user, logistics, fuel_category, unit
):
    """The endpoint enforces the Wing's AOR, so the picker is not the guard."""
    _wing_guard(unit)
    source = _drum_source(fuel_category, unit)

    response = as_user(logistics).post(
        "/api/inventory/fuel/transfer/",
        {
            "destination": "Outside Field (GOUT)",
            "latitude": 14.6,
            "longitude": 121.0,
            "moves": [{"item_id": source.id, "drums": 2}],
        },
        format="json",
    )

    assert response.status_code == 400
    assert "does not cover" in str(response.json()["errors"]["destination"])
    source.refresh_from_db()
    assert source.quantity_available == 10
    assert not InventoryItem.objects.filter(
        storage_location="Outside Field (GOUT)"
    ).exists()


def test_transfer_takes_the_destination_position_from_the_operational_map(
    as_user, logistics, fuel_category, unit
):
    """Client coordinates are ignored: the mapped point is the one stored."""
    _wing_guard(unit, acronym="HWING")
    source = _drum_source(fuel_category, unit)

    response = as_user(logistics).post(
        "/api/inventory/fuel/transfer/",
        {
            "destination": "Inside Field (GIN)",
            "latitude": 14.6,
            "longitude": 121.0,
            "moves": [{"item_id": source.id, "drums": 3}],
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    target = InventoryItem.objects.get(storage_location="Inside Field (GIN)")
    assert target.quantity_available == 3
    assert target.location_latitude == 8.5
    assert target.location_longitude == 125.0

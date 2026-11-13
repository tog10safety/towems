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


def test_categories_filter_by_class_and_carry_a_label(as_user, logistics):
    body = as_user(logistics).get("/api/inventory/categories/?kind=age").json()
    assert body["count"] >= 1
    assert body["results"][0]["kind_label"] == "Aerospace Ground Equipment (AGE)"


def test_logistics_officer_adds_an_asset_category(as_user, logistics):
    created = as_user(logistics).post(
        "/api/inventory/categories/",
        {"name": "AGE - Power Support", "code": "AGE-PWR", "kind": "age"},
        format="json",
    )
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


def test_a_category_holding_items_cannot_be_deleted(as_user, logistics, item):
    client = as_user(logistics)
    response = client.delete(f"/api/inventory/categories/{item.category_id}/")
    assert response.status_code == 400
    assert "still holds" in response.json()["detail"]
    assert InventoryCategory.objects.filter(pk=item.category_id).exists()


def test_an_empty_category_can_be_deleted(as_user, logistics):
    category = InventoryCategory.objects.create(name="Unused Class", code="UNU", kind="other")
    assert as_user(logistics).delete(f"/api/inventory/categories/{category.id}/").status_code == 204
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

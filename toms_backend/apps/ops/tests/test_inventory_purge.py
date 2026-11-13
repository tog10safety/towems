"""Purging an inventory item: the seat holder may take its ledger with it."""

from __future__ import annotations

import pytest

from apps.accounts import ownership
from apps.core.factories import make_user
from apps.ops.models import InventoryCategory, InventoryItem, InventoryTransaction

pytestmark = pytest.mark.django_db


def appoint(user):
    return ownership.hand_over(
        user, actor=None, reason="Initial appointment", require_owner=False
    )


def stock(actor):
    category = InventoryCategory.objects.create(
        name="Test materiel", code="TST", kind="materiel"
    )
    item = InventoryItem.objects.create(
        name="Field radio",
        category=category,
        quantity_total=2,
        quantity_available=2,
    )
    InventoryTransaction.objects.create(item=item, kind="receipt", quantity=2, actor=actor)
    return item


def test_another_administrator_cannot_purge_with_the_ledger(as_user, admin):
    appoint(admin)
    other_admin = make_user(authority="Administrator")
    item = stock(admin)

    response = as_user(other_admin).delete(
        f"/api/inventory/items/{item.id}/?purge=true", {}, format="json"
    )

    assert response.status_code == 403
    assert InventoryItem.objects.filter(pk=item.id).exists()


def test_the_seat_holder_purges_the_item_and_its_movements(as_user, admin):
    appoint(admin)
    item = stock(admin)

    response = as_user(admin).delete(
        f"/api/inventory/items/{item.id}/?purge=true", {}, format="json"
    )

    assert response.status_code == 204, response.content
    assert not InventoryItem.objects.filter(pk=item.id).exists()
    assert not InventoryTransaction.objects.filter(item_id=item.id).exists()

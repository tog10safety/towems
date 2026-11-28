"""Armory integration tests.

These tests exercise the real TOWEMS personnel, unit and mission tables through
the same APIs the React screen uses. They never write to a production database:
pytest uses the in-memory SQLite test settings and the legacy table fixture.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.command.models import UnitAppointment
from apps.core import timeutils
from apps.core.factories import make_unit, make_user
from apps.ops.models import Mission, MissionPersonnel

from apps.armory.models import (
    Ammunition,
    ArmoryTransaction,
    Firearm,
    FirearmInspection,
    PersonnelFirearmAssignment,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def armorer(db, unit):
    """A Logistics NCOIC - the post that processes issues and returns."""
    user = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=user, role="logistics_ncoic")
    return user


@pytest.fixture
def soldier(db, unit):
    return make_user(unit=unit, authority="Creator")


@pytest.fixture
def soldier_two(db, unit):
    return make_user(unit=unit, authority="Creator")


@pytest.fixture
def rifle(db, unit):
    return Firearm.objects.create(
        serial_number="RIFLE-0001",
        butt_number="R-001",
        firearm_type=Firearm.TYPE_RIFLE,
        manufacturer="Colt",
        model="M4",
        caliber="5.56mm",
        assigned_unit=unit,
        storage_location="Armory A",
    )


@pytest.fixture
def pistol(db, unit):
    return Firearm.objects.create(
        serial_number="PISTOL-0001",
        butt_number="P-001",
        firearm_type=Firearm.TYPE_PISTOL,
        manufacturer="Glock",
        model="17",
        caliber="9mm",
        assigned_unit=unit,
        storage_location="Armory A",
    )


@pytest.fixture
def ammo(db, unit):
    return Ammunition.objects.create(
        nomenclature="5.56mm Ball",
        caliber="5.56mm",
        ammunition_type="Ball",
        lot_number="LOT-2026-01",
        unit_of_measure="rounds",
        quantity_received=1000,
        stock_balance=1000,
        owning_unit=unit,
        storage_location="Ammo Bay 1",
    )


@pytest.fixture
def mission(db, unit, commander, soldier):
    mission = Mission.objects.create(
        mission_number="MISSION-2026-001",
        category=Mission.CATEGORY_GROUND,
        title="Armory integration mission",
        unit=unit,
        created_by=commander,
        status="approved",
        approval_status=Mission.APPROVAL_APPROVED,
        approved_revision=1,
        revision=1,
        approved_by=commander,
        approved_at=timeutils.now(),
        mission_order_status=Mission.MISSION_ORDER_APPROVED,
        mission_order_signed_revision=1,
        mission_order_signed_by=commander,
        mission_order_signed_at=timeutils.now(),
        start_at=timeutils.now(),
        end_at=timeutils.now() + dt.timedelta(hours=8),
        location_name="Test area",
    )
    MissionPersonnel.objects.create(mission=mission, user=soldier, role="Member")
    return mission


def assign(client, personnel, rifle=None, pistol=None):
    return client.post(
        "/api/armory/assignments/assign/",
        {
            "personnel": personnel.id,
            "rifle_firearm_id": rifle.firearm_id if rifle else "",
            "pistol_firearm_id": pistol.firearm_id if pistol else "",
            "notes": "Integration test designation",
        },
        format="json",
    )


def issue_url():
    return "/api/armory/issuances/"


def issue_payload(mission, personnel, firearms, ammo=None, **overrides):
    body = {
        "mission": mission.id if mission is not None else None,
        "purpose": "Standalone accountability test" if mission is None else "",
        "personnel": personnel.id,
        "firearms": [
            {
                "firearm_id": firearm.firearm_id,
                "butt_number": firearm.butt_number,
                "inspection_status": "serviceable",
                "inspection_remarks": "Inspected at issue",
            }
            for firearm in firearms
        ],
        "ammunition": ammo or [],
        "identity_verified": True,
        "acknowledged": True,
        "acknowledgement_name": personnel.get_full_name(),
        "inspection_remarks": "Physical inspection completed.",
        "supporting_documents": "Mission Order on file",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Registration and identifier rules
# ---------------------------------------------------------------------------


def test_firearm_id_is_generated_and_not_client_editable(as_user, logistics, unit):
    client = as_user(logistics)
    response = client.post(
        "/api/armory/firearms/",
        {
            "serial_number": " afp9028394892 ",
            "butt_number": "r-900",
            "firearm_type": "rifle",
            "manufacturer": "Norinco",
            "model": "Type 56",
            "caliber": "7.62mm",
            "assigned_unit": unit.id,
            "storage_location": "Armory B",
            "firearm_id": "PAF-SHOULD-NOT-BE-USED",
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["firearm_id"] == "PAF-AFP9028394892"
    assert body["butt_number"] == "R-900"
    firearm = Firearm.objects.get(pk=body["id"])
    assert firearm.firearm_id == "PAF-AFP9028394892"


def test_duplicate_serial_is_rejected_case_insensitively(as_user, logistics, rifle, unit):
    response = as_user(logistics).post(
        "/api/armory/firearms/",
        {
            "serial_number": rifle.serial_number.lower(),
            "butt_number": "R-002",
            "firearm_type": "rifle",
            "assigned_unit": unit.id,
        },
        format="json",
    )
    assert response.status_code == 400
    assert not Firearm.objects.filter(butt_number="R-002").exists()


def test_butt_number_unique_within_type_but_reusable_across_types(
    as_user, logistics, rifle, unit
):
    client = as_user(logistics)
    duplicate = client.post(
        "/api/armory/firearms/",
        {
            "serial_number": "RIFLE-0002",
            "butt_number": rifle.butt_number,
            "firearm_type": "rifle",
            "assigned_unit": unit.id,
        },
        format="json",
    )
    assert duplicate.status_code == 400
    assert "butt number" in duplicate.json()["detail"].lower()

    other_type = client.post(
        "/api/armory/firearms/",
        {
            "serial_number": "PISTOL-0002",
            "butt_number": rifle.butt_number,
            "firearm_type": "pistol",
            "assigned_unit": unit.id,
        },
        format="json",
    )
    assert other_type.status_code == 201, other_type.content


def test_serial_correction_is_controlled_and_audited(as_user, logistics, rifle):
    client = as_user(logistics)
    plain_edit = client.patch(
        f"/api/armory/firearms/{rifle.id}/",
        {"serial_number": "RIFLE-CORRECTED"},
        format="json",
    )
    assert plain_edit.status_code == 400
    assert "serial-correction" in plain_edit.json()["detail"]

    corrected = client.post(
        f"/api/armory/firearms/{rifle.id}/correct-serial/",
        {"serial_number": "RIFLE-CORRECTED", "reason": "Inventory audit correction"},
        format="json",
    )
    assert corrected.status_code == 200, corrected.content
    rifle.refresh_from_db()
    assert rifle.firearm_id == "PAF-RIFLE-CORRECTED"
    assert rifle.serial_changes.count() == 1
    assert rifle.serial_changes.first().old_firearm_id == "PAF-RIFLE-0001"


def test_personnel_can_only_read_their_own_armory(as_user, creator, rifle):
    client = as_user(creator)
    assert client.get("/api/armory/firearms/").status_code == 403
    own = client.get("/api/armory/my/")
    assert own.status_code == 200
    assert own.json()["assignments"] == []


def test_personnel_lookup_searches_beyond_the_first_page(as_user, logistics, unit):
    """Search must reach everyone in scope, not just the first 100 rows."""
    User.objects.bulk_create(
        [
            User(
                username=f"filler.{index}",
                password_hash="x",
                authority="Creator",
                category="Officer",
                rank="A1C",
                first_name=f"Filler{index:03d}",
                last_name=f"Aaa{index:03d}",
                serial_number=f"FILLER-{index:05d}",
                branch_of_service="PAF",
                unit=unit,
                unit_legacy=unit.name,
                is_active=True,
            )
            for index in range(105)
        ]
    )
    target_unit = make_unit(name="Search Target Unit", acronym="TGTUNIT", parent=unit)
    target = make_user(
        unit=target_unit,
        rank="MSg",
        first_name="Zebedee",
        last_name="Zulu",
        serial_number="SEARCH-9999",
    )
    client = as_user(logistics)

    first_page = client.get("/api/armory/personnel/").json()
    assert first_page["count"] > 100
    assert len(first_page["results"]) == 100
    assert target.id not in {row["id"] for row in first_page["results"]}

    for query in (
        "Zebedee",
        "Zulu",
        "MSg",
        "SEARCH-9999",
        "TGTUNIT",
        "Zebedee Zulu",
        "TGTUNIT Zulu",
    ):
        body = client.get("/api/armory/personnel/", {"search": query}).json()
        assert target.id in {row["id"] for row in body["results"]}, query

    exact = client.get(
        "/api/armory/personnel/", {"search": "TGTUNIT", "limit": 10}
    ).json()
    assert exact["count"] == 1
    assert exact["results"][0]["id"] == target.id


# ---------------------------------------------------------------------------
# Ammunition inventory
# ---------------------------------------------------------------------------


def test_ammunition_id_and_initial_balance_are_generated(as_user, logistics, unit):
    response = as_user(logistics).post(
        "/api/armory/ammunition/",
        {
            "nomenclature": "9mm Ball",
            "caliber": "9mm",
            "ammunition_type": "Ball",
            "lot_number": "LOT-9-1",
            "unit_of_measure": "rounds",
            "quantity_received": 250,
            "owning_unit": unit.id,
            "storage_location": "Ammo Bay 2",
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["ammunition_id"].startswith("AMM-")
    assert body["stock_balance"] == 250
    assert body["quantity_issued"] == 0


def test_ammunition_adjustment_is_atomic_and_audited(as_user, logistics, ammo):
    client = as_user(logistics)
    response = client.post(
        f"/api/armory/ammunition/{ammo.id}/adjust/",
        {"stock_balance": 875, "reason": "Physical count correction"},
        format="json",
    )
    assert response.status_code == 201, response.content
    ammo.refresh_from_db()
    assert ammo.stock_balance == 875
    assert ammo.quantity_received == 1000
    assert ammo.quantity_issued == 0
    txn = ArmoryTransaction.objects.get(pk=response.json()["transaction"]["id"])
    assert txn.transaction_type == ArmoryTransaction.TYPE_ADJUSTMENT
    assert txn.discrepancy_notes == "Physical count correction"


def test_safety_officer_reads_but_cannot_change_armory(as_user, safety, rifle):
    client = as_user(safety)
    assert client.get("/api/armory/firearms/").status_code == 200
    assert client.get("/api/armory/ammunition/").status_code == 200
    assert client.get("/api/armory/transactions/").status_code == 200
    refused = client.patch(
        f"/api/armory/firearms/{rifle.id}/", {"remarks": "changed"}, format="json"
    )
    assert refused.status_code == 403


# ---------------------------------------------------------------------------
# Designated rifle and pistol
# ---------------------------------------------------------------------------


def test_assignment_creates_one_rifle_and_one_pistol(as_user, armorer, soldier, rifle, pistol):
    client = as_user(armorer)
    response = assign(client, soldier, rifle=rifle, pistol=pistol)
    assert response.status_code == 201, response.content
    assert {row["assignment_type"] for row in response.json()} == {"rifle", "pistol"}
    rifle.refresh_from_db()
    pistol.refresh_from_db()
    assert rifle.assigned_personnel_id == soldier.id
    assert pistol.assigned_personnel_id == soldier.id
    assert PersonnelFirearmAssignment.objects.filter(user=soldier, is_active=True).count() == 2


def test_one_firearm_cannot_be_actively_assigned_to_two_people(
    as_user, armorer, soldier, soldier_two, rifle
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle).status_code == 201
    response = assign(client, soldier_two, rifle=rifle)
    assert response.status_code == 400
    assert PersonnelFirearmAssignment.objects.filter(firearm=rifle, is_active=True).count() == 1


def test_reassignment_closes_history_instead_of_deleting_it(
    as_user, armorer, soldier, rifle, unit
):
    replacement = Firearm.objects.create(
        serial_number="RIFLE-0003",
        butt_number="R-003",
        firearm_type=Firearm.TYPE_RIFLE,
        assigned_unit=unit,
    )
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle).status_code == 201
    response = assign(client, soldier, rifle=replacement)
    assert response.status_code == 201, response.content
    rows = PersonnelFirearmAssignment.objects.filter(
        user=soldier, assignment_type=Firearm.TYPE_RIFLE
    ).order_by("id")
    assert rows.count() == 2
    assert rows[0].is_active is False
    assert rows[1].firearm_id == replacement.id
    assert rows[0].released_at is not None


# ---------------------------------------------------------------------------
# Mission-based issue and return
# ---------------------------------------------------------------------------


def test_issue_requires_an_approved_signed_mission_order(
    as_user, armorer, mission, soldier, rifle, pistol
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    mission.mission_order_status = Mission.MISSION_ORDER_DRAFT
    mission.save(update_fields=["mission_order_status"])
    response = client.post(
        issue_url(),
        issue_payload(mission, soldier, [rifle, pistol]),
        format="json",
    )
    assert response.status_code == 400
    assert "Mission Order" in response.json()["detail"]


def test_linked_mission_requires_personnel_in_the_mission_order(
    as_user, armorer, mission, soldier_two, rifle, pistol
):
    client = as_user(armorer)
    # Designate the person, but do not add them to MissionPersonnel.
    assert assign(client, soldier_two, rifle=rifle, pistol=pistol).status_code == 201
    response = client.post(
        issue_url(),
        issue_payload(mission, soldier_two, [rifle, pistol]),
        format="json",
    )
    assert response.status_code == 400
    assert "not included" in response.json()["detail"]


def test_issue_requires_both_firearm_id_and_butt_number(
    as_user, armorer, mission, soldier, rifle, pistol
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    payload = issue_payload(mission, soldier, [rifle, pistol])
    payload["firearms"][0]["butt_number"] = "WRONG-BUTT"
    response = client.post(issue_url(), payload, format="json")
    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]
    rifle.refresh_from_db()
    assert rifle.custody_status == Firearm.CUSTODY_STORED


def test_successful_issue_updates_custody_and_ammunition_stock(
    as_user, armorer, mission, soldier, rifle, pistol, ammo
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    response = client.post(
        issue_url(),
        issue_payload(
            mission,
            soldier,
            [rifle, pistol],
            ammo=[{"ammunition": ammo.id, "quantity": 120}],
        ),
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["transaction_type"] == "issuance"
    assert body["status"] == "open"
    assert body["mission_order_reference"] == mission.mission_number
    assert len(body["lines"]) == 3
    rifle.refresh_from_db()
    pistol.refresh_from_db()
    ammo.refresh_from_db()
    assert rifle.custody_status == Firearm.CUSTODY_ISSUED
    assert pistol.custody_status == Firearm.CUSTODY_ISSUED
    assert ammo.quantity_issued == 120
    assert ammo.stock_balance == 880
    assert ArmoryTransaction.objects.filter(
        mission=mission,
        personnel=soldier,
        transaction_type=ArmoryTransaction.TYPE_ISSUANCE,
        status=ArmoryTransaction.STATUS_OPEN,
    ).count() == 1
    assert FirearmInspection.objects.filter(firearm__in=[rifle, pistol]).count() == 2


def test_regular_issue_requires_a_purpose(
    as_user, armorer, soldier, rifle, pistol
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    payload = issue_payload(None, soldier, [rifle, pistol])
    payload["purpose"] = ""
    refused = client.post(issue_url(), payload, format="json")
    assert refused.status_code == 400
    assert "purpose" in refused.json()["detail"].lower()

    payload["purpose"] = "Range qualification"
    issued = client.post(issue_url(), payload, format="json")
    assert issued.status_code == 201, issued.content
    assert issued.json()["purpose"] == "Range qualification"


def test_sync_uuid_makes_issue_retry_idempotent(
    as_user, armorer, soldier, rifle, pistol
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    client_uuid = str(uuid.uuid4())
    payload = issue_payload(None, soldier, [rifle, pistol])
    payload["client_uuid"] = client_uuid
    first = client.post(issue_url(), payload, format="json")
    assert first.status_code == 201, first.content
    second = client.post(issue_url(), payload, format="json")
    assert second.status_code == 200, second.content
    assert second.json()["id"] == first.json()["id"]
    assert ArmoryTransaction.objects.filter(sync_uuid=client_uuid).count() == 1


def test_assignment_client_uuid_is_idempotent(as_user, armorer, soldier, rifle, pistol):
    client = as_user(armorer)
    client_uuid = str(uuid.uuid4())
    body = {
        "personnel": soldier.id,
        "rifle_firearm_id": rifle.firearm_id,
        "pistol_firearm_id": pistol.firearm_id,
        "client_uuid": client_uuid,
    }
    first = client.post("/api/armory/assignments/assign/", body, format="json")
    assert first.status_code == 201, first.content
    second = client.post("/api/armory/assignments/assign/", body, format="json")
    assert second.status_code == 200, second.content
    assert [row["id"] for row in first.json()] == [row["id"] for row in second.json()]
    assert PersonnelFirearmAssignment.objects.filter(sync_uuid=client_uuid).count() == 2


def test_sync_snapshot_returns_scoped_data(
    as_user, armorer, mission, soldier, rifle, pistol, ammo
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    response = client.get("/api/armory/sync/snapshot/")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["schema"] == 1
    assert soldier.id in {row["id"] for row in body["personnel"]}
    assert rifle.firearm_id in {row["firearm_id"] for row in body["firearms"]}
    assert ammo.ammunition_id in {row["ammunition_id"] for row in body["ammunition"]}
    assert soldier.id in {row["user"] for row in body["assignments"]}
    mission_row = next(row for row in body["missions"] if row["id"] == mission.id)
    assert mission_row["order_ready"] is True
    assert soldier.id in {row["personnel"] for row in mission_row["personnel"]}


def test_standalone_issue_without_mission_order(
    as_user, armorer, soldier, rifle, pistol
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201

    eligibility = client.get("/api/armory/mission-eligibility/")
    assert eligibility.status_code == 200, eligibility.content
    data = eligibility.json()
    assert data["mission"] is None
    assert data["order_approved"] is True
    row = next(
        item for item in data["personnel"] if item["personnel"] == soldier.id
    )
    assert row["ready"] is True
    assert row["mission_role"] == ""

    response = client.post(
        issue_url(),
        issue_payload(None, soldier, [rifle, pistol]),
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["mission"] is None
    assert body["mission_order_reference"] == ""
    txn = ArmoryTransaction.objects.get(pk=body["id"])
    assert txn.mission_id is None
    assert txn.unit_id == soldier.unit_id

    second = client.post(
        issue_url(),
        issue_payload(None, soldier, [rifle, pistol]),
        format="json",
    )
    assert second.status_code == 400
    assert "open armory issue" in second.json()["detail"].lower()


def test_standalone_issue_can_be_returned(
    as_user, armorer, soldier, rifle, pistol
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    issued = client.post(
        issue_url(),
        issue_payload(None, soldier, [rifle, pistol]),
        format="json",
    )
    assert issued.status_code == 201, issued.content
    receipt = client.get(f"/api/armory/receipts/{issued.json()['id']}/")
    assert receipt.status_code == 200
    assert receipt["Content-Type"] == "application/pdf"
    response = client.post(
        "/api/armory/returns/",
        {
            "issuance": issued.json()["id"],
            "firearms": [
                {
                    "firearm_id": row.firearm_id,
                    "butt_number": row.butt_number,
                    "condition_status": "serviceable",
                }
                for row in (rifle, pistol)
            ],
            "identity_verified": True,
            "acknowledged": True,
            "acknowledgement_name": soldier.get_full_name(),
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["transaction_type"] == "return"
    assert body["mission"] is None
    assert body["mission_order_reference"] == ""
    rifle.refresh_from_db()
    pistol.refresh_from_db()
    assert rifle.custody_status == Firearm.CUSTODY_STORED
    assert pistol.custody_status == Firearm.CUSTODY_STORED


def test_standalone_issue_refuses_personnel_outside_scope(
    as_user, armorer, other_unit, rifle, pistol
):
    outsider = make_user(unit=other_unit)
    PersonnelFirearmAssignment.objects.create(
        user=outsider, firearm=rifle, assignment_type=Firearm.TYPE_RIFLE
    )
    PersonnelFirearmAssignment.objects.create(
        user=outsider, firearm=pistol, assignment_type=Firearm.TYPE_PISTOL
    )
    response = as_user(armorer).post(
        issue_url(),
        issue_payload(None, outsider, [rifle, pistol]),
        format="json",
    )
    assert response.status_code == 400
    assert "scope" in response.json()["detail"].lower()


def test_second_issue_while_first_is_open_is_refused(
    as_user, armorer, mission, soldier, rifle, pistol
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    payload = issue_payload(mission, soldier, [rifle, pistol])
    assert client.post(issue_url(), payload, format="json").status_code == 201
    second = client.post(issue_url(), payload, format="json")
    assert second.status_code == 400
    assert "open issue" in second.json()["detail"].lower()


def test_successful_return_links_to_issuance_restores_stock_and_closes_accountability(
    as_user, armorer, mission, soldier, rifle, pistol, ammo
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    issued = client.post(
        issue_url(),
        issue_payload(
            mission,
            soldier,
            [rifle, pistol],
            ammo=[{"ammunition": ammo.id, "quantity": 120}],
        ),
        format="json",
    )
    assert issued.status_code == 201, issued.content
    issuance = ArmoryTransaction.objects.get(pk=issued.json()["id"])
    ammo_line = issuance.lines.get(ammunition=ammo)
    returned = client.post(
        "/api/armory/returns/",
        {
            "issuance": issuance.id,
            "firearms": [
                {
                    "firearm_id": rifle.firearm_id,
                    "butt_number": rifle.butt_number,
                    "condition_status": "serviceable",
                    "inspection_remarks": "Clean and serviceable",
                },
                {
                    "firearm_id": pistol.firearm_id,
                    "butt_number": pistol.butt_number,
                    "condition_status": "serviceable",
                    "inspection_remarks": "Clean and serviceable",
                },
            ],
            "ammunition": [
                {
                    "line": ammo_line.id,
                    "quantity": 100,
                    "serviceability": "serviceable",
                    "discrepancy_note": "20 rounds consumed on mission",
                }
            ],
            "identity_verified": True,
            "acknowledged": True,
            "acknowledgement_name": soldier.get_full_name(),
            "inspection_remarks": "Return inspection complete.",
            "discrepancy_notes": "20 rounds consumed.",
            "supporting_documents": "Range expenditure certificate",
        },
        format="json",
    )
    assert returned.status_code == 201, returned.content
    body = returned.json()
    assert body["transaction_type"] == "return"
    assert body["return_of"] == issuance.id
    assert "20 rounds" in body["discrepancy_notes"]
    rifle.refresh_from_db()
    pistol.refresh_from_db()
    ammo.refresh_from_db()
    issuance.refresh_from_db()
    assert rifle.custody_status == Firearm.CUSTODY_STORED
    assert pistol.custody_status == Firearm.CUSTODY_STORED
    assert ammo.quantity_issued == 0
    assert ammo.stock_balance == 980
    assert issuance.status == ArmoryTransaction.STATUS_CLOSED
    assert issuance.returned_at is not None
    dashboard = client.get("/api/armory/dashboard/")
    assert dashboard.status_code == 200
    assert dashboard.json()["pending_returns"] == 0


def test_return_requires_every_original_firearm(
    as_user, armorer, mission, soldier, rifle, pistol
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    issued = client.post(
        issue_url(),
        issue_payload(mission, soldier, [rifle, pistol]),
        format="json",
    )
    assert issued.status_code == 201
    response = client.post(
        "/api/armory/returns/",
        {
            "issuance": issued.json()["id"],
            "firearms": [
                {
                    "firearm_id": rifle.firearm_id,
                    "butt_number": rifle.butt_number,
                    "condition_status": "serviceable",
                }
            ],
            "identity_verified": True,
            "acknowledged": True,
            "acknowledgement_name": soldier.get_full_name(),
        },
        format="json",
    )
    assert response.status_code == 400
    assert "All firearms" in response.json()["detail"]
    issuance = ArmoryTransaction.objects.get(pk=issued.json()["id"])
    assert issuance.status == ArmoryTransaction.STATUS_OPEN


def test_safety_officer_cannot_issue_or_return(
    as_user, safety, mission, soldier, rifle, pistol
):
    assert as_user(safety).post(
        issue_url(),
        issue_payload(mission, soldier, [rifle, pistol]),
        format="json",
    ).status_code == 403


# ---------------------------------------------------------------------------
# Dashboard, receipts and reports
# ---------------------------------------------------------------------------


def test_dashboard_counts_and_recent_transactions(
    as_user, armorer, mission, soldier, rifle, pistol, ammo
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    assert client.post(
        issue_url(),
        issue_payload(
            mission,
            soldier,
            [rifle, pistol],
            ammo=[{"ammunition": ammo.id, "quantity": 50}],
        ),
        format="json",
    ).status_code == 201
    response = client.get("/api/armory/dashboard/")
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["total_firearms"] == 2
    assert data["total_rifles"] == 1
    assert data["total_pistols"] == 1
    assert data["issued_firearms"] == 2
    assert data["pending_returns"] == 1
    assert data["total_ammunition_stock"] == 950
    assert data["recent_transactions"][0]["transaction_type"] == "issuance"


def test_receipt_is_available_to_the_person_but_not_to_an_unrelated_creator(
    as_user, armorer, mission, soldier, soldier_two, rifle, pistol
):
    client = as_user(armorer)
    assert assign(client, soldier, rifle=rifle, pistol=pistol).status_code == 201
    issued = client.post(
        issue_url(),
        issue_payload(mission, soldier, [rifle, pistol]),
        format="json",
    )
    assert issued.status_code == 201
    txn_id = issued.json()["id"]
    own = as_user(soldier).get(f"/api/armory/receipts/{txn_id}/")
    assert own.status_code == 200
    assert own["Content-Type"] == "application/pdf"
    other = as_user(soldier_two).get(f"/api/armory/receipts/{txn_id}/")
    assert other.status_code == 404


def test_pdf_and_excel_reports_have_firearm_identity(
    as_user, logistics, rifle, ammo
):
    client = as_user(logistics)
    pdf = client.get("/api/armory/reports/firearms/?export=pdf")
    assert pdf.status_code == 200
    assert pdf["Content-Type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    xlsx = client.get("/api/armory/reports/firearms/?export=xlsx")
    assert xlsx.status_code == 200
    assert xlsx["Content-Type"].startswith(XLSX_MIME := "application/vnd.openxmlformats")
    ammo_report = client.get("/api/armory/reports/ammunition/?export=xlsx")
    assert ammo_report.status_code == 200
    assert client.get("/api/armory/reports/not-a-report/").status_code == 404


def test_creator_cannot_download_register_reports(as_user, creator):
    assert as_user(creator).get("/api/armory/reports/firearms/").status_code == 403


@pytest.mark.parametrize(
    "report_slug",
    [
        "firearms",
        "ammunition",
        "assignments",
        "issuances",
        "returns",
        "mission-accountability",
        "ammunition-reconciliation",
        "inspections",
        "monthly-summary",
    ],
)
@pytest.mark.parametrize("export", ["pdf", "xlsx"])
def test_every_armory_report_renders(as_user, logistics, report_slug, export):
    response = as_user(logistics).get(
        f"/api/armory/reports/{report_slug}/?export={export}"
    )
    assert response.status_code == 200, response.content
    if export == "pdf":
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")
    else:
        assert response["Content-Type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

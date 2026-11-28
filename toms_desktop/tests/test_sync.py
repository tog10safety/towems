from __future__ import annotations

from toms_armory import db, services
from toms_armory.api_client import ApiError
from toms_armory.models import (
    Ammunition,
    Assignment,
    Firearm,
    LocalUser,
    SyncQueue,
    Transaction,
)
from toms_armory.sync import SyncService


class FakeClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.username = ""
        self.next_firearm = 200
        self.next_ammo = 300
        self.next_assignment = 400
        self.next_transaction = 500
        self.next_line = 700
        self.firearms: dict[int, dict] = {}
        self.ammunition: dict[int, dict] = {}
        self.assignments_store: list[dict] = []
        self.transactions: dict[int, dict] = {}
        self.ammunition_lines: dict[int, int] = {}
        self.fail_create = False

    def login(self, username: str, password: str):
        self.username = username

    def restore_session(self, username: str) -> bool:
        self.username = username
        return True

    def snapshot(self) -> dict:
        return {
            "schema": 1,
            "units": [
                {"id": 10, "name": "Test Wing", "acronym": "TW", "parent_id": None}
            ],
            "personnel": [
                {
                    "id": 20,
                    "username": "soldier1",
                    "full_name": "SSg Test Soldier",
                    "last_name": "Soldier",
                    "rank": "SSg",
                    "serial_number": "900001",
                    "branch_of_service": "PAF",
                    "unit_id": 10,
                    "contact_number": "",
                    "is_active": True,
                }
            ],
            "firearms": list(self.firearms.values()),
            "ammunition": list(self.ammunition.values()),
            "assignments": list(self.assignments_store),
            "missions": [
                {
                    "id": 30,
                    "mission_number": "MO-1",
                    "title": "Range",
                    "unit": 10,
                    "order_ready": True,
                    "personnel": [
                        {"personnel": 20, "role": "Rifleman", "custom_role": ""}
                    ],
                    "start_at": None,
                    "end_at": None,
                    "status": "approved",
                    "approval_status": "approved",
                    "mission_order_status": "signed",
                }
            ],
        }

    def create_firearm(self, payload: dict) -> dict:
        if self.fail_create:
            raise ApiError("Duplicate serial in central register.", status=400)
        remote_id = self.next_firearm
        self.next_firearm += 1
        data = dict(payload)
        data.update(
            {
                "id": remote_id,
                "firearm_id": f"PAF-{payload['serial_number']}",
                "assigned_personnel": None,
                "custody_status": "stored",
                "condition_status": payload.get("condition_status", "serviceable"),
            }
        )
        self.firearms[remote_id] = data
        return data

    def create_ammunition(self, payload: dict) -> dict:
        remote_id = self.next_ammo
        self.next_ammo += 1
        data = dict(payload)
        data.update(
            {
                "id": remote_id,
                "ammunition_id": f"AMM-{remote_id}",
                "quantity_issued": 0,
                "stock_balance": payload["quantity_received"],
            }
        )
        self.ammunition[remote_id] = data
        return data

    def assignments(self, personnel_remote_id: int, active: str = "all") -> list[dict]:
        output = [
            row
            for row in self.assignments_store
            if row["user"] == personnel_remote_id
            and (active not in {"1"} or row["is_active"])
        ]
        return output

    def assign(
        self,
        *,
        personnel: int,
        client_uuid: str,
        rifle_firearm_id: str = "",
        pistol_firearm_id: str = "",
        notes: str = "",
    ) -> list[dict]:
        output = []
        for kind, identifier in (
            ("rifle", rifle_firearm_id),
            ("pistol", pistol_firearm_id),
        ):
            if not identifier:
                continue
            weapon = next(
                (
                    item
                    for item in self.firearms.values()
                    if item["firearm_id"] == identifier
                ),
                None,
            )
            if weapon is None:
                raise ApiError(f"No firearm is registered as {identifier}.", status=400)
            current = next(
                (
                    item
                    for item in self.assignments_store
                    if item["user"] == personnel
                    and item["assignment_type"] == kind
                    and item["is_active"]
                ),
                None,
            )
            if current is not None and current["firearm"]["firearm_id"] == identifier:
                output.append(current)
                continue
            if current is not None:
                current["is_active"] = False
            remote_id = self.next_assignment
            self.next_assignment += 1
            row = {
                "id": remote_id,
                "user": personnel,
                "assignment_type": kind,
                "is_active": True,
                "assigned_at": "2026-11-28T12:00:00Z",
                "notes": notes,
                "firearm": {
                    "id": weapon["id"],
                    "firearm_id": weapon["firearm_id"],
                    "butt_number": weapon["butt_number"],
                    "serial_number": weapon["serial_number"],
                },
            }
            self.assignments_store.append(row)
            weapon["assigned_personnel"] = personnel
            output.append(row)
        return output

    def release_assignment(self, remote_id: int, reason: str = "") -> dict:
        for row in self.assignments_store:
            if row["id"] == remote_id:
                row["is_active"] = False
        return {}

    def issue(self, payload: dict) -> dict:
        for line in payload["firearms"]:
            if not any(
                row["user"] == payload["personnel"]
                and row["is_active"]
                and row["firearm"]["firearm_id"] == line["firearm_id"]
                for row in self.assignments_store
            ):
                raise ApiError(
                    f"{line['firearm_id']} is not designated to this person.", status=400
                )
        remote_id = self.next_transaction
        self.next_transaction += 1
        lines = []
        for line in payload["firearms"]:
            lines.append(
                {
                    "id": self.next_line,
                    "firearm_id_snapshot": line["firearm_id"],
                    "butt_number_snapshot": line["butt_number"],
                    "quantity": 0,
                }
            )
            self.next_line += 1
        for line in payload.get("ammunition", []):
            lot = self.ammunition[line["ammunition"]]
            lot["quantity_issued"] += line["quantity"]
            lot["stock_balance"] -= line["quantity"]
            remote_line_id = self.next_line
            self.next_line += 1
            self.ammunition_lines[remote_line_id] = line["ammunition"]
            lines.append(
                {
                    "id": remote_line_id,
                    "firearm_id_snapshot": "",
                    "ammunition": line["ammunition"],
                    "quantity": line["quantity"],
                }
            )
        for line in payload["firearms"]:
            for weapon in self.firearms.values():
                if weapon["firearm_id"] == line["firearm_id"]:
                    weapon["custody_status"] = "issued"
        data = {
            "id": remote_id,
            "reference_number": f"REMOTE-ISS-{remote_id}",
            "lines": lines,
        }
        self.transactions[remote_id] = data
        return data

    def return_firearms(self, payload: dict) -> dict:
        original = self.transactions.get(payload["issuance"])
        if original is None:
            raise ApiError("Original issuance not found.", status=404)
        remote_id = self.next_transaction
        self.next_transaction += 1
        lines = []
        for line in payload["firearms"]:
            lines.append(
                {
                    "id": self.next_line,
                    "firearm_id_snapshot": line["firearm_id"],
                    "butt_number_snapshot": line["butt_number"],
                    "quantity": 0,
                }
            )
            self.next_line += 1
            for weapon in self.firearms.values():
                if weapon["firearm_id"] == line["firearm_id"]:
                    weapon["custody_status"] = "stored"
        for line in payload.get("ammunition", []):
            ammunition_id = self.ammunition_lines.get(line["line"])
            if ammunition_id is None:
                raise ApiError("Original ammunition line not found.", status=400)
            lot = self.ammunition[ammunition_id]
            lot["stock_balance"] += line["quantity"]
            lot["quantity_issued"] -= (
                original
                and next(
                    (
                        item["quantity"]
                        for item in original["lines"]
                        if item.get("id") == line["line"]
                    ),
                    0,
                )
            )
            remote_line_id = self.next_line
            self.next_line += 1
            lines.append(
                {
                    "id": remote_line_id,
                    "firearm_id_snapshot": "",
                    "ammunition": ammunition_id,
                    "quantity": line["quantity"],
                }
            )
        data = {
            "id": remote_id,
            "reference_number": f"REMOTE-RET-{remote_id}",
            "lines": lines,
        }
        self.transactions[remote_id] = data
        return data

    def inspect_firearm(self, remote_id: int, payload: dict) -> dict:
        return {
            "firearm": {"id": remote_id},
            "inspection": {"id": self.next_line},
        }

    def adjust_ammunition(self, remote_id: int, payload: dict) -> dict:
        lot = self.ammunition.get(remote_id)
        if lot is None:
            raise ApiError("Ammunition lot not found.", status=404)
        lot["stock_balance"] = payload["stock_balance"]
        remote_id_txn = self.next_transaction
        self.next_transaction += 1
        return {
            "ammunition": {"id": remote_id},
            "transaction": {
                "id": remote_id_txn,
                "reference_number": f"REMOTE-ADJ-{remote_id_txn}",
            },
        }


def _prepare_running_data(base_data):
    with db.session_scope() as session:
        actor = session.get(LocalUser, base_data["admin_id"])
        services.assign_designated_firearms(
            session,
            actor=actor,
            personnel_id=base_data["person_id"],
            rifle_firearm_id=base_data["rifle_id"],
            pistol_firearm_id=base_data["pistol_id"],
        )
        issue = services.issue_firearms(
            session,
            actor=actor,
            personnel_id=base_data["person_id"],
            mission_id=None,
            purpose="Range qualification",
            firearm_lines=[
                {
                    "firearm_id": base_data["rifle_firearm_id"],
                    "butt_number": base_data["rifle_butt"],
                },
                {
                    "firearm_id": base_data["pistol_firearm_id"],
                    "butt_number": base_data["pistol_butt"],
                },
            ],
            ammunition_lines=[
                {"ammunition_id": base_data["ammo_id"], "quantity": 10}
            ],
            identity_verified=True,
            acknowledged=True,
            acknowledgement_name=base_data["person_name"],
        )
        ammo_line = next(line for line in issue.lines if line.ammunition_id)
        returned = services.return_firearms(
            session,
            actor=actor,
            issuance_id=issue.id,
            firearm_lines=[
                {
                    "firearm_id": base_data["rifle_firearm_id"],
                    "butt_number": base_data["rifle_butt"],
                    "condition_status": "serviceable",
                },
                {
                    "firearm_id": base_data["pistol_firearm_id"],
                    "butt_number": base_data["pistol_butt"],
                    "condition_status": "serviceable",
                },
            ],
            ammunition_lines=[
                {
                    "line_id": ammo_line.id,
                    "quantity": 9,
                    "serviceability": "serviceable",
                }
            ],
            identity_verified=True,
            acknowledged=True,
            acknowledgement_name=base_data["person_name"],
        )
        services.adjust_ammunition(
            session,
            actor=actor,
            ammunition_id=base_data["ammo_id"],
            new_balance=120,
            reason="Count correction",
        )
        return issue.id, returned.id


def test_offline_queue_uploads_and_maps_remote_ids(base_data):
    issue_id, return_id = _prepare_running_data(base_data)
    fake = FakeClient("http://127.0.0.1:8000/api")
    result = SyncService(lambda _base: fake).run(
        username="admin",
        password="test",
        base_url="http://127.0.0.1:8000/api",
    )
    assert result.status == "success", result.errors
    assert result.uploaded >= 7
    with db.session_scope() as session:
        queue = list(session.query(SyncQueue))
        assert queue
        assert all(row.state == "synced" for row in queue), [
            (row.action, row.state, row.last_error) for row in queue
        ]
        issue = session.get(Transaction, issue_id)
        returned = session.get(Transaction, return_id)
        assert issue.remote_id is not None
        assert issue.remote_reference.startswith("REMOTE-ISS")
        assert returned.remote_id is not None
        for line in issue.lines:
            assert line.remote_id is not None
        assignment = (
            session.query(Assignment)
            .filter(Assignment.personnel_id == base_data["person_id"])
            .first()
        )
        assert assignment.remote_id is not None
        assert assignment.remote_id in {row["id"] for row in fake.assignments_store}
        weapon = session.get(Firearm, base_data["rifle_id"])
        assert weapon.remote_id is not None
        lot = session.get(Ammunition, base_data["ammo_id"])
        assert lot.remote_id is not None
        assert lot.stock_balance == 120
        assert (
            session.query(Transaction)
            .filter(Transaction.transaction_type == "adjustment")
            .one()
            .remote_id
            is not None
        )


def test_second_sync_does_not_duplicate(base_data):
    _prepare_running_data(base_data)
    fake = FakeClient("http://127.0.0.1:8000/api")
    service = SyncService(lambda _base: fake)
    first = service.run(
        username="admin", password="test", base_url="http://127.0.0.1:8000/api"
    )
    second = service.run(
        username="admin", password="test", base_url="http://127.0.0.1:8000/api"
    )
    assert first.status == "success"
    assert second.status == "success"
    assert second.uploaded == 0
    with db.session_scope() as session:
        assert session.query(SyncQueue).filter(SyncQueue.state == "pending").count() == 0
        assert session.query(Transaction).count() == 3


def test_permanent_upload_error_marks_queue_failed(base_data):
    fake = FakeClient("http://127.0.0.1:8000/api")
    fake.fail_create = True
    result = SyncService(lambda _base: fake).run(
        username="admin",
        password="test",
        base_url="http://127.0.0.1:8000/api",
    )
    assert result.status == "partial"
    with db.session_scope() as session:
        failed = session.query(SyncQueue).filter(SyncQueue.state == "failed").all()
        assert len(failed) == 2
        assert "Duplicate serial" in failed[0].last_error

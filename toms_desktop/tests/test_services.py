from __future__ import annotations

from datetime import date

import pytest

from toms_armory import db, services
from toms_armory.models import (
    Ammunition,
    Assignment,
    Firearm,
    LocalUser,
    Mission,
    MissionPersonnel,
    Personnel,
    SyncQueue,
    Transaction,
)
from toms_armory.security import hash_password, verify_password


def _actor(session, base):
    return session.get(LocalUser, base["admin_id"])


def _assign(session, base, rifle_id=None, pistol_id=None):
    return services.assign_designated_firearms(
        session,
        actor=_actor(session, base),
        personnel_id=base["person_id"],
        rifle_firearm_id=rifle_id or base["rifle_id"],
        pistol_firearm_id=pistol_id or base["pistol_id"],
    )


def _issue(session, base, *, purpose="Range practice", ammo=10, mission_id=None):
    return services.issue_firearms(
        session,
        actor=_actor(session, base),
        personnel_id=base["person_id"],
        mission_id=mission_id,
        purpose=purpose,
        firearm_lines=[
            {
                "firearm_id": base["rifle_firearm_id"],
                "butt_number": base["rifle_butt"],
            },
            {
                "firearm_id": base["pistol_firearm_id"],
                "butt_number": base["pistol_butt"],
            },
        ],
        ammunition_lines=(
            [{"ammunition_id": base["ammo_id"], "quantity": ammo}] if ammo else []
        ),
        identity_verified=True,
        acknowledged=True,
        acknowledgement_name=base["person_name"],
    )


def test_firearm_identifier_and_butt_rules(base_data):
    with db.session_scope() as session:
        actor = _actor(session, base_data)
        rifle = session.get(Firearm, base_data["rifle_id"])
        assert rifle.firearm_id == "PAF-RIFLE-001"
        assert rifle.butt_number == "R-1"
        with pytest.raises(services.ArmoryError, match="butt number"):
            services.register_firearm(
                session,
                actor=actor,
                serial_number="RIFLE-002",
                butt_number="R-1",
                firearm_type="rifle",
                manufacturer="Colt",
                model="M4",
                caliber="5.56",
                unit_id=base_data["unit_id"],
            )
        # The same butt number is allowed for a different firearm type.
        pistol = services.register_firearm(
            session,
            actor=actor,
            serial_number="PISTOL-002",
            butt_number="R-1",
            firearm_type="pistol",
            manufacturer="Glock",
            model="19",
            caliber="9mm",
            unit_id=base_data["unit_id"],
        )
        assert pistol.firearm_id == "PAF-PISTOL-002"
        with pytest.raises(services.ArmoryError, match="serial number"):
            services.register_firearm(
                session,
                actor=actor,
                serial_number="rifle-001",
                butt_number="R-9",
                firearm_type="rifle",
                manufacturer="Colt",
                model="M4",
                caliber="5.56",
                unit_id=base_data["unit_id"],
            )


def test_assignment_requires_both_and_preserves_history(base_data):
    with db.session_scope() as session:
        _assign(session, base_data)
        active = services.active_assignments(session, base_data["person_id"])
        assert {row.assignment_type for row in active} == {"rifle", "pistol"}
        second_rifle = services.register_firearm(
            session,
            actor=_actor(session, base_data),
            serial_number="RIFLE-002",
            butt_number="R-2",
            firearm_type="rifle",
            manufacturer="Colt",
            model="M4A1",
            caliber="5.56",
            unit_id=base_data["unit_id"],
        )
        _assign(session, base_data, rifle_id=second_rifle.id)
        active = services.active_assignments(session, base_data["person_id"])
        rifle_ids = [row.firearm_id for row in active if row.assignment_type == "rifle"]
        assert rifle_ids == [second_rifle.id]
        history = services.assignment_history(session, base_data["person_id"])
        assert len(history) == 3
        assert sum(1 for row in history if not row.is_active) == 1
        old = next(row for row in history if not row.is_active)
        assert old.firearm_id == base_data["rifle_id"]
        assert old.firearm.assigned_personnel_id is None


def test_cannot_designate_one_person_without_both(base_data):
    with db.session_scope() as session:
        with pytest.raises(services.ArmoryError, match="one designated rifle"):
            services.assign_designated_firearms(
                session,
                actor=_actor(session, base_data),
                personnel_id=base_data["person_id"],
                rifle_firearm_id=base_data["rifle_id"],
                pistol_firearm_id=None,
            )


def test_duplicate_active_firearm_is_refused(base_data):
    with db.session_scope() as session:
        _assign(session, base_data)
        other = services.create_personnel(
            session,
            actor=_actor(session, base_data),
            username="soldier2",
            rank="Cpl",
            first_name="Other",
            middle_name="",
            last_name="Soldier",
            serial_number="900002",
            branch="PAF",
            unit_id=base_data["unit_id"],
        )
        with pytest.raises(services.ArmoryError, match="already designated"):
            services.assign_designated_firearms(
                session,
                actor=_actor(session, base_data),
                personnel_id=other.id,
                rifle_firearm_id=base_data["rifle_id"],
                pistol_firearm_id=None,
            )


def test_regular_issue_requires_purpose_and_identity(base_data):
    with db.session_scope() as session:
        _assign(session, base_data)
        with pytest.raises(services.ArmoryError, match="purpose"):
            _issue(session, base_data, purpose="")
        with pytest.raises(services.ArmoryError, match="acknowledgement"):
            services.issue_firearms(
                session,
                actor=_actor(session, base_data),
                personnel_id=base_data["person_id"],
                mission_id=None,
                purpose="Range",
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
                ammunition_lines=[],
                identity_verified=True,
                acknowledged=True,
                acknowledgement_name="Wrong Name",
            )
        with pytest.raises(services.ArmoryError, match="identity"):
            services.issue_firearms(
                session,
                actor=_actor(session, base_data),
                personnel_id=base_data["person_id"],
                mission_id=None,
                purpose="Range",
                firearm_lines=[],
                ammunition_lines=[],
                identity_verified=False,
                acknowledged=True,
                acknowledgement_name=base_data["person_name"],
            )
        transaction = _issue(session, base_data)
        assert transaction.status == "open"
        assert transaction.remote_id is None
        rifle = session.get(Firearm, base_data["rifle_id"])
        assert rifle.custody_status == "issued"
        lot = session.get(Ammunition, base_data["ammo_id"])
        assert lot.stock_balance == 90
        assert lot.quantity_issued == 10
        with pytest.raises(services.ArmoryError, match="open issue"):
            _issue(session, base_data)


def test_mission_issue_does_not_require_purpose_and_uses_roster(base_data):
    with db.session_scope() as session:
        _assign(session, base_data)
        mission = Mission(
            mission_number="MO-2026-001",
            title="Range qualification",
            unit_id=base_data["unit_id"],
            order_ready=True,
        )
        session.add(mission)
        session.flush()
        session.add(
            MissionPersonnel(
                mission_id=mission.id,
                personnel_id=base_data["person_id"],
                role="Rifleman",
            )
        )
        session.flush()
        transaction = _issue(session, base_data, purpose="", mission_id=mission.id)
        assert transaction.mission_order_reference == "MO-2026-001"
        assert transaction.purpose == ""
        # A person outside the roster is refused.
        outsider = services.create_personnel(
            session,
            actor=_actor(session, base_data),
            username="outsider",
            rank="Cpl",
            first_name="Out",
            middle_name="",
            last_name="Sider",
            serial_number="900003",
            branch="PAF",
            unit_id=base_data["unit_id"],
        )
        with pytest.raises(services.ArmoryError, match="not included"):
            services.issue_firearms(
                session,
                actor=_actor(session, base_data),
                personnel_id=outsider.id,
                mission_id=mission.id,
                purpose="",
                firearm_lines=[
                    {
                        "firearm_id": base_data["rifle_firearm_id"],
                        "butt_number": base_data["rifle_butt"],
                    }
                ],
                ammunition_lines=[],
                identity_verified=True,
                acknowledged=True,
                acknowledgement_name="Out Sider",
            )


def test_return_reconciles_ammunition_and_discrepancies(base_data):
    with db.session_scope() as session:
        _assign(session, base_data)
        issuance = _issue(session, base_data, ammo=10)
        ammo_line = next(line for line in issuance.lines if line.ammunition_id)
        result = services.return_firearms(
            session,
            actor=_actor(session, base_data),
            issuance_id=issuance.id,
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
                    "quantity": 8,
                    "serviceability": "serviceable",
                    "discrepancy_note": "Two rounds consumed on range.",
                }
            ],
            identity_verified=True,
            acknowledged=True,
            acknowledgement_name=base_data["person_name"],
        )
        assert result.status == "closed"
        assert issuance.status == "closed"
        lot = session.get(Ammunition, base_data["ammo_id"])
        assert lot.stock_balance == 98
        assert lot.quantity_issued == 0
        assert "not returned 2" in result.discrepancy_notes
        assert session.get(Firearm, base_data["rifle_id"]).custody_status == "stored"
        with pytest.raises(services.ArmoryError, match="already closed"):
            services.return_firearms(
                session,
                actor=_actor(session, base_data),
                issuance_id=issuance.id,
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
                ammunition_lines=[],
                identity_verified=True,
                acknowledged=True,
                acknowledgement_name=base_data["person_name"],
            )


def test_stock_adjustment_is_a_transaction_and_queue_entry(base_data):
    with db.session_scope() as session:
        transaction = services.adjust_ammunition(
            session,
            actor=_actor(session, base_data),
            ammunition_id=base_data["ammo_id"],
            new_balance=140,
            reason="Physical count correction",
        )
        lot = session.get(Ammunition, base_data["ammo_id"])
        assert lot.stock_balance == 140
        assert lot.quantity_received == 140
        assert transaction.transaction_type == "adjustment"
        queued = session.query(SyncQueue).filter(SyncQueue.action == "ammunition.adjust").one()
        assert queued.state == "pending"
        assert queued.entity_uuid == transaction.uuid


def test_password_hashing_is_salted_and_verifiable():
    first = hash_password("StrongPass123")
    second = hash_password("StrongPass123")
    assert first != second
    assert verify_password("StrongPass123", first)
    assert not verify_password("wrong", first)
    assert first.startswith("pbkdf2_sha256$600000$")


def test_downloaded_personnel_identity_is_read_only(base_data):
    with db.session_scope() as session:
        person = session.get(Personnel, base_data["person_id"])
        person.remote_id = 7001
        with pytest.raises(services.ArmoryError, match="maintained in TOWEMS"):
            services.update_personnel(
                session,
                actor=_actor(session, base_data),
                personnel_id=person.id,
                rank=person.rank,
                first_name="Changed",
                middle_name=person.middle_name,
                last_name=person.last_name,
                branch=person.branch_of_service,
                unit_id=person.unit_id,
                contact=person.contact_number,
                is_authorized=person.is_authorized,
                is_active=person.is_active,
            )
        updated = services.update_personnel(
            session,
            actor=_actor(session, base_data),
            personnel_id=person.id,
            rank=person.rank,
            first_name=person.first_name,
            middle_name=person.middle_name,
            last_name=person.last_name,
            branch=person.branch_of_service,
            unit_id=person.unit_id,
            contact=person.contact_number,
            is_authorized=False,
            is_active=True,
        )
        assert updated.is_authorized is False

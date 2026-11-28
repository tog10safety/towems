from __future__ import annotations

import pytest

from toms_armory import db, services


@pytest.fixture()
def database(tmp_path):
    db.dispose_engine()
    db.init_db(tmp_path / "armory.db")
    yield
    db.dispose_engine()


@pytest.fixture()
def base_data(database):
    with db.session_scope() as session:
        admin = services.create_initial_admin(
            session,
            username="admin",
            display_name="Test Administrator",
            password="StrongPass123",
            unit_name="Test Wing",
            unit_acronym="TW",
        )
        unit = services.units(session)[0]
        rifle = services.register_firearm(
            session,
            actor=admin,
            serial_number="RIFLE-001",
            butt_number="R-1",
            firearm_type="rifle",
            manufacturer="Colt",
            model="M4",
            caliber="5.56",
            unit_id=unit.id,
        )
        pistol = services.register_firearm(
            session,
            actor=admin,
            serial_number="PISTOL-001",
            butt_number="P-1",
            firearm_type="pistol",
            manufacturer="Glock",
            model="17",
            caliber="9mm",
            unit_id=unit.id,
        )
        person = services.create_personnel(
            session,
            actor=admin,
            username="soldier1",
            rank="SSg",
            first_name="Test",
            middle_name="",
            last_name="Soldier",
            serial_number="900001",
            branch="PAF",
            unit_id=unit.id,
        )
        lot = services.register_ammunition(
            session,
            actor=admin,
            nomenclature="5.56 mm Ball",
            caliber="5.56",
            ammunition_type="Ball",
            lot_number="LOT-1",
            unit_of_measure="rounds",
            quantity_received=100,
            unit_id=unit.id,
        )
        return {
            "admin_id": admin.id,
            "unit_id": unit.id,
            "rifle_id": rifle.id,
            "rifle_firearm_id": rifle.firearm_id,
            "rifle_butt": rifle.butt_number,
            "rifle_serial": rifle.serial_number,
            "pistol_id": pistol.id,
            "pistol_firearm_id": pistol.firearm_id,
            "pistol_butt": pistol.butt_number,
            "pistol_serial": pistol.serial_number,
            "person_id": person.id,
            "person_name": person.full_name,
            "ammo_id": lot.id,
            "ammo_identifier": lot.ammunition_id,
        }

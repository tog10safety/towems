from __future__ import annotations

from openpyxl import load_workbook

from toms_armory import db, reports, services
from toms_armory.api_client import ApiError, validate_base_url
from toms_armory.models import LocalUser
from toms_armory.security import hash_password, verify_password


def test_receipt_and_excel_are_real_files(base_data, tmp_path):
    with db.session_scope() as session:
        actor = session.get(LocalUser, base_data["admin_id"])
        services.assign_designated_firearms(
            session,
            actor=actor,
            personnel_id=base_data["person_id"],
            rifle_firearm_id=base_data["rifle_id"],
            pistol_firearm_id=base_data["pistol_id"],
        )
        transaction = services.issue_firearms(
            session,
            actor=actor,
            personnel_id=base_data["person_id"],
            mission_id=None,
            purpose="Receipt test",
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
                {"ammunition_id": base_data["ammo_id"], "quantity": 5}
            ],
            identity_verified=True,
            acknowledged=True,
            acknowledgement_name=base_data["person_name"],
        )
        receipt = reports.write_receipt(transaction, tmp_path / "receipt.pdf")
        assert receipt.read_bytes().startswith(b"%PDF")
        workbook = reports.write_excel(
            tmp_path / "inventory.xlsx",
            {
                "Firearms": (
                    reports.FIREARM_REPORT_HEADERS,
                    reports.firearm_export_rows(
                        services.firearm_rows(session)
                    ),
                )
            },
        )
        loaded = load_workbook(workbook)
        assert "Firearms" in loaded.sheetnames
        assert loaded["Firearms"].max_row == 3
        report = reports.write_report(
            title="Inventory",
            subtitle="Test",
            headers=reports.FIREARM_REPORT_HEADERS,
            rows=reports.firearm_export_rows(services.firearm_rows(session)),
            path=tmp_path / "report.pdf",
            generated_by="Tester",
        )
        assert report.read_bytes().startswith(b"%PDF")


def test_https_policy_and_password_policy():
    assert validate_base_url("https://towems.example/api") == "https://towems.example/api"
    assert validate_base_url("http://127.0.0.1:8000/api") == "http://127.0.0.1:8000/api"
    try:
        validate_base_url("http://towems.example/api")
    except ApiError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("remote http was accepted")
    encoded = hash_password("AnotherPass123")
    assert verify_password("AnotherPass123", encoded)
    assert not verify_password("wrong", encoded)


def test_database_backup_is_readable(base_data, tmp_path):
    destination = tmp_path / "backup.db"
    db.backup_database(destination)
    assert destination.exists()
    assert destination.stat().st_size > 0
    import sqlite3

    with sqlite3.connect(destination) as connection:
        count = connection.execute("select count(*) from firearm").fetchone()[0]
    assert count == 2

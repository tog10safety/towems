"""MRM PDF: viewing, download, stored approved copies and access control."""

from __future__ import annotations

import uuid

import pytest

from apps.core.factories import make_unit, make_user
from apps.legacy.models import MRMForm
from apps.ops import pdf as pdf_service

pytestmark = pytest.mark.django_db

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_creator_can_view_pdf(as_user, creator, mrm_form):
    response = as_user(creator).get(f"/api/risk-register/{mrm_form.id}/pdf/")
    assert response.status_code == 200, response.content
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert f'inline; filename="MRM_{mrm_form.mrm_number}.pdf"' in response["Content-Disposition"]


def test_pdf_download_is_an_attachment(as_user, creator, mrm_form):
    response = as_user(creator).get(f"/api/risk-register/{mrm_form.id}/pdf/?download=1")
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")


def test_pdf_requires_authentication(api, mrm_form):
    # DRF answers 403 here because SessionAuthentication is listed first and
    # supplies no WWW-Authenticate header; JWT endpoints answer 401.
    response = api.get(f"/api/risk-register/{mrm_form.id}/pdf/")
    assert response.status_code == 403


def test_creator_from_another_unit_cannot_open_the_pdf(as_user, other_unit, mrm_form):
    stranger = make_user(unit=other_unit, authority="Creator")
    response = as_user(stranger).get(f"/api/risk-register/{mrm_form.id}/pdf/")
    assert response.status_code == 404  # outside the caller's unit scope


def test_unrelated_creator_is_denied_even_when_record_is_visible(
    as_user, creator, mrm_form
):
    # A creator who can see the record but did not raise it, and for which no
    # authority approval exists, must not receive the document.
    mrm_form.authority_approved = False
    mrm_form.save(update_fields=["authority_approved"])

    # ``can_view_pdf`` is the gate applied after scoping; exercise it directly.
    other = make_user(unit=creator.unit, authority="Creator")
    assert pdf_service.can_view_pdf(creator, mrm_form) is True
    assert pdf_service.can_view_pdf(other, mrm_form) is False


def test_admin_can_open_any_pdf(as_user, admin, mrm_form):
    response = as_user(admin).get(f"/api/risk-register/{mrm_form.id}/pdf/")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_unit_safety_officer_can_open_a_pdf_in_their_unit(as_user, safety, mrm_form):
    response = as_user(safety).get(f"/api/risk-register/{mrm_form.id}/pdf/")
    assert response.status_code == 200, response.content
    assert response.content.startswith(b"%PDF")


def test_unit_safety_officer_cannot_open_another_units_pdf(
    as_user, safety, other_unit
):
    stranger = make_user(unit=other_unit, authority="Creator")
    foreign = MRMForm.objects.create(
        mrm_number=f"MRM-{uuid.uuid4().hex[:6]}",
        activity_mission="Other unit mission",
        created_by=stranger,
        safety_objectives="Safe operations",
        status="draft",
        review_status="pending",
        original_unit_name=other_unit.name,
        original_unit_id=other_unit.id,
    )
    response = as_user(safety).get(f"/api/risk-register/{foreign.id}/pdf/")
    assert response.status_code == 404  # outside the caller's unit scope


def test_stored_approved_pdf_is_served_verbatim(
    as_user, admin, mrm_form, tmp_path, settings
):
    payload = b"%PDF-1.4 stored copy for test"
    (tmp_path / "MRM_001.pdf").write_bytes(payload)

    settings.LEGACY_PDF_DIR = tmp_path
    mrm_form.approved_pdf_filename = "MRM_001.pdf"
    mrm_form.authority_approved = True
    mrm_form.save(update_fields=["approved_pdf_filename", "authority_approved"])

    response = as_user(admin).get(f"/api/risk-register/{mrm_form.id}/pdf/")
    assert response.status_code == 200
    assert response.content == payload
    assert 'filename="MRM_001.pdf"' in response["Content-Disposition"]


def test_missing_stored_file_falls_back_to_generation(
    as_user, admin, mrm_form, tmp_path, settings
):
    settings.LEGACY_PDF_DIR = tmp_path  # empty directory
    mrm_form.approved_pdf_filename = "ghost.pdf"
    mrm_form.save(update_fields=["approved_pdf_filename"])

    response = as_user(admin).get(f"/api/risk-register/{mrm_form.id}/pdf/")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_stored_pdf_path_rejects_traversal(mrm_form, tmp_path, settings):
    settings.LEGACY_PDF_DIR = tmp_path
    mrm_form.approved_pdf_filename = "../outside.pdf"
    mrm_form.save(update_fields=["approved_pdf_filename"])
    assert pdf_service.stored_pdf_path(mrm_form) is None

    mrm_form.approved_pdf_filename = ""
    assert pdf_service.stored_pdf_path(mrm_form) is None


def test_generated_pdf_contains_the_document_text(as_user, admin, mrm_form, evaluator):
    # Reviewer assigned so the "Reviewed by" block has a name to render.
    mrm_form.reviewed_by = evaluator
    mrm_form.save(update_fields=["reviewed_by"])

    response = as_user(admin).get(f"/api/risk-register/{mrm_form.id}/pdf/")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 2_000  # header, hazard table, authority matrix


def test_generated_pdf_accepts_the_percent_suffixed_risk_band(
    as_user, admin, mrm_form
):
    """65% lands in the "51–65%" band; its label must parse as a number."""
    mrm_form.total_percent = 65
    mrm_form.save(update_fields=["total_percent"])

    response = as_user(admin).get(f"/api/risk-register/{mrm_form.id}/pdf/")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 2_000  # not the small error fallback PDF


def test_the_parent_command_is_printed_once_in_the_header(unit):
    """A unit whose parent is the PAF command must not repeat the root line."""
    root = make_unit(name="Philippine Air Force", acronym="PAF", category="Command")
    wing = make_unit(
        name="Air Education Wing", acronym="AEW", category="Wing", parent=root
    )
    squadron = make_unit(
        name="Delta Squadron", acronym="DELTA", category="Squadron", parent=wing
    )

    header = pdf_service.unit_hierarchy_display(squadron)

    assert header["hierarchy_lines"].count("PHILIPPINE AIR FORCE") == 1
    assert header["hierarchy_lines"] == [
        "PHILIPPINE AIR FORCE",
        "AIR EDUCATION WING",
    ]
    assert header["current_unit"] == "HEADQUARTERS DELTA SQUADRON"


def test_a_wing_header_lists_only_its_commander_above_it():
    """A wing is the current unit, never also one of its parent lines."""
    root = make_unit(name="Philippine Air Force", acronym="PAF", category="Command")
    wing = make_unit(
        name="Tactical Operations Wing Eastern Mindanao",
        acronym="TOWEASTMIN",
        category="Wing",
        location="Davao Air Station, Brgy. Sasa, Davao City",
        parent=root,
    )

    header = pdf_service.unit_hierarchy_display(wing)

    assert header["hierarchy_lines"] == ["PHILIPPINE AIR FORCE"]
    assert header["current_unit"] == (
        "HEADQUARTERS TACTICAL OPERATIONS WING EASTERN MINDANAO"
    )
    assert header["location"] == "DAVAO AIR STATION, BRGY. SASA, DAVAO CITY"


def test_a_wing_seal_is_found_from_an_acronym_named_file(settings, tmp_path):
    """Drop-in seals (``TOWEASTMIN.png``) are used when no table row exists."""
    settings.LEGACY_LOGO_DIR = tmp_path
    settings.LEGACY_UPLOAD_DIR = tmp_path
    wing = make_unit(
        name="Tactical Operations Wing Eastern Mindanao",
        acronym="TOWEASTMIN",
        category="Wing",
    )
    (tmp_path / "TOWEASTMIN.png").write_bytes(PNG_BYTES)

    assert pdf_service._unit_logo_flowable(wing.id, {}) is not None


def test_the_mother_unit_is_the_immediate_parent_not_the_root():
    """The left logo shows the parent unit; the right one the current unit."""
    root = make_unit(name="Philippine Air Force", acronym="PAF", category="Command")
    wing = make_unit(
        name="Air Education Wing", acronym="AEW", category="Wing", parent=root
    )
    squadron = make_unit(
        name="Delta Squadron", acronym="DELTA", category="Squadron", parent=wing
    )

    header = pdf_service.unit_hierarchy_display(squadron)

    assert header["mother_unit_id"] == wing.id
    assert header["current_unit_id"] == squadron.id


def test_pdf_endpoint_is_listed_in_the_schema(as_user, admin):
    response = as_user(admin).get("/api/schema/")
    assert response.status_code == 200
    assert "/api/risk-register/{id}/pdf/" in response.content.decode("utf-8", "ignore")


def test_pdf_is_generated_from_the_saved_assessment(
    as_user, creator, mrm_form, monkeypatch
):
    """Viewing the PDF renders the assessment as it is now, not a stale copy."""
    from apps.legacy.models import HazardRegistry

    hazard = HazardRegistry.objects.create(
        unit=creator.unit.name,
        unit_id=creator.unit,
        activity_code="Aerodrome Operations",
        hazard_description="Aircraft Ground Collision",
        before_likelihood=4,
        before_severity="A",
        before_risk_rating="4A",
        mitigations="Enforce marshaller procedures\nObserve the taxi lines",
        after_likelihood=2,
        after_severity="C",
        after_risk_rating="2C",
        created_by=creator,
    )
    # An assessment needs at least eight identified hazards before it saves.
    others = [
        HazardRegistry.objects.create(
            unit=creator.unit.name,
            unit_id=creator.unit,
            activity_code="Aerodrome Operations",
            hazard_description=f"Ground hazard {index + 1}",
            before_likelihood=4,
            before_severity="A",
            before_risk_rating="4A",
            mitigations="Observe the taxi lines",
            after_likelihood=2,
            after_severity="D",
            after_risk_rating="2D",
            created_by=creator,
        )
        for index in range(7)
    ]
    saved = as_user(creator).patch(
        f"/api/risk-register/{mrm_form.id}/details/",
        {
            "form": {"aircraft_vehicle": "KIA Nr 12345567"},
            "environment": "Rainy and wet. Overnight.",
            "hazards": [
                {
                    "hazard_id": hazard.id,
                    "override_likelihood": 2,
                    "override_severity": "D",
                    "additional_mitigations": "Extra brief before departure",
                }
            ]
            + [
                {
                    "hazard_id": other.id,
                    "override_likelihood": 2,
                    "override_severity": "D",
                }
                for other in others
            ],
        },
        format="json",
    )
    assert saved.status_code == 200, saved.content

    captured = {}

    def fake_build(form, creator_user, reviewer, authority_user, hazards):
        captured["environment"] = form.environment
        captured["aircraft_vehicle"] = form.aircraft_vehicle
        captured["hazards"] = [
            (
                link.hazard_id,
                link.override_likelihood,
                link.override_severity,
                link.additional_mitigations,
            )
            for link in hazards
        ]
        return b"%PDF-1.4 rendered from current data"

    monkeypatch.setattr(pdf_service, "_build", fake_build)

    response = as_user(creator).get(f"/api/risk-register/{mrm_form.id}/pdf/")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert captured["environment"] == "Rainy and wet. Overnight."
    assert captured["aircraft_vehicle"] == "KIA Nr 12345567"
    assert len(captured["hazards"]) == 8
    assert (hazard.id, 2, "D", "Extra brief before departure") in captured["hazards"]

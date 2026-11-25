"""Risk register access: who sees an MRM, how it is filtered, who may delete it.

The policy under test:

* Administrator - every unit, reads and edits; permanent deletion is reserved
  to the Super Administrator seat holder.
* Unit Safety Officer - own/assigned units and subordinates; no deletion.
* Commander, Deputy Commander, Director for Operations, Operations Officer and
  Operations Center members - own unit and subordinates, read-only.
* Everyone else - only the assessments they raised, were assigned, or were
  tagged on.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from apps.command.models import UnitAppointment
from apps.core.factories import make_unit, make_user
from apps.legacy.models import (
    HazardRegistry,
    MRMFeedback,
    MRMForm,
    MRMPersonnel,
    MissionHazards,
    UnitAssignment,
)
from apps.ops.models import (
    AuditLog,
    MRMAttentionDismissal,
    Mission,
    RiskAssessment,
)

pytestmark = pytest.mark.django_db


def make_form(unit, user, **overrides):
    data = {
        "mrm_number": f"MRM-{uuid.uuid4().hex[:8]}",
        "activity_mission": f"Assessment for {unit.acronym}",
        "created_by": user,
        "safety_objectives": "Safe operations",
        "status": "draft",
        "review_status": "pending",
        "original_unit": unit,
        "original_unit_name": unit.name,
    }
    data.update(overrides)
    return MRMForm.objects.create(**data)


def visible_ids(client):
    response = client.get("/api/risk-register/?page_size=100")
    assert response.status_code == 200, response.content
    return {row["id"] for row in response.json()["results"]}


# ---------------------------------------------------------------------------
# Read scope
# ---------------------------------------------------------------------------


def test_a_creator_sees_only_the_assessments_they_raised(as_user, creator, mrm_form):
    colleague = make_user(unit=creator.unit, authority="Creator")
    theirs = make_form(creator.unit, colleague)

    assert visible_ids(as_user(creator)) == {mrm_form.id}

    as_user(colleague)
    assert visible_ids(as_user(colleague)) == {theirs.id}


def test_an_assigned_evaluator_sees_the_assessment(as_user, creator, evaluator, mrm_form):
    assert mrm_form.id not in visible_ids(as_user(evaluator))

    mrm_form.reviewed_by = evaluator
    mrm_form.reviewer_name = evaluator.full_name
    mrm_form.save(update_fields=["reviewed_by", "reviewer_name"])

    assert mrm_form.id in visible_ids(as_user(evaluator))


def test_tagged_personnel_see_the_assessment(as_user, creator, other_unit, mrm_form):
    tagged = make_user(unit=other_unit, authority="Creator")
    MRMPersonnel.objects.create(
        mrm=mrm_form, user=tagged, role="Member", tagged_by=creator, is_active=True
    )

    assert mrm_form.id in visible_ids(as_user(tagged))


def test_a_commander_sees_own_unit_and_subordinate_units(
    as_user, unit, other_unit, creator, mrm_form
):
    subordinate = make_unit(name="Sub Squadron", acronym="SUB", parent=unit)
    member = make_user(unit=subordinate, authority="Creator")
    below = make_form(subordinate, member)
    elsewhere = make_form(other_unit, make_user(unit=other_unit, authority="Creator"))

    commander = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=commander, role="commander")

    ids = visible_ids(as_user(commander))
    assert mrm_form.id in ids
    assert below.id in ids
    assert elsewhere.id not in ids


@pytest.mark.parametrize(
    "role", ["deputy_commander", "director_operations", "operations_officer"]
)
def test_the_other_command_posts_also_oversee_subordinates(
    as_user, unit, role, mrm_form
):
    subordinate = make_unit(name=f"Sub {role}", acronym="SUB", parent=unit)
    below = make_form(subordinate, make_user(unit=subordinate, authority="Creator"))

    officer = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=officer, role=role)

    assert below.id in visible_ids(as_user(officer))


def test_operations_center_members_oversee_their_unit(as_user, planner, unit, mrm_form):
    subordinate = make_unit(name="Sub Ops", acronym="SOPS", parent=unit)
    below = make_form(subordinate, make_user(unit=subordinate, authority="Creator"))

    ids = visible_ids(as_user(planner))
    assert mrm_form.id in ids
    assert below.id in ids


def test_unit_safety_officer_sees_assigned_units_and_subordinates(
    as_user, unit, safety, mrm_form
):
    assigned = make_unit(name="Assigned Wing", acronym="AW")
    UnitAssignment.objects.create(user=safety, unit=assigned, is_primary=True)
    assigned_form = make_form(assigned, make_user(unit=assigned, authority="Creator"))

    unrelated = make_unit(name="Unrelated Wing", acronym="UW")
    hidden = make_form(unrelated, make_user(unit=unrelated, authority="Creator"))

    ids = visible_ids(as_user(safety))
    assert mrm_form.id in ids  # own unit
    assert assigned_form.id in ids  # additionally assigned
    assert hidden.id not in ids


def test_administrator_sees_every_unit(as_user, admin, other_unit, creator, mrm_form):
    elsewhere = make_form(other_unit, make_user(unit=other_unit, authority="Creator"))

    ids = visible_ids(as_user(admin))
    assert mrm_form.id in ids
    assert elsewhere.id in ids


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_the_unit_filter_narrows_to_one_unit(as_user, admin, other_unit, creator, mrm_form):
    elsewhere = make_form(other_unit, make_user(unit=other_unit, authority="Creator"))

    response = as_user(admin).get(f"/api/risk-register/?unit={other_unit.id}")
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["results"]}
    assert ids == {elsewhere.id}
    assert mrm_form.id not in ids


def test_the_units_endpoint_lists_filter_choices(as_user, admin, other_unit, mrm_form):
    response = as_user(admin).get("/api/risk-register/units/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["results"]}
    assert {mrm_form.original_unit_id, other_unit.id} <= ids


def test_date_covered_uses_the_linked_mission_window(as_user, creator, unit, mrm_form):
    mission = Mission.objects.create(
        mission_number=f"M-{uuid.uuid4().hex[:6]}",
        category="ground",
        title="Patrol",
        unit=unit,
        created_by=creator,
        start_at=dt.datetime(2026, 3, 10, 8, 0),
        end_at=dt.datetime(2026, 3, 12, 18, 0),
    )
    RiskAssessment.objects.create(mission=mission, mrm_form=mrm_form)

    inside = as_user(creator).get(
        "/api/risk-register/?date_from=2026-03-11&date_to=2026-03-11"
    )
    assert mrm_form.id in {row["id"] for row in inside.json()["results"]}

    outside = as_user(creator).get(
        "/api/risk-register/?date_from=2026-04-01&date_to=2026-04-30"
    )
    assert mrm_form.id not in {row["id"] for row in outside.json()["results"]}


def test_date_covered_falls_back_to_the_raised_date_for_legacy_rows(
    as_user, creator, mrm_form
):
    mrm_form.date_created = dt.datetime(2026, 2, 1, 9, 0)
    mrm_form.save(update_fields=["date_created"])

    response = as_user(creator).get(
        "/api/risk-register/?date_from=2026-01-25&date_to=2026-02-02"
    )
    assert mrm_form.id in {row["id"] for row in response.json()["results"]}


def test_a_manual_mrm_covers_the_day_it_was_raised(as_user, creator, mrm_form):
    """With no mission window, the assessment covers the day it was raised."""
    mrm_form.date_created = dt.datetime(2026, 2, 1, 9, 0)
    mrm_form.save(update_fields=["date_created"])

    rows = as_user(creator).get("/api/risk-register/").json()["results"]
    row = next(row for row in rows if row["id"] == mrm_form.id)

    assert row["date_covered_start"] == row["date_covered_end"]
    assert row["date_covered_start"].startswith("2026-02-01T09:00")


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


def test_administrator_delete_removes_every_dependent_row(
    as_user, admin, seat_admin, creator, unit, mrm_form, tmp_path, settings
):
    settings.LEGACY_PDF_DIR = tmp_path
    (tmp_path / "approved.pdf").write_bytes(b"%PDF-1.4 test")
    mrm_form.approved_pdf_filename = "approved.pdf"
    mrm_form.authority_approved = True
    mrm_form.save(update_fields=["approved_pdf_filename", "authority_approved"])

    hazard = HazardRegistry.objects.create(
        unit=unit.name,
        unit_id=unit,
        activity_code="OPS-1",
        hazard_description="Loose equipment",
        before_likelihood=3,
        before_severity="C",
        before_risk_rating="3C",
        mitigations="Tie it down",
        after_likelihood=2,
        after_severity="D",
        after_risk_rating="2D",
        created_by=creator,
    )
    MissionHazards.objects.create(mrm=mrm_form, hazard=hazard)
    MRMPersonnel.objects.create(
        mrm=mrm_form, user=creator, tagged_by=creator, is_active=True
    )
    MRMFeedback.objects.create(mrm=mrm_form, user=creator)
    MRMAttentionDismissal.objects.create(user=admin, mrm_form=mrm_form)
    mission = Mission.objects.create(
        mission_number=f"M-{uuid.uuid4().hex[:6]}",
        category="ground",
        title="Patrol",
        unit=unit,
        created_by=creator,
        risk_form=mrm_form,
    )
    RiskAssessment.objects.create(mission=mission, mrm_form=mrm_form)

    form_id = mrm_form.id
    response = as_user(seat_admin).delete(f"/api/risk-register/{form_id}/")

    assert response.status_code == 204
    assert not MRMForm.objects.filter(pk=form_id).exists()
    assert not MissionHazards.objects.filter(mrm_id=form_id).exists()
    assert not MRMPersonnel.objects.filter(mrm_id=form_id).exists()
    assert not MRMFeedback.objects.filter(mrm_id=form_id).exists()
    assert not MRMAttentionDismissal.objects.filter(mrm_form_id=form_id).exists()
    assert not RiskAssessment.objects.filter(mrm_form_id=form_id).exists()
    mission.refresh_from_db()
    assert mission.risk_form_id is None
    assert not (tmp_path / "approved.pdf").exists()
    assert AuditLog.objects.filter(
        entity_type="mrm_form", entity_id=str(form_id), action="delete"
    ).exists()


def test_recalculate_is_refused_once_the_mrm_is_approved(as_user, admin, mrm_form):
    mrm_form.authority_approved = True
    mrm_form.save(update_fields=["authority_approved"])

    response = as_user(admin).post(
        f"/api/risk-register/{mrm_form.id}/recalculate/", {}, format="json"
    )

    assert response.status_code == 403
    assert "approved" in response.json()["detail"].lower()


def test_recalculate_still_works_before_approval(as_user, admin, mrm_form):
    response = as_user(admin).post(
        f"/api/risk-register/{mrm_form.id}/recalculate/", {}, format="json"
    )

    assert response.status_code == 200, response.content


def test_unit_safety_officer_may_not_delete_an_assessment(as_user, safety, mrm_form):
    response = as_user(safety).delete(f"/api/risk-register/{mrm_form.id}/")

    assert response.status_code == 403
    assert "Administrator" in response.json()["detail"]
    assert MRMForm.objects.filter(pk=mrm_form.id).exists()


def test_unit_safety_officer_may_not_delete_an_approved_assessment(
    as_user, safety, mrm_form
):
    mrm_form.authority_approved = True
    mrm_form.save(update_fields=["authority_approved"])

    response = as_user(safety).delete(f"/api/risk-register/{mrm_form.id}/")

    assert response.status_code == 403
    assert "Administrator" in response.json()["detail"]
    assert MRMForm.objects.filter(pk=mrm_form.id).exists()


def test_unit_safety_officer_cannot_reach_another_units_assessment(
    as_user, safety, other_unit
):
    foreign = make_form(other_unit, make_user(unit=other_unit, authority="Creator"))

    response = as_user(safety).delete(f"/api/risk-register/{foreign.id}/")

    assert response.status_code == 404
    assert MRMForm.objects.filter(pk=foreign.id).exists()


def test_creators_may_not_delete_their_own_assessment(as_user, creator, mrm_form):
    response = as_user(creator).delete(f"/api/risk-register/{mrm_form.id}/")

    assert response.status_code == 403
    assert MRMForm.objects.filter(pk=mrm_form.id).exists()


def test_the_serializer_only_offers_the_button_to_the_seat_holder(
    as_user, admin, seat_admin, safety, creator, mrm_form
):
    approved = make_form(creator.unit, creator, authority_approved=True)
    bystander = make_user(unit=creator.unit, authority="Administrator")

    assert as_user(seat_admin).get(f"/api/risk-register/{mrm_form.id}/").json()["can_delete"] is True
    assert as_user(seat_admin).get(f"/api/risk-register/{approved.id}/").json()["can_delete"] is True
    assert as_user(bystander).get(f"/api/risk-register/{mrm_form.id}/").json()["can_delete"] is False
    assert as_user(safety).get(f"/api/risk-register/{mrm_form.id}/").json()["can_delete"] is False
    assert as_user(creator).get(f"/api/risk-register/{mrm_form.id}/").json()["can_delete"] is False


def test_bulk_delete_is_refused_to_everyone_but_the_seat_holder(
    as_user, seat_admin, safety, creator, mrm_form
):
    approved = make_form(
        creator.unit, creator, authority_approved=True, status="reviewed"
    )

    refused = as_user(safety).post(
        "/api/risk-register/bulk-delete/",
        {"ids": [mrm_form.id, approved.id]},
        format="json",
    )

    assert refused.status_code == 200, refused.content
    assert refused.json()["deleted"] == []
    assert len(refused.json()["skipped"]) == 2
    assert MRMForm.objects.filter(pk__in=[mrm_form.id, approved.id]).count() == 2

    allowed = as_user(seat_admin).post(
        "/api/risk-register/bulk-delete/",
        {"ids": [mrm_form.id, approved.id]},
        format="json",
    )

    assert allowed.status_code == 200, allowed.content
    assert sorted(allowed.json()["deleted"]) == sorted([mrm_form.id, approved.id])
    assert not MRMForm.objects.filter(pk__in=[mrm_form.id, approved.id]).exists()


def test_bulk_delete_reports_ids_outside_the_callers_scope(as_user, safety, other_unit):
    foreign = make_form(other_unit, make_user(unit=other_unit, authority="Creator"))

    response = as_user(safety).post(
        "/api/risk-register/bulk-delete/", {"ids": [foreign.id]}, format="json"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == []
    assert [row["id"] for row in body["skipped"]] == [foreign.id]
    assert MRMForm.objects.filter(pk=foreign.id).exists()


def test_bulk_delete_requires_a_selection(as_user, admin):
    response = as_user(admin).post(
        "/api/risk-register/bulk-delete/", {"ids": []}, format="json"
    )

    assert response.status_code == 400
    assert "ids" in response.json()["errors"]

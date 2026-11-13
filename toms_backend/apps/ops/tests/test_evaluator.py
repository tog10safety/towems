"""Evaluator selection via the API - the fixed MRMS defect, server-side."""

from __future__ import annotations

import pytest

from apps.command.models import UnitAppointment
from apps.core.factories import make_unit, make_user
from apps.legacy.models import MRMForm, MRMPersonnel

pytestmark = pytest.mark.django_db


def assign(client, form, evaluator_id):
    return client.post(
        f"/api/risk-register/{form.id}/assign-evaluator/",
        {"evaluator_id": evaluator_id},
        format="json",
    )


def test_creator_can_assign_a_valid_evaluator(as_user, creator, evaluator, mrm_form):
    client = as_user(creator)
    response = assign(client, mrm_form, evaluator.id)
    assert response.status_code == 200, response.content
    mrm_form.refresh_from_db()
    assert mrm_form.reviewed_by_id == evaluator.id
    assert mrm_form.reviewer_name == evaluator.full_name


def test_non_evaluator_is_rejected(as_user, admin, creator, mrm_form):
    client = as_user(admin)
    response = assign(client, mrm_form, creator.id)
    assert response.status_code == 400
    assert "not an evaluator" in str(response.json()).lower()
    mrm_form.refresh_from_db()
    assert mrm_form.reviewed_by_id is None


def test_unit_safety_officer_is_not_an_evaluator(as_user, admin, safety, mrm_form):
    client = as_user(admin)
    response = assign(client, mrm_form, safety.id)
    assert response.status_code == 400


def test_inactive_evaluator_is_rejected(as_user, admin, evaluator, mrm_form):
    evaluator.is_active = False
    evaluator.save(update_fields=["is_active"])
    client = as_user(admin)
    response = assign(client, mrm_form, evaluator.id)
    assert response.status_code == 400
    assert "inactive" in str(response.json()).lower()


def test_evaluator_outside_authorised_units_is_rejected(
    as_user, creator, evaluator_other_unit, mrm_form
):
    client = as_user(creator)
    response = assign(client, mrm_form, evaluator_other_unit.id)
    assert response.status_code == 400
    assert "outside" in str(response.json()).lower()


def test_self_assignment_is_rejected(as_user, evaluator, unit):
    own_form = MRMForm.objects.create(
        mrm_number="MRM-SELF-1",
        activity_mission="Self assignment",
        created_by=evaluator,
        safety_objectives="x",
        original_unit_id=unit.id,
    )
    client = as_user(evaluator)
    response = assign(client, own_form, evaluator.id)
    assert response.status_code == 400
    assert "yourself" in str(response.json()).lower()


def test_empty_selection_is_rejected(as_user, creator, mrm_form):
    client = as_user(creator)
    response = assign(client, mrm_form, None)
    assert response.status_code == 400


def test_non_numeric_selection_is_rejected(as_user, creator, mrm_form):
    client = as_user(creator)
    response = client.post(
        f"/api/risk-register/{mrm_form.id}/assign-evaluator/",
        {"evaluator_id": "Sgt Evaluator"},
        format="json",
    )
    assert response.status_code == 400


def test_unrelated_user_cannot_reassign(as_user, creator, evaluator, mrm_form):
    # Tagged, so the assessment is inside their read scope. The refusal is about
    # the reassignment permission, not about hiding the record.
    MRMPersonnel.objects.create(
        mrm=mrm_form, user=evaluator, tagged_by=creator, is_active=True
    )
    client = as_user(evaluator)
    response = assign(client, mrm_form, evaluator.id)
    assert response.status_code == 403


def test_a_user_outside_the_read_scope_cannot_see_the_assessment(
    as_user, evaluator, mrm_form
):
    # Not the creator, not assigned, not tagged, no oversight post: the register
    # answers exactly as if the assessment did not exist.
    response = as_user(evaluator).get(f"/api/risk-register/{mrm_form.id}/")
    assert response.status_code == 404


def test_evaluator_cannot_change_after_review(as_user, admin, evaluator, mrm_form):
    mrm_form.status = "reviewed"
    mrm_form.save(update_fields=["status"])
    client = as_user(admin)
    response = assign(client, mrm_form, evaluator.id)
    assert response.status_code == 400


def test_evaluator_is_retained_across_submit(as_user, creator, evaluator, mrm_form):
    client = as_user(creator)
    assert assign(client, mrm_form, evaluator.id).status_code == 200
    submit = client.post(f"/api/risk-register/{mrm_form.id}/submit/", {}, format="json")
    assert submit.status_code == 200, submit.content
    mrm_form.refresh_from_db()
    assert mrm_form.reviewed_by_id == evaluator.id
    assert mrm_form.status == "submitted"
    assert submit.json()["evaluator_name"] == evaluator.full_name


def test_submit_requires_an_evaluator(as_user, creator, mrm_form):
    client = as_user(creator)
    response = client.post(f"/api/risk-register/{mrm_form.id}/submit/", {}, format="json")
    assert response.status_code == 400


def test_evaluators_endpoint_excludes_self_and_returns_fields(
    as_user, evaluator, unit
):
    colleague = make_user(unit=unit, authority="Evaluator")
    child = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    subordinate = make_user(unit=child, authority="Evaluator")

    client = as_user(evaluator)
    response = client.get("/api/risk-register/evaluators/")
    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["results"]]
    assert colleague.id in ids
    assert subordinate.id not in ids
    assert evaluator.id not in ids


def test_evaluators_endpoint_hides_command_posts_and_designations(
    as_user, creator, unit
):
    by_post = []
    for role in ("commander", "deputy_commander", "director_operations"):
        person = make_user(unit=unit, authority="Evaluator")
        UnitAppointment.objects.create(unit=unit, user=person, role=role)
        by_post.append(person)

    by_designation = make_user(
        unit=unit, authority="Evaluator", designation="Director for Operations"
    )

    response = as_user(creator).get("/api/risk-register/evaluators/")
    assert response.status_code == 200
    rows = response.json()["results"]
    ids = [row["id"] for row in rows]

    for person in [*by_post, by_designation]:
        assert person.id not in ids
    assert all("rank" in row for row in rows)


def test_review_requires_the_assigned_evaluator(
    as_user, evaluator, evaluator_other_unit, mrm_form
):
    mrm_form.reviewed_by = evaluator
    mrm_form.status = "submitted"
    mrm_form.save(update_fields=["reviewed_by", "status"])

    client = as_user(evaluator_other_unit)
    response = client.post(
        f"/api/risk-register/{mrm_form.id}/review/",
        {"decision": "approved"},
        format="json",
    )
    assert response.status_code in (403, 404)

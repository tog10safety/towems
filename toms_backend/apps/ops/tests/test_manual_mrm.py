"""A Creator's manual MRM: raised without an Operations Center mission.

The draft must carry the creator's unit, the chosen evaluator and a normal MRM
number, but no ``RiskAssessment`` or ``Mission.risk_form`` link.
"""

from __future__ import annotations

import pytest

from apps.core.factories import make_unit, make_user
from apps.legacy.models import MRMForm
from apps.ops.models import Mission, RiskAssessment

pytestmark = pytest.mark.django_db


def payload(evaluator, **overrides):
    data = {
        "activity_mission": "Battalion road march",
        "mission_statement": "Move personnel and equipment to the forward base.",
        "evaluator_id": evaluator.id,
    }
    data.update(overrides)
    return data


def test_creator_raises_a_manual_mrm_without_a_mission(as_user, creator, evaluator):
    response = as_user(creator).post(
        "/api/risk-register/", payload(evaluator), format="json"
    )
    assert response.status_code == 201, response.content
    body = response.json()

    form = MRMForm.objects.get(pk=body["id"])
    assert form.mrm_number.isdigit() and len(form.mrm_number) == 10
    assert form.activity_mission == "Battalion road march"
    assert form.mission_statement.startswith("Move personnel")
    assert form.status == "draft"
    assert form.review_status == "pending"
    assert form.created_by_id == creator.id
    assert form.creator_name == creator.full_name
    assert form.original_unit_id == creator.unit_id
    assert form.original_creator_unit_id == creator.unit_id
    assert form.reviewed_by_id == evaluator.id
    assert form.reviewer_name == evaluator.full_name
    assert form.original_reviewer_unit_id == evaluator.unit_id
    assert form.date_created is not None

    # Nothing links this assessment to a mission or a mission risk record.
    assert not RiskAssessment.objects.filter(mrm_form=form).exists()
    assert not Mission.objects.filter(risk_form=form).exists()
    assert body["mission"] is None
    assert body["mission_number"] == ""

    # The creator and the named evaluator can both open it.
    assert (
        as_user(creator).get(f"/api/risk-register/{form.id}/").status_code == 200
    )
    assert (
        as_user(evaluator).get(f"/api/risk-register/{form.id}/").status_code == 200
    )


def test_manual_mrm_is_refused_to_roles_that_do_not_raise_assessments(
    as_user, evaluator, safety
):
    for user in (evaluator, safety):
        response = as_user(user).post(
            "/api/risk-register/", payload(evaluator), format="json"
        )
        assert response.status_code == 403, user.authority


def test_manual_mrm_requires_an_activity_and_an_evaluator(as_user, creator):
    missing_title = as_user(creator).post(
        "/api/risk-register/",
        {"evaluator_id": creator.id},
        format="json",
    )
    assert missing_title.status_code == 400
    assert "activity_mission" in missing_title.json().get("errors", {})

    missing_evaluator = as_user(creator).post(
        "/api/risk-register/",
        {"activity_mission": "Manual assessment"},
        format="json",
    )
    assert missing_evaluator.status_code == 400
    assert "evaluator_id" in missing_evaluator.json().get("errors", {})

    assert MRMForm.objects.count() == 0


def test_manual_mrm_requires_a_purpose(as_user, creator, evaluator):
    response = as_user(creator).post(
        "/api/risk-register/",
        {"activity_mission": "Battalion road march", "evaluator_id": evaluator.id},
        format="json",
    )
    assert response.status_code == 400
    assert "mission_statement" in response.json().get("errors", {})
    assert MRMForm.objects.count() == 0


def test_manual_mrm_rejects_an_evaluator_outside_the_creators_scope(
    as_user, creator, evaluator_other_unit
):
    response = as_user(creator).post(
        "/api/risk-register/", payload(evaluator_other_unit), format="json"
    )
    assert response.status_code == 400
    assert "evaluator_id" in response.json().get("errors", {})
    assert MRMForm.objects.count() == 0


def test_manual_mrm_rejects_a_subordinate_units_evaluator(
    as_user, creator, unit
):
    child = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    subordinate = make_user(unit=child, authority="Evaluator")

    response = as_user(creator).post(
        "/api/risk-register/", payload(subordinate), format="json"
    )
    assert response.status_code == 400
    errors = response.json().get("errors", {})
    assert "evaluator_id" in errors
    assert "same unit" in str(errors["evaluator_id"])
    assert MRMForm.objects.count() == 0


def test_manual_mrm_requires_the_creator_to_have_a_unit(as_user, evaluator):
    unitless = make_user(unit=None, authority="Creator")
    response = as_user(unitless).post(
        "/api/risk-register/", payload(evaluator), format="json"
    )
    assert response.status_code == 400
    assert "unit" in response.json()["detail"]
    assert MRMForm.objects.count() == 0

"""Post-mission feedback: who is asked, who audits it, and what the audit can do.

The MRM tags the people who were on the mission. Once the mission is finished
and the assessment approved, those people are asked what actually happened; the
unit's Safety Officer reads every answer, records what was done about it, and
can turn a recommendation into a maintained hazard-library row.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.core import timeutils
from apps.core.factories import make_user
from apps.legacy.models import HazardRegistry, MRMFeedback, MRMPersonnel
from apps.ops.models import Mission, Notification, RiskAssessment

pytestmark = pytest.mark.django_db


@pytest.fixture
def mission(db, planner, unit):
    return Mission.objects.create(
        mission_number="TOWEMS-GO-FBK-001",
        category=Mission.CATEGORY_GROUND,
        title="Feedback convoy",
        unit=unit,
        created_by=planner,
        status="completed",
        start_at=dt.datetime(2026, 10, 1, 6, 0),
        end_at=dt.datetime(2026, 10, 1, 18, 0),
        completed_at=dt.datetime(2026, 10, 1, 18, 0),
    )


@pytest.fixture
def assessed(mission, mrm_form, unit):
    """An approved assessment tagged to one person and linked to a finished mission."""
    mrm_form.authority_approved = True
    mrm_form.review_status = "approved"
    mrm_form.date_approved = dt.datetime(2026, 10, 1, 18, 30)
    mrm_form.original_unit_id = unit.id
    mrm_form.original_unit_name = unit.name
    mrm_form.save()
    RiskAssessment.objects.create(mission=mission, mrm_form=mrm_form, status="approved")
    return mrm_form


@pytest.fixture
def tagged(assessed, creator):
    MRMPersonnel.objects.create(
        mrm=assessed,
        user=creator,
        role="Team Leader",
        tagged_by=assessed.created_by,
        tagged_at=timeutils.now(),
        is_active=True,
    )
    return creator


def feedback_payload(**overrides):
    data = {
        "risk_assessment_accuracy": 4,
        "hazard_identification_rating": 3,
        "mitigation_effectiveness": 5,
        "what_went_well": "The convoy brief was clear.",
        "what_could_be_improved": "Radio check before departure.",
        "lessons_learned": "Confirm the lead vehicle first.",
        "is_anonymous": False,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Requesting and submitting
# ---------------------------------------------------------------------------


def test_a_tagged_person_is_asked_after_the_mission_finishes(
    as_user, tagged, assessed
):
    body = as_user(tagged).get("/api/feedback/requests/").json()

    assert body["requested_count"] == 1
    request = body["requested"][0]
    assert request["mrm"] == assessed.id
    assert request["state"] == "open"
    assert request["mission"]["status"] == "completed"


def test_feedback_is_closed_until_the_mission_completes(
    as_user, tagged, assessed, mission
):
    Mission.objects.filter(pk=mission.pk).update(status="active", completed_at=None)

    body = as_user(tagged).get("/api/feedback/requests/").json()
    assert body["requested"] == []
    assert len(body["upcoming"]) == 1
    assert "completed" in body["upcoming"][0]["reason"].lower()

    response = as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/", feedback_payload(), format="json"
    )
    assert response.status_code == 400
    assert "completed" in str(response.json()).lower()
    assert not MRMFeedback.objects.exists()


def test_submitting_feedback_creates_the_record_and_notifies_the_safety_office(
    as_user, tagged, assessed, safety
):
    response = as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/", feedback_payload(), format="json"
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == "pending"
    assert body["mitigation_effectiveness"] == 5

    row = MRMFeedback.objects.get(pk=body["id"])
    assert row.mrm_id == assessed.id
    assert row.user_id == tagged.id
    assert row.submitted_at is not None

    note = Notification.objects.filter(
        recipient=safety, category="feedback", requires_action=True
    ).first()
    assert note is not None
    assert note.action_url == "/feedback"


def test_the_same_person_cannot_submit_twice(as_user, tagged, assessed):
    first = as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/", feedback_payload(), format="json"
    )
    assert first.status_code == 201
    second = as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/", feedback_payload(), format="json"
    )

    assert second.status_code == 400
    assert "already submitted" in str(second.json()).lower()
    assert MRMFeedback.objects.count() == 1


def test_a_person_not_tagged_cannot_submit(as_user, assessed, evaluator_other_unit):
    response = as_user(evaluator_other_unit).post(
        f"/api/feedback/requests/{assessed.id}/", feedback_payload(), format="json"
    )

    assert response.status_code == 400
    assert "not tagged" in str(response.json()).lower()


def test_an_empty_submission_is_refused(as_user, tagged, assessed):
    response = as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/",
        {"is_anonymous": False},
        format="json",
    )

    assert response.status_code == 400
    assert "at least one rating" in str(response.json()).lower()


def test_a_rating_outside_the_scale_is_refused(as_user, tagged, assessed):
    response = as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/",
        feedback_payload(mitigation_effectiveness=9),
        format="json",
    )

    assert response.status_code == 400
    assert "1 to 5" in str(response.json())


def test_a_hazard_recommendation_needs_a_description(as_user, tagged, assessed):
    response = as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/",
        feedback_payload(recommend_add_to_hazard_registry=True),
        format="json",
    )

    assert response.status_code == 400
    assert "Describe the hazard" in str(response.json())


def test_a_submission_shows_up_as_filed_not_requested(as_user, tagged, assessed):
    as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/", feedback_payload(), format="json"
    )

    body = as_user(tagged).get("/api/feedback/requests/").json()

    assert body["requested"] == []
    assert len(body["submitted"]) == 1
    assert body["submitted"][0]["feedback"]["status"] == "pending"


# ---------------------------------------------------------------------------
# The safety office audit
# ---------------------------------------------------------------------------


@pytest.fixture
def audited(as_user, tagged, assessed, safety):
    response = as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/",
        feedback_payload(
            recommend_add_to_hazard_registry=True,
            recommended_hazard_description="Unmarked trench beside the staging area",
            recommended_mitigations="Mark and brief the trench before night moves",
        ),
        format="json",
    )
    assert response.status_code == 201, response.content
    return MRMFeedback.objects.get(pk=response.json()["id"])


def test_the_safety_officer_sees_the_queue(as_user, audited, safety):
    body = as_user(safety).get("/api/feedback/audit/").json()

    assert body["count"] == 1
    assert body["summary"]["awaiting_audit"] == 1
    assert body["summary"]["recommendations_pending"] == 1
    assert body["results"][0]["submitter"]
    assert body["results"][0]["what_went_well"]


def test_a_creator_cannot_open_the_audit_queue(as_user, audited, creator):
    assert as_user(creator).get("/api/feedback/audit/").status_code == 403


def test_another_units_safety_officer_sees_nothing(
    as_user, audited, other_unit
):
    outsider = make_user(unit=other_unit, authority="Unit Safety Officer")

    body = as_user(outsider).get("/api/feedback/audit/").json()
    assert body["count"] == 0

    refused = as_user(outsider).post(
        f"/api/feedback/audit/{audited.id}/review/",
        {"status": "reviewed"},
        format="json",
    )
    assert refused.status_code == 400
    assert "do not audit" in str(refused.json())


def test_reviewing_notifies_the_submitter_and_records_the_audit(
    as_user, audited, safety, tagged
):
    response = as_user(safety).post(
        f"/api/feedback/audit/{audited.id}/review/",
        {
            "status": "actioned",
            "review_notes": "Valid observation; added to the quarter's safety brief.",
            "action_taken": "Included in the unit safety bulletin.",
        },
        format="json",
    )

    assert response.status_code == 200, response.content
    audited.refresh_from_db()
    assert audited.status == "actioned"
    assert audited.reviewed_by_id == safety.id
    assert audited.reviewed_at is not None
    assert audited.action_taken_at is not None

    note = Notification.objects.filter(recipient=tagged, category="feedback").first()
    assert note is not None
    assert "audited" in note.title.lower()


def test_dismissing_requires_a_reason(as_user, audited, safety):
    response = as_user(safety).post(
        f"/api/feedback/audit/{audited.id}/review/",
        {"status": "dismissed"},
        format="json",
    )

    assert response.status_code == 400
    assert "dismissed" in str(response.json()).lower()


def test_an_unknown_status_is_refused(as_user, audited, safety):
    response = as_user(safety).post(
        f"/api/feedback/audit/{audited.id}/review/",
        {"status": "ignored"},
        format="json",
    )

    assert response.status_code == 400


def test_adopting_a_recommendation_adds_it_to_the_hazard_library(
    as_user, audited, safety, unit
):
    response = as_user(safety).post(
        f"/api/feedback/audit/{audited.id}/adopt/",
        {
            "activity_code": "GND-09",
            "before_likelihood": 4,
            "before_severity": "B",
            "after_likelihood": 2,
            "after_severity": "D",
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    body = response.json()
    hazard = HazardRegistry.objects.get(pk=body["hazard"]["id"])
    assert hazard.activity_code == "GND-09"
    assert hazard.hazard_description == "Unmarked trench beside the staging area"
    assert "trench" in hazard.mitigations.lower()
    assert hazard.unit_id_id == unit.id
    assert hazard.before_risk_rating == "4B"

    audited.refresh_from_db()
    assert audited.status == "actioned"
    assert str(hazard.id) in audited.action_taken


def test_adopting_needs_a_recommendation(as_user, assessed, safety, tagged):
    response = as_user(tagged).post(
        f"/api/feedback/requests/{assessed.id}/", feedback_payload(), format="json"
    )
    row = MRMFeedback.objects.get(pk=response.json()["id"])

    refused = as_user(safety).post(
        f"/api/feedback/audit/{row.id}/adopt/",
        {"activity_code": "X", "before_likelihood": 1, "before_severity": "A",
         "after_likelihood": 1, "after_severity": "E"},
        format="json",
    )

    assert refused.status_code == 400
    assert "did not recommend" in str(refused.json()).lower()


def test_adopting_with_an_invalid_assessment_is_refused(
    as_user, audited, safety
):
    response = as_user(safety).post(
        f"/api/feedback/audit/{audited.id}/adopt/",
        {
            "activity_code": "GND-10",
            "before_likelihood": 9,
            "before_severity": "B",
            "after_likelihood": 2,
            "after_severity": "D",
        },
        format="json",
    )

    assert response.status_code == 400
    assert not HazardRegistry.objects.exists()

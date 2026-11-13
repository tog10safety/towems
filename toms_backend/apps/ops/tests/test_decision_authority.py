"""Who signs an MRM, and the signature steps around submission.

The unit's ladder maps residual risk onto a signer: creator (<=50%), evaluator
(51-65%), Squadron Commander (66-75%, routing to the Director for Operations
when the unit has none), Director for Operations (76-85%) and Commander (86%+).
These tests pin the mapping, the fallbacks, and the two signature paths - "sign
now" at submission and "sign later" from the workflow.
"""

from __future__ import annotations

import pytest

from apps.command.models import UnitAppointment
from apps.core.factories import make_user
from apps.ops import decision_authority
from apps.ops.models import Notification
from apps.legacy.models import MRMForm

pytestmark = pytest.mark.django_db


def set_risk(form, percent, *, status="reviewed", review="approved", evaluator=None):
    form.total_percent = percent
    form.status = status
    form.review_status = review
    if evaluator is not None:
        form.reviewed_by = evaluator
        form.evaluator_signed = True
    form.save()
    return form


def sign_creator(user):
    user.signature_filename = f"signature_{user.id}.png"
    user.save(update_fields=["signature_filename"])
    return user


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "percent,band",
    [
        (0, decision_authority.CREATOR),
        (50, decision_authority.CREATOR),
        (50.1, decision_authority.SUPERVISOR),
        (65, decision_authority.SUPERVISOR),
        (65.1, decision_authority.SQUADRON_COMMANDER),
        (75, decision_authority.SQUADRON_COMMANDER),
        (75.1, decision_authority.DIRECTOR),
        (85, decision_authority.DIRECTOR),
        (85.1, decision_authority.COMMANDER),
        (100, decision_authority.COMMANDER),
    ],
)
def test_bands_follow_the_policy_ladder(percent, band):
    assert decision_authority.band_for(percent) == band


def test_missing_percentage_is_the_lowest_band():
    assert decision_authority.band_for(None) == decision_authority.CREATOR


def test_creator_band_resolves_to_the_creator(db, creator, mrm_form):
    set_risk(mrm_form, 40)

    authority = decision_authority.resolve(mrm_form)

    assert authority.band == decision_authority.CREATOR
    assert authority.user.id == creator.id


def test_supervisor_band_resolves_to_the_evaluator(db, creator, evaluator, mrm_form):
    set_risk(mrm_form, 60, evaluator=evaluator)

    authority = decision_authority.resolve(mrm_form)

    assert authority.band == decision_authority.SUPERVISOR
    assert authority.user.id == evaluator.id


def test_squadron_commander_resolves_from_the_unit_appointment(db, unit, commander, mrm_form):
    set_risk(mrm_form, 70)

    authority = decision_authority.resolve(mrm_form)

    assert authority.band == decision_authority.SQUADRON_COMMANDER
    assert authority.user.id == commander.id


def test_a_flight_routes_to_its_parent_squadron_commander(db, unit, commander, mrm_form):
    from apps.core.factories import make_unit

    flight = make_unit(name="Flight A", acronym="FLT-A", category="Flight", parent=unit)
    mrm_form.original_unit = flight
    mrm_form.original_unit_name = flight.name
    mrm_form.save(update_fields=["original_unit", "original_unit_name"])
    set_risk(mrm_form, 70)

    authority = decision_authority.resolve(mrm_form)

    assert authority.band == decision_authority.SQUADRON_COMMANDER
    assert authority.user.id == commander.id
    assert "parent squadron" in authority.reason


def test_a_unit_without_a_squadron_commander_routes_to_the_director(
    db, creator, mrm_form
):
    from apps.core.factories import make_unit

    group = make_unit(name="Group 1", acronym="G1", category="Group")
    mrm_form.original_unit = group
    mrm_form.original_unit_name = group.name
    mrm_form.save(update_fields=["original_unit", "original_unit_name"])
    director = make_user(unit=group, authority="Creator")
    UnitAppointment.objects.create(unit=group, user=director, role="director_operations")
    set_risk(mrm_form, 70)

    authority = decision_authority.resolve(mrm_form)

    assert authority.band == decision_authority.SQUADRON_COMMANDER
    assert authority.user.id == director.id
    assert "Director for Operations" in authority.reason


def test_director_band_resolves_from_the_appointment(db, unit, creator, mrm_form):
    director = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=director, role="director_operations")
    set_risk(mrm_form, 80)

    authority = decision_authority.resolve(mrm_form)

    assert authority.band == decision_authority.DIRECTOR
    assert authority.user.id == director.id


def test_commander_band_falls_back_to_the_deputy(db, unit, creator, mrm_form):
    deputy = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=deputy, role="deputy_commander")
    set_risk(mrm_form, 90)

    authority = decision_authority.resolve(mrm_form)

    assert authority.band == decision_authority.COMMANDER
    assert authority.user.id == deputy.id


def test_unresolved_post_says_so(db, unit, creator, mrm_form):
    set_risk(mrm_form, 90)

    authority = decision_authority.resolve(mrm_form)

    assert authority.user is None
    assert "Commander" in authority.reason


# ---------------------------------------------------------------------------
# Sign now / sign later
# ---------------------------------------------------------------------------


def submit(client, form_id, **body):
    return client.post(f"/api/risk-register/{form_id}/submit/", body, format="json")


def test_signing_now_without_a_signature_is_refused(as_user, creator, evaluator, mrm_form):
    mrm_form.reviewed_by = evaluator
    mrm_form.save(update_fields=["reviewed_by"])
    creator.signature_filename = ""
    creator.save(update_fields=["signature_filename"])

    response = submit(as_user(creator), mrm_form.id, sign=True)

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == "signature_required"
    mrm_form.refresh_from_db()
    assert mrm_form.status == "draft"
    assert mrm_form.creator_signed is False


def test_signing_now_records_the_signature(as_user, creator, evaluator, mrm_form):
    sign_creator(creator)
    mrm_form.reviewed_by = evaluator
    mrm_form.save(update_fields=["reviewed_by"])

    response = submit(as_user(creator), mrm_form.id, sign=True)

    assert response.status_code == 200, response.content
    mrm_form.refresh_from_db()
    assert mrm_form.status == "submitted"
    assert mrm_form.creator_signed is True
    assert mrm_form.creator_signature_date is not None


def test_signing_later_is_a_separate_step(as_user, creator, evaluator, mrm_form):
    sign_creator(creator)
    mrm_form.reviewed_by = evaluator
    mrm_form.save(update_fields=["reviewed_by"])

    assert submit(as_user(creator), mrm_form.id, sign=False).status_code == 200
    mrm_form.refresh_from_db()
    assert mrm_form.creator_signed is False

    response = as_user(creator).post(f"/api/risk-register/{mrm_form.id}/sign/", {}, format="json")

    assert response.status_code == 200, response.content
    mrm_form.refresh_from_db()
    assert mrm_form.creator_signed is True


def test_signing_later_without_a_signature_is_refused(as_user, creator, evaluator, mrm_form):
    creator.signature_filename = ""
    creator.save(update_fields=["signature_filename"])
    mrm_form.reviewed_by = evaluator
    mrm_form.status = "submitted"
    mrm_form.save(update_fields=["reviewed_by", "status"])

    response = as_user(creator).post(f"/api/risk-register/{mrm_form.id}/sign/", {}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"]["code"] == "signature_required"


def test_creator_signing_completes_a_low_risk_assessment(
    as_user, creator, evaluator, mrm_form
):
    """Below 66% the creator/evaluator are the signers, so the last signature closes it."""
    sign_creator(creator)
    set_risk(mrm_form, 40, status="submitted", review="approved", evaluator=evaluator)

    response = as_user(creator).post(f"/api/risk-register/{mrm_form.id}/sign/", {}, format="json")

    assert response.status_code == 200, response.content
    mrm_form.refresh_from_db()
    assert mrm_form.creator_signed is True
    assert mrm_form.authority_approved is True
    assert mrm_form.authority_assigned_to_id == creator.id


def test_signing_at_submission_does_not_notify_the_authority_yet(
    as_user, unit, creator, evaluator, mrm_form
):
    director = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=director, role="director_operations")
    sign_creator(creator)
    mrm_form.reviewed_by = evaluator
    mrm_form.total_percent = 80
    mrm_form.save(update_fields=["reviewed_by", "total_percent"])

    assert submit(as_user(creator), mrm_form.id, sign=True).status_code == 200

    assert not Notification.objects.filter(recipient=director).exists()
    mrm_form.refresh_from_db()
    assert mrm_form.authority_assigned_to_id == director.id


def test_signing_after_approval_routes_to_the_authority(
    as_user, unit, creator, evaluator, mrm_form
):
    director = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=director, role="director_operations")
    sign_creator(creator)
    set_risk(mrm_form, 80, status="submitted", review="approved", evaluator=evaluator)

    response = as_user(creator).post(f"/api/risk-register/{mrm_form.id}/sign/", {}, format="json")

    assert response.status_code == 200, response.content
    assert Notification.objects.filter(
        recipient=director, title__icontains="awaits your decision"
    ).exists()


def test_evaluator_approval_notifies_an_unsigned_creator(
    as_user, creator, evaluator, mrm_form
):
    set_risk(mrm_form, 80, status="submitted", review="pending", evaluator=evaluator)

    response = as_user(evaluator).post(
        f"/api/risk-register/{mrm_form.id}/review/",
        {"decision": "approved", "notes": "Looks good"},
        format="json",
    )

    assert response.status_code == 200, response.content
    note = Notification.objects.filter(
        recipient=creator, title__icontains="Sign MRM", requires_action=True
    ).first()
    assert note is not None
    assert note.action_url == f"/risk/{mrm_form.id}"


def test_evaluator_approval_notifies_the_director_band_signer(
    as_user, unit, creator, evaluator, mrm_form
):
    director = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=director, role="director_operations")
    set_risk(mrm_form, 80, status="submitted", review="pending", evaluator=evaluator)

    response = as_user(evaluator).post(
        f"/api/risk-register/{mrm_form.id}/review/",
        {"decision": "approved"},
        format="json",
    )

    assert response.status_code == 200, response.content
    assert Notification.objects.filter(
        recipient=director, title__icontains="awaits your decision"
    ).exists()
    mrm_form.refresh_from_db()
    assert mrm_form.authority_assigned_to_id == director.id


# ---------------------------------------------------------------------------
# The decision authority signs, nobody else
# ---------------------------------------------------------------------------


def approvable(form, *, creator_signed=True, evaluator=None):
    form.status = "reviewed"
    form.review_status = "approved"
    form.creator_signed = creator_signed
    if evaluator is not None:
        form.reviewed_by = evaluator
        form.evaluator_signed = True
    form.total_percent = 90
    form.save()
    return form


def test_the_approve_button_follows_the_route(
    as_user, unit, commander, safety, creator, evaluator, mrm_form
):
    approvable(mrm_form, evaluator=evaluator)

    mine = as_user(commander).get(f"/api/risk-register/{mrm_form.id}/").json()
    assert mine["decision_authority"] == "commander"
    assert mine["decision_authority_name"] == commander.full_name
    assert mine["can_approve_authority"] is True

    theirs = as_user(safety).get(f"/api/risk-register/{mrm_form.id}/").json()
    assert theirs["can_approve_authority"] is False


def test_only_the_resolved_commander_can_approve(
    as_user, unit, commander, safety, creator, evaluator, mrm_form
):
    approvable(mrm_form, evaluator=evaluator)

    denied = as_user(safety).post(f"/api/risk-register/{mrm_form.id}/approve/", {}, format="json")
    assert denied.status_code == 403
    assert commander.full_name in str(denied.json())

    allowed = as_user(commander).post(
        f"/api/risk-register/{mrm_form.id}/approve/", {}, format="json"
    )
    assert allowed.status_code == 200, allowed.content
    mrm_form.refresh_from_db()
    assert mrm_form.authority_approved is True
    assert mrm_form.authority_assigned_to_id == commander.id

"""Post-mission feedback on an MRM, audited by the Unit Safety Officer.

The record lives in the legacy ``mrm_feedback`` table, which already carries the
ratings, the free-text answers, the recommendation to grow the hazard library
and the review fields. TOWEMS adds no parallel table: this module writes and reads
that one, and the TOWEMS notification bell carries the work item. An MRM deleted
in TOWEMS already removes its feedback rows, so nothing here leaves a dangling
reference.

Lifecycle
---------
``requested``
    The MRM is approved and the mission it belongs to has finished. Every active
    person tagged on the MRM is asked for feedback.
``pending``
    A tagged person submitted; the Unit Safety Officer has not read it yet.
``reviewed``
    The officer read it and left notes.
``actioned``
    The officer recorded what was done - for example adding the recommended
    hazard to the unit's library.
``dismissed``
    The officer closed it without action, with a reason on the record.

Feedback is voluntary in the sense that it never blocks closing a mission, but
it is not free-form: the officer's queue says exactly which assessments are
still waiting on people, and a submission cannot be edited away afterwards -
a correction is a new officer note on the same record.
"""

from __future__ import annotations

from django.db.models import Q

from apps.core import roles, timeutils
from apps.core.scoping import accessible_unit_ids
from apps.legacy.models import MRMFeedback, MRMPersonnel
from apps.ops import hazards as hazard_service

MAX_RATING = 5

STATUS_PENDING = "pending"
STATUS_REVIEWED = "reviewed"
STATUS_ACTIONED = "actioned"
STATUS_DISMISSED = "dismissed"
STATUS_LABELS = {
    STATUS_PENDING: "Awaiting audit",
    STATUS_REVIEWED: "Reviewed",
    STATUS_ACTIONED: "Actioned",
    STATUS_DISMISSED: "Dismissed",
}
REVIEW_STATUSES = (STATUS_REVIEWED, STATUS_ACTIONED, STATUS_DISMISSED)

RATING_FIELDS = (
    ("risk_assessment_accuracy", "Risk assessment accuracy"),
    ("hazard_identification_rating", "Hazard identification"),
    ("mitigation_effectiveness", "Mitigation effectiveness"),
)
TEXT_FIELDS = (
    ("what_went_well", "What went well"),
    ("what_could_be_improved", "What could be improved"),
    ("new_hazards_identified", "New hazards identified"),
    ("lessons_learned", "Lessons learned"),
    ("additional_comments", "Additional comments"),
)

OPEN = "open"
CLOSED = "closed"


class FeedbackError(Exception):
    """The submission, review or adoption is not permitted or not valid."""

    def __init__(self, message, errors: dict | None = None):
        super().__init__(message)
        self.errors = errors or {}


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def _authenticated(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False))


def can_audit(user) -> bool:
    return _authenticated(user) and roles.has_capability(user, roles.CAP_AUDIT_FEEDBACK)


def is_administrator(user) -> bool:
    return _authenticated(user) and getattr(user, "authority", None) == roles.BASE_ADMIN


def audit_unit_ids(user) -> set[int]:
    """Units whose feedback this officer may audit. Administrator: everything."""
    if not can_audit(user):
        return set()
    if is_administrator(user):
        return set()
    return accessible_unit_ids(user)


# ---------------------------------------------------------------------------
# Which assessment, and is feedback open?
# ---------------------------------------------------------------------------


def linked_mission(form):
    from apps.ops.models import RiskAssessment

    row = (
        RiskAssessment.objects.filter(mrm_form_id=form.id)
        .select_related("mission")
        .first()
    )
    return row.mission if row else None


def is_approved(form) -> bool:
    return bool(form.authority_approved or (form.review_status or "") == "approved")


def feedback_window(form) -> dict:
    """Whether feedback may be filed now, and the reason when it may not."""
    if not is_approved(form):
        return {
            "state": CLOSED,
            "reason": "Feedback opens once the assessment is approved by its decision authority.",
        }
    mission = linked_mission(form)
    if mission is not None and mission.status not in {"completed", "cancelled"}:
        return {
            "state": CLOSED,
            "reason": (
                f"Feedback opens after the mission is completed. "
                f"{mission.mission_number} is {mission.get_status_display().lower()}."
            ),
            "mission": mission,
        }
    return {"state": OPEN, "reason": "", "mission": mission}


def tagged_forms(user):
    """MRMs this user is actively tagged on."""
    ids = (
        MRMPersonnel.objects.filter(user_id=user.id, is_active=True)
        .values_list("mrm_id", flat=True)
        .distinct()
    )
    if not ids:
        return []
    from apps.legacy.models import MRMForm

    return list(MRMForm.objects.filter(id__in=list(ids)).select_related("original_unit"))


def _submitted_by(form_id, user_id):
    return (
        MRMFeedback.objects.filter(mrm_id=form_id, user_id=user_id)
        .order_by("-submitted_at", "-id")
        .first()
    )


# ---------------------------------------------------------------------------
# Read model
# ---------------------------------------------------------------------------


def _safe_name(user) -> str:
    return getattr(user, "full_name", "") or getattr(user, "username", "") or ""


def feedback_payload(row, *, viewer=None) -> dict:
    anonymous = bool(row.is_anonymous)
    submitter = _safe_name(row.user) if row.user_id else ""
    can_see_name = not anonymous or (viewer is not None and can_audit(viewer))
    return {
        "id": row.id,
        "mrm": row.mrm_id,
        "mrm_number": getattr(row.mrm, "mrm_number", ""),
        "activity_mission": getattr(row.mrm, "activity_mission", ""),
        "unit_id": getattr(row.mrm, "original_unit_id", None),
        "unit_name": getattr(getattr(row.mrm, "original_unit", None), "name", "")
        or getattr(row.mrm, "original_unit_name", "")
        or "",
        "unit_acronym": getattr(
            getattr(row.mrm, "original_unit", None), "acronym", ""
        )
        or "",
        "user": row.user_id,
        "submitter": submitter if can_see_name else "Anonymous",
        "is_anonymous": anonymous,
        "risk_assessment_accuracy": row.risk_assessment_accuracy,
        "hazard_identification_rating": row.hazard_identification_rating,
        "mitigation_effectiveness": row.mitigation_effectiveness,
        "what_went_well": row.what_went_well or "",
        "what_could_be_improved": row.what_could_be_improved or "",
        "new_hazards_identified": row.new_hazards_identified or "",
        "lessons_learned": row.lessons_learned or "",
        "additional_comments": row.additional_comments or "",
        "recommend_add_to_hazard_registry": bool(row.recommend_add_to_hazard_registry),
        "recommended_hazard_description": row.recommended_hazard_description or "",
        "recommended_mitigations": row.recommended_mitigations or "",
        "submitted_at": row.submitted_at,
        "status": row.status or STATUS_PENDING,
        "status_label": STATUS_LABELS.get(row.status or STATUS_PENDING, row.status or ""),
        "reviewed_by_name": _safe_name(row.reviewed_by) if row.reviewed_by_id else "",
        "reviewed_at": row.reviewed_at,
        "review_notes": row.review_notes or "",
        "action_taken": row.action_taken or "",
        "action_taken_at": row.action_taken_at,
    }


def requests_for(user) -> dict:
    """What this person is asked for, and what they have already filed."""
    wanted = []
    submitted = []
    upcoming = []
    for form in tagged_forms(user):
        window = feedback_window(form)
        mission = window.get("mission")
        mine = _submitted_by(form.id, user.id)
        base = {
            "mrm": form.id,
            "mrm_number": form.mrm_number,
            "activity_mission": form.activity_mission,
            "unit_name": getattr(form.original_unit, "name", "")
            or form.original_unit_name
            or "",
            "unit_acronym": getattr(form.original_unit, "acronym", "") or "",
            "risk_category": form.risk_category,
            "date_approved": form.date_approved or form.authority_approval_date,
            "mission": (
                {
                    "id": mission.id,
                    "mission_number": mission.mission_number,
                    "title": mission.title,
                    "status": mission.status,
                    "completed_at": mission.completed_at,
                }
                if mission is not None
                else None
            ),
            "state": window["state"],
            "reason": window["reason"],
            "feedback": feedback_payload(mine, viewer=user) if mine is not None else None,
        }
        if mine is not None:
            submitted.append(base)
        elif window["state"] == OPEN:
            wanted.append(base)
        elif window.get("mission") is not None:
            # Told, not asked yet: a person tagged on a mission still running
            # should see that feedback is coming, and why it is not open yet.
            upcoming.append(base)

    return {
        "requested": wanted,
        "submitted": submitted,
        "upcoming": upcoming,
        "rating_scale": list(range(1, MAX_RATING + 1)),
    }


def audit_queryset(user, *, unit=None, status="", date_from=None, date_to=None, q=""):
    if not can_audit(user):
        raise FeedbackError("Only the Unit Safety Officer may audit post-mission feedback.")

    queryset = (
        MRMFeedback.objects.select_related("mrm", "mrm__original_unit", "user", "reviewed_by")
        .filter(mrm_id__isnull=False)
    )
    if not is_administrator(user):
        ids = audit_unit_ids(user)
        if not ids:
            return queryset.none()
        queryset = queryset.filter(mrm__original_unit_id__in=ids)
    if unit not in (None, ""):
        try:
            queryset = queryset.filter(mrm__original_unit_id=int(unit))
        except (TypeError, ValueError):
            raise FeedbackError("The unit filter must be a unit id.") from None
    if status in STATUS_LABELS:
        queryset = queryset.filter(status=status)
    if date_from:
        queryset = queryset.filter(submitted_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(submitted_at__date__lte=date_to)
    if q:
        queryset = queryset.filter(
            Q(mrm__mrm_number__icontains=q)
            | Q(mrm__activity_mission__icontains=q)
            | Q(what_went_well__icontains=q)
            | Q(what_could_be_improved__icontains=q)
        )
    return queryset.order_by("-submitted_at", "-id")


def summary(user) -> dict:
    """The audit queue's opening figures, scoped like the queue itself."""
    queryset = audit_queryset(user)
    rows = list(queryset.values_list("status", flat=True))
    counts = {key: rows.count(key) for key in STATUS_LABELS}
    ratings = list(
        queryset.exclude(mitigation_effectiveness__isnull=True).values_list(
            "mitigation_effectiveness", flat=True
        )
    )
    average = round(sum(ratings) / len(ratings), 2) if ratings else None
    recommended = queryset.filter(
        recommend_add_to_hazard_registry=True, status=STATUS_PENDING
    ).count()
    return {
        "total": len(rows),
        "awaiting_audit": counts.get(STATUS_PENDING, 0),
        "reviewed": counts.get(STATUS_REVIEWED, 0),
        "actioned": counts.get(STATUS_ACTIONED, 0),
        "dismissed": counts.get(STATUS_DISMISSED, 0),
        "average_mitigation_effectiveness": average,
        "recommendations_pending": recommended,
    }


def pending_requests(user) -> int:
    """How many assessments are still waiting on this person (for the bell)."""
    return len(requests_for(user)["requested"])


# ---------------------------------------------------------------------------
# Submitting
# ---------------------------------------------------------------------------


def _rating(value, field, label, errors):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors[field] = f"{label}: choose a rating from 1 to 5."
        return None
    if not 1 <= number <= MAX_RATING:
        errors[field] = f"{label}: choose a rating from 1 to 5."
        return None
    return number


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def submit(user, form_id, data: dict) -> MRMFeedback:
    """File this person's feedback for one assessment."""
    from apps.legacy.models import MRMForm

    if not _authenticated(user):
        raise FeedbackError("Sign in to submit feedback.")
    form = MRMForm.objects.filter(pk=form_id).first()
    if form is None:
        raise FeedbackError("That assessment does not exist.")
    tagged = MRMPersonnel.objects.filter(
        mrm_id=form.id, user_id=user.id, is_active=True
    ).exists()
    if not tagged:
        raise FeedbackError("You are not tagged for feedback on this assessment.")

    window = feedback_window(form)
    if window["state"] != OPEN:
        raise FeedbackError(window["reason"])

    if _submitted_by(form.id, user.id) is not None:
        raise FeedbackError(
            "You have already submitted feedback for this assessment. "
            "Ask the Unit Safety Officer to record a correction."
        )

    errors: dict[str, str] = {}
    values = {
        field: _rating(data.get(field), field, label, errors)
        for field, label in RATING_FIELDS
    }
    for field, _label in TEXT_FIELDS:
        values[field] = _text(data.get(field))[:20000]

    recommended = bool(data.get("recommend_add_to_hazard_registry"))
    recommended_description = _text(data.get("recommended_hazard_description"))[:20000]
    recommended_mitigations = _text(data.get("recommended_mitigations"))[:20000]
    if recommended and not recommended_description:
        errors["recommended_hazard_description"] = (
            "Describe the hazard you recommend adding to the registry."
        )
    if errors:
        raise FeedbackError("Some answers need attention.", errors)

    if not any(values[field] for field, _label in TEXT_FIELDS) and not any(
        values[field] for field, _label in RATING_FIELDS
    ):
        raise FeedbackError(
            "Add at least one rating or written answer; an empty feedback record helps nobody."
        )

    now = timeutils.now()
    row = MRMFeedback.objects.create(
        mrm=form,
        user_id=user.id,
        risk_assessment_accuracy=values["risk_assessment_accuracy"],
        hazard_identification_rating=values["hazard_identification_rating"],
        mitigation_effectiveness=values["mitigation_effectiveness"],
        what_went_well=values["what_went_well"],
        what_could_be_improved=values["what_could_be_improved"],
        new_hazards_identified=values["new_hazards_identified"],
        lessons_learned=values["lessons_learned"],
        additional_comments=values["additional_comments"],
        recommend_add_to_hazard_registry=recommended,
        recommended_hazard_description=recommended_description,
        recommended_mitigations=recommended_mitigations,
        submitted_at=now,
        is_anonymous=bool(data.get("is_anonymous")),
        status=STATUS_PENDING,
    )

    from apps.core.audit import log_action

    log_action(
        actor=user,
        action="feedback_submit",
        entity_type="mrm_feedback",
        entity_id=row.id,
        after={"mrm": form.id, "is_anonymous": row.is_anonymous},
    )

    _notify_safety_team(form, user, row)
    return row


def _safety_recipients(unit_id):
    """The unit's safety office, including legacy safety-officer accounts."""
    from apps.accounts.models import User
    from apps.command.services import administrators, users_holding_post

    users = list(users_holding_post(unit_id, roles.ROLE_SAFETY_OFFICER))
    users += list(
        User.objects.filter(
            is_active=True, authority=roles.BASE_SAFETY, unit_id=unit_id
        )
    )
    seen = {}
    for user in users:
        seen[user.id] = user
    if not seen:
        for user in administrators():
            seen[user.id] = user
    return list(seen.values())


def _notify_safety_team(form, submitter, row):
    from apps.ops.notifications import notify_many

    note = notify_many(
        _safety_recipients(form.original_unit_id),
        title=f"Post-mission feedback: {form.mrm_number}",
        message=(
            f"{'An anonymous submitter' if row.is_anonymous else _safe_name(submitter)} "
            f"filed post-mission feedback for {form.activity_mission}."
        ),
        level="info",
        category="feedback",
        priority="high",
        related=("mrm_feedback", row.id),
        actor=submitter,
        requires_action=True,
        action_label="Audit the feedback",
        action_url="/feedback",
    )
    return note


# ---------------------------------------------------------------------------
# Auditing
# ---------------------------------------------------------------------------


def _load_for_audit(user, feedback_id) -> MRMFeedback:
    if not can_audit(user):
        raise FeedbackError("Only the Unit Safety Officer may audit post-mission feedback.")
    row = (
        MRMFeedback.objects.select_related("mrm", "user", "reviewed_by")
        .filter(pk=feedback_id)
        .first()
    )
    if row is None:
        raise FeedbackError("That feedback does not exist.")
    if not is_administrator(user):
        ids = audit_unit_ids(user)
        if row.mrm_id is None or row.mrm.original_unit_id not in ids:
            raise FeedbackError("That feedback belongs to a unit you do not audit.")
    return row


def review(user, feedback_id, data: dict) -> MRMFeedback:
    """Record the officer's reading of the feedback."""
    row = _load_for_audit(user, feedback_id)
    status = _text(data.get("status")) or STATUS_REVIEWED
    if status not in REVIEW_STATUSES:
        raise FeedbackError(
            "Choose reviewed, actioned or dismissed."
        )
    notes = _text(data.get("review_notes"))[:20000]
    action = _text(data.get("action_taken"))[:20000]
    if status == STATUS_DISMISSED and not notes:
        raise FeedbackError("Say why the feedback is being dismissed.")

    now = timeutils.now()
    row.status = status
    row.reviewed_by_id = user.id
    row.reviewed_at = now
    row.review_notes = notes
    if action:
        row.action_taken = action
        row.action_taken_by_id = user.id
        row.action_taken_at = now
    row.save()

    from apps.core.audit import log_action

    log_action(
        actor=user,
        action="feedback_review",
        entity_type="mrm_feedback",
        entity_id=row.id,
        after={"status": status, "mrm": row.mrm_id},
        note=notes[:200] if notes else "",
    )

    from apps.ops.notifications import complete_action

    complete_action(
        related_type="mrm_feedback",
        related_ids=[row.id],
        recipients=_safety_recipients(getattr(row.mrm, "original_unit_id", None)),
        note=f"Feedback {STATUS_LABELS.get(status, status).lower()}.",
        actor=user,
    )
    if not row.is_anonymous and row.user_id:
        from apps.ops.notifications import notify

        notify(
            row.user,
            title=f"Your feedback on {getattr(row.mrm, 'mrm_number', 'an MRM')} was audited",
            message=f"{STATUS_LABELS.get(status, status)}"
            + (f": {notes}" if notes else "."),
            category="feedback",
            related=("mrm_feedback", row.id),
            actor=user,
            action_url="/feedback",
        )
    return row


def adopt_into_registry(user, feedback_id, data: dict) -> dict:
    """Add the submitter's recommended hazard to the unit's library.

    This is the loop the whole feature exists for: a crew member reports a
    hazard that was not in the library, and the Safety Officer turns it into a
    maintained row with assessed likelihood, severity and mitigations.
    """
    row = _load_for_audit(user, feedback_id)
    if not row.recommend_add_to_hazard_registry:
        raise FeedbackError("This feedback did not recommend a hazard for the registry.")
    if not (row.recommended_hazard_description or "").strip():
        raise FeedbackError("The feedback has no recommended hazard description to adopt.")

    payload = {
        "unit": getattr(row.mrm, "original_unit_id", None),
        "activity_code": data.get("activity_code"),
        "hazard_description": row.recommended_hazard_description,
        "before_likelihood": data.get("before_likelihood"),
        "before_severity": data.get("before_severity"),
        "mitigations": data.get("mitigations") or row.recommended_mitigations,
        "after_likelihood": data.get("after_likelihood"),
        "after_severity": data.get("after_severity"),
    }
    try:
        hazard = hazard_service.create_hazard(user, payload)
    except hazard_service.HazardError as exc:
        raise FeedbackError(str(exc)) from exc
    except hazard_service.HazardImportError as exc:
        message = "; ".join(exc.errors.values()) if exc.errors else str(exc)
        raise FeedbackError(message) from exc

    now = timeutils.now()
    row.status = STATUS_ACTIONED
    row.reviewed_by_id = user.id
    row.reviewed_at = row.reviewed_at or now
    row.action_taken = f"Added to the hazard registry as hazard #{hazard.id}."
    row.action_taken_by_id = user.id
    row.action_taken_at = now
    row.save()

    from apps.core.audit import log_action

    log_action(
        actor=user,
        action="feedback_adopt_hazard",
        entity_type="mrm_feedback",
        entity_id=row.id,
        after={"hazard": hazard.id, "mrm": row.mrm_id},
    )

    from apps.ops.notifications import complete_action

    complete_action(
        related_type="mrm_feedback",
        related_ids=[row.id],
        recipients=_safety_recipients(getattr(row.mrm, "original_unit_id", None)),
        note="Adopted into the hazard registry.",
        actor=user,
    )
    if not row.is_anonymous and row.user_id:
        from apps.ops.notifications import notify

        notify(
            row.user,
            title=f"Your hazard recommendation was adopted",
            message=(
                f"The hazard you recommended on {getattr(row.mrm, 'mrm_number', 'an MRM')} "
                "is now in the unit hazard registry."
            ),
            category="feedback",
            related=("mrm_feedback", row.id),
            actor=user,
            action_url="/feedback",
        )
    return {"feedback": feedback_payload(row, viewer=user), "hazard": hazard_service.hazard_payload(hazard)}

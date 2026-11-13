"""Instructions and action items: issue → acknowledge → complete → verify (spec §9D).

The module is open to every signed-in account, because an instruction is addressed
to a person who must then acknowledge and complete it. The one restricted view is
*"All visible"* — every instruction in scope rather than the caller's own — which
belongs to the Administrator, the Commander, the Deputy Commander and the unit's
Operations Center office.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.command.models import (
    Instruction,
    InstructionRecipient,
    OfficeSection,
    SectionAssignment,
)
from apps.core.factories import make_unit, make_user
from apps.ops.models import Notification

pytestmark = pytest.mark.django_db


def issue(as_user, commander, unit, recipients, **overrides):
    payload = {
        "unit_id": unit.id,
        "subject": "Correct the risk assessment",
        "body": "Mitigation measures are incomplete; update and resubmit.",
        "recipient_ids": [person.id for person in recipients],
        "priority": "high",
    }
    payload.update(overrides)
    return as_user(commander).post("/api/instructions/", payload, format="json")


def oc_member(unit, authority="Creator"):
    """A member of the unit's Operations Center office (holds the office role)."""
    office = OfficeSection.objects.filter(
        unit=unit, function=OfficeSection.FUNCTION_OPERATIONS_CENTER, is_active=True
    ).first()
    if office is None:
        office = OfficeSection.objects.create(
            unit=unit,
            name="Operations Center",
            code="OPSCTR",
            kind="office",
            function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
        )
    person = make_user(unit=unit, authority=authority)
    SectionAssignment.objects.create(user=person, section=office, assignment_type="primary")
    return person


# ---------------------------------------------------------------------------
# Who may open the "All visible" view
# ---------------------------------------------------------------------------


def test_all_visible_is_reserved_for_command_and_the_operations_center(as_user, commander, admin, unit):
    assert as_user(commander).get("/api/instructions/").status_code == 200
    assert as_user(admin).get("/api/instructions/").status_code == 200
    assert as_user(oc_member(unit)).get("/api/instructions/").status_code == 200


def test_a_deputy_commander_sees_all_visible(as_user, unit):
    from apps.command.models import UnitAppointment

    deputy = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=deputy, role="deputy_commander")

    assert as_user(deputy).get("/api/instructions/").status_code == 200


def test_everyone_else_is_refused_the_all_visible_view(as_user, creator, evaluator, safety):
    for person in (creator, evaluator, safety):
        assert as_user(person).get("/api/instructions/").status_code == 403


def test_a_recipient_keeps_their_own_views(as_user, commander, creator, unit):
    """Restricting "All visible" must not break delivery to the person addressed."""
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    client = as_user(creator)

    outstanding = client.get("/api/instructions/?scope=outstanding").json()
    assert [row["id"] for row in outstanding["results"]] == [instruction_id]
    assert client.get(f"/api/instructions/{instruction_id}/").status_code == 200
    assert (
        client.post(f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json").status_code
        == 200
    )


def test_an_issuer_keeps_their_issued_view(as_user, commander, creator, unit):
    issue(as_user, commander, unit, [creator])

    issued = as_user(commander).get("/api/instructions/?scope=issued").json()
    assert issued["count"] == 1


# ---------------------------------------------------------------------------
# Issuing and the action items it creates
# ---------------------------------------------------------------------------


def test_issuing_an_instruction_creates_a_persistent_action_for_each_recipient(
    as_user, commander, creator, unit
):
    response = issue(as_user, commander, unit, [creator])
    assert response.status_code == 201, response.content
    instruction = Instruction.objects.get(id=response.json()["id"])
    assert instruction.status == Instruction.STATUS_OPEN

    item = Notification.objects.get(
        recipient_id=creator.id,
        related_type="instruction",
        related_id=str(instruction.id),
    )
    assert item.requires_action is True
    assert item.action_label == "Acknowledge"
    assert item.action_status == Notification.ACTION_OPEN
    assert item.created_by_id == commander.id


def test_instruction_requires_at_least_one_recipient(as_user, commander, unit):
    response = issue(as_user, commander, unit, [])
    assert response.status_code == 400


def test_recipient_outside_the_unit_cannot_receive_an_instruction(as_user, commander, unit):
    outsider = make_user(unit=make_unit(name="Charlie", acronym="CHAR"))
    response = issue(as_user, commander, unit, [outsider])
    assert response.status_code == 403


def test_reading_the_notification_does_not_complete_the_instruction(
    as_user, commander, creator, unit
):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    item = Notification.objects.get(recipient_id=creator.id, related_type="instruction")

    as_user(creator).post(f"/api/notifications/{item.id}/mark-read/")
    item.refresh_from_db()
    assert item.is_read is True
    assert item.action_status == Notification.ACTION_OPEN

    recipient = InstructionRecipient.objects.get(instruction_id=instruction_id, user=creator)
    assert recipient.acknowledged_at is None
    assert recipient.completed_at is None


def test_acknowledge_moves_to_in_progress_but_not_complete(as_user, commander, creator, unit):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]

    response = as_user(creator).post(
        f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json"
    )
    assert response.status_code == 200, response.content

    recipient = InstructionRecipient.objects.get(instruction_id=instruction_id, user=creator)
    assert recipient.acknowledged_at is not None
    assert recipient.completed_at is None

    instruction = Instruction.objects.get(id=instruction_id)
    assert instruction.status == Instruction.STATUS_IN_PROGRESS

    item = Notification.objects.get(recipient_id=creator.id, related_type="instruction")
    assert item.action_status == Notification.ACTION_IN_PROGRESS


def test_acknowledgement_notifies_the_sender(as_user, commander, creator, unit):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    as_user(creator).post(
        f"/api/instructions/{instruction_id}/acknowledge/",
        {"note": "Received, working on it."},
        format="json",
    )

    update = Notification.objects.get(
        recipient_id=commander.id,
        related_type="instruction",
        related_id=str(instruction_id),
    )
    assert update.created_by_id == creator.id
    assert "acknowledged" in update.title
    assert update.message == "Received, working on it."


def test_completion_notifies_the_sender_and_asks_for_verification(
    as_user, commander, creator, unit
):
    instruction_id = issue(
        as_user, commander, unit, [creator], requires_completion=True
    ).json()["id"]
    client = as_user(creator)
    client.post(f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json")
    client.post(
        f"/api/instructions/{instruction_id}/complete/",
        {"note": "Corrective action done."},
        format="json",
    )

    # The acknowledgement and the completion each notify the sender.
    updates = Notification.objects.filter(
        recipient_id=commander.id,
        related_type="instruction",
        related_id=str(instruction_id),
    ).order_by("id")
    assert updates.count() == 2
    update = updates.last()
    assert update.created_by_id == creator.id
    assert "completed" in update.title
    assert update.message == "Corrective action done."
    assert update.requires_action is True
    assert update.action_label == "Verify completion"


def test_only_a_recipient_can_acknowledge(as_user, commander, creator, evaluator, unit):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    response = as_user(evaluator).post(
        f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json"
    )
    assert response.status_code in (403, 404)


def test_complete_requires_acknowledgement_first(as_user, commander, creator, unit):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    response = as_user(creator).post(
        f"/api/instructions/{instruction_id}/complete/", {}, format="json"
    )
    assert response.status_code == 400
    assert "Acknowledge" in response.json()["detail"]


def test_completion_closes_the_action_and_is_auditable(as_user, commander, creator, unit):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    client = as_user(creator)
    client.post(f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json")
    response = client.post(
        f"/api/instructions/{instruction_id}/complete/",
        {"note": "Resubmitted with new controls."},
        format="json",
    )
    assert response.status_code == 200, response.content

    recipient = InstructionRecipient.objects.get(instruction_id=instruction_id, user=creator)
    assert recipient.completed_at is not None
    assert recipient.completion_note == "Resubmitted with new controls."

    instruction = Instruction.objects.get(id=instruction_id)
    assert instruction.status == Instruction.STATUS_VERIFIED

    item = Notification.objects.get(recipient_id=creator.id, related_type="instruction")
    assert item.action_status == Notification.ACTION_COMPLETED
    assert item.completed_by_id == creator.id
    assert item.completed_at is not None


def test_verification_waits_until_every_recipient_completes(as_user, commander, creator, unit):
    second = make_user(unit=unit)
    instruction_id = issue(
        as_user, commander, unit, [creator, second], requires_completion=True
    ).json()["id"]
    client = as_user(creator)
    client.post(f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json")
    client.post(f"/api/instructions/{instruction_id}/complete/", {}, format="json")

    premature = as_user(commander).post(
        f"/api/instructions/{instruction_id}/verify/", {}, format="json"
    )
    assert premature.status_code == 400
    assert "not completed" in premature.json()["detail"]

    other = as_user(second)
    other.post(f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json")
    other.post(f"/api/instructions/{instruction_id}/complete/", {}, format="json")

    final = as_user(commander).post(
        f"/api/instructions/{instruction_id}/verify/", {}, format="json"
    )
    assert final.status_code == 200
    assert final.json()["status"] == Instruction.STATUS_VERIFIED


def test_only_the_sender_or_admin_can_verify(as_user, commander, creator, admin, unit):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    client = as_user(creator)
    client.post(f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json")
    client.post(f"/api/instructions/{instruction_id}/complete/", {}, format="json")

    denied = as_user(creator).post(
        f"/api/instructions/{instruction_id}/verify/", {}, format="json"
    )
    assert denied.status_code == 403

    allowed = as_user(admin).post(
        f"/api/instructions/{instruction_id}/verify/", {}, format="json"
    )
    assert allowed.status_code == 200


def test_instructions_are_hidden_from_unrelated_units(as_user, commander, creator, unit):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    outsider = make_user(unit=make_unit(name="Charlie", acronym="CHAR"))

    # Their own scoped views show nothing, and the record is not visible either.
    mine = as_user(outsider).get("/api/instructions/?scope=mine").json()
    assert mine["count"] == 0
    assert as_user(outsider).get(f"/api/instructions/{instruction_id}/").status_code == 404


def test_subordinate_units_personnel_can_receive_an_instruction(as_user, commander, unit):
    subordinate = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    person = make_user(unit=subordinate)
    response = issue(as_user, commander, unit, [person])
    assert response.status_code == 201, response.content


def test_instruction_unit_must_be_own_unit_or_a_subordinate(
    as_user, commander, unit, creator
):
    sister = make_unit(name="Sister Squadron", acronym="SIS")
    response = issue(as_user, commander, sister, [creator])
    assert response.status_code == 403
    assert "subordinate" in response.json()["detail"].lower()


def test_subordinate_unit_is_an_allowed_instruction_unit(as_user, commander, unit):
    subordinate = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    person = make_user(unit=subordinate)
    response = issue(as_user, commander, subordinate, [person])
    assert response.status_code == 201, response.content


def test_administrator_is_limited_to_their_own_unit_tree(as_user, admin, creator):
    sister = make_unit(name="Sister Squadron", acronym="SIS")
    response = issue(as_user, admin, sister, [creator])
    assert response.status_code == 403


def test_instruction_units_lists_only_own_and_subordinate_units(
    as_user, commander, unit
):
    subordinate = make_unit(name="Alpha Detachment", acronym="ALPHA-D", parent=unit)
    make_unit(name="Sister Squadron", acronym="SIS")

    body = as_user(commander).get("/api/instructions/units/").json()
    ids = {row["id"] for row in body["results"]}
    assert ids == {unit.id, subordinate.id}


def complete_with_files(as_user, creator, instruction_id, files):
    client = as_user(creator)
    client.post(f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json")
    return client.post(
        f"/api/instructions/{instruction_id}/complete/",
        {"note": "Evidence attached.", "attachments": files},
        format="multipart",
    )


def test_recipient_can_attach_a_pdf_when_completing(as_user, commander, creator, unit):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    pdf = SimpleUploadedFile(
        "evidence.pdf", b"%PDF-1.4 completion evidence", content_type="application/pdf"
    )

    response = complete_with_files(as_user, creator, instruction_id, [pdf])
    assert response.status_code == 200, response.content

    recipient = InstructionRecipient.objects.get(instruction_id=instruction_id, user=creator)
    attachment = recipient.attachments.get()
    assert attachment.original_name == "evidence.pdf"
    assert attachment.content_type == "application/pdf"

    row = next(item for item in response.json()["recipients"] if item["user"] == creator.id)
    assert [item["original_name"] for item in row["attachments"]] == ["evidence.pdf"]

    download = as_user(creator).get(
        f"/api/instructions/{instruction_id}/attachments/{attachment.id}/"
    )
    assert download.status_code == 200
    assert b"".join(download.streaming_content) == b"%PDF-1.4 completion evidence"


def test_recipient_can_attach_a_picture_when_completing(as_user, commander, creator, unit):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    png = SimpleUploadedFile(
        "photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 24, content_type="image/png"
    )

    response = complete_with_files(as_user, creator, instruction_id, [png])
    assert response.status_code == 200, response.content
    recipient = InstructionRecipient.objects.get(instruction_id=instruction_id, user=creator)
    assert recipient.attachments.get().content_type == "image/png"


def test_other_file_types_are_refused_and_completion_is_not_recorded(
    as_user, commander, creator, unit
):
    instruction_id = issue(as_user, commander, unit, [creator]).json()["id"]
    notes = SimpleUploadedFile("notes.txt", b"plain text", content_type="text/plain")

    response = complete_with_files(as_user, creator, instruction_id, [notes])
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]

    recipient = InstructionRecipient.objects.get(instruction_id=instruction_id, user=creator)
    assert recipient.completed_at is None
    assert not recipient.attachments.exists()


def test_outstanding_scope_lists_only_open_items(as_user, commander, creator, unit):
    open_id = issue(as_user, commander, unit, [creator]).json()["id"]
    done_id = issue(
        as_user, commander, unit, [creator], subject="Already handled"
    ).json()["id"]

    client = as_user(creator)
    for instruction_id in (open_id, done_id):
        client.post(f"/api/instructions/{instruction_id}/acknowledge/", {}, format="json")
        client.post(f"/api/instructions/{instruction_id}/complete/", {}, format="json")

    remaining = client.get("/api/instructions/?scope=outstanding").json()
    ids = {row["id"] for row in remaining["results"]}
    assert open_id not in ids
    assert done_id not in ids

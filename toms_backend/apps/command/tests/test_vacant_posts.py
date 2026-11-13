"""Vacancy notifications: a command post without a holder reaches the bell.

When one of a unit's required posts is vacated - or was never filled - the
people who can fill it are told, and the item closes when somebody is appointed.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.command import services, vacancies
from apps.command.models import UnitAppointment
from apps.core.factories import make_unit, make_user
from apps.ops.models import Notification

pytestmark = pytest.mark.django_db


def appoint(unit, user, role, actor=None):
    return UnitAppointment.objects.create(unit=unit, user=user, role=role, appointed_by=actor)


def vacancy_notifications(**filters):
    return Notification.objects.filter(
        related_type=vacancies.VACANCY_RELATED_TYPE, **filters
    )


def test_ending_a_command_post_notifies_the_people_who_can_fill_it(
    as_user, admin, unit, operations_center, planner
):
    holder = make_user(unit=unit, authority="Creator")
    appointment = appoint(unit, holder, "commander", admin)

    response = as_user(admin).delete(f"/api/appointments/{appointment.id}/")
    assert response.status_code == 200, response.content

    item = vacancy_notifications(recipient=planner, requires_action=True).first()
    assert item is not None
    assert item.title.startswith("Vacant post: Commander")
    assert unit.acronym in item.title
    assert item.action_url == vacancies.APPOINTMENTS_URL
    assert item.action_status == Notification.ACTION_OPEN
    # The bell distinguishes an actionable item from a notice.
    assert item.requires_action is True


def test_administration_is_told_even_when_nobody_else_is_in_scope(admin, unit):
    holder = make_user(unit=unit, authority="Creator")
    appointment = appoint(unit, holder, "deputy_commander")

    services.end_appointment(appointment)

    assert vacancy_notifications(recipient=admin).exists()


def test_the_headquarters_above_the_unit_is_told(admin, unit):
    hq = make_unit(acronym="WING")
    unit.parent = hq
    unit.save(update_fields=["parent"])
    wing_commander = make_user(unit=hq, authority="Creator")
    appoint(hq, wing_commander, "commander")

    appointment = appoint(unit, make_user(unit=unit), "director_operations")
    services.end_appointment(appointment)

    assert vacancy_notifications(recipient=wing_commander).exists()


def test_appointing_somebody_closes_the_vacancy(as_user, admin, unit, operations_center, planner):
    appointment = appoint(unit, make_user(unit=unit), "commander", admin)
    services.end_appointment(appointment)
    item = vacancy_notifications(recipient=planner).first()
    assert item.action_status == Notification.ACTION_OPEN

    replacement = make_user(unit=unit, authority="Creator")
    response = as_user(admin).post(
        "/api/appointments/",
        {"unit_id": unit.id, "user_id": replacement.id, "role": "commander"},
        format="json",
    )
    assert response.status_code == 201, response.content

    item.refresh_from_db()
    assert item.action_status == Notification.ACTION_COMPLETED
    assert item.completed_at is not None


def test_a_shared_post_only_notifies_when_the_last_holder_leaves(admin, unit):
    first = appoint(unit, make_user(unit=unit), "safety_officer")
    second = appoint(unit, make_user(unit=unit), "safety_officer")

    services.end_appointment(first)
    assert vacancy_notifications().count() == 0

    services.end_appointment(second)
    assert vacancy_notifications().exists()


def test_a_post_outside_the_required_list_is_not_a_vacancy(admin, unit):
    appointment = appoint(unit, make_user(unit=unit), "admin_officer")

    services.end_appointment(appointment)

    assert vacancy_notifications().count() == 0


def test_the_units_admin_officer_is_told(admin, unit):
    officer = make_user(unit=unit, authority="Creator")
    appoint(unit, officer, "admin_officer")

    appointment = appoint(unit, make_user(unit=unit), "commander")
    services.end_appointment(appointment)

    item = vacancy_notifications(recipient=officer).first()
    assert item is not None
    assert item.title.startswith("Vacant post: Commander")
    assert item.action_url == vacancies.APPOINTMENTS_URL


def test_an_open_vacancy_reaches_a_newly_appointed_admin_officer(admin, unit):
    call_command("check_vacant_posts", unit=unit.id)
    before = vacancy_notifications().count()

    officer = make_user(unit=unit, authority="Creator")
    appoint(unit, officer, "admin_officer")
    call_command("check_vacant_posts", unit=unit.id)

    assert vacancy_notifications(recipient=officer).count() == len(vacancies.REQUIRED_POSTS)
    after = before + len(vacancies.REQUIRED_POSTS)
    assert vacancy_notifications().count() == after

    # Existing recipients are not told a second time.
    call_command("check_vacant_posts", unit=unit.id)
    assert vacancy_notifications().count() == after


def test_the_check_finds_every_gap_once(admin, unit, operations_center, planner):
    call_command("check_vacant_posts", unit=unit.id)

    # Five required posts, two recipients who can fill them (the administrator
    # and the Operations Center member).
    first = vacancy_notifications().count()
    assert first == len(vacancies.REQUIRED_POSTS) * 2
    assert set(vacancy_notifications().values_list("recipient_id", flat=True)) == {
        admin.id,
        planner.id,
    }

    call_command("check_vacant_posts", unit=unit.id)
    assert vacancy_notifications().count() == first


def test_the_dry_run_notifies_nobody(unit):
    call_command("check_vacant_posts", unit=unit.id, dry_run=True)

    assert vacancy_notifications().count() == 0

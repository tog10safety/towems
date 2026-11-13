"""Vacant unit posts and who is told about them.

A unit's posts are ``UnitAppointment`` rows. When one of the posts a unit is
expected to keep filled loses its holder — the appointment was ended, the
person was reassigned, or the unit was stood up without one — the people who
can fill it are told through the notification bell. The work item stays open
until somebody is appointed, then the appointment closes it.

One open item per unit and post: re-running the check cannot flood a bell.
"""

from __future__ import annotations

from apps.core.roles import (
    ROLE_ADMIN_OFFICER,
    ROLE_COMMANDER,
    ROLE_DEPUTY_COMMANDER,
    ROLE_DIRECTOR_OPERATIONS,
    ROLE_LABELS,
    ROLE_OPERATIONS_CENTER,
    ROLE_OPERATIONS_OFFICER,
    ROLE_SAFETY_OFFICER,
)

#: Posts a unit is expected to keep filled. The list is deliberately short:
#: these carry standing responsibility for the unit's operations.
REQUIRED_POSTS = (
    ROLE_COMMANDER,
    ROLE_DEPUTY_COMMANDER,
    ROLE_DIRECTOR_OPERATIONS,
    ROLE_OPERATIONS_OFFICER,
    ROLE_SAFETY_OFFICER,
)

#: ``Notification.related_type`` for a vacancy, so filling the post can close it.
VACANCY_RELATED_TYPE = "unit_vacancy"

#: Where the recipient goes to fill the post.
APPOINTMENTS_URL = "/admin/users"


def vacancy_key(unit_id, role) -> str:
    """``unit_vacancy`` related id: one per unit and post."""
    return f"{unit_id}:{role}"


def has_holder(unit_id, role) -> bool:
    from apps.command.models import UnitAppointment

    return UnitAppointment.objects.filter(
        unit_id=unit_id, role=role, is_active=True
    ).exists()


def vacant_posts(unit) -> list[str]:
    """The required posts of ``unit`` with no active holder."""
    if unit is None:
        return []
    return [role for role in REQUIRED_POSTS if not has_holder(unit.id, role)]


def vacancy_recipients(unit):
    """Who is told: administration, the Admin Officer, the Operations Center, command.

    Command is read through the unit's ancestors as well, so a flight without a
    commander still reaches the wing above it. The Admin Officer is the unit's
    own: they keep the personnel records and office/section assignments that a
    new appointment is made from.
    """
    from apps.accounts.models import User
    from apps.command.models import UnitAppointment
    from apps.command.services import administrators, users_holding_post

    people = set(
        users_holding_post(
            unit.id, ROLE_COMMANDER, ROLE_DEPUTY_COMMANDER, ROLE_DIRECTOR_OPERATIONS
        )
    )
    admin_officers = UnitAppointment.objects.filter(
        is_active=True, role=ROLE_ADMIN_OFFICER, unit_id=unit.id
    ).values("user_id")
    people |= set(User.objects.filter(id__in=admin_officers))
    # The Operations Center is an office, so its members come from membership
    # rows for this unit only.
    people |= set(users_holding_post(unit.id, ROLE_OPERATIONS_CENTER))
    people |= set(administrators())
    return [person for person in people if getattr(person, "is_active", True)]


def _notified_recipient_ids(unit_id, role) -> set[int]:
    """Who already holds an open item for this vacancy."""
    from apps.ops.models import Notification

    return set(
        Notification.objects.filter(
            related_type=VACANCY_RELATED_TYPE,
            related_id=vacancy_key(unit_id, role),
            requires_action=True,
            action_status__in=[Notification.ACTION_OPEN, Notification.ACTION_IN_PROGRESS],
        ).values_list("recipient_id", flat=True)
    )


def notify_vacancy(unit, role, actor=None):
    """Tell the people who can fill ``role`` that ``unit`` has no holder.

    Returns the notifications created. Nobody who already has an open item is
    told again, but somebody who became eligible later - the unit's newly
    appointed Admin Officer, say - still receives one.
    """
    from apps.ops.notifications import notify_many

    if unit is None or role not in REQUIRED_POSTS:
        return []

    already = _notified_recipient_ids(unit.id, role)
    recipients = [
        person for person in vacancy_recipients(unit) if person.id not in already
    ]
    if not recipients:
        return []

    label = ROLE_LABELS.get(role, role)
    return notify_many(
        recipients,
        title=f"Vacant post: {label} - {unit.acronym}",
        message=(
            f"{label} of {unit.name} ({unit.acronym}) has no active holder. "
            "Appoint somebody to the post, or assign an acting appointment until it is filled."
        ),
        level="warning",
        category="appointment",
        related=(VACANCY_RELATED_TYPE, vacancy_key(unit.id, role)),
        actor=actor,
        priority="high",
        requires_action=True,
        action_label=f"Fill {label}",
        action_url=APPOINTMENTS_URL,
    )


def notify_if_vacant(unit, role, actor=None):
    """Notify only when the post really has no holder left.

    Ending one of two safety officers, for instance, is not a vacancy.
    """
    if unit is None or role not in REQUIRED_POSTS:
        return []
    if has_holder(unit.id, role):
        return []
    return notify_vacancy(unit, role, actor=actor)


def notify_vacancies(unit, roles=None, actor=None):
    """Notify for every vacant post of ``unit`` (or the given ``roles``)."""
    targets = list(roles) if roles is not None else vacant_posts(unit)
    created = []
    for role in targets:
        created.extend(notify_vacancy(unit, role, actor=actor))
    return created


def clear_vacancy(unit, role, actor=None, note=""):
    """Close the vacancy item once the post has a holder."""
    from apps.ops.notifications import complete_action

    if unit is None or role not in REQUIRED_POSTS:
        return 0
    label = ROLE_LABELS.get(role, role)
    return complete_action(
        related_type=VACANCY_RELATED_TYPE,
        related_ids=[vacancy_key(unit.id, role)],
        note=note or f"{label} filled.",
        actor=actor,
    )

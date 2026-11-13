"""Telling another unit when a mission enters its AOR.

A mission is normally plotted inside its own unit's area. When the Operations
Center pins an operational area outside that area, the unit owning the
destination AOR has to know. Only that unit's Operations Center is notified,
and only it monitors the movement: the office reads the mission details and
then decides whether to forward them to its Commander, Deputy Commander,
Director for Operations and Operations Officer.

The check treats the mission unit's command chain as "inside": a squadron
plotting inside its group's AOR is not a handoff, and neither is a group
operating inside a subordinate squadron's area. Only an unrelated unit's area
counts as a destination.

The mission itself stays with its owning unit. The destination Operations
Center gets read access so it can watch the mission; changing it is still the
owning unit's work. Forwarded command posts may read the mission too, but the
live movement board stays with the Operations Center.
"""

from __future__ import annotations

from apps.core import geo, roles
from apps.ops.models import AirMissionDetail, AreaOfResponsibility, Notification

HANDOFF_CATEGORY = "aor_handoff"
FORWARD_CATEGORY = "aor_handoff_forward"

#: The destination unit's office that is told and monitors the movement.
DESTINATION_POSTS = (roles.ROLE_OPERATIONS_CENTER,)
DESTINATION_POST_LABELS = [roles.ROLE_LABELS[role] for role in DESTINATION_POSTS]

#: The destination unit's command posts the Operations Center may forward to.
FORWARD_POSTS = (
    roles.ROLE_COMMANDER,
    roles.ROLE_DEPUTY_COMMANDER,
    roles.ROLE_DIRECTOR_OPERATIONS,
    roles.ROLE_OPERATIONS_OFFICER,
)
FORWARD_POST_LABELS = [roles.ROLE_LABELS[role] for role in FORWARD_POSTS]


def covering_aor(latitude, longitude):
    """The active AOR polygon containing the point, if there is one."""
    if latitude is None or longitude is None:
        return None
    candidates = (
        AreaOfResponsibility.objects.filter(
            is_active=True,
            min_lat__lte=latitude,
            max_lat__gte=latitude,
            min_lon__lte=longitude,
            max_lon__gte=longitude,
        )
        .select_related("unit")
        .order_by("id")
    )
    for row in candidates:
        try:
            if geo.point_in_geometry(longitude, latitude, row.geometry):
                return row
        except geo.GeometryError:
            continue
    return None


def _command_chain_ids(unit) -> set[int]:
    """The unit, its superiors, and its active subordinates."""
    from apps.command.services import ancestor_ids, descendant_ids

    if unit is None:
        return set()
    return set(ancestor_ids(unit.id)) | set(descendant_ids(unit.id))


def _operations_center_users(unit_id):
    """Active members of one unit's Operations Center office.

    The Operations Center is an office, not a personal post, so its members
    are the people assigned to the office. A unit-level ``operations_center``
    appointment is accepted as well, but never an appointment held at a parent
    command: the destination unit's own office is what is notified.
    """
    from apps.accounts.models import User
    from apps.command.models import OfficeSection, SectionAssignment, UnitAppointment

    if unit_id is None:
        return User.objects.none()

    member_ids = set(
        SectionAssignment.objects.filter(
            is_active=True,
            section__is_active=True,
            section__unit_id=unit_id,
            section__function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
        ).values_list("user_id", flat=True)
    )
    member_ids.update(
        UnitAppointment.objects.filter(
            is_active=True,
            unit_id=unit_id,
            role=roles.ROLE_OPERATIONS_CENTER,
        ).values_list("user_id", flat=True)
    )
    if not member_ids:
        return User.objects.none()
    return User.objects.filter(id__in=member_ids, is_active=True)


def command_recipients(unit_id):
    """One row per command post of ``unit_id`` that has an active holder."""
    from apps.command.models import UnitAppointment

    if unit_id is None:
        return []

    rows = (
        UnitAppointment.objects.filter(
            unit_id=unit_id, role__in=FORWARD_POSTS, is_active=True
        )
        .select_related("user")
        .order_by("id")
    )
    held = {}
    seen = set()
    for row in rows:
        person = row.user
        if person is None or not person.is_active or person.id in seen:
            continue
        seen.add(person.id)
        held[row.role] = person

    return [
        {
            "id": held[role].id,
            "full_name": held[role].full_name,
            "serial_number": held[role].serial_number,
            "role": role,
            "post": roles.ROLE_LABELS[role],
        }
        for role in FORWARD_POSTS
        if role in held
    ]


def destination(unit, latitude, longitude):
    """Describe the AOR owner that must be told, or ``None`` when there is none.

    ``None`` covers the ordinary cases as well as the exceptional one: the pin
    is inside the unit's own command area, no AOR covers the point, or the AOR
    has no owning unit. The caller shows the notification prompt only for a
    dictionary, so no prompt ever appears without a unit to notify.
    """
    aor = covering_aor(latitude, longitude)
    if aor is None or aor.unit_id is None or aor.unit is None:
        return None
    if aor.unit_id in _command_chain_ids(unit):
        return None

    recipients = sorted(
        _operations_center_users(aor.unit_id),
        key=lambda person: (person.full_name or "", person.id),
    )
    return {
        "unit_id": aor.unit_id,
        "unit_name": aor.unit.name,
        "unit_acronym": aor.unit.acronym,
        "aor_id": aor.id,
        "aor_name": aor.name,
        "posts": DESTINATION_POST_LABELS,
        "recipients": [
            {
                "id": person.id,
                "full_name": person.full_name,
                "serial_number": person.serial_number,
            }
            for person in recipients
        ],
        "recipient_count": len(recipients),
    }


def shared_mission_ids(user) -> list[int]:
    """Missions this user may read because of an AOR handoff.

    The destination Operations Center is a recipient of the handoff itself;
    the command posts it later forwards to are recipients of the forward.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return []
    values = Notification.objects.filter(
        recipient_id=user.id,
        category__in=[HANDOFF_CATEGORY, FORWARD_CATEGORY],
        related_type="mission",
    ).values_list("related_id", flat=True)
    return sorted({int(value) for value in values if str(value).isdigit()})


def _window(mission) -> str:
    def stamp(value) -> str:
        return value.strftime("%d %b %Y %H:%M") if value else ""

    start, end = stamp(mission.start_at), stamp(mission.end_at)
    if start and end:
        return f"{start} – {end} (PST)"
    if start:
        return f"from {start} (PST)"
    if end:
        return f"until {end} (PST)"
    return "not scheduled"


def _details(mission, destination_row) -> str:
    """The complete picture the destination unit needs to monitor the mission."""
    unit = mission.unit
    unit_label = (
        f"{unit.acronym} — {unit.name}" if unit is not None else "unit not recorded"
    )
    coordinates = ""
    if mission.latitude is not None and mission.longitude is not None:
        coordinates = f"{mission.latitude:.6f}, {mission.longitude:.6f}"
        if mission.mgrs:
            coordinates = f"{coordinates} · MGRS {mission.mgrs}"

    personnel = mission.personnel.filter(is_active=True).count()
    vehicles = mission.trips.exclude(status="cancelled").count()

    rows = [
        mission.title,
        f"{mission.get_category_display()} · {unit_label}",
        f"Area: {mission.location_name or 'not named'}",
        f"Coordinates: {coordinates or 'not pinned'}",
        f"Window: {_window(mission)}",
    ]
    if mission.office_section:
        rows.append(f"Office/Section: {mission.office_section}")
    if mission.purpose:
        purpose = mission.purpose.strip()
        rows.append(
            "Purpose: " + (purpose[:280] + "…" if len(purpose) > 280 else purpose)
        )
    air = (
        AirMissionDetail.objects.filter(mission=mission)
        .only("aircraft_type", "aircraft_tail_number", "flight_identification")
        .first()
    )
    if air is not None:
        aircraft = " ".join(
            part
            for part in (
                air.aircraft_type,
                air.aircraft_tail_number,
                f"({air.flight_identification})" if air.flight_identification else "",
            )
            if part
        )
        if aircraft:
            rows.append(f"Aircraft: {aircraft}")
    rows.extend(
        [
            f"Personnel: {personnel} · Vehicles: {vehicles}",
            (
                f"The operational area falls in {destination_row['unit_acronym']} AOR "
                f"({destination_row['aor_name']})."
            ),
        ]
    )
    return "\n".join(rows)


def notify_destination(mission, actor=None):
    """Notify the destination Operations Center once per mission and recipient.

    Returns the destination description, how many notifications were created
    and how many recipients already had one, so a repeated save is safe and the
    caller can report the real result.
    """
    from apps.ops.notifications import notify_many

    destination_row = destination(
        mission.unit, mission.latitude, mission.longitude
    )
    if destination_row is None:
        return {"notified": 0, "already_notified": 0, "destination": None}

    recipients = list(_operations_center_users(destination_row["unit_id"]))
    recipient_ids = [person.id for person in recipients]
    already = set(
        Notification.objects.filter(
            category=HANDOFF_CATEGORY,
            related_type="mission",
            related_id=str(mission.id),
            recipient_id__in=recipient_ids,
        ).values_list("recipient_id", flat=True)
    )
    fresh = [person for person in recipients if person.id not in already]

    created = notify_many(
        fresh,
        title=f"Mission {mission.mission_number} in your AOR",
        message=(
            _details(mission, destination_row)
            + "\n\nYour Operations Center is monitoring this mission. Forward the "
            "details to your Commander, Deputy Commander, Director for Operations "
            "and Operations Officer from the mission page if they need them."
        ),
        category=HANDOFF_CATEGORY,
        priority="high",
        related=("mission", mission.id),
        actor=actor,
    )

    destination_row["recipient_count"] = len(recipients)
    destination_row["recipients"] = [
        {
            "id": person.id,
            "full_name": person.full_name,
            "serial_number": person.serial_number,
        }
        for person in recipients
    ]
    return {
        "notified": len(created),
        "already_notified": len(already),
        "destination": destination_row,
    }


def _forwarded_ids(mission) -> set[int]:
    return set(
        Notification.objects.filter(
            category=FORWARD_CATEGORY,
            related_type="mission",
            related_id=str(mission.id),
        ).values_list("recipient_id", flat=True)
    )


def can_forward(mission, user) -> bool:
    """Only the destination unit's Operations Center may forward the details."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    destination_row = destination(
        mission.unit, mission.latitude, mission.longitude
    )
    if destination_row is None:
        return False
    return user.id in {person["id"] for person in destination_row["recipients"]}


def status(mission, user):
    """The handoff panel: destination, forwardable posts, and who has them."""
    destination_row = destination(
        mission.unit, mission.latitude, mission.longitude
    )
    if destination_row is None:
        return {
            "destination": None,
            "can_forward": False,
            "recipients": [],
            "forwarded": [],
        }

    recipients = command_recipients(destination_row["unit_id"])
    forwarded = _forwarded_ids(mission)
    center_ids = {person["id"] for person in destination_row["recipients"]}
    return {
        "destination": {
            "unit_id": destination_row["unit_id"],
            "unit_name": destination_row["unit_name"],
            "unit_acronym": destination_row["unit_acronym"],
            "aor_id": destination_row["aor_id"],
            "aor_name": destination_row["aor_name"],
            "operations_center": destination_row["recipients"],
        },
        "can_forward": bool(user is not None and user.id in center_ids),
        "recipients": recipients,
        "forwarded": [row for row in recipients if row["id"] in forwarded],
    }


def forward(mission, actor=None):
    """Forward the mission details to the destination unit's command posts.

    Repeating the forward is safe: a post that already received the details is
    counted as already forwarded and is not notified again.
    """
    from apps.accounts.models import User
    from apps.ops.notifications import notify_many

    destination_row = destination(
        mission.unit, mission.latitude, mission.longitude
    )
    if destination_row is None:
        return {
            "forwarded": 0,
            "already_forwarded": 0,
            "recipients": [],
            "destination": None,
        }

    recipients = command_recipients(destination_row["unit_id"])
    already = _forwarded_ids(mission)
    fresh = [row for row in recipients if row["id"] not in already]

    if fresh:
        people = list(User.objects.filter(id__in=[row["id"] for row in fresh]))
        notify_many(
            people,
            title=f"Mission {mission.mission_number} in your AOR",
            message=(
                _details(mission, destination_row)
                + f"\n\nForwarded by the {destination_row['unit_acronym']} "
                "Operations Center"
                + (f" ({actor.full_name})." if actor is not None else ".")
            ),
            category=FORWARD_CATEGORY,
            priority="high",
            related=("mission", mission.id),
            actor=actor,
        )

    return {
        "forwarded": len(fresh),
        "already_forwarded": len(recipients) - len(fresh),
        "recipients": recipients,
        "destination": destination_row,
    }

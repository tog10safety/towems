"""Seed the local Playwright smoke account and its Air Operations fixture.

This command is deliberately local-only. It writes to ``toms_staging`` and
refuses any other database, so a developer cannot accidentally create the
test account in production. Re-running it is safe: the account, office
membership and mission are updated in place.

The account is a Creator with active membership of the unit's Operations
Center, which gives it the Air Operations planning/view permissions without
requiring MFA (MFA applies to Administrators and Commanders only).

The mission is deterministic: planned ETD 06:00, actual takeoff 03:00, so the
board must show the "actual takeoff is 3 h before the planned ETD" warning.
"""

from __future__ import annotations

import os
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.accounts.models import User, UserMFA, UserProfile
from apps.command.models import OfficeSection, SectionAssignment
from apps.core import timeutils
from apps.legacy.models import Unit
from apps.ops.models import (
    AirMissionAircraft,
    AirMissionCrew,
    AirMissionDetail,
    AirMissionElement,
    AirMissionItinerary,
    Mission,
)

DEFAULT_USERNAME = "playwright.smoke"
DEFAULT_PASSWORD = "Playwright!2026"
UNIT_NAME = "Playwright Smoke Unit"
UNIT_ACRONYM = "PW-SMK"
OFFICE_CODE = "PW-OPS"
MISSION_NUMBER = "PW-SMOKE-AO-001"
OPERATION_DATE = date(2099, 11, 11)


class Command(BaseCommand):
    help = "Seed the local Playwright smoke account and Air Operations timing fixture."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=os.environ.get("TOWEMS_E2E_USERNAME", DEFAULT_USERNAME),
        )
        parser.add_argument(
            "--password",
            default=os.environ.get("TOWEMS_E2E_PASSWORD", DEFAULT_PASSWORD),
        )

    def handle(self, *args, **options):
        if connection.settings_dict["NAME"] != "toms_staging":
            raise CommandError(
                "Refusing to seed Playwright data outside the local toms_staging database."
            )

        username = options["username"].strip()
        password = options["password"]
        if not username or not password:
            raise CommandError("Both username and password are required.")

        with transaction.atomic():
            unit = self._unit()
            user = self._user(unit, username, password)
            office = self._office(unit, user)
            self._membership(user, office)
            self._mission(unit, user)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded Playwright account @{username} and mission {MISSION_NUMBER} "
                f"for {OPERATION_DATE.isoformat()}."
            )
        )

    def _unit(self) -> Unit:
        unit, _created = Unit.objects.update_or_create(
            name=UNIT_NAME,
            defaults={
                "acronym": UNIT_ACRONYM,
                "category": "Squadron",
                "location": "Playwright Local Base",
                "is_active": True,
                "date_created": timeutils.now(),
            },
        )
        return unit

    def _user(self, unit: Unit, username: str, password: str) -> User:
        user = User.objects.filter(username=username).first()
        if user is None:
            user = User(username=username, serial_number="PW-SMOKE-001")
        user.authority = "Creator"
        user.category = "Officer"
        user.rank = "Test"
        user.designation = "Playwright Smoke"
        user.first_name = "Playwright"
        user.middle_name = ""
        user.last_name = "Smoke"
        user.branch_of_service = "PAF"
        user.unit = unit
        user.unit_legacy = unit.name
        user.contact_number = ""
        user.is_active = True
        user.force_password_change = False
        user.password_expiry_notified = False
        user.set_password(password)
        user.save()

        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                "onboarding_required": False,
                "onboarding_completed_at": timeutils.now(),
                "theme_preference": "light",
            },
        )
        # The smoke account must not be caught by the privileged MFA policy if
        # a previous seed or manual test enrolled it.
        UserMFA.objects.filter(pk=user.pk).delete()
        return user

    def _office(self, unit: Unit, user: User) -> OfficeSection:
        # A unit has at most one active Operations Center. The smoke unit is
        # dedicated to this command, but make the seed idempotent if an older
        # row used a different code.
        OfficeSection.objects.filter(
            unit=unit,
            function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
            is_active=True,
        ).exclude(code=OFFICE_CODE).update(is_active=False)

        office, _created = OfficeSection.objects.update_or_create(
            unit=unit,
            code=OFFICE_CODE,
            defaults={
                "name": "Playwright Operations Center",
                "kind": "office",
                "function": OfficeSection.FUNCTION_OPERATIONS_CENTER,
                "is_active": True,
                "is_custom": True,
                "created_by": user,
            },
        )
        return office

    def _membership(self, user: User, office: OfficeSection) -> None:
        SectionAssignment.objects.filter(
            user=user, is_active=True
        ).exclude(section=office).update(
            is_active=False,
            ended_at=timeutils.now(),
        )
        SectionAssignment.objects.update_or_create(
            user=user,
            section=office,
            assignment_type=SectionAssignment.TYPE_PRIMARY,
            defaults={"is_active": True, "assigned_by": user},
        )

    def _mission(self, unit: Unit, user: User) -> Mission:
        departure = datetime(2099, 11, 11, 6, 0)
        arrival = datetime(2099, 11, 11, 7, 30)
        actual_departure = datetime(2099, 11, 11, 3, 0)
        actual_arrival = datetime(2099, 11, 11, 7, 35)

        mission, _created = Mission.objects.update_or_create(
            mission_number=MISSION_NUMBER,
            defaults={
                "category": Mission.CATEGORY_AIR,
                "title": "Playwright timing warning",
                "purpose": "Deterministic Air Operations smoke fixture",
                "unit": unit,
                "office_section": OFFICE_CODE,
                "status": "planned",
                "is_held": False,
                "held_at": None,
                "depends_on": None,
                "start_at": departure,
                "end_at": arrival,
                "dispatched_at": None,
                "completed_at": None,
                "location_name": "Playwright Local Base",
                "created_by": user,
                "remarks": "Seeded by seed_playwright for browser smoke tests.",
            },
        )

        # Rebuild the nested flight sheet so repeated runs cannot accumulate
        # duplicate elements, aircraft or legs.
        AirMissionDetail.objects.filter(mission=mission).delete()
        detail = AirMissionDetail.objects.create(
            mission=mission,
            operation_date=OPERATION_DATE,
            mission_configuration="single",
            aircraft_count=1,
            aircraft_type="C-208",
            aircraft_tail_number="PW-01",
            flight_identification="PW-01",
            departure_location="Playwright Local Base",
            destination_location="Test Field",
            route="SMOKE – TEST",
            scheduled_departure=departure,
            scheduled_arrival=arrival,
        )
        element = AirMissionElement.objects.create(
            air_detail=detail,
            sequence=1,
            mission="Playwright Air Operations smoke mission.",
        )
        aircraft = AirMissionAircraft.objects.create(
            element=element,
            sequence=1,
            aircraft_type="C-208",
            tail_number="PW-01",
            callsign="SMOKE 01",
            is_lead=True,
        )
        AirMissionCrew.objects.create(
            aircraft=aircraft,
            sequence=1,
            role=AirMissionCrew.ROLE_PIC,
            name="Playwright Smoke",
        )
        AirMissionItinerary.objects.create(
            aircraft=aircraft,
            sequence=1,
            etd=departure,
            eta=arrival,
            atd=actual_departure,
            ata=actual_arrival,
            status=AirMissionItinerary.STATUS_LANDED,
            origin="Playwright Local Base",
            destination="Test Field",
            route="SMOKE – TEST",
            mission="Playwright Air Operations smoke mission.",
        )
        return mission

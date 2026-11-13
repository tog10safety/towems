"""Notify command, the Admin Officer and administrators about vacant unit posts.

An appointment can end without anybody noticing - a unit is stood up, a holder
retires, a transfer is recorded elsewhere. Running this daily (and after any
establishment change) puts every gap in front of the people who can fill it.
Re-running it is safe: an open item is never duplicated.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.command import vacancies
from apps.command.services import CommandError
from apps.legacy.models import Unit


class Command(BaseCommand):
    help = (
        "Notify command, the unit's Admin Officer and administrators about "
        "vacant required posts."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--unit",
            type=int,
            help="Check one unit id instead of every active unit.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the vacant posts without notifying anybody.",
        )

    def handle(self, *args, **options):
        units = Unit.objects.filter(is_active=True).order_by("id")
        if options.get("unit"):
            units = units.filter(id=options["unit"])
            if not units.exists():
                raise CommandError(f"No active unit with id {options['unit']}.")

        dry_run = options.get("dry_run")
        created = 0
        vacancies_found = 0

        for unit in units:
            for role in vacancies.vacant_posts(unit):
                vacancies_found += 1
                label = role.replace("_", " ")
                if dry_run:
                    self.stdout.write(f"{unit.acronym}: {label} is vacant")
                    continue
                made = vacancies.notify_vacancy(unit, role)
                created += len(made)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{unit.acronym}: {label} is vacant - {len(made)} notified"
                    )
                )

        if dry_run:
            self.stdout.write(f"{vacancies_found} vacant post(s) found.")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"{vacancies_found} vacant post(s); {created} notification(s) created."
            )
        )

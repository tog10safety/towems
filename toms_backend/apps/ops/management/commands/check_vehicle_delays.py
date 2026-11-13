"""Report vehicles that have fallen behind their plan.

The system already notices lateness when a driver's phone reports, when a
movement event is recorded, and when a monitor opens the board. This command is
the scheduled backstop for a vehicle whose phone is off: run it from cron (or
the Windows Task Scheduler) every ten minutes.

    python manage.py check_vehicle_delays
    python manage.py check_vehicle_delays --mission TOWEMS-GO-20261030-001
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.ops.models import Mission
from apps.ops.tracking import DELAY_GRACE_MINUTES, alert_delays


class Command(BaseCommand):
    help = "Notify monitoring roles about vehicles running behind their plan."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mission",
            default="",
            help="Only this mission number (default: every active mission).",
        )

    def handle(self, *args, **options):
        missions = Mission.objects.filter(status="active")
        if options["mission"]:
            missions = missions.filter(mission_number=options["mission"])
        missions = missions.order_by("mission_number")

        total = 0
        for mission in missions:
            created = alert_delays(mission)
            total += created
            if created:
                self.stdout.write(
                    f"{mission.mission_number}: {created} alert(s) sent."
                )
        self.stdout.write(
            self.style.SUCCESS(
                f"Checked {missions.count()} active mission(s); {total} alert(s) "
                f"sent (grace {DELAY_GRACE_MINUTES} min)."
            )
        )

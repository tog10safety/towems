"""Deactivate accounts with 30 days of no authenticated activity.

Run daily (for example from the deployment's cron or systemd timer).  The
command uses the same revocation path as the Administrator action, so a
deactivated account loses its web sessions, Android refresh chain and alert
tokens immediately.
"""

from django.core.management.base import BaseCommand

from apps.accounts import security


class Command(BaseCommand):
    help = "Deactivate active accounts idle for ACCOUNT_INACTIVITY_DAYS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the accounts that would be deactivated without changing them.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            from apps.accounts.models import User
            from apps.core.permissions import ADMIN
            from apps.accounts.ownership import is_system_owner

            cutoff = timeutils_cutoff()
            rows = User.objects.filter(is_active=True).select_related("profile")
            for user in rows:
                if is_system_owner(user) or (
                    user.authority == ADMIN
                    and User.objects.filter(authority=ADMIN, is_active=True).count() <= 1
                ):
                    continue
                profile = getattr(user, "profile", None)
                # Background polling updates last_seen, but only an actual
                # person using the web or Android app advances this clock.
                last = (
                    getattr(profile, "security_last_active_at", None)
                    or user.date_created
                )
                if last is not None and last <= cutoff:
                    self.stdout.write(f"would deactivate {user.username} (last {last})")
            return

        ids = security.deactivate_inactive_accounts()
        if ids:
            self.stdout.write(
                self.style.WARNING(
                    f"Deactivated {len(ids)} inactive account(s): {', '.join(map(str, ids))}"
                )
            )
        else:
            self.stdout.write("No inactive accounts to deactivate.")


def timeutils_cutoff():
    from django.utils import timezone

    return timezone.now() - security.account_inactivity_limit()

"""Force every active account through the first-sign-in profile refresh.

Run once, in the maintenance window, after ``migrate`` and before TOWEMS starts
serving::

    python manage.py require_profile_refresh --dry-run
    python manage.py require_profile_refresh

Each account is then asked, on its next sign-in, for a new password, a picture,
a signature and a Google (Gmail) account, and straight afterwards for Google
Authenticator enrolment. ``--clear`` releases the prompt for accounts that have
not yet signed in (it does not hand back the old passwords).

This is a data step only: it writes ``accounts_user_profile`` (additive) and the
existing ``users.force_password_change`` column. No DDL, and the legacy table's
schema is untouched.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User, UserProfile


class Command(BaseCommand):
    help = (
        "Ask every active account to refresh its password, picture, signature "
        "and Gmail address on its next sign-in (Google Authenticator enrolment "
        "follows immediately)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Release the prompt instead of setting it (passwords are kept).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        users = User.objects.filter(is_active=True)
        total = users.count()

        if options["clear"]:
            if options["dry_run"]:
                self.stdout.write(
                    f"Would release the profile refresh for {total} active account(s)."
                )
                return
            released = UserProfile.objects.filter(user__in=users).update(
                onboarding_required=False
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Released the profile refresh for {released} of {total} "
                    "active account(s)."
                )
            )
            return

        if options["dry_run"]:
            outstanding = UserProfile.objects.filter(
                user__in=users, onboarding_required=True
            ).count()
            self.stdout.write(
                f"Would require the profile refresh for {total} active account(s); "
                f"{outstanding} already flagged."
            )
            return

        with transaction.atomic():
            user_ids = list(users.values_list("id", flat=True))
            existing = set(
                UserProfile.objects.filter(user_id__in=user_ids).values_list(
                    "user_id", flat=True
                )
            )
            UserProfile.objects.bulk_create(
                [
                    UserProfile(user_id=user_id, onboarding_required=True)
                    for user_id in user_ids
                    if user_id not in existing
                ],
                ignore_conflicts=True,
            )
            flagged = UserProfile.objects.filter(user_id__in=user_ids).update(
                onboarding_required=True, onboarding_completed_at=None
            )
            passwords = User.objects.filter(pk__in=user_ids).update(
                force_password_change=True
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Profile refresh required for {flagged} account profile(s), "
                f"{passwords} password change(s) forced of {total} active account(s)."
            )
        )
        self.stdout.write(
            "Next sign-in: new password, picture, signature and Gmail address, "
            "then Google Authenticator enrolment."
        )

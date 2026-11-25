"""Break-glass access to the single Super Administrator seat.

The seat changes hands through the API. This command exists for the case the API
cannot solve: nobody who can sign in holds it, for example because the only
Administrator has lost their password. It requires shell access to the server,
refuses to act without a written reason, and writes the same append-only audit
entry as the API - with no actor, because the change came from the operating
system rather than from a signed-in person.

    python manage.py appoint_system_owner --username jdelacruz \\
        --reason "Recovering a locked-out system" --promote
    python manage.py appoint_system_owner --show
    python manage.py appoint_system_owner --clear --reason "System decommissioned"
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts import ownership
from apps.accounts.models import User
from apps.core import roles


class Command(BaseCommand):
    help = (
        "Show, appoint or vacate the single Administrator seat. "
        "Break-glass only: requires shell access to the server."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            help="Account that should hold the seat.",
        )
        parser.add_argument(
            "--reason",
            default="",
            help="Why the seat is changing. Required, and recorded in the audit trail.",
        )
        parser.add_argument(
            "--show",
            action="store_true",
            help="Print the current holder and every active Administrator.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Vacate the seat instead of appointing an owner.",
        )
        parser.add_argument(
            "--promote",
            action="store_true",
            help=(
                "Also make the account an Administrator if it is not one. "
                "Recovery needs this when no Administrator can sign in."
            ),
        )

    def handle(self, *args, **options):
        changing = bool(options["username"]) or bool(options["clear"])
        if options["show"] or not changing:
            self._show()
            return

        reason = (options["reason"] or "").strip()
        if len(reason) < 3:
            raise CommandError(
                "Pass --reason to record why the seat is changing; it is audited."
            )

        if options["clear"]:
            outgoing = ownership.vacate(reason=reason)
            if outgoing is None:
                self.stdout.write("The seat was already vacant.")
                return
            self.stdout.write(
                self.style.WARNING(
                    f"The Administrator seat held by @{outgoing.username} is vacant."
                )
            )
            return

        username = (options["username"] or "").strip()
        target = User.objects.filter(username=username).first()
        if target is None:
            raise CommandError(f"No account with username {username!r}.")

        try:
            ownership.hand_over(
                target,
                actor=None,
                reason=reason,
                require_owner=False,
                promote=options["promote"],
            )
        except ownership.SystemOwnerError as exc:
            raise CommandError(str(exc))

        self.stdout.write(
            self.style.SUCCESS(
                f"@{target.username} now holds the Administrator seat."
            )
        )

    def _show(self):
        owner = ownership.current_owner()
        if owner is None:
            self.stdout.write(self.style.WARNING("The Administrator seat is vacant."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Administrator: @{owner.username} ({owner.full_name})")
            )
        administrators = User.objects.filter(
            authority=roles.BASE_ADMIN, is_active=True
        ).order_by("username")
        if administrators:
            self.stdout.write("Active Administrator accounts:")
            for account in administrators:
                self.stdout.write(f"  @{account.username} ({account.full_name})")
        else:
            self.stdout.write("No active Administrator accounts.")

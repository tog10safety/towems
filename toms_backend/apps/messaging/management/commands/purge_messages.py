"""Delete messages past the configured retention window.

Run it from the deployment's scheduler:

    venv/bin/python toms_backend/manage.py purge_messages
    venv/bin/python toms_backend/manage.py purge_messages --days 180 --dry-run

The window comes from ``MESSAGING_RETENTION_DAYS`` (``TOMS_MESSAGING_RETENTION_DAYS``
in the environment), so retention is a deployment decision rather than a code
change. Deletion is recorded in the audit trail with a count and the cutoff -
never with the text that was deleted.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core import timeutils
from apps.core.audit import log_action
from apps.messaging.models import Conversation, Message, TypingState


class Command(BaseCommand):
    help = "Delete private messages older than the retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override MESSAGING_RETENTION_DAYS for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting it.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days is None:
            days = int(getattr(settings, "MESSAGING_RETENTION_DAYS", 365))
        if days < 1:
            raise CommandError(
                "Retention must be at least one day; refusing to delete a whole "
                "conversation history."
            )

        cutoff = timeutils.now() - timedelta(days=days)
        stale = Message.objects.filter(created_at__lt=cutoff)
        count = stale.count()
        conversation_ids = list(
            stale.values_list("conversation_id", flat=True).distinct()
        )

        if options["dry_run"]:
            self.stdout.write(
                f"{count} message(s) older than {cutoff:%Y-%m-%d %H:%M} would be "
                f"deleted ({days}-day window)."
            )
            return

        stale.delete()
        # Typing rows are transient; anything older than an hour is abandoned.
        TypingState.objects.filter(
            updated_at__lt=timeutils.now() - timedelta(hours=1)
        ).delete()

        # The inbox ordering must not point at a message that no longer exists.
        for conversation_id in conversation_ids:
            last = (
                Message.objects.filter(conversation_id=conversation_id)
                .order_by("-id")
                .first()
            )
            Conversation.objects.filter(pk=conversation_id).update(
                last_message_at=last.created_at if last else None
            )

        log_action(
            actor=None,
            action="messaging_retention_purge",
            entity_type="message",
            note=(
                f"Deleted {count} message(s) older than {days} days "
                f"(cutoff {cutoff:%Y-%m-%d %H:%M})."
            ),
            after={
                "deleted": count,
                "days": days,
                "cutoff": cutoff.isoformat(),
                "conversations": len(conversation_ids),
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {count} message(s) older than {days} days."
            )
        )

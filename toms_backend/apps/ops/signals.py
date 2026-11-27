"""Hooks that react to rows the rest of the application writes.

The only hook so far is phone push: every notification, whatever workflow made
it, becomes one FCM message. Hooking the model instead of ``notify()`` matters
because messaging writes its own rows directly, and a chat alert must reach a
closed phone the same way an abort order does.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.ops.models import Notification


@receiver(post_save, sender=Notification, dispatch_uid="towems_push_new_notification")
def queue_push_for_new_notification(sender, instance, created, **kwargs):
    """Hand a new notification to the push sender after its transaction commits.

    ``on_commit`` keeps a rollback from buzzing a phone about a notice that was
    never stored, and keeps the HTTP request from waiting on Google.
    """
    if not created:
        return

    from apps.ops import push

    if not push.configured():
        return

    payload = {
        "recipient_id": instance.recipient_id,
        "title": instance.title,
        "body": instance.message,
        "path": instance.action_url
        or ("/messages" if instance.category == "message" else "/notifications"),
        "priority": instance.priority,
        "category": instance.category,
    }
    transaction.on_commit(lambda: push.queue_notification(**payload))

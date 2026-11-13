"""Notification creation helpers.

One place builds every notification so that ``requires_action`` semantics are
consistent: a work item is created with ``action_status='open'`` and stays there
until the owning workflow records completion.
"""

from __future__ import annotations


def notify(
    user,
    *,
    title: str,
    message: str = "",
    level: str = "info",
    category: str = "",
    related=None,
    actor=None,
    priority: str = "normal",
    requires_action: bool = False,
    action_label: str = "",
    action_url: str = "",
    due_at=None,
):
    """Create one notification for ``user``.

    ``related`` is a ``(type, id)`` pair used to deep-link the record. Action
    notifications must supply ``action_label`` so the bell can tell the
    recipient what is expected of them.
    """
    from apps.ops.models import Notification

    if user is None:
        return None
    recipient_id = getattr(user, "id", user)
    if not recipient_id:
        return None

    return Notification.objects.create(
        recipient_id=recipient_id,
        title=title[:200],
        message=message or "",
        level=level,
        category=category,
        related_type=related[0] if related else "",
        related_id=str(related[1]) if related else "",
        created_by_id=getattr(actor, "id", None),
        priority=priority,
        requires_action=requires_action,
        action_status=Notification.ACTION_OPEN if requires_action else Notification.ACTION_NONE,
        action_label=action_label[:120],
        action_url=action_url[:300],
        due_at=due_at,
    )


def notify_many(users, **kwargs):
    """Notify a collection of users, skipping duplicates and the actor."""
    from apps.ops.models import Notification

    actor = kwargs.get("actor")
    actor_id = getattr(actor, "id", None)
    seen: set[int] = set()
    created = []
    for user in users or []:
        user_id = getattr(user, "id", user)
        if not user_id or user_id in seen or user_id == actor_id:
            continue
        seen.add(user_id)
        created.append(notify(user, **kwargs))
    return [row for row in created if row is not None]


def complete_action(
    *,
    related_type: str,
    related_ids,
    recipients=None,
    capability_free: bool = True,
    note: str = "",
    actor=None,
):
    """Close every pending action notification for a record.

    Called by the workflow that actually performs the work - approving a
    mission, deciding an inventory request, verifying an instruction. Reading
    a notification never calls this.
    """
    from django.utils import timezone

    from apps.ops.models import Notification

    if not related_type or not related_ids:
        return 0
    if isinstance(related_ids, (str, int)):
        related_ids = [related_ids]

    queryset = Notification.objects.filter(
        related_type=related_type,
        related_id__in=[str(rid) for rid in related_ids],
        requires_action=True,
        action_status__in=[Notification.ACTION_OPEN, Notification.ACTION_IN_PROGRESS],
    )
    if recipients is not None:
        queryset = queryset.filter(recipient_id__in=[getattr(u, "id", u) for u in recipients])

    now = timezone.now()
    return queryset.update(
        action_status=Notification.ACTION_COMPLETED,
        completed_at=now,
        completed_by_id=getattr(actor, "id", None),
        completion_note=note[:2000],
        is_read=True,
        read_at=now,
    )

"""Make the post-approval actions land: sign the order, then dispatch."""

from pathlib import Path

# ------------------------------------------------------------------ approvals
approvals = Path('toms_backend/apps/ops/approvals.py')
text = approvals.read_text(encoding='utf-8')

old = '''def start_route(mission, *, actor, note: str = "", request=None) -> dict:
'''
new = '''def notify_dispatch_ready(mission, *, actor=None):
    """Ask the Operations Center to dispatch once every gate is open.

    Air missions are dispatchable the moment they are approved; ground missions
    only after the Mission Order is signed. Callers invoke this after either
    event, and it stays silent while a blocker remains or an open dispatch task
    already exists.
    """
    from apps.ops.models import Notification
    from apps.ops.notifications import notify

    if mission.created_by_id is None:
        return None
    if readiness(mission).get("state") != "ready":
        return None
    if Notification.objects.filter(
        related_type="mission",
        related_id=str(mission.id),
        recipient_id=mission.created_by_id,
        requires_action=True,
        action_status__in=[
            Notification.ACTION_OPEN,
            Notification.ACTION_IN_PROGRESS,
        ],
    ).exists():
        return None
    return notify(
        mission.created_by,
        title=f"Mission {mission.mission_number} is ready to dispatch",
        message=(
            "The mission and its Mission Order are approved. Dispatch it when ready."
        ),
        category="mission_routing",
        priority="high",
        related=("mission", mission.id),
        actor=actor,
        requires_action=True,
        action_label="Dispatch mission",
        action_url=f"/missions/{mission.id}",
    )


def start_route(mission, *, actor, note: str = "", request=None) -> dict:
'''
assert old in text
text = text.replace(old, new, 1)

# Complete the approving authority's own action BEFORE the order notice exists,
# otherwise clearing all actions for the mission closes the signatory's task.
old = '''    # The approved mission order is now the signatory's task. The order itself
    # was drafted during planning and travelled with the mission route.
    mission_order_workflow.release_for_signature(mission, actor=actor, request=request)
'''
new = '''    # Close the approving authority's own task first. Clearing every action on
    # the mission here would also close the signatory notice created below.
    complete_action(
        related_type="mission",
        related_ids=[mission.id],
        recipients=[actor],
        note=f"Approved by {actor.username}",
        actor=actor,
    )
    # The approved mission order is now the signatory's task. The order itself
    # was drafted during planning and travelled with the mission route.
    mission_order_workflow.release_for_signature(mission, actor=actor, request=request)
'''
assert old in text
text = text.replace(old, new, 1)

old = '''    complete_action(
        related_type="mission",
        related_ids=[mission.id],
        note=f"Approved by {actor.username}",
        actor=actor,
    )
    # Deputy Commander and Commander (or whoever sits above the named authority)
'''
new = '''    # Deputy Commander and Commander (or whoever sits above the named authority)
'''
assert old in text
text = text.replace(old, new, 1)

old = '''        related=("mission", mission.id),
        actor=actor,
    )
    return mission


@transaction.atomic
def record_route_decision'''
new = '''        related=("mission", mission.id),
        actor=actor,
    )
    # Air missions are ready now; ground ones wait for the order signature,
    # which notifies the Operations Center itself when it happens.
    notify_dispatch_ready(mission, actor=actor)
    return mission


@transaction.atomic
def record_route_decision'''
assert old in text
text = text.replace(old, new, 1)
approvals.write_text(text, encoding='utf-8')
print('approvals.py: signatory action preserved; dispatch task after approval')

# --------------------------------------------------------- mission order flow
order = Path('toms_backend/apps/ops/mission_order_workflow.py')
text = order.read_text(encoding='utf-8')

old = '''        related=("mission", locked.id),
        actor=actor,
    )
    return locked


def state(mission, user=None)'''
new = '''        related=("mission", locked.id),
        actor=actor,
    )
    # The order was the last gate: hand the Operations Center its dispatch task.
    from apps.ops import approvals

    approvals.notify_dispatch_ready(locked, actor=actor)
    return locked


def state(mission, user=None)'''
assert old in text
text = text.replace(old, new, 1)
order.write_text(text, encoding='utf-8')
print('mission_order_workflow.py: dispatch task created after signing')

# ---------------------------------------------------------------- dispatch view
views = Path('toms_backend/apps/ops/views.py')
text = views.read_text(encoding='utf-8')

old = '''        mission.status = "active"
        mission.dispatched_at = _now()
        mission.save(update_fields=["status", "dispatched_at", "updated_at"])
'''
new = '''        mission.status = "active"
        mission.dispatched_at = _now()
        mission.save(update_fields=["status", "dispatched_at", "updated_at"])

        # Dispatching answers the ready-to-dispatch task in the bell.
        from apps.ops.notifications import complete_action

        complete_action(
            related_type="mission",
            related_ids=[mission.pk],
            note="Mission dispatched",
            actor=request.user,
        )
'''
assert old in text
text = text.replace(old, new, 1)
views.write_text(text, encoding='utf-8')
print('views.py: dispatch closes the dispatch task')

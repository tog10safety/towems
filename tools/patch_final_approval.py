"""Make the final approval read as final, and notify higher posts for information."""

from pathlib import Path

# ------------------------------------------------------------------ backend
approvals = Path('toms_backend/apps/ops/approvals.py')
text = approvals.read_text(encoding='utf-8')

old = '''def start_route(mission, *, actor, note: str = "", request=None) -> dict:
'''
new = '''def notify_higher_authorities_of_approval(mission, *, actor=None):
    """Tell posts above the approving authority that approval is final.

    They sit outside the route, so this carries no action: it records that the
    mission was approved and that their post was informed.
    """
    from apps.accounts.models import User
    from apps.ops.notifications import notify_many

    plan = route_plan(mission)
    higher = plan.get("higher_authorities") or []
    if not higher:
        return []
    people = list(
        User.objects.filter(
            id__in=[row["user_id"] for row in higher], is_active=True
        )
    )
    final_label = plan.get("final_label") or ROUTE_ROLE_LABELS.get(
        mission.approving_authority_role, "the approving authority"
    )
    return notify_many(
        people,
        title=f"Mission {mission.mission_number} approved",
        message=(
            f"{final_label} approved this mission. Approval is final; your post is "
            "notified for information only and no action is required."
        ),
        category="mission_routing",
        level="info",
        priority="normal",
        related=("mission", mission.id),
        actor=actor,
    )


def start_route(mission, *, actor, note: str = "", request=None) -> dict:
'''
assert old in text
text = text.replace(old, new, 1)

old = '''    complete_action(
        related_type="mission",
        related_ids=[mission.id],
        note=f"Approved by {actor.username}",
        actor=actor,
    )
    notify(
        mission.created_by,
'''
new = '''    complete_action(
        related_type="mission",
        related_ids=[mission.id],
        note=f"Approved by {actor.username}",
        actor=actor,
    )
    # Deputy Commander and Commander (or whoever sits above the named authority)
    # are outside the route; they learn the outcome without being given a task.
    notify_higher_authorities_of_approval(mission, actor=actor)
    notify(
        mission.created_by,
'''
assert old in text
text = text.replace(old, new, 1)
approvals.write_text(text, encoding='utf-8')
print('approvals.py: final approval notifies higher posts for information')

# ----------------------------------------------------------------- frontend
page = Path('toms_frontend/src/pages/MissionDetailPage.jsx')
text = page.read_text(encoding='utf-8')

old = '''  const routeSteps = routePlan.stages || [];
'''
new = '''  const routeSteps = routePlan.stages || [];
  // The named authority is the last signature: approving there approves the
  // mission itself, it does not forward it to anybody.
  const isFinalApproval = Boolean(
    routePlan.current_role &&
      routePlan.final_role &&
      routePlan.current_role === routePlan.final_role
  );
  const higherLabels = (routePlan.higher_authorities || []).map((row) => row.label);
'''
assert old in text
text = text.replace(old, new, 1)

old = '''      setNotice(
        decision === "approve"
          ? "Approval and comment recorded. The mission was forwarded to the next authority."
'''
new = '''      setNotice(
        decision === "approve"
          ? isFinalApproval
            ? `Mission approved.${
                higherLabels.length
                  ? ` ${higherLabels.join(" and ")} notified for information.`
                  : ""
              }`
            : "Approval and comment recorded. The mission was forwarded to the next authority."
'''
assert old in text
text = text.replace(old, new, 1)

old = '''                <p className="hint">
                  Every routing step needs a comment. Recording a decision here forwards the
                  mission to the next authority or returns it to the Operations Center.
                </p>
'''
new = '''                <p className="hint">
                  {isFinalApproval
                    ? `Every routing step needs a comment. Approving here completes the mission's approval${
                        higherLabels.length
                          ? `; ${higherLabels.join(" and ")} will be notified for information only`
                          : ""
                      }.`
                    : "Every routing step needs a comment. Recording a decision here forwards the mission to the next authority or returns it to the Operations Center."}
                </p>
'''
assert old in text
text = text.replace(old, new, 1)

old = '''                    Approve and forward
                  </Button>
                  <Button variant="ghost" onClick={() => openDecision("return")}>
'''
new = '''                    {isFinalApproval ? "Approve mission" : "Approve and forward"}
                  </Button>
                  <Button variant="ghost" onClick={() => openDecision("return")}>
'''
assert old in text
text = text.replace(old, new, 1)

old = '''            decisionOpen === "approve"
              ? "Approve and forward mission"
'''
new = '''            decisionOpen === "approve"
              ? isFinalApproval
                ? "Approve mission"
                : "Approve and forward mission"
'''
assert old in text
text = text.replace(old, new, 1)

old = '''                  : decisionOpen === "approve"
                    ? "Approve and forward"
'''
new = '''                  : decisionOpen === "approve"
                    ? isFinalApproval
                      ? "Approve mission"
                      : "Approve and forward"
'''
assert old in text
text = text.replace(old, new, 1)
page.write_text(text, encoding='utf-8')
print('MissionDetailPage.jsx: final authority sees Approve mission')

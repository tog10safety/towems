"""Generate the ground-operations flowchart page (one-off documentation build)."""

from __future__ import annotations

import html
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ground-operations-flow.html")

W, H = 2000, 2460

CSS = r"""
:root {
  --ink: #0f172a;
  --muted: #64748b;
  --line: #d7e0ec;
  --canvas: #edf2f8;
  --plan: #2563eb;
  --plan-soft: #eff6ff;
  --mrm: #b45309;
  --mrm-soft: #fffbeb;
  --approve: #0f766e;
  --approve-soft: #f0fdfa;
  --exec: #6d28d9;
  --exec-soft: #f5f3ff;
  --gate: #b91c1c;
  --gate-soft: #fef2f2;
  --slate: #334155;
}
* { box-sizing: border-box; }
html { background: #dfe7f1; }
body {
  margin: 0;
  color: var(--ink);
  background:
    radial-gradient(circle at 20% 0%, #f8fbff 0, transparent 34rem),
    linear-gradient(180deg, #e8eef7 0%, #edf2f8 55%, #e6edf6 100%);
  font-family: "Segoe UI", Inter, system-ui, -apple-system, BlinkMacSystemFont, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.hero {
  position: relative;
  overflow: hidden;
  margin: 28px auto 20px;
  width: min(1960px, calc(100% - 40px));
  border-radius: 24px;
  padding: 30px 38px 28px;
  color: #f8fafc;
  background:
    radial-gradient(120% 190% at 92% -20%, rgba(56, 189, 248, .34), transparent 54%),
    radial-gradient(80% 140% at 8% 120%, rgba(245, 158, 11, .2), transparent 55%),
    linear-gradient(120deg, #0b1729 0%, #12233c 56%, #0c1d31 100%);
  box-shadow: 0 24px 70px rgba(15, 23, 42, .26);
  border: 1px solid rgba(148, 163, 184, .22);
}
.hero::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(148,163,184,.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148,163,184,.055) 1px, transparent 1px);
  background-size: 32px 32px;
  mask-image: linear-gradient(90deg, transparent 35%, #000 100%);
}
.eyebrow {
  display: flex; align-items: center; gap: 10px; margin-bottom: 14px;
  font-size: 11px; font-weight: 800; letter-spacing: .22em; text-transform: uppercase;
  color: #7dd3fc;
}
.eyebrow .dot { width: 7px; height: 7px; border-radius: 50%; background: #38bdf8; box-shadow: 0 0 16px #38bdf8; }
.hero h1 { margin: 0; max-width: 1180px; font-size: 42px; line-height: 1.08; letter-spacing: -.025em; font-weight: 800; }
.hero h1 span { color: #93c5fd; }
.hero p { margin: 12px 0 0; max-width: 1020px; font-size: 14px; line-height: 1.6; color: #cbd5e1; }
.hero .meta { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
.hero .meta span {
  border: 1px solid rgba(148, 163, 184, .28);
  background: rgba(15, 23, 42, .35);
  border-radius: 999px; padding: 6px 11px;
  font-size: 10.5px; font-weight: 700; letter-spacing: .04em; color: #dbeafe;
}
.hero .meta b { color: #7dd3fc; font-weight: 800; }
.phase-key { position: absolute; right: 38px; top: 34px; display: grid; gap: 7px; width: 180px; z-index: 2; }
.phase-key div { display: flex; align-items: center; gap: 8px; font-size: 10.5px; font-weight: 700; color: #cbd5e1; }
.phase-key i { width: 18px; height: 4px; border-radius: 999px; display: inline-block; }

main { width: min(2000px, calc(100% - 40px)); margin: 0 auto 28px; }
.diagram-shell {
  border-radius: 24px;
  border: 1px solid #cfdae8;
  background: var(--canvas);
  box-shadow: 0 16px 50px rgba(15, 23, 42, .1);
  overflow: auto;
  position: relative;
}
.scroll-hint {
  position: sticky; left: 0; top: 0; z-index: 8;
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid #d7e0ec;
  background: rgba(245, 249, 255, .9);
  backdrop-filter: blur(8px);
  font-size: 10.5px; letter-spacing: .04em; color: #64748b; text-transform: uppercase; font-weight: 800;
}
.diagram { position: relative; width: 2000px; height: 2460px; }
.edges { position: absolute; inset: 0; width: 2000px; height: 2460px; z-index: 1; overflow: visible; }
.edge { fill: none; stroke: #94a3b8; stroke-width: 1.8; }
.edge.plan { stroke: #60a5fa; }
.edge.mrm { stroke: #f59e0b; }
.edge.approve { stroke: #14b8a6; }
.edge.exec { stroke: #a78bfa; }
.edge.gate { stroke: #ef4444; }
.edge.loop { stroke-dasharray: 6 5; }
.edge.optional { stroke-dasharray: 5 5; }
.edge-label {
  font-family: inherit; font-size: 9.5px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase;
  fill: #475569; paint-order: stroke; stroke: #edf2f8; stroke-width: 5px; stroke-linejoin: round;
}
.band {
  position: absolute; border-radius: 20px;
  border: 1px solid #cbd8e7;
  background: rgba(255, 255, 255, .56);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .8);
}
.band.plan { background: linear-gradient(180deg, #f7fbff 0%, #eef6ff 100%); border-color: #bfdbfe; }
.band.ready { background: linear-gradient(180deg, #fffef8 0%, #fbfaf2 55%, #f6fbfa 100%); border-color: #e2dfc8; }
.band.exec { background: linear-gradient(180deg, #faf8ff 0%, #f1edff 100%); border-color: #ddd6fe; }
.band-head {
  position: relative; z-index: 3; display: flex; align-items: center; gap: 12px;
  padding: 14px 20px 8px; margin-bottom: 2px;
  border-radius: 19px 19px 0 0;
}
.band.plan .band-head { background: #f7fbff; }
.band.ready .band-head { background: #fffef8; }
.band.exec .band-head { background: #faf8ff; }
.band-no {
  display: inline-grid; place-items: center; min-width: 31px; height: 24px; padding: 0 8px;
  border-radius: 7px; background: #0f172a; color: #fff;
  font-size: 11px; font-weight: 800; letter-spacing: .08em;
}
.band-title { font-size: 13px; font-weight: 900; letter-spacing: .16em; text-transform: uppercase; color: #1e293b; }
.band-note { font-size: 10.5px; color: #64748b; margin-left: auto; font-weight: 650; }
.band.plan .band-title { color: #1d4ed8; }
.band.ready .band-title { color: #92400e; }
.band.exec .band-title { color: #5b21b6; }
.track-chip {
  display: inline-flex; align-items: center; gap: 5px; margin-left: 4px;
  padding: 3px 8px; border-radius: 999px; border: 1px solid currentColor;
  font-size: 8.4px; font-weight: 900; letter-spacing: .1em; text-transform: uppercase;
  opacity: .9;
}
.track-chip.mrm { color: #b45309; background: #fffbeb; }
.track-chip.approve { color: #0f766e; background: #f0fdfa; margin-left: auto; }
.plan-strip {
  position: absolute; z-index: 2; display: grid; grid-template-columns: repeat(3, 1fr);
  padding: 12px 18px; border: 1px dashed #bfdbfe; border-radius: 14px;
  background: rgba(239, 246, 255, .82);
}
.plan-strip > div { padding: 0 16px; border-left: 1px solid #dbeafe; }
.plan-strip > div:first-child { padding-left: 0; border-left: 0; }
.plan-strip > div:last-child { padding-right: 0; }
.plan-strip b {
  display: block; margin-bottom: 4px;
  font-size: 9.5px; font-weight: 900; letter-spacing: .1em; text-transform: uppercase; color: #1d4ed8;
}
.plan-strip span { display: block; font-size: 11px; line-height: 1.45; color: #475569; }
.split-banner {
  position: absolute; z-index: 2; display: grid; grid-template-columns: 1fr 1fr;
  padding: 12px 18px; border: 1px solid #dbeafe; border-radius: 14px;
  background: #fff; box-shadow: 0 5px 16px rgba(15, 23, 42, .045);
}
.split-banner > div { padding: 0 16px; border-left: 1px solid #e2e8f0; }
.split-banner > div:first-child { padding-left: 0; border-left: 0; }
.split-banner > div:last-child { padding-right: 0; }
.split-banner b {
  display: block; margin-bottom: 4px;
  font-size: 9.5px; font-weight: 900; letter-spacing: .1em; text-transform: uppercase; color: #0f766e;
}
.split-banner span { display: block; font-size: 11px; line-height: 1.45; color: #475569; }
.node {
  position: absolute; z-index: 2; display: flex; flex-direction: column;
  padding: 12px 14px 11px 17px;
  border: 1px solid #d6dee9; border-radius: 13px;
  background: #fff;
  box-shadow: 0 7px 18px rgba(15, 23, 42, .065), 0 1px 0 rgba(255,255,255,.9) inset;
  overflow: hidden;
}
.node::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 5px; background: var(--accent, #64748b);
}
.node.plan { --accent: var(--plan); }
.node.mrm { --accent: var(--mrm); }
.node.approve { --accent: var(--approve); }
.node.exec { --accent: var(--exec); }
.node.gate { --accent: var(--gate); background: linear-gradient(180deg, #fff 0%, var(--gate-soft) 100%); border-color: #fecaca; }
.node.terminal { background: linear-gradient(180deg, #fff 0%, #f8fafc 100%); }
.node.optional { border-style: dashed; }
.node-meta { display: flex; align-items: center; gap: 7px; min-width: 0; }
.step {
  display: inline-grid; place-items: center; min-width: 27px; height: 20px; padding: 0 6px;
  border-radius: 6px; background: #f1f5f9; color: #475569;
  font-size: 9.5px; font-weight: 900; letter-spacing: .04em; flex: 0 0 auto;
}
.node.plan .step { background: #dbeafe; color: #1d4ed8; }
.node.mrm .step { background: #fef3c7; color: #92400e; }
.node.approve .step { background: #ccfbf1; color: #0f766e; }
.node.exec .step { background: #ede9fe; color: #5b21b6; }
.node.gate .step { background: #fee2e2; color: #991b1b; }
.actor {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 8.8px; line-height: 1.2; font-weight: 800; letter-spacing: .06em; text-transform: uppercase;
  color: #64748b;
}
.node h3 { margin: 6px 0 6px; font-size: 14.5px; line-height: 1.18; letter-spacing: -.012em; font-weight: 800; color: #0f172a; }
.node.gate h3 { color: #991b1b; }
.node ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 2.5px; }
.node li { position: relative; padding-left: 11px; font-size: 10.35px; line-height: 1.33; color: #334155; }
.node li::before { content: ""; position: absolute; left: 0; top: .52em; width: 4px; height: 4px; border-radius: 50%; background: var(--accent, #94a3b8); opacity: .68; }
.node li b { color: #0f172a; font-weight: 750; }
.node code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 9.4px; background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0 3px; color: #0f172a; }
.node-foot { margin-top: auto; display: flex; align-items: center; flex-wrap: wrap; gap: 5px; padding-top: 6px; }
.chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2.5px 6px; border-radius: 5px; border: 1px solid transparent;
  font-size: 8.4px; font-weight: 900; line-height: 1.2; letter-spacing: .07em; text-transform: uppercase;
}
.chip.draft { background: #f1f5f9; color: #475569; border-color: #e2e8f0; }
.chip.pending { background: #fef3c7; color: #92400e; border-color: #fde68a; }
.chip.submitted { background: #dbeafe; color: #1d4ed8; border-color: #bfdbfe; }
.chip.approved { background: #dcfce7; color: #166534; border-color: #bbf7d0; }
.chip.active { background: #ede9fe; color: #5b21b6; border-color: #ddd6fe; }
.chip.closed { background: #e2e8f0; color: #334155; border-color: #cbd5e1; }
.chip.returned { background: #ffedd5; color: #9a3412; border-color: #fed7aa; }
.chip.cancelled { background: #fee2e2; color: #991b1b; border-color: #fecaca; }
.chip.optional { background: #f8fafc; color: #64748b; border-color: #cbd5e1; border-style: dashed; }
.note {
  display: block; margin-top: 4px; font-size: 9.3px; line-height: 1.3; color: #64748b;
}
.gate-flag {
  display: inline-grid; place-items: center; width: 15px; height: 15px; border-radius: 4px;
  background: #fee2e2; color: #b91c1c; font-size: 9px; font-weight: 900; margin-left: auto;
}
.divider {
  position: absolute; z-index: 0; top: 842px; bottom: 2020px; left: 1000px; width: 1px;
  background: repeating-linear-gradient(180deg, #d4dce8 0 5px, transparent 5px 10px);
}

.reference { display: grid; gap: 18px; margin-top: 22px; }
.ref-grid { display: grid; grid-template-columns: 1.1fr .9fr; gap: 18px; }
.card {
  background: #fff; border: 1px solid #d7e0ec; border-radius: 18px;
  box-shadow: 0 10px 28px rgba(15, 23, 42, .06); overflow: hidden;
}
.card-head { padding: 16px 20px 13px; border-bottom: 1px solid #e6edf6; display: flex; align-items: baseline; gap: 10px; }
.card-head h2 { margin: 0; font-size: 13px; letter-spacing: .11em; text-transform: uppercase; color: #0f172a; }
.card-head span { margin-left: auto; font-size: 10px; color: #64748b; font-weight: 700; }
.card-body { padding: 16px 20px 19px; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 9.5px; letter-spacing: .09em; text-transform: uppercase; color: #64748b; font-weight: 800; padding: 0 8px 8px 0; border-bottom: 1px solid #e2e8f0; }
td { padding: 9px 8px 9px 0; border-bottom: 1px solid #eef2f7; font-size: 11.4px; line-height: 1.4; color: #334155; vertical-align: top; }
tr:last-child td { border-bottom: 0; }
td strong { color: #0f172a; }
.band-pill {
  display: inline-block; min-width: 66px; text-align: center; padding: 4px 7px; border-radius: 6px;
  font-size: 10px; font-weight: 900; background: #f1f5f9; color: #334155;
}
.band-pill.low { background: #dcfce7; color: #166534; }
.band-pill.mid { background: #fef3c7; color: #92400e; }
.band-pill.high { background: #ffedd5; color: #9a3412; }
.band-pill.critical { background: #fee2e2; color: #991b1b; }
.ladder { display: grid; gap: 8px; }
.ladder-row { display: grid; grid-template-columns: 80px 1fr; gap: 10px; align-items: center; padding: 9px 10px; border: 1px solid #e8eef6; border-radius: 9px; background: #fbfdff; }
.ladder-row b { font-size: 10px; letter-spacing: .06em; text-transform: uppercase; color: #475569; }
.ladder-row span { font-size: 11px; color: #334155; }
.subflow { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.substep { position: relative; padding: 11px 12px; border: 1px solid #e2e8f0; border-radius: 10px; background: #f8fafc; min-height: 86px; }
.substep:not(:last-child)::after {
  content: "→"; position: absolute; right: -7px; top: 50%; transform: translateY(-50%);
  width: 14px; height: 14px; display: grid; place-items: center; border-radius: 50%;
  background: #fff; border: 1px solid #d7e0ec; color: #64748b; font-size: 9px; z-index: 2;
}
.substep b { display: block; font-size: 9px; letter-spacing: .1em; text-transform: uppercase; color: #b45309; margin-bottom: 5px; }
.substep span { display: block; font-size: 10.5px; line-height: 1.4; color: #334155; }
.note-strip { margin-top: 14px; padding: 11px 13px; border-radius: 9px; background: #fffbeb; border: 1px solid #fde68a; font-size: 10.8px; line-height: 1.55; color: #78350f; }
.lists { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
.list-card h3 { font-size: 10px; letter-spacing: .11em; text-transform: uppercase; margin: 0 0 9px; color: #0f172a; }
.list-card ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; }
.list-card li { position: relative; padding-left: 13px; font-size: 10.9px; line-height: 1.45; color: #334155; }
.list-card li::before { content: ""; position: absolute; left: 0; top: .48em; width: 5px; height: 5px; border-radius: 1px; background: #94a3b8; }
.legend { display: flex; flex-wrap: wrap; gap: 7px; }
.legend .chip { font-size: 8.8px; }
footer {
  width: min(1960px, calc(100% - 40px)); margin: 0 auto 32px; padding: 0 4px 8px;
  display: flex; gap: 18px; flex-wrap: wrap; align-items: center; justify-content: space-between;
  color: #64748b; font-size: 10px; line-height: 1.5;
}
footer b { color: #334155; }
@media print {
  html, body { background: #fff; }
  .hero { box-shadow: none; width: 100%; margin: 0 0 10px; }
  main, footer { width: 100%; }
  .scroll-hint { display: none; }
  .diagram-shell { border: 0; box-shadow: none; overflow: visible; }
  .node, .card { box-shadow: none; }
  .reference { break-before: page; }
}
"""

NODES = {}


def node(key, x, y, w, h, phase, step, actor, title, bullets, status=None, status_tone="draft", extra="", cls=""):
    items = "".join(f"<li>{b}</li>" for b in bullets)
    chip = f'<span class="chip {status_tone}">{status}</span>' if status else ""
    NODES[key] = dict(x=x, y=y, w=w, h=h, phase=phase, step=step, actor=actor, title=title)
    return f"""
    <div class="node {phase} {cls}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">
      <div class="node-meta"><span class="step">{step}</span><span class="actor">{actor}</span></div>
      <h3>{title}</h3>
      <ul>{items}</ul>
      <div class="node-foot">{chip}{extra}</div>
    </div>"""


# -- master flow nodes -------------------------------------------------------

plan_nodes = [
    node("A1", 70, 270, 270, 200, "plan", "A1", "A3 Ops · Operations Center", "Plan a ground operation", [
        "Open <b>Mission Planning → Ground</b>",
        "Requires <code>manage_missions</code>",
        "Unit-scoped; A3 / Operations Center",
        "Nothing is executable yet",
    ], "START", "draft"),
    node("A2", 360, 270, 300, 200, "plan", "A2", "Planner", "Mission details + area", [
        "Title, purpose, UPR, O/SPR",
        "Map pin → lat/lon + MGRS",
        "Estimated start/end (PST, 24h)",
        "Operational area + risk notes",
        "End may not precede start",
    ]),
    node("A3", 680, 270, 290, 200, "plan", "A3", "Planner", "Personnel involved", [
        "Choose people in a roster modal",
        "Set designation per person",
        "OIC · Team Leader · Driver · Member",
        "Roster feeds MRM + vehicle drivers",
        "One person may drive one vehicle",
    ]),
    node("A4", 990, 270, 300, 200, "plan", "A4", "Planner", "Staff the workflow", [
        "MRM lead — from the roster",
        "MRM evaluator — unit-scoped",
        "Approving post + specific person",
        "Cdr · Dy Cdr · DFO · Ops Officer",
        "Naming a post is not an approval",
    ], "REQUIRED", "pending"),
    node("A5", 1310, 270, 290, 200, "plan", "A5", "Planner", "Vehicle movement", [
        "Optional: attach a trip",
        "1 vehicle → 1 unique driver",
        "Sole Driver auto-fills one vehicle",
        "Per-vehicle itinerary + stops",
        "Road route calculated after save",
    ], "OPTIONAL", "optional", cls="optional"),
    node("A6", 1620, 270, 290, 200, "plan", "A6", "System · planner", "Save the mission", [
        "<code>POST /api/missions/</code>",
        "Mission number assigned",
        "Revision 1 · status DRAFT",
        "MRM team + approver notified",
        "Material edits bump revision",
    ], "DRAFT", "draft"),
]

mrm_nodes = [
    node("B1", 100, 850, 360, 130, "mrm", "B1", "MRM lead", "MRM lead starts assessment", [
        "Only the assigned <b>lead</b> sees Start",
        "Creates draft MRM + two-way link",
        "Evaluator is pre-set from the mission",
    ], "PENDING", "pending"),
    node("B2", 100, 1015, 360, 190, "mrm", "B2", "MRM lead", "Complete the assessment", [
        "Mission details + safety objectives",
        "I'M SAFE + environmental conditions",
        "Hazards from the unit's own library",
        "Likelihood, severity, mitigations",
        "Tag personnel for feedback",
        "Saving recalculates residual risk",
    ]),
    node("B3", 100, 1240, 360, 160, "mrm", "B3", "MRM lead", "Submit for review", [
        "Sign now, or choose sign later",
        "A signature must exist in the profile",
        "Status → <b>SUBMITTED</b> · review pending",
        "Routes the decision authority by risk band",
    ], "SUBMITTED", "submitted"),
    node("B4", 100, 1435, 360, 150, "mrm", "B4", "Assigned evaluator", "Evaluator review", [
        "Decision: approved / needs revision / rejected",
        "Evaluator signature is recorded",
        "Approved → creator signature is required",
        "Returned → correct the assessment",
    ], "DECISION", "pending"),
    node("B5", 100, 1620, 360, 140, "mrm", "B5", "Creator", "Creator signs", [
        "Required if not signed at submission",
        "Upload signature in the profile",
        "Reminder sent after evaluator approval",
    ], "SIGNED", "submitted"),
    node("B6", 100, 1795, 360, 175, "mrm", "B6", "Decision authority", "MRM authority-approved", [
        "≤50% creator · 51–65% evaluator",
        "66–75% squadron commander / DFO",
        "76–85% Director for Operations",
        "86–100% Commander / Deputy fallback",
        "≤65% closes on last signature; above needs the signer",
    ], "MRM APPROVED", "approved"),
]

approve_nodes = [
    node("C1", 1100, 850, 360, 130, "approve", "C1", "Creator / Unit Safety", "Submit mission for approval", [
        "Creator, Unit Safety or <code>manage_missions</code>",
        "Status DRAFT / PLANNED → <b>PLANNED</b>",
        "Records <code>operations_center / routed</code>",
        "Operations Center is notified",
    ], "PLANNED", "submitted"),
    node("C2", 1100, 1015, 360, 170, "approve", "C2", "Operations Center", "Screen and route", [
        "Checks completeness of the plan",
        "Confirms or assigns the approving authority",
        "Routes to the Operations Officer",
        "Return to creator → draft",
    ], "ROUTED", "submitted"),
    node("C3", 1100, 1220, 360, 150, "approve", "C3", "Operations Officer", "Operations Officer review", [
        "Requires <code>review_missions</code>",
        "Reviews and recommends / approves",
        "Return → correct and resubmit",
        "Operations Center cannot act at this stage",
    ], "IN REVIEW", "submitted"),
    node("C4", 1100, 1405, 360, 210, "approve", "C4", "System gate", "Final approval gate", [
        "MRM authority-approved?",
        "Operations Officer approved?",
        "Approving authority assigned?",
        "Approval covers the current revision?",
        "Approver is not the mission creator?",
    ], "BLOCKED UNTIL ALL PASS", "cancelled", extra='<span class="gate-flag">!</span>', cls="gate"),
    node("C5", 1100, 1650, 360, 150, "approve", "C5", "Assigned approving authority", "Approving authority decides", [
        "Only the named person may decide",
        "Approve · return · reject",
        "Approve requires a complete MRM",
        "Decision is audited and notified",
    ], "PENDING", "pending"),
    node("C6", 1100, 1835, 360, 135, "approve", "C6", "System · approving authority", "Mission approved", [
        "Status → <b>APPROVED</b>",
        "<code>approval_status = approved</code>",
        "Approved revision = current revision",
        "Ready for dispatch",
    ], "APPROVED", "approved"),
    node("C7", 1540, 1650, 360, 150, "approve", "C7", "System", "Returned for revision", [
        "Mission returns to PLANNED",
        "The same approver stays assigned",
        "Resubmit through the same chain",
    ], "RETURNED", "returned"),
    node("C8", 1540, 1835, 360, 135, "approve", "C8", "System", "Rejected / cancelled", [
        "Mission → CANCELLED",
        "Cannot be dispatched",
        "History and audit are retained",
    ], "CANCELLED", "cancelled", cls="terminal"),
]

exec_nodes = [
    node("D1", 70, 2140, 320, 170, "exec", "D1", "Operations Center", "Dispatch mission", [
        "Requires <code>manage_missions</code>",
        "Rechecks MRM + approval + revision",
        "Status → <b>ACTIVE</b>",
        "Audit action <code>dispatch</code>",
    ], "ACTIVE", "active"),
    node("D2", 450, 2140, 310, 170, "exec", "D2", "Mission record", "Ground operation runs", [
        "Personnel + planned vehicles",
        "Mission board shows ACTIVE",
        "Role-scoped visibility",
        "Plan remains the record of intent",
    ], "ACTIVE", "active"),
    node("D3", 820, 2110, 420, 230, "exec", "D3", "Operations Center · viewers", "Track & monitor", [
        "<code>/missions/{id}/track</code> full-map tab",
        "Road route per vehicle + ordered stops",
        "Mission objective pin + route legs",
        "List shows risk %, MRM, approval, readiness",
        "Planned route only — <b>not live GPS</b>",
        "Route can be recalculated from the itinerary",
    ], "MONITORING", "submitted"),
    node("D4", 1300, 2140, 330, 170, "exec", "D4", "Mission record", "Close the record", [
        "Status → COMPLETED or CANCELLED",
        "Approval + MRM history retained",
        "Closed missions cannot be dispatched",
        "No new approvals after closure",
    ], "CLOSED", "closed"),
    node("D5", 1690, 2140, 240, 170, "exec", "D5", "System", "Evidence retained", [
        "Audit trail",
        "Action notifications",
        "MRM record + PDF when available",
    ], "ARCHIVE", "closed", cls="terminal"),
]

# -- edges ------------------------------------------------------------------


def border(key, side):
    n = NODES[key]
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    return {
        "top": (x + w / 2, y),
        "bottom": (x + w / 2, y + h),
        "left": (x, y + h / 2),
        "right": (x + w, y + h / 2),
        "center": (x + w / 2, y + h / 2),
    }[side]


def path_v(a, b, cls, label=""):
    x1, y1 = border(a, "bottom")
    x2, y2 = border(b, "top")
    gap = max(8, (y2 - y1) * 0.45)
    d = f"M{x1:.1f},{y1:.1f} C{x1:.1f},{y1 + gap:.1f} {x2:.1f},{y2 - gap:.1f} {x2:.1f},{y2:.1f}"
    return edge(d, cls, label, ((x1 + x2) / 2, (y1 + y2) / 2))


def path_h(a, b, cls, label=""):
    x1, y1 = border(a, "right")
    x2, y2 = border(b, "left")
    gap = max(6, (x2 - x1) * 0.45)
    d = f"M{x1:.1f},{y1:.1f} C{x1 + gap:.1f},{y1:.1f} {x2 - gap:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"
    return edge(d, cls, label, ((x1 + x2) / 2, (y1 + y2) / 2 - 6))


def edge(d, cls, label="", label_xy=None, dashed=None):
    klass = f"edge {cls}" + (f" {dashed}" if dashed else "")
    path = f'<path class="{klass}" d="{d}" marker-end="url(#arrow-{cls})"/>'
    if label and label_xy:
        x, y = label_xy
        path += f'<text class="edge-label" x="{x}" y="{y}" text-anchor="middle">{html.escape(label)}</text>'
    return path


edges = []
for a, b in [("A1", "A2"), ("A2", "A3"), ("A3", "A4")]:
    edges.append(path_h(a, b, "plan"))

# A4 -> A5 is the optional vehicle branch, A5 -> A6 continues to save.
# A5 already carries an OPTIONAL chip; a label in the 20px gap would be
# clipped by the next card.
edges.append(path_h("A4", "A5", "plan"))
edges.append(path_h("A5", "A6", "plan"))

# Saved mission forks into the MRM track and the mission-approval track.
edges.append('<path class="edge plan" d="M1765,470 V735 H270 V850" marker-end="url(#arrow-plan)"/>')
edges.append('<path class="edge plan" d="M1765,735 H1270 V850" marker-end="url(#arrow-approve)"/>')
edges.append('<text class="edge-label" x="1790" y="610" text-anchor="start">saved mission</text>')

for a, b in [("B1", "B2"), ("B2", "B3"), ("B3", "B4"), ("B4", "B5"), ("B5", "B6")]:
    edges.append(path_v(a, b, "mrm"))

# Evaluator returns the assessment to the lead for correction.
edges.append('<path class="edge mrm loop" d="M100,1510 H58 V1280 H100" marker-end="url(#arrow-mrm)"/>')
edges.append('<text class="edge-label" x="52" y="1395" text-anchor="middle" transform="rotate(-90 52 1395)">needs revision</text>')

for a, b in [("C1", "C2"), ("C2", "C3"), ("C3", "C4"), ("C4", "C5"), ("C5", "C6")]:
    edges.append(path_v(a, b, "approve"))
edges.append(path_h("C5", "C7", "approve", "return"))
# Reject leaves below the return line and enters C8 from its left edge, so it
# never crosses the Mission approved card.
edges.append('<path class="edge approve" d="M1460,1772 H1500 V1902 H1540" marker-end="url(#arrow-approve)"/>')
edges.append('<text class="edge-label" x="1508" y="1826" text-anchor="start">reject</text>')
# Resubmission loop along the outer right margin.
edges.append('<path class="edge approve loop" d="M1900,1725 H1938 V915 H1460" marker-end="url(#arrow-approve)"/>')
edges.append('<text class="edge-label" x="1946" y="1320" text-anchor="middle" transform="rotate(90 1946 1320)">resubmit</text>')
# The MRM result unlocks the final approval gate.
edges.append('<path class="edge mrm" d="M460,1882 C700,1882 830,1510 1100,1510" marker-end="url(#arrow-mrm)"/>')
edges.append('<text class="edge-label" x="785" y="1692" text-anchor="middle">MRM approved</text>')

# Approved mission goes to dispatch.
# Drop below the execute band's header before crossing it, so the hand-off
# label is not printed on top of the phase title.
edges.append('<path class="edge exec" d="M1280,1970 V2108 H230 V2140" marker-end="url(#arrow-exec)"/>')
edges.append('<text class="edge-label" x="720" y="2100" text-anchor="middle">approved mission</text>')

for a, b in [("D1", "D2"), ("D2", "D3"), ("D3", "D4"), ("D4", "D5")]:
    edges.append(path_h(a, b, "exec"))

# -- page assembly -----------------------------------------------------------

REFERENCES = """
<section class="reference">
  <div class="ref-grid">
    <div class="card">
      <div class="card-head"><h2>MRM decision-authority ladder</h2><span>Residual risk after mitigations</span></div>
      <div class="card-body">
        <table>
          <thead><tr><th style="width:120px">Residual risk</th><th>Signer</th><th>If the post is vacant</th></tr></thead>
          <tbody>
            <tr><td><span class="band-pill low">0 – 50%</span></td><td><strong>Creator</strong> signs the assessment</td><td>—</td></tr>
            <tr><td><span class="band-pill low">51 – 65%</span></td><td><strong>Assigned evaluator</strong> acts as supervisor</td><td>Unit Safety Officer → Operations Officer</td></tr>
            <tr><td><span class="band-pill mid">66 – 75%</span></td><td><strong>Squadron Commander</strong> of the unit or parent squadron</td><td>Routes to the Director for Operations</td></tr>
            <tr><td><span class="band-pill high">76 – 85%</span></td><td><strong>Director for Operations</strong></td><td>A user designated “Director” in the unit</td></tr>
            <tr><td><span class="band-pill critical">86 – 100%</span></td><td><strong>Commander</strong></td><td>Deputy Commander</td></tr>
          </tbody>
        </table>
        <div class="note-strip">
          At <strong>65% or below</strong>, the MRM closes automatically on the last required signature: evaluator review approved + creator signed.
          Above <strong>65%</strong>, the routed decision authority must approve explicitly; only that person (or an Administrator) can sign.
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Status models</h2><span>What the screens read</span></div>
      <div class="card-body">
        <div class="ladder">
          <div class="ladder-row"><b>Mission</b><span>DRAFT → PLANNED → APPROVED → ACTIVE → COMPLETED / CANCELLED</span></div>
          <div class="ladder-row"><b>Approval</b><span>NOT ROUTED → PENDING → APPROVED / RETURNED / REJECTED</span></div>
          <div class="ladder-row"><b>MRM</b><span>NOT CREATED → IN PROGRESS → SUBMITTED → APPROVED / RETURNED</span></div>
          <div class="ladder-row"><b>Readiness</b><span>BLOCKED → READY FOR DISPATCH → IN PROGRESS → CLOSED</span></div>
        </div>
        <div class="note-strip">
          A material edit to an approved mission — title, purpose, unit, times, location, coordinates, MRM link, personnel, areas or category detail — bumps the revision and makes the approval stale. The mission must be approved again before dispatch.
        </div>
        <div class="legend" style="margin-top:14px">
          <span class="chip draft">Draft</span><span class="chip submitted">Planned</span>
          <span class="chip approved">Approved</span><span class="chip active">Active</span>
          <span class="chip closed">Closed</span><span class="chip cancelled">Blocked</span>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Optional vehicle-movement branch</h2><span>Ground operations only</span></div>
    <div class="card-body">
      <div class="subflow">
        <div class="substep"><b>01 · Add trip</b><span>Departure and return default to the mission's estimated window.</span></div>
        <div class="substep"><b>02 · Pick vehicles</b><span>Inventory vehicles only; maintenance, unserviceable or conflicting vehicles are refused.</span></div>
        <div class="substep"><b>03 · Designate drivers</b><span>One unique driver per vehicle, chosen from the mission roster. A sole Driver auto-fills a single vehicle.</span></div>
        <div class="substep"><b>04 · Build itinerary</b><span>Each vehicle gets its own origin (vehicle unit), destination (operational area), stops and optional return.</span></div>
      </div>
      <div class="subflow" style="margin-top:8px; grid-template-columns: repeat(3, 1fr);">
        <div class="substep"><b>05 · Save</b><span>One trip per vehicle, status PLANNED, linked to the mission.</span></div>
        <div class="substep"><b>06 · Calculate road route</b><span>OSRM-compatible driving route through every stop; no straight-line fallback.</span></div>
        <div class="substep"><b>07 · Optional trip approval</b><span>Command or Unit Safety may approve the trip separately. Mission dispatch does not require it.</span></div>
      </div>
      <div class="note-strip">
        The map draws the routing engine's road geometry. If a route cannot be calculated, the page says so and shows only the stops — it never implies a live vehicle position or an aerial track.
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Roles, screens and hard gates</h2><span>Who acts, where, and what cannot be bypassed</span></div>
    <div class="card-body lists">
      <div class="list-card">
        <h3>Who does what</h3>
        <ul>
          <li><strong>A3 – Operations / Operations Center:</strong> plans the ground mission.</li>
          <li><strong>Operations Center:</strong> routes the mission, assigns the approving authority, and dispatches. It cannot review at the Operations Officer stage or give final approval.</li>
          <li><strong>Operations Officer:</strong> reviews and approves, or returns, at their own stage.</li>
          <li><strong>Assigned approving authority:</strong> approves, returns or rejects the mission.</li>
          <li><strong>MRM lead / evaluator / decision authority:</strong> own the assessment, review and final MRM signature.</li>
        </ul>
      </div>
      <div class="list-card">
        <h3>Where it happens</h3>
        <ul>
          <li><code>/missions/new</code> — plan the operation, personnel, MRM staffing, vehicles.</li>
          <li><code>/missions/{id}</code> — MRM status, approval, dispatch, planned routes.</li>
          <li><code>/risk/{id}</code> — assessment editor, submit, review, signatures.</li>
          <li><code>/missions/{id}/track</code> — full-map tab for planned road routes.</li>
          <li><code>/risk</code> — MRM register, filters, PDF, deletion.</li>
        </ul>
      </div>
      <div class="list-card">
        <h3>Hard gates</h3>
        <ul>
          <li>Final approval needs an Operations Officer approval, an authority-approved MRM, an assigned approver, and an approval covering the current revision.</li>
          <li>Dispatch rechecks the MRM, the approval and the revision. A stale approval blocks it.</li>
          <li>A creator cannot approve their own mission.</li>
          <li>Every selected vehicle needs a unique driver from the mission roster.</li>
          <li>The Start button is shown only to the mission's assigned MRM lead.</li>
        </ul>
      </div>
    </div>
  </div>
</section>
"""


def band(x, y, w, h, cls, no, title, note, extra=""):
    return f"""
    <div class="band {cls}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">
      <div class="band-head"><span class="band-no">{no}</span><span class="band-title">{title}</span><span class="band-note">{note}</span>{extra}</div>
    </div>"""


body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TOWEMS — Ground Operations Flow</title>
<style>{CSS}</style>
</head>
<body>
<header class="hero">
  <div class="eyebrow"><span class="dot"></span> Current system flow · Ground Operations</div>
  <h1>TOWEMS <span>Ground Operations</span><br>Mission &amp; risk workflow</h1>
  <p>End-to-end flow from ground mission planning through MRM assessment, mission approval, dispatch and planned-route monitoring. Drawn from the current frontend screens, API routes, approval gates and role rules.</p>
  <div class="meta">
    <span><b>As implemented</b> 31 Oct 2026</span>
    <span><b>Scope</b> Ground · air excluded</span>
    <span><b>MRM</b> Parallel gate</span>
    <span><b>Tracking</b> Planned road routes, not GPS</span>
  </div>
  <div class="phase-key">
    <div><i style="background:#60a5fa"></i> Plan &amp; staff</div>
    <div><i style="background:#f59e0b"></i> Risk / MRM</div>
    <div><i style="background:#14b8a6"></i> Mission approval</div>
    <div><i style="background:#a78bfa"></i> Execute &amp; monitor</div>
  </div>
</header>

<main>
  <div class="diagram-shell">
    <div class="scroll-hint"><span>Ground operations · master flow</span><span>Scroll horizontally at smaller widths · full-resolution PNG in docs/</span></div>
    <div class="diagram">
      <svg class="edges" viewBox="0 0 2000 2460" aria-hidden="true">
        <defs>
          <marker id="arrow-plan" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#60a5fa"/></marker>
          <marker id="arrow-mrm" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#f59e0b"/></marker>
          <marker id="arrow-approve" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#14b8a6"/></marker>
          <marker id="arrow-exec" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#a78bfa"/></marker>
        </defs>
        {"".join(edges)}
      </svg>

      {band(40, 190, 1920, 560, "plan", "01", "Plan &amp; staff the ground operation", "A3 – Operations / Operations Center")}
      {band(40, 780, 1920, 1210, "ready", "02", "Build readiness — two parallel tracks", "MRM assessment  ·  mission routing / approval")}
      {band(40, 2020, 1920, 400, "exec", "03", "Execute, monitor &amp; retain", "Operations Center · mission record")}

      <div class="divider"></div>

      {"".join(plan_nodes)}

      <div class="plan-strip" style="left:70px;top:515px;width:1600px">
        <div><b>Required to save</b><span>Unit, title, purpose, estimated start and end, location. Saving records revision 1 and an audit entry.</span></div>
        <div><b>Required to open the MRM</b><span>MRM lead from the personnel involved, plus an evaluator from the unit. No team, no Start button.</span></div>
        <div><b>Optional vehicle branch</b><span>One trip per vehicle, each with a unique driver from the roster and an itinerary; the road route is calculated after saving.</span></div>
      </div>

      <div class="split-banner" style="left:70px;top:620px;width:1600px">
        <div><b>Saved mission opens two tracks</b><span>MRM readiness (left) and mission approval (right) run in parallel. Finishing one does not finish the other.</span></div>
        <div><b>Final approval needs both</b><span>An authority-approved MRM, plus a mission approval that covers the current revision.</span></div>
      </div>

      {"".join(mrm_nodes)}
      {"".join(approve_nodes)}
      {"".join(exec_nodes)}
    </div>
  </div>

  {REFERENCES}
</main>

<footer>
  <span><b>TOWEMS · Tactical Operations Wings Enterprise Management System</b> — Ground Operations flow. Generated from current mission, MRM, approval and vehicle-trip implementation.</span>
  <span>Readiness = MRM authority-approved + approval covering the current revision. Tracking = planned road geometry only.</span>
</footer>
</body>
</html>
"""

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(body)

# A diagram-only page, used for the clean PNG. It reuses the same rendered
# diagram, so the image can never drift from the interactive page.
DIAGRAM_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ground-operations-flow-diagram.html")
shell_start = body.index('<div class="diagram-shell">')
shell_end = body.index('<section class="reference">', shell_start)
shell = body[shell_start:shell_end].rstrip()
standalone = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TOWEMS — Ground Operations Flow (diagram)</title>
<style>{CSS}
html, body {{ background: #edf2f8; }}
body {{ margin: 0; position: relative; }}
.diagram-shell {{ width: 2000px; height: 2460px; border: 0; border-radius: 0; box-shadow: none; overflow: hidden; }}
.scroll-hint {{ display: none; }}
.flow-title {{ position: absolute; left: 42px; top: 30px; z-index: 10; font-size: 26px; font-weight: 800; letter-spacing: -.02em; color: #0f172a; }}
.flow-title b {{ color: #1d4ed8; }}
.flow-sub {{ position: absolute; left: 44px; top: 67px; z-index: 10; font-size: 10.5px; font-weight: 800; letter-spacing: .18em; text-transform: uppercase; color: #64748b; }}
.flow-meta {{ position: absolute; right: 40px; top: 60px; z-index: 10; font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: #475569; }}
.flow-meta b {{ color: #1d4ed8; }}
.flow-legend {{ position: absolute; right: 40px; top: 30px; z-index: 10; display: flex; gap: 14px; align-items: center; }}
.flow-legend span {{ display: flex; align-items: center; gap: 6px; font-size: 9.5px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: #475569; }}
.flow-legend i {{ display: inline-block; width: 16px; height: 3px; border-radius: 2px; }}
.flow-legend i.dash {{ background: repeating-linear-gradient(90deg, #94a3b8 0 4px, transparent 4px 7px); }}
</style>
</head>
<body>
<div class="flow-title">TOWEMS&nbsp;<b>Ground Operations</b> · Mission &amp; risk workflow</div>
<div class="flow-sub">Current system flow · ground scope · planned road routes, not GPS</div>
<div class="flow-legend">
  <span><i style="background:#60a5fa"></i>Plan</span>
  <span><i style="background:#f59e0b"></i>Risk / MRM</span>
  <span><i style="background:#14b8a6"></i>Approval</span>
  <span><i style="background:#a78bfa"></i>Execute</span>
  <span><i class="dash"></i>Loop / optional</span>
</div>
<div class="flow-meta"><b>As implemented</b> 31 Oct 2026</div>
{shell}
</body>
</html>
"""
with open(DIAGRAM_OUT, "w", encoding="utf-8") as fh:
    fh.write(standalone)
print(OUT, len(body))
print(DIAGRAM_OUT, len(standalone))

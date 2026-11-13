import { useState } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import MrmTeamPicker from "../components/MrmTeamPicker.jsx";
import PdfViewer from "../components/PdfViewer.jsx";
import AirFlightOperationsView from "../components/AirFlightOperationsView.jsx";
import {
  Badge,
  Button,
  // Every mission section can fold away, so the long record stays navigable.
  CollapsibleCard as Card,
  DateTimeInput,
  ErrorBanner,
  Input,
  Loading,
  Modal,
  Select,
  StatusBadge,
  Textarea,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateTime, toApiDateTime, toInputDateTime } from "../utils/format.js";
import { refreshNotifications } from "../utils/notifications.js";

//: Fallback list of posts that may hold approval authority. The API sends its
//: own copy; this is only used before that response arrives.
const APPROVING_POSTS = [
  { value: "commander", label: "Commander" },
  { value: "deputy_commander", label: "Deputy Commander" },
  { value: "director_operations", label: "Director for Operations" },
  { value: "operations_officer", label: "Operations Officer" },
];

export default function MissionDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { user, permissions } = useAuth();
  const { data, loading, error, reload } = useApi(`/missions/${id}/`, { deps: [id] });
  const canPlan = Boolean(permissions?.can_plan_missions);
  // The eligible approvers, scoped by the server to this mission's unit. Only
  // the planner sees the picker, so only the planner needs the roster.
  const eligibility = useApi(`/missions/${id}/approving-authorities/`, {
    enabled: canPlan,
    // Re-run once permissions resolve, otherwise the roster would stay empty
    // for a planner whose permissions arrive after the first render.
    deps: [id, canPlan],
  });
  // The destination-AOR handoff. The destination Operations Center alone is
  // notified and monitors; it decides whether to forward to command posts.
  const handoff = useApi(`/missions/${id}/aor-handoff/`, { deps: [id] });
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [teamOpen, setTeamOpen] = useState(false);
  const [teamDraft, setTeamDraft] = useState([]);
  const [teamBusy, setTeamBusy] = useState(false);
  const [teamError, setTeamError] = useState("");
  const [starting, setStarting] = useState(false);
  const [authorityRole, setAuthorityRole] = useState("");
  const [authorityUser, setAuthorityUser] = useState("");
  const [authorityNote, setAuthorityNote] = useState("");
  const [authorityBusy, setAuthorityBusy] = useState(false);
  const [authorityError, setAuthorityError] = useState("");
  const [orderSignatory, setOrderSignatory] = useState("");
  const [orderSignatoryBusy, setOrderSignatoryBusy] = useState(false);
  const [orderSignatoryError, setOrderSignatoryError] = useState("");
  const [orderNote, setOrderNote] = useState("");
  const [orderSignBusy, setOrderSignBusy] = useState(false);
  const [orderSignError, setOrderSignError] = useState("");
  // "return" or "reject" while the explanation dialog is open.
  const [decisionOpen, setDecisionOpen] = useState(null);
  const [decisionNote, setDecisionNote] = useState("");
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [decisionError, setDecisionError] = useState("");
  const [dispatchBusy, setDispatchBusy] = useState(false);
  const [dispatchError, setDispatchError] = useState("");
  const [arrivalOpen, setArrivalOpen] = useState(false);
  const [arrivalAt, setArrivalAt] = useState("");
  const [arrivalReady, setArrivalReady] = useState(true);
  const [arrivalComplete, setArrivalComplete] = useState(false);
  const [arrivalBusy, setArrivalBusy] = useState(false);
  const [arrivalError, setArrivalError] = useState("");
  const [forwardBusy, setForwardBusy] = useState(false);
  const [forwardError, setForwardError] = useState("");
  // The MRM record opens in place as a PDF, like the risk register does.
  const [pdfOpen, setPdfOpen] = useState(false);
  // The formal Mission Order is generated from this mission when opened.
  const [orderPdfOpen, setOrderPdfOpen] = useState(false);
  // One consolidated PDF so reviewers read the mission, its approvals and its
  // order in a couple of pages instead of scrolling the detail page.
  const [briefPdfOpen, setBriefPdfOpen] = useState(false);
  // Trip id whose road route is being calculated.
  const [routeBusy, setRouteBusy] = useState(0);
  const [routeError, setRouteError] = useState("");
  const [routingBusy, setRoutingBusy] = useState(false);
  const [routingError, setRoutingError] = useState("");
  const [resubmitting, setResubmitting] = useState(false);
  const [resubmitError, setResubmitError] = useState("");

  if (loading && !data) return <Loading label="Loading mission…" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  // Personnel, MRM, vehicle movement and the Mission Order are ground-operation
  // records. Air operations monitor the flight itself only.
  const isGround = data.category === "ground";
  const dependency = data.dependency || { enabled: false };
  const airDetail = data.air_detail || {};

  function openDecision(kind) {
    setDecisionError("");
    setDecisionNote("");
    setDecisionOpen(kind);
  }

  async function beginRouting() {
    setRoutingBusy(true);
    setRoutingError("");
    setActionError("");
    try {
      await api.post(`/missions/${data.id}/begin-routing/`, {});
      setNotice("Approval routing started. The first authority was notified.");
      reload();
    } catch (err) {
      setRoutingError(err.fieldErrors?.detail || err.message);
    } finally {
      setRoutingBusy(false);
    }
  }

  function openTeam() {
    setTeamError("");
    setTeamDraft(
      (data.mrm_team || []).map((row) => ({ user: row.user, role: row.role, name: row.name }))
    );
    setTeamOpen(true);
  }

  async function saveTeam() {
    setTeamBusy(true);
    setTeamError("");
    try {
      await api.patch(`/missions/${data.id}/`, {
        mrm_team: teamDraft.map((row) => ({ user: Number(row.user), role: row.role })),
      });
      setTeamOpen(false);
      setNotice("MRM team updated. Newly assigned personnel were notified.");
      reload();
    } catch (err) {
      setTeamError(err.fieldErrors?.mrm_team || err.message);
    } finally {
      setTeamBusy(false);
    }
  }

  async function startMrm() {
    setStarting(true);
    setActionError("");
    setNotice("");
    try {
      const created = await api.post(`/missions/${data.id}/start-mrm/`, {});
      refreshNotifications();
      // Straight into the assessment: a freshly created draft is empty, so
      // staying here would only repeat that it exists.
      const mrmId = created?.mrm_form;
      if (mrmId) {
        navigate(`/risk/${mrmId}`);
        return;
      }
      setNotice("MRM draft created and linked to this mission.");
      reload();
    } catch (err) {
      setActionError(err.fieldErrors?.detail || err.message);
    } finally {
      setStarting(false);
    }
  }

  async function assignAuthority() {
    setAuthorityError("");
    setActionError("");
    if (!authorityRole || !authorityUser) {
      setAuthorityError("Choose the post and the person who will approve this mission.");
      return;
    }
    setAuthorityBusy(true);
    try {
      await api.post(`/missions/${data.id}/approving-authority/`, {
        role: authorityRole,
        user_id: Number(authorityUser),
        note: authorityNote,
      });
      refreshNotifications();
      setNotice("Approving authority assigned. They were notified and the mission is in their queue.");
      setAuthorityNote("");
      reload();
    } catch (err) {
      setAuthorityError(err.fieldErrors?.detail || err.message);
    } finally {
      setAuthorityBusy(false);
    }
  }

  async function assignOrderSignatory() {
    setOrderSignatoryError("");
    setActionError("");
    if (!orderSignatory) {
      setOrderSignatoryError("Choose the person who will sign the Mission Order.");
      return;
    }
    setOrderSignatoryBusy(true);
    try {
      await api.post(`/missions/${data.id}/mission-order/signatory/`, {
        user_id: Number(orderSignatory),
      });
      setNotice(
        "Mission Order signatory designated. They will sign after the mission is approved."
      );
      reload();
    } catch (err) {
      setOrderSignatoryError(err.fieldErrors?.detail || err.message);
    } finally {
      setOrderSignatoryBusy(false);
    }
  }

  async function approveMissionOrder() {
    setOrderSignError("");
    setActionError("");
    setOrderSignBusy(true);
    try {
      await api.post(`/missions/${data.id}/mission-order/approve/`, {
        note: orderNote,
      });
      refreshNotifications();
      setOrderNote("");
      setNotice("Mission Order signed and approved. Dispatch is no longer blocked by the order.");
      reload();
    } catch (err) {
      setOrderSignError(err.fieldErrors?.detail || err.message);
    } finally {
      setOrderSignBusy(false);
    }
  }

  async function decide(decision, note) {
    if (!note.trim()) {
      setDecisionError("Add your comment before recording the routing decision.");
      return;
    }
    setDecisionBusy(true);
    setDecisionError("");
    setActionError("");
    try {
      await api.post(`/missions/${data.id}/routing-decision/`, { decision, note });
      refreshNotifications();
      setDecisionOpen(null);
      setDecisionNote("");
      setNotice(
        decision === "approve"
          ? isFinalApproval
            ? `Mission approved.${
                higherLabels.length
                  ? ` ${higherLabels.join(" and ")} notified for information.`
                  : ""
              }`
            : "Approval and comment recorded. The mission was forwarded to the next authority."
          : decision === "return"
            ? "Mission returned for revision."
            : "Mission rejected."
      );
      reload();
    } catch (err) {
      setDecisionError(err.fieldErrors?.detail || err.message);
    } finally {
      setDecisionBusy(false);
    }
  }

  function openAirBoard() {
    const flightDate =
      airDetail.operation_date || String(data.start_at || "").slice(0, 10);
    navigate("/missions/air", { state: { airDate: flightDate } });
  }

  async function dispatchMission() {
    setDispatchBusy(true);
    setDispatchError("");
    setActionError("");
    try {
      await api.post(`/missions/${data.id}/dispatch/`, {});
      refreshNotifications();
      setNotice("Mission dispatched. It is now in progress.");
      reload();
    } catch (err) {
      setDispatchError(err.fieldErrors?.detail || err.message);
    } finally {
      setDispatchBusy(false);
    }
  }

  function openArrival() {
    setArrivalAt(toInputDateTime(new Date().toISOString()));
    setArrivalReady(true);
    setArrivalComplete(false);
    setArrivalError("");
    setArrivalOpen(true);
  }

  async function recordArrival() {
    setArrivalBusy(true);
    setArrivalError("");
    setActionError("");
    try {
      await api.post(`/missions/${data.id}/air-arrival/`, {
        at: toApiDateTime(arrivalAt),
        ready: arrivalReady,
        complete: arrivalComplete,
      });
      setNotice(
        arrivalComplete
          ? "Arrival recorded and the mission closed."
          : "Arrival recorded. The dependent mission can now be released when its condition is met."
      );
      setArrivalOpen(false);
      reload();
    } catch (err) {
      setArrivalError(err.fieldErrors?.detail || err.message);
    } finally {
      setArrivalBusy(false);
    }
  }

  async function forwardHandoff() {
    setForwardBusy(true);
    setForwardError("");
    setActionError("");
    try {
      const result = await api.post(`/missions/${data.id}/aor-handoff/`, {});
      setNotice(
        result.forwarded
          ? `Mission details forwarded to ${result.forwarded} command post recipient${
              result.forwarded === 1 ? "" : "s"
            }.`
          : "The command posts already have these mission details."
      );
      handoff.reload();
      reload();
    } catch (err) {
      setForwardError(err.fieldErrors?.detail || err.message);
    } finally {
      setForwardBusy(false);
    }
  }

  async function calculateRoute(tripId) {
    setRouteBusy(tripId);
    setRouteError("");
    setActionError("");
    try {
      await api.post(`/trips/${tripId}/route/`, {});
      setNotice("Planned road route calculated from the itinerary.");
      reload();
    } catch (err) {
      setRouteError(err.fieldErrors?.detail || err.message);
    } finally {
      setRouteBusy(0);
    }
  }


  async function resubmitMission() {
    setResubmitting(true);
    setResubmitError("");
    try {
      await api.post(`/missions/${data.id}/resubmit/`, {});
      refreshNotifications();
      setNotice("Mission resubmitted. The authority that returned it was notified.");
      reload();
    } catch (err) {
      setResubmitError(err.fieldErrors?.detail || err.message);
    } finally {
      setResubmitting(false);
    }
  }

  const mrm = data.mrm || {};
  // Only the lead the Operations Center named may create the assessment; the
  // server enforces the same rule, so the button follows it rather than
  // offering an action that would be refused.
  const isMrmLead = (data.mrm_team || []).some(
    (row) => row.user === user?.id && row.role === "lead"
  );
  const mrmApproved = mrm.status === "approved";
  const mrmTone =
    mrm.status === "approved"
      ? "success"
      : mrm.status === "returned"
        ? "danger"
        : mrm.status === "submitted"
          ? "info"
          : mrm.status === "in_progress" || mrm.status === "pending"
            ? "warning"
            : "muted";

  const approval = data.approval || {};
  const missionOrder = data.mission_order || {};
  const routePlan = data.route_plan || { stages: [], higher_authorities: [] };
  const routeSteps = routePlan.stages || [];
  // The named authority is the last signature: approving there approves the
  // mission itself, it does not forward it to anybody.
  const isFinalApproval = Boolean(
    routePlan.current_role &&
      routePlan.final_role &&
      routePlan.current_role === routePlan.final_role
  );
  const higherLabels = (routePlan.higher_authorities || []).map((row) => row.label);
  const routeStarted = (data.routing || []).length > 0;
  const routeTone =
    isGround && missionOrder.status === "approved"
      ? "success"
      : approval.status === "approved"
        ? isGround
          ? "warning"
          : "success"
        : routeStarted
          ? "warning"
          : "muted";
  const routeLabel =
    isGround && missionOrder.status === "approved"
      ? "Complete"
      : approval.status === "approved"
        ? isGround
          ? "Awaiting signature"
          : "Complete"
        : routePlan.complete
          ? "Approval complete"
          : routeStarted
            ? "In routing"
            : "Not started";
  // Once the approving authority has released the mission, or it has been
  // dispatched or concluded, the plan is fixed: its MRM team, its approving
  // authority and its planned routes were all part of what was approved. The
  // planning controls are hidden rather than offered and then refused.
  const planningLocked =
    ["approved", "active", "completed", "cancelled"].includes(data.status) ||
    (isGround && approval.status === "approved");
  const execution = data.execution || { blockers: [] };
  const vehicles = data.vehicles || [];
  const blockers = execution.blockers || [];
  const approvingPosts = eligibility.data?.roles?.length
    ? eligibility.data.roles
    : APPROVING_POSTS;
  const eligiblePeople = eligibility.data?.results ?? [];
  const eligibleForRole = authorityRole
    ? eligiblePeople.filter((person) => person.role === authorityRole)
    : eligiblePeople;

  const approvalTone =
    approval.status === "approved"
      ? "success"
      : approval.status === "pending"
        ? "warning"
        : approval.status === "returned" || approval.status === "rejected"
          ? "danger"
          : "muted";
  const executionTone =
    execution.state === "blocked"
      ? "danger"
      : execution.state === "ready"
        ? "success"
        : execution.state === "in_progress"
          ? "info"
          : "muted";
  const missionOrderTone =
    missionOrder.status === "approved"
      ? "success"
      : missionOrder.status === "awaiting_signature"
        ? "warning"
        : "muted";
  const signatoryOptions = eligiblePeople;
  const selectedSignatory =
    orderSignatory || String(missionOrder.signatory_id || "");

  // The action panel is stage-aware: it offers the one step this viewer may
  // take now, in the order the mission actually moves - MRM assessment,
  // approval routing, Mission Order signature, dispatch.
  const risk = data.risk_assessment || null;
  const myTeamRole = (data.mrm_team || []).find((row) => row.user === user?.id)?.role;
  const mrmWorkOpen = isGround && Boolean(mrm.requires_mrm) && !mrmApproved;
  const isMrmCreator = myTeamRole === "lead";
  const isMrmEvaluator = myTeamRole === "reviewer" || mrm.evaluator === user?.id;
  const canStartMrm = mrmWorkOpen && !mrm.mrm_form && isMrmCreator;
  const canContinueMrm =
    mrmWorkOpen && Boolean(mrm.mrm_form) && isMrmCreator && !risk?.creator_signed;
  const canEvaluateMrm =
    mrmWorkOpen &&
    Boolean(mrm.mrm_form) &&
    isMrmEvaluator &&
    Boolean(risk?.creator_signed) &&
    !risk?.evaluator_signed;
  const isMrmAuthority =
    Boolean(risk?.decision_authority_id) && risk.decision_authority_id === user?.id;
  const canApproveMrm =
    mrmWorkOpen &&
    Boolean(mrm.mrm_form) &&
    isMrmAuthority &&
    Boolean(risk?.evaluator_signed) &&
    !risk?.authority_approved;
  // The Operations Center owns the revision: a returned mission is edited and
  // resubmitted from here, not approved by the reviewer while unchanged.
  const canRevise =
    isGround &&
    approval.status === "returned" &&
    (canPlan || data.created_by === user?.id);
  const canEditAir =
    data.category === "air" &&
    canPlan &&
    !["completed", "cancelled"].includes(data.status);
  const action =
    data.category === "air"
      ? canEditAir
        ? "air_actions"
        : "none"
      : canRevise
        ? "revise"
        : missionOrder.can_approve
          ? "sign_order"
          : approval.can_decide
            ? "decide"
            : canStartMrm
              ? "start_mrm"
              : canContinueMrm
                ? "continue_mrm"
                : canEvaluateMrm
                  ? "evaluate_mrm"
                  : canApproveMrm
                    ? "approve_mrm"
                    : canPlan && !planningLocked && routeSteps.length > 0 && !routeStarted
                      ? "start_routing"
                      : canPlan && !planningLocked && routeSteps.length === 0
                        ? "assign_authority"
                        : canPlan && execution.state === "ready"
                          ? "dispatch"
                          : "none";
  const actionStage =
    action === "air_actions"
      ? "Flight Mission"
      : action === "revise"
        ? `Revision requested${routePlan.current_label ? ` · ${routePlan.current_label}` : ""}`
        : action === "sign_order"
          ? "Mission Order · signature"
          : action === "decide"
            ? `Approval routing${routePlan.current_label ? ` · ${routePlan.current_label}` : ""}`
            : action === "start_mrm" || action === "continue_mrm"
              ? "Mission risk management · creator"
              : action === "evaluate_mrm"
                ? "Mission risk management · evaluator"
                : action === "approve_mrm"
                  ? "Mission risk management · decision authority"
                  : action === "none"
                    ? "Current stage"
                    : "Operations Center";
  const waitingOn =
    isGround && approval.status === "returned"
      ? "the Operations Center to revise and resubmit"
      : isGround && routePlan.current_label
        ? `${routePlan.current_label}${
            routePlan.current_assignee_name ? ` — ${routePlan.current_assignee_name}` : ""
          }`
        : mrmWorkOpen
          ? `the MRM assessment (${(mrm.label || "pending").toLowerCase()})`
          : data.category === "air"
            ? "the Daily Operations Board"
            : execution.state === "in_progress"
              ? "the mission to be completed"
              : "no further step";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>
            {data.mission_number} · {data.title}
          </h1>
          <p>
            <Badge tone={data.category === "air" ? "info" : "muted"}>
              {data.category === "air" ? "Air Operations" : "Ground Operations"}
            </Badge>{" "}
            <StatusBadge status={data.status} />
          </p>
        </div>
        <div className="btn-row">
          <Button variant="ghost" size="md" onClick={() => navigate("/missions")}>
            Back
          </Button>
        </div>
      </div>

      <ErrorBanner message={actionError} />
      {notice && <div className="alert alert-success">{notice}</div>}
      {isGround && location.state?.missionUpdated && approval.status === "returned" && (
        <div className="alert alert-warning" role="status">
          <div>
            <strong>Mission updated.</strong>{" "}
            {approval.returned_by_name
              ? `${approval.returned_by_name}${
                  approval.returned_by_stage_label
                    ? ` (${approval.returned_by_stage_label})`
                    : ""
                } returned it.`
              : "It was returned for revision."}{" "}
            Resubmit the revised mission to that post so the changes can be reviewed.
          </div>
          <div className="btn-row" style={{ marginTop: 8 }}>
            <Button onClick={resubmitMission} disabled={resubmitting}>
              {resubmitting
                ? "Resubmitting…"
                : `Resubmit to ${approval.returned_by_name || "the reviewer"}`}
            </Button>
          </div>
          {resubmitError && <p className="field-message">{resubmitError}</p>}
        </div>
      )}

      <div className="mission-view">
        <div className="mission-view-main">
          <section className="card mission-brief-card">
            <header className="card-head">
              <h2>Mission Brief</h2>
              <div className="card-actions">
                <Button variant="ghost" size="sm" onClick={() => setBriefPdfOpen(true)}>
                  Open full screen
                </Button>
              </div>
            </header>
            <div className="mission-brief-body">
              <PdfViewer
                // Remount when the mission changes so the brief on screen is
                // never the version from before an approval or signature.
                key={data.updated_at}
                path={`/missions/${data.id}/brief/`}
                title={`Mission Brief ${data.mission_number}`}
              />
            </div>
          </section>
        </div>

        <div className="mission-view-side">
          <Card
            title={
              <span className="status-chip">
                Your action
                <Badge tone={action === "none" ? "muted" : "warning"}>{actionStage}</Badge>
              </span>
            }
            defaultOpen
          >
            {action === "revise" && (
              <>
                <p className="hint">
                  <strong>Returned for revision.</strong>{" "}
                  {approval.note
                    ? approval.note
                    : "The reviewing authority asked for changes before this mission can be approved."}
                </p>
                <p className="hint">
                  {approval.returned_by_name
                    ? `${approval.returned_by_name}${
                        approval.returned_by_stage_label
                          ? ` (${approval.returned_by_stage_label})`
                          : ""
                      } returned it`
                    : "The reviewing authority returned it"}
                  {approval.decided_at ? ` on ${formatDateTime(approval.decided_at)}` : ""}.
                  Edit the mission, save the changes, then resubmit it to the same post.
                </p>
                <div className="btn-row">
                  <Button onClick={() => navigate(`/missions/${data.id}/edit`)}>
                    Edit mission
                  </Button>
                  <Button variant="ghost" onClick={resubmitMission} disabled={resubmitting}>
                    {resubmitting
                      ? "Resubmitting…"
                      : `Resubmit to ${approval.returned_by_name || "the reviewer"}`}
                  </Button>
                </div>
                {resubmitError && <p className="field-message">{resubmitError}</p>}
              </>
            )}

            {action === "decide" && (
              <>
                <p className="hint">
                  {isFinalApproval
                    ? `Every routing step needs a comment. Approving here completes the mission's approval${
                        higherLabels.length
                          ? `; ${higherLabels.join(" and ")} will be notified for information only`
                          : ""
                      }.`
                    : "Every routing step needs a comment. Recording a decision here forwards the mission to the next authority or returns it to the Operations Center."}
                </p>
                <div className="btn-row">
                  <Button
                    onClick={() => openDecision("approve")}
                    disabled={isGround && !mrmApproved}
                    title={
                      !isGround || mrmApproved
                        ? undefined
                        : "The mission's MRM assessment must be completed and approved first."
                    }
                  >
                    {isFinalApproval ? "Approve mission" : "Approve and forward"}
                  </Button>
                  <Button variant="ghost" onClick={() => openDecision("return")}>
                    Return for revision
                  </Button>
                  <Button variant="ghost" onClick={() => openDecision("reject")}>
                    Reject
                  </Button>
                </div>
              </>
            )}

            {action === "sign_order" && (
              <>
                <p className="hint">
                  Your signature applies to mission revision {missionOrder.revision}. Dispatch
                  stays blocked until this order is approved.
                </p>
                <Input
                  value={orderNote}
                  onChange={(event) => setOrderNote(event.target.value)}
                  placeholder="Signature note (optional)"
                  aria-label="Mission Order signature note"
                />
                <div className="btn-row" style={{ marginTop: 10 }}>
                  <Button onClick={approveMissionOrder} disabled={orderSignBusy}>
                    {orderSignBusy ? "Signing…" : "Sign and approve Mission Order"}
                  </Button>
                </div>
                {orderSignError && <p className="field-message">{orderSignError}</p>}
              </>
            )}

            {action === "start_mrm" && (
              <>
                <p className="hint">
                  You are the MRM creator for this mission. Start the assessment, complete it,
                  and it will pass to the evaluator.
                </p>
                <Button
                  onClick={startMrm}
                  disabled={starting || !(data.mrm_team || []).length}
                >
                  {starting ? "Creating…" : "Start MRM assessment"}
                </Button>
                {!(data.mrm_team || []).length && (
                  <p className="hint">Assign at least one MRM team member first.</p>
                )}
              </>
            )}

            {(action === "continue_mrm" || action === "evaluate_mrm") && (
              <>
                <p className="hint">
                  {action === "continue_mrm"
                    ? "The assessment is with you. Complete the hazard scoring and sign it as creator."
                    : "The creator has signed the assessment. Review it and record your evaluation."}
                </p>
                <Button onClick={() => navigate(`/risk/${mrm.mrm_form}`)}>
                  {action === "continue_mrm" ? "Continue assessment" : "Evaluate assessment"}
                </Button>
              </>
            )}

            {action === "approve_mrm" && (
              <>
                <p className="hint">
                  The creator and the evaluator have signed this assessment. As decision
                  authority, approve it to release the mission&apos;s MRM requirement.
                </p>
                <Button onClick={() => navigate(`/risk/${mrm.mrm_form}`)}>
                  Approve assessment
                </Button>
              </>
            )}

            {action === "start_routing" && (
              <>
                <p className="hint">
                  The route is configured. Starting it notifies the first authority and opens
                  the approval chain.
                </p>
                <Button onClick={beginRouting} disabled={routingBusy}>
                  {routingBusy ? "Starting…" : "Start approval routing"}
                </Button>
                {routingError && <p className="field-message">{routingError}</p>}
              </>
            )}

            {action === "assign_authority" && (
              <>
                <p className="hint">
                  Name the approving authority. The route follows the command ladder only as far
                  as that post, and the person named is the only one who may decide.
                </p>
                {eligiblePeople.length === 0 ? (
                  <p className="hint">
                    No active person holds Commander, Deputy Commander, Director for Operations
                    or Operations Officer in this unit. Appoint somebody to the post before
                    routing this mission.
                  </p>
                ) : (
                  <>
                    <Select
                      value={authorityRole}
                      onChange={(event) => {
                        setAuthorityRole(event.target.value);
                        setAuthorityUser("");
                      }}
                      aria-label="Approving authority post"
                      style={{ marginBottom: 10 }}
                    >
                      <option value="">Select the post…</option>
                      {approvingPosts.map((post) => (
                        <option key={post.value} value={post.value}>
                          {post.label}
                        </option>
                      ))}
                    </Select>
                    <Select
                      value={authorityUser}
                      onChange={(event) => setAuthorityUser(event.target.value)}
                      aria-label="Approving authority"
                      style={{ marginBottom: 10 }}
                    >
                      <option value="">Select the person…</option>
                      {eligibleForRole.map((person) => (
                        <option key={person.user_id} value={person.user_id}>
                          {person.label}
                        </option>
                      ))}
                    </Select>
                    <Input
                      placeholder="Note for the approver (optional)"
                      value={authorityNote}
                      onChange={(event) => setAuthorityNote(event.target.value)}
                      aria-label="Note for the approver"
                    />
                    <div className="btn-row" style={{ marginTop: 10 }}>
                      <Button onClick={assignAuthority} disabled={authorityBusy}>
                        {authorityBusy ? "Assigning…" : "Assign approving authority"}
                      </Button>
                      {mrmApproved ? (
                        <Button variant="ghost" onClick={() => setPdfOpen(true)}>
                          View MRM PDF
                        </Button>
                      ) : (
                        <Button variant="ghost" onClick={openTeam}>
                          Manage MRM team
                        </Button>
                      )}
                    </div>
                    {authorityError && <p className="field-message">{authorityError}</p>}
                    {isGround && (
                      <div className="form-section">
                        <h3>Mission Order signatory</h3>
                        <Select
                          value={selectedSignatory}
                          onChange={(event) => setOrderSignatory(event.target.value)}
                          aria-label="Mission Order signatory"
                        >
                          <option value="">Default to the approving authority</option>
                          {signatoryOptions.map((person) => (
                            <option key={person.user_id} value={person.user_id}>
                              {person.label}
                            </option>
                          ))}
                        </Select>
                        <div className="btn-row" style={{ marginTop: 10 }}>
                          <Button
                            variant="ghost"
                            onClick={assignOrderSignatory}
                            disabled={orderSignatoryBusy}
                          >
                            {orderSignatoryBusy ? "Saving…" : "Save signatory"}
                          </Button>
                        </div>
                        {orderSignatoryError && (
                          <p className="field-message">{orderSignatoryError}</p>
                        )}
                      </div>
                    )}
                  </>
                )}
              </>
            )}

            {action === "air_actions" && (
              <>
                <p className="hint">
                  Edit the flight mission, or open the Daily Operations Board on
                  its flight date to manage departure and actual times.
                </p>
                <div className="btn-row">
                  <Button onClick={() => navigate(`/missions/${data.id}/edit`)}>
                    Edit mission
                  </Button>
                  <Button variant="ghost" onClick={openAirBoard}>
                    Daily Operations Board
                  </Button>
                </div>
              </>
            )}

            {action === "dispatch" && (
              <>
                <p className="hint">
                  The MRM, the mission-details approval and the Mission Order are all
                  approved. Dispatched, the mission is in progress.
                </p>
                <Button onClick={dispatchMission} disabled={dispatchBusy}>
                  {dispatchBusy ? "Dispatching…" : "Dispatch mission"}
                </Button>
                {dispatchError && <p className="field-message">{dispatchError}</p>}
              </>
            )}

            {action === "none" && (
              <>
                <p className="hint">
                  {["completed", "cancelled"].includes(data.status)
                    ? `This mission is ${data.status}. Nothing further is required from you.`
                    : execution.state === "blocked" && blockers.length
                      ? `Execution is blocked: ${blockers
                          .map((blocker) => blocker.label)
                          .join(", ")}. Waiting on ${waitingOn}.`
                      : `Nothing for you at this stage. Waiting on ${waitingOn}.`}
                </p>
                {data.category === "air" && (
                  <div className="btn-row" style={{ marginTop: 10 }}>
                    <Button variant="ghost" onClick={openAirBoard}>
                      Daily Operations Board
                    </Button>
                  </div>
                )}
              </>
            )}
          </Card>

          {(dependency.enabled || data.category === "air") && (
            <Card title="Mission sequence" defaultOpen>
              {dependency.enabled ? (
                <>
                  <p>
                    <strong>{dependency.mission_number}</strong> · {dependency.title}
                  </p>
                  <p className="hint">
                    {dependency.release_label} · {dependency.buffer_minutes} minute
                    buffer
                  </p>
                  <dl className="details">
                    <div className="detail">
                      <dt>Predecessor status</dt>
                      <dd>{dependency.status_label || dependency.status}</dd>
                    </div>
                    <div className="detail">
                      <dt>Planned ETA</dt>
                      <dd>{dependency.eta ? formatDateTime(dependency.eta) : "—"}</dd>
                    </div>
                    <div className="detail">
                      <dt>Actual arrival</dt>
                      <dd>
                        {dependency.actual_arrival
                          ? formatDateTime(dependency.actual_arrival)
                          : "—"}
                      </dd>
                    </div>
                    <div className="detail">
                      <dt>Rendezvous ready</dt>
                      <dd>
                        {dependency.rendezvous_ready_at
                          ? formatDateTime(dependency.rendezvous_ready_at)
                          : "—"}
                      </dd>
                    </div>
                    <div className="detail">
                      <dt>Earliest departure</dt>
                      <dd>
                        {dependency.earliest_departure
                          ? formatDateTime(dependency.earliest_departure)
                          : "—"}
                      </dd>
                    </div>
                  </dl>
                  <Badge
                    tone={
                      dependency.state === "ready"
                        ? "success"
                        : dependency.state === "failed"
                          ? "danger"
                          : "warning"
                    }
                  >
                    {dependency.label}
                  </Badge>
                </>
              ) : (
                <p className="hint">
                  This mission has no preposition mission. It starts independently.
                </p>
              )}

              {data.category === "air" && (
                <div className="form-section" style={{ marginTop: 12 }}>
                  <h3>Rendezvous arrival</h3>
                  <p className="hint">
                    Actual arrival:{" "}
                    {airDetail.actual_arrival
                      ? formatDateTime(airDetail.actual_arrival)
                      : "not recorded"}
                    <br />
                    Rendezvous ready:{" "}
                    {airDetail.rendezvous_ready_at
                      ? formatDateTime(airDetail.rendezvous_ready_at)
                      : "not recorded"}
                  </p>
                  {data.status === "active" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={openArrival}
                    >
                      Record arrival
                    </Button>
                  )}
                </div>
              )}
            </Card>
          )}

          {data.category === "air" && <AirFlightOperationsView detail={airDetail} />}

          {isGround && (
          <Card title="Routing trail" defaultOpen>
            {(data.routing || []).length === 0 ? (
              <p className="hint">No routing action is recorded for this mission yet.</p>
            ) : (
              <div className="trail-scroll">
                <table className="trail-table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Post / action</th>
                      <th>By</th>
                      <th>Comment</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.routing.map((row) => (
                      <tr
                        key={row.id}
                        className={row.action === "returned" ? "is-return" : ""}
                      >
                        <td className="trail-when">{formatDateTime(row.created_at)}</td>
                        <td>
                          <strong>{row.stage_label || row.stage}</strong>
                          <span className="trail-action">
                            {row.action_label || row.action}
                          </span>
                        </td>
                        <td>{row.actor_name || row.actor || "—"}</td>
                        <td>{row.note || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          )}

          {canPlan && !planningLocked && (isGround || vehicles.length > 0) && (
            <Card title="Planning tools" defaultOpen>
              {isGround && (
                <div className="btn-row">
                  {mrmApproved ? (
                    <Button variant="ghost" size="sm" onClick={() => setPdfOpen(true)}>
                      View MRM PDF
                    </Button>
                  ) : (
                    <Button variant="ghost" size="sm" onClick={openTeam}>
                      Manage MRM team
                    </Button>
                  )}
                </div>
              )}
              {vehicles.map((entry) => (
                <div key={`plan-${entry.trip_id}-${entry.vehicle_id}`} style={{ marginTop: 12 }}>
                  <p className="hint">
                    {entry.display_name || entry.registration_number} · {entry.trip_number}
                  </p>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => calculateRoute(entry.trip_id)}
                    disabled={routeBusy === entry.trip_id || entry.itinerary.length < 2}
                  >
                    {routeBusy === entry.trip_id ? "Calculating…" : "Calculate road route"}
                  </Button>
                  {entry.itinerary.length < 2 && (
                    <span className="hint"> Add at least two stops first.</span>
                  )}
                </div>
              ))}
              {routeError && <p className="field-message">{routeError}</p>}
            </Card>
          )}

          {handoff.data?.destination &&
            (handoff.data.can_forward || handoff.data.forwarded.length > 0) && (
              <Card title={`Destination AOR — ${handoff.data.destination.unit_acronym}`}>
                <p className="hint">
                  This mission&apos;s operational area is in {handoff.data.destination.aor_name}{" "}
                  ({handoff.data.destination.unit_acronym} AOR). Only the{" "}
                  {handoff.data.destination.unit_acronym} Operations Center was notified and can
                  monitor the movement.
                </p>

                {handoff.data.can_forward && (
                  <div className="form-section">
                    <h3>Forward to your command posts</h3>
                    {handoff.data.recipients.length === 0 ? (
                      <p className="hint">
                        No Commander, Deputy Commander, Director for Operations or Operations
                        Officer holds a post at {handoff.data.destination.unit_acronym} yet.
                      </p>
                    ) : (
                      <>
                        <ul className="selection-list">
                          {handoff.data.recipients.map((row) => (
                            <li key={`${row.role}-${row.id}`}>
                              <span>
                                <strong>{row.post}</strong>
                                <span className="hint">{row.full_name}</span>
                              </span>
                            </li>
                          ))}
                        </ul>
                        <div className="btn-row" style={{ marginTop: 12 }}>
                          <Button onClick={forwardHandoff} disabled={forwardBusy}>
                            {forwardBusy ? "Forwarding…" : "Forward mission details"}
                          </Button>
                        </div>
                        {forwardError && <p className="field-message">{forwardError}</p>}
                      </>
                    )}
                  </div>
                )}

                {handoff.data.forwarded.length > 0 && (
                  <p className="hint" style={{ marginTop: 12 }}>
                    Forwarded to{" "}
                    {handoff.data.forwarded
                      .map((row) => `${row.post} (${row.full_name})`)
                      .join(", ")}
                    .
                  </p>
                )}
              </Card>
            )}
        </div>
      </div>

      {isGround && orderPdfOpen && (
        <Modal
          title={`Mission Order · ${data.mission_number}`}
          onClose={() => setOrderPdfOpen(false)}
          wide
          footer={
            <Button variant="ghost" size="sm" onClick={() => setOrderPdfOpen(false)}>
              Close
            </Button>
          }
        >
          <PdfViewer
            path={`/missions/${data.id}/mission-order/`}
            title={`Mission Order ${data.mission_number}`}
          />
        </Modal>
      )}

      {isGround && pdfOpen && mrm.mrm_form && (
        <Modal
          title={`MRM ${mrm.mrm_number || ""} · ${data.mission_number}`}
          onClose={() => setPdfOpen(false)}
          wide
          footer={
            <Button variant="ghost" size="sm" onClick={() => setPdfOpen(false)}>
              Close
            </Button>
          }
        >
          <PdfViewer
            path={`/risk-register/${mrm.mrm_form}/pdf/`}
            title={`MRM ${mrm.mrm_number || data.mission_number}`}
          />
        </Modal>
      )}

      {briefPdfOpen && (
        <Modal
          title={`Mission Brief · ${data.mission_number}`}
          onClose={() => setBriefPdfOpen(false)}
          wide
          footer={
            <Button variant="ghost" size="sm" onClick={() => setBriefPdfOpen(false)}>
              Close
            </Button>
          }
        >
          <PdfViewer
            path={`/missions/${data.id}/brief/`}
            title={`Mission Brief ${data.mission_number}`}
          />
        </Modal>
      )}

      {isGround && teamOpen && !mrmApproved && (
        <Modal
          title={`MRM team · ${data.mission_number}`}
          onClose={() => setTeamOpen(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setTeamOpen(false)}>
                Cancel
              </Button>
              <Button onClick={saveTeam} disabled={teamBusy}>
                {teamBusy ? "Saving…" : "Save MRM team"}
              </Button>
            </>
          }
        >
          <p className="hint" style={{ marginBottom: 10 }}>
            Only personnel in this mission unit's command tree can be assigned. Removing someone
            ends their assignment but preserves the audit history.
          </p>
          <MrmTeamPicker
            unitId={data.unit}
            value={teamDraft}
            onChange={setTeamDraft}
            disabled={teamBusy}
          />
          {teamError && <p className="field-message">{teamError}</p>}
        </Modal>
      )}

      {decisionOpen && (
        <Modal
          title={
            decisionOpen === "approve"
              ? isFinalApproval
                ? "Approve mission"
                : "Approve and forward mission"
              : decisionOpen === "return"
                ? "Return mission for revision"
                : "Reject mission"
          }
          onClose={() => setDecisionOpen(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDecisionOpen(null)}>
                Cancel
              </Button>
              <Button
                onClick={() => decide(decisionOpen, decisionNote)}
                disabled={decisionBusy || !decisionNote.trim()}
              >
                {decisionBusy
                  ? "Saving…"
                  : decisionOpen === "approve"
                    ? isFinalApproval
                      ? "Approve mission"
                      : "Approve and forward"
                    : decisionOpen === "return"
                      ? "Return for revision"
                      : "Reject mission"}
              </Button>
            </>
          }
        >
          <p className="hint" style={{ marginBottom: 10 }}>
            Your comment is required. It is recorded with this routing step and kept in the
            mission&apos;s routing history for monitoring and audit.
          </p>
          <Textarea
            value={decisionNote}
            onChange={(event) => setDecisionNote(event.target.value)}
            placeholder="Comment for the routing record"
            aria-label="Routing comment"
            rows={4}
          />
          {decisionError && <p className="field-message">{decisionError}</p>}
        </Modal>
      )}

      {arrivalOpen && (
        <Modal
          title={`Rendezvous arrival · ${data.mission_number}`}
          onClose={() => setArrivalOpen(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setArrivalOpen(false)}>
                Cancel
              </Button>
              <Button onClick={recordArrival} disabled={arrivalBusy || !arrivalAt}>
                {arrivalBusy ? "Saving…" : "Save arrival"}
              </Button>
            </>
          }
        >
          <p className="hint" style={{ marginBottom: 10 }}>
            Record when the aircraft actually arrived. Mark it rendezvous-ready only
            after it is parked, secure and ready to join the formation.
          </p>
          <label className="form-section" style={{ margin: 0 }}>
            <span className="hint">Actual arrival</span>
            <DateTimeInput value={arrivalAt} onChange={setArrivalAt} />
          </label>
          <label style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <input
              type="checkbox"
              checked={arrivalReady}
              onChange={(event) => setArrivalReady(event.target.checked)}
            />
            <span>Mark the aircraft rendezvous-ready</span>
          </label>
          <label style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <input
              type="checkbox"
              checked={arrivalComplete}
              onChange={(event) => setArrivalComplete(event.target.checked)}
            />
            <span>Complete this preposition mission</span>
          </label>
          {arrivalError && <p className="field-message">{arrivalError}</p>}
        </Modal>
      )}

    </>
  );
}

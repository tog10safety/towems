import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, API_BASE } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Field,
  Loading,
  Modal,
  Select,
  StatusBadge,
  Textarea,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateTime } from "../utils/format.js";
import { NOTIFICATION_EVENT } from "../utils/notifications.js";
import PdfViewer from "../components/PdfViewer.jsx";

/**
 * The MRM record, laid out like the Group Operations mission page: the full
 * assessment PDF on the left, the action for this account and the routing
 * trail on the right. Editing the assessment itself happens on its own page.
 */
export default function RiskDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user, permissions } = useAuth();
  const { data, loading, error, reload } = useApi(`/risk-register/${id}/`, {
    deps: [id],
  });
  const trail = useApi(`/risk-register/${id}/trail/`, { deps: [id] });
  // The editor holds the hazards; this page only needs to know whether any are
  // selected before it offers submission.
  const assessment = useApi(`/risk-register/${id}/details/`, { deps: [id] });

  // The evaluator is fixed by the Operations Center on the mission's MRM team
  // and appears in the assessment above; there is nothing to assign here.
  const [notes, setNotes] = useState("");
  const [decision, setDecision] = useState("approved");
  const [message, setMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [signOpen, setSignOpen] = useState(false);
  const [signaturePrompt, setSignaturePrompt] = useState(false);
  const [pdfOpen, setPdfOpen] = useState(false);

  useEffect(() => {
    if (data) setNotes(data.review_notes || "");
  }, [data?.id, data?.review_notes]); // eslint-disable-line react-hooks/exhaustive-deps


  if (loading && !data) return <Loading label="Loading risk assessment…" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const isCreator =
    data.created_by === user?.id ||
    (data.creator_name && user?.full_name === data.creator_name);
  const isAssignedEvaluator = data.reviewed_by === user?.id;
  // The evaluator only sees a complete draft: at least 8 identified hazards and
  // every I'M SAFE item ticked. The editor refuses to save anything less.
  const imsafeItems = assessment.data?.options?.imsafe ?? [];
  const imsafeComplete =
    imsafeItems.length > 0 &&
    imsafeItems.every((item) => assessment.data?.imsafe?.[item.key]);
  const assessmentReady =
    (assessment.data?.hazards?.length ?? 0) >= 8 && imsafeComplete;
  const canSubmit = (isCreator || permissions.is_admin) && data.status === "draft";
  const canReview = (isAssignedEvaluator || permissions.is_admin) && data.status === "submitted";
  const canApprove = Boolean(data.can_approve_authority);
  const approved = Boolean(data.authority_approved);

  const actionStage = approved
    ? "Complete"
    : canApprove
      ? "Authority decision"
      : canReview
        ? "Evaluation"
        : data.can_sign
          ? "Your signature"
          : canSubmit
            ? "Draft"
            : "Waiting";
  const actionTone = approved || actionStage === "Waiting" ? "muted" : "warning";

  const waitingOn = approved
    ? "nothing further"
    : canSubmit
      ? "you"
      : canReview
        ? "you"
        : data.status === "draft"
          ? "the creator"
          : data.can_sign
            ? "you"
            : canApprove
              ? "you"
              : data.decision_authority_name
                ? `${data.decision_authority_name} (${data.decision_authority_label || "decision authority"})`
                : "the decision authority";

  /** The server refuses a signature it cannot store: send the user to upload one. */
  function isSignatureRequired(err) {
    const code = err?.payload?.errors?.code;
    const codes = Array.isArray(code) ? code : [code];
    return codes.includes("signature_required");
  }

  const uploadSignature = () => {
    setSignOpen(false);
    setSignaturePrompt(false);
    navigate("/profile#signature");
  };

  function refreshBell() {
    window.dispatchEvent(new Event(NOTIFICATION_EVENT));
  }

  async function run(action, payload, successMessage = "Saved.") {
    setBusy(true);
    setActionError("");
    setMessage("");
    try {
      await action(payload);
      setMessage(successMessage);
      refreshBell();
      reload();
      trail.reload();
    } catch (err) {
      setActionError(err.message || "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  /**
   * Submission asks the creator whether to sign now or later. Signing now needs
   * an uploaded signature; "later" leaves the signature step on this page.
   */
  async function submit(sign) {
    setBusy(true);
    setActionError("");
    setMessage("");
    try {
      await api.post(`/risk-register/${data.id}/submit/`, { sign });
      setSignOpen(false);
      setMessage(
        sign
          ? "Submitted and signed. It is now with the evaluator."
          : "Submitted. Remember to sign it from this page when you are ready."
      );
      refreshBell();
      reload();
      trail.reload();
    } catch (err) {
      if (isSignatureRequired(err)) {
        setSignOpen(false);
        setSignaturePrompt(true);
        return;
      }
      setActionError(err.message || "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  const sign = () =>
    run(
      () => api.post(`/risk-register/${data.id}/sign/`, {}),
      undefined,
      "Signed. The decision authority can act once both signatures are in."
    );
  const review = () =>
    run(
      (payload) => api.post(`/risk-register/${data.id}/review/`, payload),
      { decision, notes },
      decision === "approved"
        ? "Evaluation approved and recorded. It moves to the creator's signature and then the decision authority."
        : "Evaluation recorded. The creator was asked to correct and resubmit."
    );
  const approve = () =>
    run(
      () => api.post(`/risk-register/${data.id}/approve/`, {}),
      undefined,
      "MRM approved. The mission's MRM requirement is released."
    );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>
            {data.mrm_number} · {data.activity_mission}
          </h1>
          <p>
            <StatusBadge status={data.status} /> <StatusBadge status={data.review_status} />{" "}
            {approved && <Badge tone="success">Authority approved</Badge>}
          </p>
        </div>
        <div className="btn-row">
          <Button variant="ghost" onClick={() => navigate("/risk")}>
            Back
          </Button>
          <a
            className="btn btn-ghost btn-md"
            href={`${API_BASE}/risk-register/${data.id}/pdf/?download=1`}
          >
            Download PDF
          </a>
        </div>
      </div>

      <ErrorBanner message={actionError} />
      {message && <div className="alert alert-success">{message}</div>}

      <div className="mission-view">
        <div className="mission-view-main">
          <section className="card mission-brief-card">
            <header className="card-head">
              <h2>
                MRM assessment{" "}
                {data.total_percent != null && (
                  <Badge tone={riskTone(data.risk_category)}>
                    {data.risk_category || "Risk"} -{" "}
                    {Number(data.total_percent).toFixed(1)}%
                  </Badge>
                )}
              </h2>
              <div className="card-actions">
                <Button variant="ghost" size="sm" onClick={() => setPdfOpen(true)}>
                  Open full screen
                </Button>
              </div>
            </header>
            <div className="mission-brief-body">
              <PdfViewer
                // Remount after every action so the PDF never shows the version
                // from before an evaluation or authority signature.
                key={data.date_approved || data.date_reviewed || data.date_submitted || data.date_created}
                path={`/risk-register/${data.id}/pdf/`}
                title={`MRM ${data.mrm_number}`}
              />
            </div>
          </section>
        </div>

        <div className="mission-view-side">
          <Card
            title={
              <span className="status-chip">
                Your action
                <Badge tone={actionTone}>{actionStage}</Badge>
              </span>
            }
            defaultOpen
          >
            {data.can_edit && (
              <div className="btn-row" style={{ marginBottom: 10 }}>
                <Button size="sm" onClick={() => navigate(`/risk/${data.id}/edit`)}>
                  Edit MRM
                </Button>
              </div>
            )}
            {approved ? (
              <p className="hint">
                <strong>This assessment is approved.</strong> The decision authority signed it
                {data.date_approved ? ` on ${formatDateTime(data.date_approved)}` : ""}. Nothing
                further is required from you.
              </p>
            ) : (
              <>
                {canSubmit && (
                  <>
                    {data.review_status === "needs_revision" && (
                      <div className="alert alert-warning">
                        <p style={{ marginTop: 0 }}>
                          <strong>Returned for revision.</strong>{" "}
                          {data.review_notes || "The evaluator asked for changes."} Correct the
                          assessment, save it, then submit it again.
                        </p>
                      </div>
                    )}
                    <p className="hint">
                      You raised this assessment. Complete the hazard scoring and save it, then
                      submit it to <strong>{data.evaluator_name || "the evaluator"}</strong>.
                    </p>
                    <div className="btn-row">
                      <Button
                        onClick={() => setSignOpen(true)}
                        disabled={busy || !assessmentReady}
                        title={
                          assessmentReady
                            ? undefined
                            : "Select at least 8 hazards and tick every I'M SAFE item, then save."
                        }
                      >
                        Submit for review
                      </Button>
                    </div>
                    {!assessmentReady && (
                      <p className="hint">
                        Select at least 8 hazards and tick every I&apos;M SAFE item in the
                        assessment before submitting it.
                      </p>
                    )}
                  </>
                )}

                {data.can_sign && (
                  <div className="alert alert-warning">
                    <p style={{ marginTop: 0 }}>
                      Your signature is still required. The decision authority cannot sign this
                      MRM until the creator has signed it.
                    </p>
                    {data.current_user_has_signature ? (
                      <Button size="sm" onClick={sign} disabled={busy}>
                        {busy ? "Signing…" : "Sign MRM now"}
                      </Button>
                    ) : (
                      <Button size="sm" onClick={uploadSignature} disabled={busy}>
                        Upload a signature to sign
                      </Button>
                    )}
                  </div>
                )}

                {canReview && (
                  <>
                    <p className="hint">
                      You are the assigned evaluator. Approving records your signature and sends
                      the assessment on for the creator&apos;s signature and the decision
                      authority&apos;s approval. Returning it asks the creator to correct and
                      resubmit.
                    </p>
                    <div className="form-grid">
                      <Field label="Decision">
                        <Select
                          value={decision}
                          onChange={(event) => setDecision(event.target.value)}
                        >
                          <option value="approved">Approved</option>
                          <option value="rejected">Rejected</option>
                          <option value="needs_revision">Needs revision</option>
                        </Select>
                      </Field>
                      <Field label="Review notes" className="span-2">
                        <Textarea
                          value={notes}
                          onChange={(event) => setNotes(event.target.value)}
                          placeholder="What you checked, and anything the creator must correct…"
                        />
                      </Field>
                      <div className="btn-row">
                        <Button onClick={review} disabled={busy}>
                          {busy ? "Recording…" : "Record review"}
                        </Button>
                      </div>
                    </div>
                  </>
                )}

                {canApprove && (
                  <>
                    <p className="hint">
                      You are the decision authority this risk band routes to. Your signature is
                      the final approval for this MRM and releases the mission&apos;s MRM
                      requirement.
                    </p>
                    <div className="btn-row">
                      <Button onClick={approve} disabled={busy}>
                        {busy
                          ? "Signing…"
                          : data.decision_authority_label
                            ? `Approve assessment as ${data.decision_authority_label}`
                            : "Approve assessment"}
                      </Button>
                    </div>
                  </>
                )}

                {!canSubmit && !canReview && !canApprove && !data.can_sign && (
                  <p className="hint">Nothing for you at this stage. Waiting on {waitingOn}.</p>
                )}
              </>
            )}
          </Card>

          <Card title="Routing trail" defaultOpen>
            {(trail.data?.results || []).length === 0 ? (
              <p className="hint">No workflow action is recorded for this assessment yet.</p>
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
                    {trail.data.results.map((row) => (
                      <tr key={row.id} className={row.is_return ? "is-return" : ""}>
                        <td className="trail-when">{formatDateTime(row.created_at)}</td>
                        <td>
                          <strong>{row.stage_label || "MRM workflow"}</strong>
                          <span className="trail-action">{row.action_label || row.action}</span>
                        </td>
                        <td>{row.actor_name || "—"}</td>
                        <td>{row.note || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

        </div>
      </div>

      {signOpen && (
        <Modal
          title="Sign this MRM?"
          onClose={() => setSignOpen(false)}
          footer={
            <div className="btn-row">
              <Button
                onClick={() =>
                  data.current_user_has_signature ? submit(true) : uploadSignature()
                }
                disabled={busy}
              >
                {busy
                  ? "Submitting…"
                  : data.current_user_has_signature
                    ? "Sign now and submit"
                    : "Upload signature to sign"}
              </Button>
              <Button variant="ghost" onClick={() => submit(false)} disabled={busy}>
                Sign later
              </Button>
            </div>
          }
        >
          <p>
            Submitting sends this assessment to{" "}
            <strong>{data.evaluator_name || "the evaluator"}</strong> for review. You can sign it
            now, or sign it later from this page.
          </p>
          {data.current_user_has_signature ? (
            <p className="hint">
              Your uploaded signature will be applied to the certificate and the PDF.
            </p>
          ) : (
            <div className="alert alert-warning">
              <p style={{ marginTop: 0 }}>
                You have not uploaded a signature yet. Signing needs one; submitting without a
                signature is still allowed.
              </p>
              <Button size="sm" onClick={uploadSignature}>
                Upload my signature
              </Button>
            </div>
          )}
        </Modal>
      )}

      {signaturePrompt && (
        <Modal title="Upload a signature first" onClose={() => setSignaturePrompt(false)}>
          <p>
            This MRM can only be signed with a signature on your profile. Nothing has been
            submitted or signed yet.
          </p>
          <div className="btn-row">
            <Button onClick={uploadSignature}>Go to my profile</Button>
            <Button variant="ghost" onClick={() => setSignaturePrompt(false)}>
              Not now
            </Button>
          </div>
        </Modal>
      )}

      {pdfOpen && (
        <Modal
          title={`${data.mrm_number} · ${data.activity_mission}`}
          onClose={() => setPdfOpen(false)}
          wide
          footer={
            <div className="btn-row">
              <a
                className="btn btn-ghost btn-sm"
                href={`${API_BASE}/risk-register/${data.id}/pdf/?download=1`}
              >
                Download
              </a>
              <Button variant="ghost" size="sm" onClick={() => setPdfOpen(false)}>
                Close
              </Button>
            </div>
          }
        >
          <PdfViewer
            path={`/risk-register/${data.id}/pdf/`}
            title={`MRM ${data.mrm_number}`}
          />
        </Modal>
      )}
    </>
  );
}

/** The badge colour for a risk category: green low, amber medium, red high. */
function riskTone(category) {
  if (category === "High") return "danger";
  if (category === "Medium") return "warning";
  return "success";
}

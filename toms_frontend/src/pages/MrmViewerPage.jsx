import { Link, useNavigate, useParams } from "react-router-dom";
import { API_BASE } from "../api/client.js";
import {
  Badge,
  Button,
  Card,
  Detail,
  ErrorBanner,
  Loading,
  StatusBadge,
} from "../components/ui.jsx";
import PdfViewer from "../components/PdfViewer.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateRange, formatDateTime } from "../utils/format.js";

/**
 * The finished MRM, on its own read-only page.
 *
 * The register is the list and the assessment page is where a draft is edited;
 * this page is the document a reviewer, signer or auditor opens. Keeping it
 * separate means opening a completed assessment never pulls the register's
 * modal or the editor into the same screen, the way a mission opens its own
 * detail page from the Ground Operations register.
 */
export default function MrmViewerPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data, loading, error, reload } = useApi(`/risk-register/${id}/`, {
    deps: [id],
  });
  const trail = useApi(`/risk-register/${id}/trail/`, { deps: [id] });

  if (loading && !data) return <Loading label="Loading MRM…" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const approved = Boolean(data.authority_approved);
  const trailRows = trail.data?.results || [];

  return (
    <>
      <div className="page-head">
        <div>
          <p>MRM viewer · read-only</p>
          <h1>
            {data.mrm_number} · {data.activity_mission}
          </h1>
          <p>
            <StatusBadge status={data.status} />{" "}
            <StatusBadge status={data.review_status} />{" "}
            {approved && <Badge tone="success">Authority approved</Badge>}
          </p>
        </div>
        <div className="btn-row">
          <Button variant="ghost" onClick={() => navigate("/risk")}>
            Back to risk register
          </Button>
          {data.can_edit && (
            <Button onClick={() => navigate(`/risk/${data.id}/edit`)}>
              Edit MRM
            </Button>
          )}
          <a
            className="btn btn-ghost btn-md"
            href={`${API_BASE}/risk-register/${data.id}/pdf/?download=1`}
          >
            Download PDF
          </a>
        </div>
      </div>

      <div className="mission-view">
        <div className="mission-view-main">
          <section className="card mission-brief-card">
            <header className="card-head">
              <h2>MRM document</h2>
              <div className="card-actions">
                <a
                  className="btn btn-ghost btn-sm"
                  href={`${API_BASE}/risk-register/${data.id}/pdf/`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open in new tab
                </a>
              </div>
            </header>
            <div className="mission-brief-body">
              <PdfViewer
                key={
                  data.date_approved ||
                  data.date_reviewed ||
                  data.date_submitted ||
                  data.date_created
                }
                path={`/risk-register/${data.id}/pdf/`}
                title={`MRM ${data.mrm_number}`}
              />
            </div>
          </section>
        </div>

        <div className="mission-view-side">
          <Card title="Assessment summary" defaultOpen>
            <dl className="details">
              <Detail label="Raised by" value={data.creator_name} />
              <Detail
                label="Unit"
                value={
                  data.unit_acronym ||
                  data.unit_name ||
                  data.original_unit_name ||
                  "—"
                }
              />
              <Detail
                label="Evaluator"
                value={data.evaluator_name || "Not assigned"}
              />
              <Detail label="Date covered" value={coveredDates(data)} />
              <Detail
                label="Residual risk"
                value={
                  data.total_percent != null
                    ? `${Number(data.total_percent).toFixed(1)}%`
                    : "—"
                }
              />
              <Detail label="Risk category" value={data.risk_category || "—"} />
              <Detail
                label="Decision authority"
                value={data.decision_authority_name || "Not designated"}
              />
              <Detail
                label="Approved"
                value={approved ? formatDateTime(data.date_approved) : "Not approved"}
              />
            </dl>
            {data.mission && (
              <p className="hint">
                Mission:{" "}
                <Link to={`/missions/${data.mission}`}>
                  {data.mission_number || `Mission #${data.mission}`}
                </Link>
              </p>
            )}
          </Card>

          <Card title="Routing trail" defaultOpen>
            {trailRows.length === 0 ? (
              <p className="hint">
                No workflow action is recorded for this assessment yet.
              </p>
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
                    {trailRows.map((row) => (
                      <tr key={row.id} className={row.is_return ? "is-return" : ""}>
                        <td className="trail-when">{formatDateTime(row.created_at)}</td>
                        <td>
                          <strong>{row.stage_label || "MRM workflow"}</strong>
                          <span className="trail-action">
                            {row.action_label || row.action}
                          </span>
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
    </>
  );
}

/** The dates the assessment covers: its mission window, else when it was raised. */
function coveredDates(row) {
  const start = row.date_covered_start;
  const end = row.date_covered_end;
  if (!start && !end) return formatDateRange(row.date_created, row.date_created);
  return formatDateRange(start, end);
}

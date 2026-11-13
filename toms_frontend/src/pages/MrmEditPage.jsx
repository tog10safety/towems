import { useNavigate, useParams } from "react-router-dom";
import { Button, ErrorBanner, Loading, StatusBadge } from "../components/ui.jsx";
import MrmAssessmentEditor from "../components/MrmAssessmentEditor.jsx";
import { useApi } from "../hooks/useApi.js";

/**
 * Editing a draft MRM is its own page, not a panel on the assessment page.
 *
 * The workflow page owns the routing trail and the submit/sign actions; this
 * page owns the assessment body (hazards, conditions, personnel). Saving here
 * returns the user to the assessment page to submit the corrected version.
 */
export default function MrmEditPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data, loading, error, reload } = useApi(`/risk-register/${id}/`, {
    deps: [id],
  });

  if (loading && !data) return <Loading label="Loading the assessment…" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const editable = Boolean(data.can_edit);

  return (
    <>
      <div className="page-head">
        <div>
          <p>MRM assessment · edit</p>
          <h1>
            {data.mrm_number} · {data.activity_mission}
          </h1>
          <p>
            <StatusBadge status={data.status} />{" "}
            <StatusBadge status={data.review_status} />
          </p>
        </div>
        <div className="btn-row">
          <Button variant="ghost" onClick={() => navigate(`/risk/${data.id}`)}>
            Back to assessment
          </Button>
          <Button
            variant="ghost"
            onClick={() => navigate(`/risk/${data.id}/view`)}
          >
            View MRM
          </Button>
        </div>
      </div>

      {data.review_status === "needs_revision" && (
        <div className="alert alert-warning">
          <p style={{ marginTop: 0 }}>
            <strong>Returned for revision.</strong>{" "}
            {data.review_notes || "The evaluator asked for changes."} Save the
            assessment below, then return to the assessment page and submit it
            again.
          </p>
        </div>
      )}

      {editable ? (
        <>
          <p className="hint">
            Edit the hazards, conditions and personnel, then choose{" "}
            <strong>Save changes</strong>. You return to the assessment page,
            where the saved version can be submitted.
          </p>
          <MrmAssessmentEditor
            mrmId={data.id}
            onSaved={() => navigate(`/risk/${data.id}`)}
          />
        </>
      ) : (
        <>
          <div className="alert alert-warning">
            This MRM can no longer be edited. Only a draft is editable; a
            submitted or approved assessment is a record of what was signed.
          </div>
          <Button onClick={() => navigate(`/risk/${data.id}`)}>
            Back to assessment
          </Button>
        </>
      )}
    </>
  );
}

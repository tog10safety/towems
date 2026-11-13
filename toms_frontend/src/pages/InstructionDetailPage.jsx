import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, API_BASE } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { formatDateTime } from "../utils/format.js";
import {
  Badge,
  Button,
  Card,
  Detail,
  ErrorBanner,
  Loading,
  Textarea,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { refreshNotifications } from "../utils/notifications.js";

export default function InstructionDetailPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const { data, loading, error, reload } = useApi(`/instructions/${id}/`);
  const [note, setNote] = useState("");
  const [files, setFiles] = useState([]);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");

  const mine = data?.recipients?.find((row) => row.username === user?.username);

  async function act(kind, uploads = []) {
    setBusy(kind);
    setActionError("");
    setNotice("");
    try {
      if (kind === "complete") {
        // Completion evidence is multipart: PDF or pictures only (server-checked).
        const form = new FormData();
        if (note) form.append("note", note);
        for (const file of uploads) form.append("attachments", file);
        await api.upload(`/instructions/${id}/complete/`, form);
      } else {
        await api.post(`/instructions/${id}/${kind}/`, note ? { note } : {});
      }
      setNote("");
      setFiles([]);
      setNotice(
        kind === "acknowledge"
          ? "Acknowledged. The item remains open until it is completed."
          : kind === "complete"
            ? "Completion recorded."
            : "Instruction verified."
      );
      reload();
      refreshNotifications();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy("");
    }
  }

  if (loading && !data) return <Loading label="Loading instruction…" />;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="hint">
            <Link to="/instructions">← All instructions</Link>
          </p>
          <h1>{data?.subject || "Instruction"}</h1>
          {data && (
            <p>
              Issued by @{data.sender_username} · {data.unit_acronym} ·{" "}
              {formatWhen(data.created_at)}
            </p>
          )}
        </div>
        {data && (
          <div className="btn-row">
            <Badge tone={data.priority === "high" || data.priority === "urgent" ? "danger" : "muted"}>
              {data.priority}
            </Badge>
            <Badge tone={data.status === "verified" ? "success" : "warning"}>
              {data.status.replace(/_/g, " ")}
            </Badge>
          </div>
        )}
      </div>

      <ErrorBanner message={error || actionError} onRetry={error ? reload : undefined} />
      {notice && <div className="alert alert-success">{notice}</div>}

      {data && (
        <div className="grid grid-2">
          <Card title="Instruction">
            <p className="instruction-body">{data.body}</p>
            <dl className="details">
              <Detail label="Priority">{data.priority}</Detail>
              <Detail label="Due">{data.due_at ? formatWhen(data.due_at) : "No due date"}</Detail>
              <Detail label="Acknowledgement required">
                {data.requires_acknowledgement ? "Yes" : "No"}
              </Detail>
              <Detail label="Completion verification">
                {data.requires_completion ? "Yes" : "No"}
              </Detail>
              {data.mission_number && (
                <Detail label="Mission">
                  <Link to={`/missions/${data.mission}`}>{data.mission_number}</Link>
                </Detail>
              )}
              <Detail label="Verified">
                {data.verified_at ? formatWhen(data.verified_at) : "Not yet verified"}
              </Detail>
            </dl>

            {mine && mine.completed_at == null && (
              <div style={{ marginTop: 16 }}>
                <Textarea
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="Response, corrective action or supporting evidence…"
                  rows={3}
                />
                {mine.acknowledged_at != null && (
                  <div style={{ marginTop: 10 }}>
                    <label className="hint" htmlFor="completion-files">
                      Supporting documents (PDF or pictures, up to 5)
                    </label>
                    <input
                      id="completion-files"
                      type="file"
                      multiple
                      accept="application/pdf,image/png,image/jpeg,image/webp,image/gif"
                      onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
                    />
                    {files.length > 0 && (
                      <p className="hint">
                        {files.map((file) => file.name).join(", ")}
                      </p>
                    )}
                  </div>
                )}
                <div className="btn-row" style={{ marginTop: 10 }}>
                  {mine.acknowledged_at == null ? (
                    <Button onClick={() => act("acknowledge")} disabled={busy !== ""}>
                      {busy === "acknowledge" ? "Recording…" : "Acknowledge"}
                    </Button>
                  ) : (
                    <Button onClick={() => act("complete", files)} disabled={busy !== ""}>
                      {busy === "complete" ? "Recording…" : "Mark my part complete"}
                    </Button>
                  )}
                </div>
              </div>
            )}
          </Card>

          <Card title="Recipients">
            <ul className="recipient-list">
              {(data.recipients ?? []).map((row) => (
                <li key={row.id}>
                  <div>
                    <strong>{row.username}</strong> — {row.user_name}
                    <p className="hint">
                      {row.completed_at
                        ? `Completed ${formatWhen(row.completed_at)}`
                        : row.acknowledged_at
                          ? `Acknowledged ${formatWhen(row.acknowledged_at)} — outstanding`
                          : "Not yet acknowledged"}
                    </p>
                    {row.completion_note && (
                      <p className="hint">Note: {row.completion_note}</p>
                    )}
                    {(row.attachments ?? []).length > 0 && (
                      <div className="btn-row" style={{ marginTop: 6 }}>
                        {row.attachments.map((attachment) => (
                          <a
                            key={attachment.id}
                            className="btn btn-ghost btn-sm"
                            href={`${API_BASE}/instructions/${data.id}/attachments/${attachment.id}/`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {attachment.original_name}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                  <Badge tone={row.completed_at ? "success" : row.acknowledged_at ? "info" : "warning"}>
                    {row.completed_at ? "addressed" : row.acknowledged_at ? "in progress" : "pending"}
                  </Badge>
                </li>
              ))}
            </ul>

            {data.sender === user?.id && data.status !== "verified" && (
              <div className="btn-row" style={{ marginTop: 16 }}>
                <Button variant="ghost" onClick={() => act("verify")} disabled={busy !== ""}>
                  {busy === "verify" ? "Verifying…" : "Verify completion"}
                </Button>
              </div>
            )}
          </Card>
        </div>
      )}
    </>
  );
}

// Philippine time, 24-hour military format (see utils/format.js).
const formatWhen = formatDateTime;

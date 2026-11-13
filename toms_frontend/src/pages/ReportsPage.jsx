import { useMemo, useState } from "react";
import { api } from "../api/client.js";
import { Button, Card, ErrorBanner, Loading, Select, Table } from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { titleCase } from "../utils/format.js";

const REPORTS = [
  { value: "missions", label: "Missions" },
  { value: "risk", label: "Risk assessments" },
  { value: "personnel", label: "Personnel" },
  { value: "inventory", label: "Inventory" },
  { value: "trips", label: "Vehicle trips" },
  { value: "units", label: "Units" },
  { value: "aor", label: "Areas of responsibility" },
];

export default function ReportsPage() {
  const [report, setReport] = useState("missions");
  const [status, setStatus] = useState("");
  const { data, loading, error, reload } = useApi(
    `/reports/${report}/${status ? `?status=${status}` : ""}`,
    { deps: [report, status] }
  );

  const rows = useMemo(() => data?.results ?? [], [data]);
  const columns = useMemo(() => {
    if (!rows.length) return [];
    return Object.keys(rows[0]).map((key) => ({ key, label: titleCase(key) }));
  }, [rows]);

  async function exportCsv() {
    const query = status ? `?status=${status}&export=csv` : "?export=csv";
    const response = await api.download(`/reports/${report}/${query}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `toms_${report}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Reports</h1>
          <p>Permission-filtered extracts. Exports reflect exactly what you are allowed to see.</p>
        </div>
        <div className="btn-row">
          <Button variant="ghost" onClick={reload}>
            Refresh
          </Button>
          <Button onClick={exportCsv} disabled={!rows.length}>
            Export CSV
          </Button>
        </div>
      </div>

      <Card>
        <div className="inline-row" style={{ marginBottom: 14 }}>
          <Select value={report} onChange={(event) => setReport(event.target.value)}>
            {REPORTS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
          {report === "risk" && (
            <Select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">All statuses</option>
              <option value="draft">Draft</option>
              <option value="submitted">Submitted</option>
              <option value="reviewed">Reviewed</option>
            </Select>
          )}
        </div>

        <ErrorBanner message={error} onRetry={reload} />
        {loading && !data ? (
          <Loading />
        ) : (
          <Table rows={rows} columns={columns} empty="No rows for this report." />
        )}
        <p className="hint" style={{ marginTop: 12 }}>
          {data?.count ?? 0} row(s) in this extract.
        </p>
      </Card>
    </>
  );
}

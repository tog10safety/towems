import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, API_BASE, qs } from "../api/client.js";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Input,
  Loading,
  Modal,
  Pagination,
  Select,
  Stat,
  StatusBadge,
  Table,
} from "../components/ui.jsx";
import PdfViewer from "../components/PdfViewer.jsx";
import SafetyAuditChart from "../components/SafetyAuditChart.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDate, formatDateRange } from "../utils/format.js";

const PAGE_SIZE = 20;

const CHART_TYPES = [
  { value: "bar", label: "Bar chart" },
  { value: "line", label: "Line chart" },
  { value: "pie", label: "Pie chart" },
  { value: "donut", label: "Donut chart" },
];

const GRANULARITIES = [
  { value: "week", label: "Per week" },
  { value: "month", label: "Per month" },
  { value: "quarter", label: "Per quarter" },
  { value: "year", label: "Per year" },
];

function localDateInput(date = new Date()) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function coveredDates(row) {
  if (!row.date_covered_start && !row.date_covered_end) {
    return formatDateRange(row.date_created, row.date_created);
  }
  return formatDateRange(row.date_covered_start, row.date_covered_end);
}

function riskTone(category) {
  if (category === "High") return "danger";
  if (category === "Medium") return "warning";
  return category ? "success" : "muted";
}

export default function SafetyAuditPage() {
  const navigate = useNavigate();
  const today = localDateInput();
  const [dateFrom, setDateFrom] = useState(`${today.slice(0, 4)}-01-01`);
  const [dateTo, setDateTo] = useState(today);
  const [unit, setUnit] = useState("");
  const [granularity, setGranularity] = useState("month");
  const [chartType, setChartType] = useState("bar");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(new Set());
  const [zipBusy, setZipBusy] = useState(false);
  const [zipError, setZipError] = useState("");
  const [pdfPreview, setPdfPreview] = useState(null);

  const auditPath = `/risk-register/audit/${qs({
    date_from: dateFrom,
    date_to: dateTo,
    unit,
    granularity,
  })}`;
  const {
    data: audit,
    loading: auditLoading,
    error: auditError,
    reload: reloadAudit,
  } = useApi(auditPath, { deps: [dateFrom, dateTo, unit, granularity] });

  const listPath = `/risk-register/${qs({
    date_from: dateFrom,
    date_to: dateTo,
    unit,
    page,
    page_size: PAGE_SIZE,
  })}`;
  const {
    data: register,
    loading: registerLoading,
    error: registerError,
    reload: reloadRegister,
  } = useApi(listPath, { deps: [dateFrom, dateTo, unit, page] });

  const { data: units } = useApi("/risk-register/units/");

  const rows = register?.results ?? [];
  const totals = audit?.totals ?? {};
  const buckets = audit?.buckets ?? [];
  const unitCount = audit?.scope?.unit_ids?.length ?? 0;
  const allSelected = rows.length > 0 && rows.every((row) => selected.has(row.id));

  useEffect(() => {
    setSelected(new Set());
  }, [dateFrom, dateTo, unit, page]);

  function changeDate(setter, value) {
    setter(value);
    setPage(1);
  }

  function changeUnit(value) {
    setUnit(value);
    setPage(1);
  }

  function toggle(id) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function togglePage() {
    setSelected(
      allSelected ? new Set() : new Set(rows.map((row) => row.id))
    );
  }

  async function downloadZip(ids) {
    setZipBusy(true);
    setZipError("");
    try {
      const params = ids?.length
        ? { ids: ids.join(",") }
        : { date_from: dateFrom, date_to: dateTo, unit };
      const response = await api.download(`/risk-register/audit-zip/${qs(params)}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const stamp = `${dateFrom || "period"}_${dateTo || "period"}`;
      link.href = url;
      link.download = `Safety_Audit_${stamp}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setZipError(err.message || "Unable to download the selected PDFs.");
    } finally {
      setZipBusy(false);
    }
  }

  const averageTone =
    totals.average_residual > 75
      ? "danger"
      : totals.average_residual > 50
        ? "warning"
        : "success";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Safety Audit</h1>
          <p>
            Audit the MRM assessments in your unit and its subordinates, chart completions for
            the covered period, and download the finished PDFs.
          </p>
        </div>
        <div className="btn-row">
          <Button
            variant="ghost"
            onClick={() => {
              reloadAudit();
              reloadRegister();
            }}
          >
            Refresh
          </Button>
          <Button
            onClick={() => downloadZip()}
            disabled={zipBusy || !totals.mrms}
            title="Download every MRM PDF matching the covered period and unit"
          >
            {zipBusy ? "Preparing ZIP…" : "Download all for period (ZIP)"}
          </Button>
        </div>
      </div>

      <Card>
        <div className="inline-row safety-audit-filters">
          <Input
            type="date"
            aria-label="Covered from"
            title="Covered period, from"
            value={dateFrom}
            onChange={(event) => changeDate(setDateFrom, event.target.value)}
          />
          <Input
            type="date"
            aria-label="Covered to"
            title="Covered period, to"
            value={dateTo}
            onChange={(event) => changeDate(setDateTo, event.target.value)}
          />
          <Select
            aria-label="Unit"
            value={unit}
            onChange={(event) => changeUnit(event.target.value)}
          >
            <option value="">My unit and subordinates</option>
            {(units?.results ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.acronym || row.name}
              </option>
            ))}
          </Select>
          {(dateFrom || dateTo || unit) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setDateFrom(`${today.slice(0, 4)}-01-01`);
                setDateTo(today);
                setUnit("");
                setPage(1);
              }}
            >
              Reset period
            </Button>
          )}
        </div>
        <p className="hint" style={{ marginBottom: 0 }}>
          {auditLoading && !audit
            ? "Loading the audit picture…"
            : `Scope: ${audit?.scope?.unit_acronym || audit?.scope?.unit_name || "your unit"}${
                unitCount > 1 ? ` and ${unitCount - 1} subordinate unit(s)` : ""
              }. Dates follow each MRM's covered mission window, or the date it was raised when no mission is linked.`}
        </p>
      </Card>

      <ErrorBanner
        message={auditError || registerError || zipError}
        onRetry={auditError ? reloadAudit : registerError ? reloadRegister : undefined}
      />

      {auditLoading && !audit ? (
        <Loading label="Loading safety audit…" />
      ) : (
        <>
          <div className="stats stats-compact">
            <Stat
              label="Assessments in period"
              value={totals.mrms ?? 0}
              hint={`${totals.completed ?? 0} completed · ${totals.pending ?? 0} open`}
            />
            <Stat
              label="Completed"
              value={totals.completed ?? 0}
              hint={`${Number(totals.completion_rate || 0).toFixed(1)}% completion`}
              tone={(totals.completion_rate || 0) >= 80 ? "success" : "warning"}
            />
            <Stat
              label="Average residual"
              value={`${Number(totals.average_residual || 0).toFixed(1)}%`}
              hint="Completed assessments only"
              tone={averageTone}
            />
            <Stat
              label="High / Medium risk"
              value={`${totals.high ?? 0} / ${totals.medium ?? 0}`}
              hint={`${totals.low ?? 0} low · ${totals.unrated ?? 0} unrated`}
              tone={totals.high ? "danger" : totals.medium ? "warning" : undefined}
            />
          </div>

          <Card
            title="MRM completions"
            actions={
              <div className="inline-row safety-chart-controls">
                <Select
                  aria-label="Chart type"
                  value={chartType}
                  onChange={(event) => setChartType(event.target.value)}
                >
                  {CHART_TYPES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
                <Select
                  aria-label="Chart period"
                  value={granularity}
                  onChange={(event) => {
                    setGranularity(event.target.value);
                    setPage(1);
                  }}
                >
                  {GRANULARITIES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </div>
            }
          >
            <SafetyAuditChart type={chartType} buckets={buckets} />
            <p className="hint" style={{ marginBottom: 0 }}>
              A completion is counted on the decision authority's approval date. Pie and
              donut charts show the share of those completions in each period.
            </p>
          </Card>

          <Card>
            <div className="inline-row" style={{ marginBottom: 12 }}>
              <Button
                variant="ghost"
                size="sm"
                onClick={togglePage}
                disabled={!rows.length}
              >
                {allSelected ? "Clear selection" : "Select all on this page"}
              </Button>
              {selected.size > 0 && (
                <Button
                  size="sm"
                  onClick={() => downloadZip([...selected])}
                  disabled={zipBusy}
                >
                  {zipBusy ? "Preparing ZIP…" : `Download selected (${selected.size})`}
                </Button>
              )}
              <span className="hint" style={{ marginLeft: "auto" }}>
                {register?.count ?? 0} assessment(s) in the covered period
              </span>
            </div>

            {registerLoading && !register ? (
              <Loading />
            ) : (
              <>
                <Table
                  rows={rows}
                  onRowClick={(row) => navigate(`/risk/${row.id}`)}
                  empty="No MRMs match the covered period."
                  columns={[
                    {
                      key: "select",
                      label: (
                        <input
                          type="checkbox"
                          aria-label="Select all assessments on this page"
                          checked={allSelected}
                          onChange={togglePage}
                        />
                      ),
                      width: 36,
                      render: (row) => (
                        <span onClick={(event) => event.stopPropagation()}>
                          <input
                            type="checkbox"
                            aria-label={`Select ${row.mrm_number}`}
                            checked={selected.has(row.id)}
                            onChange={() => toggle(row.id)}
                          />
                        </span>
                      ),
                    },
                    {
                      key: "mrm_number",
                      label: "MRM #",
                      render: (row) => <Link to={`/risk/${row.id}`}>{row.mrm_number}</Link>,
                    },
                    { key: "activity_mission", label: "Activity / Mission" },
                    {
                      key: "unit_name",
                      label: "Unit",
                      render: (row) => row.unit_acronym || row.unit_name || row.original_unit_name || "—",
                    },
                    {
                      key: "date_covered",
                      label: "Date covered",
                      render: (row) => coveredDates(row),
                    },
                    {
                      key: "status",
                      label: "Status",
                      render: (row) => <StatusBadge status={row.status} />,
                    },
                    {
                      key: "review_status",
                      label: "Review",
                      render: (row) => <StatusBadge status={row.review_status} />,
                    },
                    {
                      key: "risk_category",
                      label: "Risk",
                      render: (row) =>
                        row.risk_category ? (
                          <Badge tone={riskTone(row.risk_category)}>
                            {`${row.risk_category}${
                              row.total_percent != null
                                ? ` ${Number(row.total_percent).toFixed(1)}%`
                                : ""
                            }`}
                          </Badge>
                        ) : (
                          "—"
                        ),
                    },
                    {
                      key: "date_approved",
                      label: "Completed",
                      render: (row) =>
                        row.authority_approved
                          ? formatDate(row.date_approved || row.date_reviewed)
                          : "—",
                    },
                    {
                      key: "actions",
                      label: "Actions",
                      render: (row) => (
                        <span
                          className="btn-row"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <Button
                            variant="ghost"
                            size="sm"
                            title={`View ${row.mrm_number} as PDF`}
                            onClick={() => setPdfPreview(row)}
                          >
                            PDF
                          </Button>
                        </span>
                      ),
                    },
                  ]}
                />
                <Pagination
                  page={page}
                  pageSize={PAGE_SIZE}
                  count={register?.count ?? 0}
                  onPage={setPage}
                />
              </>
            )}
          </Card>
        </>
      )}

      {pdfPreview && (
        <Modal
          title={`${pdfPreview.mrm_number} · ${pdfPreview.activity_mission}`}
          onClose={() => setPdfPreview(null)}
          wide
          footer={
            <div className="btn-row">
              <a
                className="btn btn-ghost btn-sm"
                href={`${API_BASE}/risk-register/${pdfPreview.id}/pdf/?download=1`}
              >
                Download
              </a>
              <Button variant="ghost" size="sm" onClick={() => setPdfPreview(null)}>
                Close
              </Button>
            </div>
          }
        >
          <PdfViewer
            path={`/risk-register/${pdfPreview.id}/pdf/`}
            title={`MRM ${pdfPreview.mrm_number}`}
          />
        </Modal>
      )}
    </>
  );
}

import { useState } from "react";
import { qs } from "../api/client.js";
import { Badge, Card, ErrorBanner, Input, Loading, Pagination, Select, Table } from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateTime } from "../utils/format.js";

const PAGE_SIZE = 25;

export default function AuditPage() {
  const [action, setAction] = useState("");
  const [entityType, setEntityType] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const path = `/audit/${qs({ action, entity_type: entityType, search, page, page_size: PAGE_SIZE })}`;
  const { data, loading, error, reload } = useApi(path, {
    deps: [action, entityType, search, page],
  });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit Trail</h1>
          <p>Every significant TOWEMS action with actor, timestamp, affected record and request id.</p>
        </div>
      </div>

      <Card>
        <div className="inline-row" style={{ marginBottom: 14 }}>
          <Input
            placeholder="Search entity id or request id…"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
          <Select
            value={action}
            onChange={(event) => {
              setAction(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All actions</option>
            {["create", "update", "delete", "approve", "review", "submit", "assign_evaluator", "login"].map(
              (value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              )
            )}
          </Select>
          <Select
            value={entityType}
            onChange={(event) => {
              setEntityType(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All entities</option>
            {["mission", "mrm_form", "personnel", "inventory_item", "vehicle_trip", "aor", "auth"].map(
              (value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              )
            )}
          </Select>
        </div>

        <ErrorBanner message={error} onRetry={reload} />
        {loading && !data ? (
          <Loading />
        ) : (
          <>
            <Table
              rows={data?.results ?? []}
              empty="No audit entries for the filters."
              columns={[
                { key: "created_at", label: "When", render: (row) => formatDateTime(row.created_at) },
                { key: "actor_name", label: "Actor", render: (row) => row.actor_name || "system" },
                { key: "action", label: "Action", render: (row) => <Badge tone="info">{row.action}</Badge> },
                { key: "entity_type", label: "Entity" },
                { key: "entity_id", label: "Record" },
                { key: "correlation_id", label: "Request", render: (row) => row.correlation_id || "—" },
              ]}
            />
            <Pagination page={page} pageSize={PAGE_SIZE} count={data?.count ?? 0} onPage={setPage} />
          </>
        )}
      </Card>
    </>
  );
}

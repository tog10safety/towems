import { useState } from "react";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Loading,
  Modal,
  Pagination,
  StatusBadge,
  Table,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateTime } from "../utils/format.js";

const PAGE_SIZE = 25;

export default function TripsPage({ embedded = false }) {
  const { permissions } = useAuth();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");

  const { data, loading, error: loadError, reload } = useApi(
    `/trips/?page=${page}&page_size=${PAGE_SIZE}`,
    { deps: [page] }
  );

  async function approve(trip) {
    setError("");
    try {
      await api.post(`/trips/${trip.id}/approve/`, {});
      setSelected(null);
      reload();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <>
      {!embedded && (
        <div className="page-head">
          <div>
            <h1>Vehicle Trips</h1>
            <p>Movements linked to missions, with vehicles and ordered itineraries.</p>
          </div>
        </div>
      )}

      <ErrorBanner message={loadError || error} onRetry={reload} />

      <Card>
        {loading && !data ? (
          <Loading />
        ) : (
          <>
            <Table
              rows={data?.results ?? []}
              onRowClick={setSelected}
              empty="No trips recorded."
              columns={[
                { key: "trip_number", label: "Trip #" },
                { key: "title", label: "Title" },
                { key: "mission_number", label: "Mission", render: (row) => row.mission_number || "—" },
                { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
                { key: "departure_at", label: "Departure", render: (row) => formatDateTime(row.departure_at) },
                { key: "return_at", label: "Return", render: (row) => formatDateTime(row.return_at) },
                {
                  key: "counts",
                  label: "Vehicles / Stops",
                  render: (row) => `${row.vehicles.length} / ${row.itinerary.length}`,
                },
              ]}
            />
            <Pagination page={page} pageSize={PAGE_SIZE} count={data?.count ?? 0} onPage={setPage} />
          </>
        )}
      </Card>

      {selected && (
        <Modal
          wide
          title={`${selected.trip_number} · ${selected.title}`}
          onClose={() => setSelected(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                Close
              </Button>
              {permissions.can_approve_operations && selected.status === "draft" && (
                <Button onClick={() => approve(selected)}>Approve trip</Button>
              )}
            </>
          }
        >
          <ErrorBanner message={error} />
          <p className="hint">
            {formatDateTime(selected.departure_at)} → {formatDateTime(selected.return_at)} ·{" "}
            <StatusBadge status={selected.status} />{" "}
            {selected.mission_number && <Badge tone="info">Mission {selected.mission_number}</Badge>}
          </p>

          <h3 style={{ margin: "14px 0 8px" }}>Vehicles</h3>
          <Table
            rows={selected.vehicles}
            empty="No vehicles assigned."
            columns={[
              {
                key: "display_name",
                label: "Vehicle",
                render: (row) => row.display_name || row.registration_number,
              },
              { key: "driver_name", label: "Driver", render: (row) => row.driver_name || "—" },
              { key: "notes", label: "Notes", render: (row) => row.notes || "—" },
            ]}
          />

          <h3 style={{ margin: "14px 0 8px" }}>Itinerary</h3>
          <Table
            rows={selected.itinerary}
            empty="No itinerary recorded."
            columns={[
              { key: "sequence", label: "#" },
              { key: "location_name", label: "Location" },
              { key: "mgrs", label: "MGRS" },
              { key: "remarks", label: "Remarks", render: (row) => row.remarks || "—" },
            ]}
          />
        </Modal>
      )}
    </>
  );
}

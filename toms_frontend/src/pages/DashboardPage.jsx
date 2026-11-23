import DashboardMap from "../components/DashboardMap.jsx";
import SafetyAnalytics from "../components/SafetyAnalytics.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useApi } from "../hooks/useApi.js";
import { Badge, Card, ErrorBanner, Loading, Stat, Table } from "../components/ui.jsx";
import { formatDate, number } from "../utils/format.js";

export default function DashboardPage() {
  const { permissions } = useAuth();
  // AOR / GIS is an Administrator tool; everyone else never sees its counters
  // or the shortcut into the coverage map.
  const isAdmin = Boolean(permissions.is_admin);
  const { data, loading, error, reload } = useApi("/dashboard/", { deps: [] });

  if (loading && !data) return <Loading label="Loading operational picture…" />;

  const missions = data?.missions ?? {};
  const risk = data?.risk ?? {};
  const mrm = data?.mrm ?? {};
  const personnel = data?.personnel ?? {};
  const inventory = data?.inventory ?? {};
  const fuel = data?.fuel ?? {};
  const vehicles = data?.vehicles ?? {};
  const onTrip = vehicles.on_trip_details ?? [];
  // The shelf lives that run out first, in this caller's unit scope.
  const watch = data?.watch ?? {};
  const fuelWatch = watch.fuel_expiring ?? [];
  const byStatus = missions.by_status ?? {};

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Operational Dashboard</h1>
          <p>Live figures derived from mission, personnel, logistics and risk records.</p>
        </div>
      </div>

      <ErrorBanner message={error} onRetry={reload} />

      <div className="stats">
        <Stat
          label="Ground Operations"
          value={missions.by_category?.ground ?? 0}
          hint={`${byStatus.active ?? 0} active · ${byStatus.planned ?? 0} planned · ${byStatus.completed ?? 0} completed`}
        />
        <Stat
          label="Air Operations"
          value={missions.by_category?.air ?? 0}
          hint={`${byStatus.active ?? 0} active · ${byStatus.planned ?? 0} planned · ${byStatus.completed ?? 0} completed`}
        />
        <Stat
          label="Risk Awaiting Action"
          value={(risk.pending_reviews ?? 0) + (risk.pending_approvals ?? 0)}
          hint={`${risk.pending_reviews ?? 0} to review · ${risk.pending_approvals ?? 0} to approve`}
          tone={risk.pending_reviews ? "warning" : undefined}
        />
        <Stat
          label="MRM Outstanding"
          value={(mrm.missing ?? 0) + (mrm.pending ?? 0)}
          hint={`${mrm.missing ?? 0} not started · ${mrm.overdue ?? 0} overdue`}
          tone={mrm.overdue ? "danger" : mrm.missing ? "warning" : undefined}
        />
        <Stat
          label="Personnel Available"
          value={personnel.available ?? 0}
          hint={`${personnel.on_mission ?? 0} on mission of ${personnel.total ?? 0}`}
        />
        <Stat
          label="Inventory Items"
          value={inventory.items ?? 0}
          hint={`${inventory.unserviceable ?? 0} unserviceable · ${inventory.low_stock ?? 0} low stock`}
          tone={inventory.unserviceable ? "danger" : inventory.low_stock ? "warning" : undefined}
        />
        <Stat
          label="POL Available"
          value={`${number(fuel.litres_available ?? 0)} L`}
          hint={`${number(fuel.drums_available ?? 0)} drums · ${number(fuel.expiring ?? 0)} expiring`}
          tone={fuel.expiring ? "warning" : undefined}
        />
        <Stat
          label="Vehicles Available"
          value={vehicles.available ?? 0}
          hint={`${vehicles.on_trip ?? 0} on trip`}
        >
          {onTrip.length > 0 && (
            <ul className="stat-list">
              {onTrip.map((row) => (
                <li key={`${row.trip_number}-${row.vehicle_id}`}>
                  <span className="stat-list-vehicle">{row.registration_number}</span>
                  {/* POV is a person's car on a mission trip; PAFMV is a unit
                      vehicle from the register, counted above. */}
                  <span className="stat-list-kind">{row.kind}</span>
                  <span className="stat-list-trip" title={row.trip_title}>
                    {row.trip_number}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Stat>
        {isAdmin && (
          <Stat
            label="AORs Mapped"
            value={data?.aor?.count ?? 0}
            hint="Active areas of responsibility"
          />
        )}
      </div>

      {/* Monitoring is what needs a decision, not another count: the POL drum
          batches whose shelf life runs out first, already expired ones at the
          top, so the issue or test order is written on the dashboard. */}
      <Card
        title="Operational watch list"
        actions={
          <Badge tone={watch.fuel_expiring_count ? "warning" : "success"}>
            {watch.fuel_expiring_count
              ? `${number(watch.fuel_expiring_count)} POL batch${watch.fuel_expiring_count === 1 ? "" : "es"} expiring`
              : "POL clear"}
          </Badge>
        }
      >
        <p className="hint" style={{ marginTop: 0 }}>
          POL drum stock in your unit scope with {watch.horizon_days ?? 30} days of shelf life
          left, or already past its date. Bulk tanks do not carry a shelf life; the inventory
          board monitors their balance.
        </p>
        <Table
          rows={fuelWatch}
          stack
          empty={`No POL drum batch expires in the next ${watch.horizon_days ?? 30} days.`}
          columns={[
            { key: "grade", label: "Grade", render: (row) => row.grade || "—" },
            { key: "location", label: "Held at", render: (row) => row.location || "—" },
            { key: "drums", label: "Drums", render: (row) => number(row.drums) },
            { key: "litres", label: "Litres", render: (row) => `${number(row.litres)} L` },
            {
              key: "expiry",
              label: "Expires",
              render: (row) => (
                <span className="inline-row" style={{ gap: 8 }}>
                  {formatDate(row.expiry_date)}
                  <Badge
                    tone={row.days_left < 0 ? "danger" : row.days_left <= 7 ? "warning" : "muted"}
                  >
                    {row.days_left < 0
                      ? "expired"
                      : row.days_left === 0
                        ? "today"
                        : `${row.days_left}d`}
                  </Badge>
                </span>
              ),
            },
          ]}
        />
      </Card>

      <SafetyAnalytics data={data?.safety} onRefresh={reload} />

      {/* Full width again: the presence rail now lives in the sidebar, so it is
          visible everywhere. Activity moved to Settings. */}
      <DashboardMap />
    </>
  );
}

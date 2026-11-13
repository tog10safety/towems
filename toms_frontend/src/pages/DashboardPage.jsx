import DashboardMap from "../components/DashboardMap.jsx";
import SafetyAnalytics from "../components/SafetyAnalytics.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { useApi } from "../hooks/useApi.js";
import { ErrorBanner, Loading, Stat } from "../components/ui.jsx";

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
  const vehicles = data?.vehicles ?? {};
  const trips = data?.trips ?? {};
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
          hint={`${byStatus.active ?? 0} active · ${byStatus.approved ?? 0} approved`}
        />
        <Stat
          label="Air Operations"
          value={missions.by_category?.air ?? 0}
          hint={`${byStatus.planned ?? 0} planned · ${byStatus.completed ?? 0} completed`}
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
          tone={inventory.low_stock ? "warning" : undefined}
        />
        <Stat
          label="Vehicles Available"
          value={vehicles.available ?? 0}
          hint={`${vehicles.on_trip ?? 0} on trip`}
        />
        <Stat
          label="Trips In Progress"
          value={trips.in_progress ?? 0}
          hint={`${trips.planned ?? 0} planned`}
        />
        {isAdmin && (
          <Stat
            label="AORs Mapped"
            value={data?.aor?.count ?? 0}
            hint="Active areas of responsibility"
          />
        )}
      </div>

      <SafetyAnalytics data={data?.safety} onRefresh={reload} />

      {/* Full width again: the presence rail now lives in the sidebar, so it is
          visible everywhere. Activity moved to Settings. */}
      <DashboardMap />
    </>
  );
}

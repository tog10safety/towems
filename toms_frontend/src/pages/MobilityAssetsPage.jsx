import { useSearchParams } from "react-router-dom";
import { Card, Tabs } from "../components/ui.jsx";
import TripsPage from "./TripsPage.jsx";
import VehiclesPage from "./VehiclesPage.jsx";

const TABS = [
  { value: "vehicles", label: "Vehicle Register" },
  { value: "trips", label: "Vehicle Trips" },
];

export default function MobilityAssetsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") === "trips" ? "trips" : "vehicles";

  function selectTab(value) {
    const next = new URLSearchParams(searchParams);
    if (value === "trips") next.set("tab", "trips");
    else next.delete("tab");
    setSearchParams(next, { replace: true });
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Mobility Assets</h1>
          <p>Unit vehicles and the missions they support, in one mobility workspace.</p>
        </div>
      </div>

      <Card>
        <Tabs tabs={TABS} active={activeTab} onChange={selectTab} />
      </Card>

      {activeTab === "vehicles" ? (
        <Card>
          <VehiclesPage embedded />
        </Card>
      ) : (
        <TripsPage embedded />
      )}
    </>
  );
}
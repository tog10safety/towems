import AirAssetsSection from "../components/AirAssetsSection.jsx";
import { Card } from "../components/ui.jsx";

export default function AirAssetsPage() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Air Assets</h1>
          <p>
            Aircraft in the unit inventory, with their assignment, operational status and
            capability.
          </p>
        </div>
      </div>

      <Card>
        <AirAssetsSection />
      </Card>
    </>
  );
}
import { Card } from "./ui.jsx";
import { legRouteLabel } from "../utils/airRoute.js";

function timeLabel(value) {
  if (!value) return "—";
  const match = String(value).match(/T(\d{2}):(\d{2})/);
  return match ? `${match[1]}${match[2]}` : String(value);
}

function routeLabel(leg) {
  return legRouteLabel(leg);
}

export default function AirFlightOperationsView({ detail = {} }) {
  const elements = detail.elements || [];
  return (
    <Card title="Flight Operations sheet" defaultOpen>
      <p className="hint">
        Operations date: {detail.operation_date || "not set"}. Planned departure is ETD;
        recorded takeoff and landing times are shown separately as ATD/ATA.
      </p>
      {elements.length === 0 ? (
        <p className="hint">No daily elements are recorded for this flight.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>NR</th>
                <th>ACFT TYPE</th>
                <th>PILOTS</th>
                <th>ETD</th>
                <th>ATD</th>
                <th>ATA</th>
                <th>ITINERARIES</th>
                <th>MISSIONS</th>
              </tr>
            </thead>
            {elements.map((element) => {
              const aircraft = element.aircraft || [];
              const rowCount = Math.max(
                1,
                aircraft.reduce(
                  (total, plane) => total + Math.max(1, (plane.itineraries || []).length),
                  0
                )
              );
              return (
                <tbody key={element.id || element.sequence}>
                  {aircraft.map((plane, aircraftIndex) => {
                    const legs = (plane.itineraries || []).length
                      ? plane.itineraries
                      : [{}];
                    return legs.map((leg, legIndex) => (
                      <tr key={`${plane.id || aircraftIndex}-${leg.id || legIndex}`}>
                        {aircraftIndex === 0 && legIndex === 0 && (
                          <td rowSpan={rowCount} style={{ fontWeight: 700 }}>
                            {element.sequence}
                          </td>
                        )}
                        {legIndex === 0 && (
                          <>
                            <td rowSpan={legs.length}>
                              {[
                                plane.aircraft_type,
                                plane.tail_number ? `# ${plane.tail_number}` : "",
                                plane.callsign,
                              ]
                                .filter(Boolean)
                                .join(" · ") || "—"}
                            </td>
                            <td rowSpan={legs.length} style={{ whiteSpace: "pre-line" }}>
                              {(plane.crew || [])
                                .map((crew) => `[${crew.role}] ${crew.name}`)
                                .join("\n") || "—"}
                            </td>
                          </>
                        )}
                        <td>{timeLabel(leg.etd)}</td>
                        <td>{timeLabel(leg.atd)}</td>
                        <td>{timeLabel(leg.ata)}</td>
                        <td>
                          {routeLabel(leg)}
                          {leg.overflight ? " (overflight)" : ""}
                          {leg.return_to_origin ? " (return)" : ""}
                        </td>
                        <td style={{ whiteSpace: "pre-line" }}>
                          {leg.display_mission || leg.mission || element.mission || "—"}
                        </td>
                      </tr>
                    ));
                  })}
                </tbody>
              );
            })}
          </table>
        </div>
      )}
    </Card>
  );
}

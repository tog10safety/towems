import { useEffect, useMemo, useState } from "react";
import { api, qs } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { Button, Field, Input, Modal, Select } from "./ui.jsx";

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

function expiryLabel(value) {
  if (!value) return "no date";
  const [year, month, day] = String(value).split("-");
  const index = Number(month) - 1;
  if (!year || !MONTHS[index] || !day) return String(value);
  return `${year} ${MONTHS[index]} ${Number(day)}`;
}

function gradeLabel(grade) {
  if (grade === "jet_a1") return "JetA1";
  if (grade === "avgas") return "AVGas";
  if (grade === "diesel") return "Diesel";
  if (grade === "gasoline") return "Gasoline";
  return "";
}

/** "TOG10 POL Dump - JetA1 20 Drums - Expiry 2026 Dec 26" */
function storeLabel(row) {
  const amount = Number(row.quantity_available) || 0;
  const unit =
    row.fuel_form === "drum"
      ? amount === 1
        ? "Drum"
        : "Drums"
      : amount === 1
        ? "Litre"
        : "Litres";
  const grade = gradeLabel(row.fuel_grade);
  const quantity = `${grade ? `${grade} ` : ""}${amount} ${unit}`;
  return `${row.storage_location || "Store"} - ${quantity} - Expiry ${expiryLabel(
    row.expiry_date
  )}`;
}

/**
 * Record (or correct) the POL-01 an aircraft burned on one itinerary leg.
 *
 * The option belongs to the leg: it opens once that leg's engine shutdown (SD)
 * is recorded, so a multi-leg sortie posts a figure per stop. The crew picks
 * the form the fuel was drawn in: whole drums leave a drum batch, whole litres
 * leave a bulk tank. Re-opening the dialog edits that leg's draw rather than
 * adding a second one.
 */
export default function AirFuelDialog({
  mission,
  aircraftId,
  legId,
  onClose,
  onSaved,
}) {
  const aircraft = useMemo(() => {
    const rows = [];
    (mission?.air_detail?.elements || []).forEach((element, elementIndex) => {
      (element.aircraft || []).forEach((plane) => {
        rows.push({
          id: plane.id,
          label:
            plane.display_name ||
            [plane.aircraft_type, plane.tail_number, plane.callsign]
              .filter(Boolean)
              .join(" · "),
          element: elementIndex + 1,
        });
      });
    });
    return rows;
  }, [mission]);

  const planesById = useMemo(() => {
    const map = new Map();
    (mission?.air_detail?.elements || []).forEach((element) => {
      (element.aircraft || []).forEach((plane) => map.set(plane.id, plane));
    });
    return map;
  }, [mission]);

  const draws = mission?.air_fuel?.draws || [];
  const outstanding = mission?.air_fuel?.outstanding || [];
  const firstOutstanding = outstanding[0];

  const [target, setTarget] = useState(
    String(aircraftId || firstOutstanding?.aircraft || aircraft[0]?.id || "")
  );
  const [legTarget, setLegTarget] = useState(
    String(legId || firstOutstanding?.leg || "")
  );

  // Only a leg that landed and shut down carries a figure; legs already holding
  // one stay selectable so the figure can be corrected.
  const legChoices = useMemo(() => {
    const plane = planesById.get(Number(target));
    const legs = (plane?.itineraries || []).filter(
      (leg) => leg.status !== "cancelled" && !leg.overflight
    );
    return legs.filter(
      (leg) =>
        leg.sd ||
        draws.some(
          (draw) => draw.aircraft === Number(target) && draw.leg === leg.id
        )
    );
  }, [planesById, target, draws]);

  const requested = Number(legTarget);
  const activeLeg = legChoices.some((leg) => leg.id === requested)
    ? requested
    : legChoices[legChoices.length - 1]?.id || null;
  const activeLegRow = legChoices.find((leg) => leg.id === activeLeg) || null;

  const currentDraw =
    draws.find(
      (draw) => draw.aircraft === Number(target) && draw.leg === activeLeg
    ) || null;

  const [source, setSource] = useState(currentDraw?.source || "drum");
  const [quantity, setQuantity] = useState(
    currentDraw ? String(currentDraw.quantity) : ""
  );
  const [itemId, setItemId] = useState(currentDraw?.item ?? "");
  // "No fuel consumed": the crew confirms there is nothing to take from stock.
  const [noFuel, setNoFuel] = useState(Boolean(currentDraw?.no_consumption));
  const [note, setNote] = useState(currentDraw?.note || "");
  const [allowPending, setAllowPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Only the stores standing at this leg's station, inside the mission's unit,
  // nearest expiry first.
  const items = useApi(
    `/missions/fuel-items/${qs({ mission: mission?.id, leg: activeLeg })}`,
    {
      deps: [mission?.id, activeLeg],
      enabled: Boolean(mission?.id && activeLeg),
    }
  );
  const stock = items.data?.results ?? [];
  const options = stock.filter((row) => row.fuel_form === source);
  const selected = options.find((row) => row.id === Number(itemId)) || options[0];

  useEffect(() => {
    const draw = draws.find(
      (row) => row.aircraft === Number(target) && row.leg === activeLeg
    );
    setSource(draw?.source || "drum");
    setQuantity(draw ? String(draw.quantity) : "");
    setItemId(draw?.item ?? "");
    setNoFuel(Boolean(draw?.no_consumption));
    setNote(draw?.note || "");
    setAllowPending(false);
    setError("");
    // Reset when the airframe or leg changes; ``draws`` is replaced after a save.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, activeLeg, mission?.id]);

  const litresPreview = useMemo(() => {
    const value = Number(quantity) || 0;
    if (source === "bulk") return value;
    return value * (Number(selected?.drum_capacity_litres) || 0);
  }, [quantity, source, selected]);

  async function save() {
    setBusy(true);
    setError("");
    try {
      const saved = await api.post(`/missions/${mission.id}/fuel-draws/`, {
        aircraft: Number(target),
        leg: activeLeg,
        source,
        quantity: noFuel ? 0 : Number(quantity),
        item: selected?.id,
        note,
        allow_pending: noFuel ? false : allowPending,
        no_consumption: noFuel,
      });
      onSaved?.(saved);
      onClose?.();
    } catch (err) {
      setError(
        err.fieldErrors?.quantity ||
          err.fieldErrors?.item ||
          err.fieldErrors?.aircraft ||
          err.fieldErrors?.leg ||
          err.fieldErrors?.source ||
          err.message ||
          "The fuel figure could not be recorded."
      );
    } finally {
      setBusy(false);
    }
  }

  const unitLabel = source === "bulk" ? "litres" : "drums";
  const available = selected?.quantity_available;
  const legLabel = activeLegRow ? `leg ${activeLegRow.sequence}` : "this leg";

  return (
    <Modal
      title={
        activeLegRow
          ? `Record fuel consumption · Leg ${activeLegRow.sequence}`
          : "Record fuel consumption"
      }
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            onClick={save}
            disabled={
              busy ||
              !target ||
              !activeLeg ||
              (!noFuel && (!selected || !(Number(quantity) >= 1)))
            }
          >
            {busy
              ? "Recording…"
              : noFuel
                ? "Record no fuel"
                : currentDraw
                  ? "Save fuel figure"
                  : "Post fuel draw"}
          </Button>
        </>
      }
    >
      <p className="hint">
        POL-01 burned on {legLabel}, as the crew confirmed it at that leg's
        engine shutdown. Drums leave the register in whole drums; a bulk tank in
        whole litres.
      </p>

      <label className="check-row">
        <input
          type="checkbox"
          checked={noFuel}
          onChange={(event) => setNoFuel(event.target.checked)}
        />
        <span>
          No fuel consumed — the crew confirms {legLabel} burned nothing to
          record.
        </span>
      </label>

      {aircraft.length > 1 && (
        <Field label="Aircraft">
          <Select
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            aria-label="Aircraft"
          >
            {aircraft.map((plane) => (
              <option key={plane.id} value={plane.id}>
                {plane.label}
                {outstanding.some((row) => row.aircraft === plane.id)
                  ? " — fuel not recorded"
                  : ""}
              </option>
            ))}
          </Select>
        </Field>
      )}

      {legChoices.length > 1 && (
        <Field label="Itinerary leg">
          <Select
            value={activeLeg ?? ""}
            onChange={(event) => setLegTarget(event.target.value)}
            aria-label="Itinerary leg"
          >
            {legChoices.map((leg) => (
              <option key={leg.id} value={leg.id}>
                L{leg.sequence}
                {leg.sd ? " · shut down" : " · not recorded"}
                {draws.some(
                  (draw) =>
                    draw.aircraft === Number(target) && draw.leg === leg.id
                )
                  ? " · recorded"
                  : ""}
              </option>
            ))}
          </Select>
        </Field>
      )}

      {activeLeg ? null : (
        <p className="field-message">
          This aircraft has no recorded shutdown yet, so no leg can take a fuel
          figure.
        </p>
      )}

      {noFuel ? null : (
        <>
          <Field label="Drawn from">
            <Select
              value={source}
              onChange={(event) => {
                setSource(event.target.value);
                setItemId("");
              }}
              aria-label="Fuel stock form"
            >
              <option value="drum">Drums</option>
              <option value="bulk">Bulk (litres)</option>
            </Select>
          </Field>

          <Field label="POL-01 store (nearest expiry first)">
            <Select
              value={selected?.id ?? ""}
              onChange={(event) => setItemId(event.target.value)}
              aria-label="POL-01 store"
            >
              {options.length ? (
                options.map((row) => (
                  <option key={row.id} value={row.id}>
                    {storeLabel(row)}
                  </option>
                ))
              ) : (
                <option value="">
                  No POL-01 {source} stock at this station
                </option>
              )}
            </Select>
          </Field>

          <Field
            label={`Fuel consumed on this leg (${unitLabel})`}
            hint={
              selected
                ? source === "drum"
                  ? `Whole drums only. ${Number(quantity) || 0} × ${selected.drum_capacity_litres || 0} L ≈ ${Math.round(litresPreview)} L.`
                  : `Whole litres from the bulk tank. ${available} L available.`
                : "Add POL-01 stock before recording a draw."
            }
          >
            <Input
              type="number"
              min="1"
              step="1"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              aria-label="Fuel consumed"
              placeholder={source === "drum" ? "e.g. 5" : "e.g. 950"}
            />
          </Field>
        </>
      )}

      <Field label="Note (optional)">
        <Input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Refuel slip, hot refuel, off-site uplift"
          aria-label="Fuel note"
        />
      </Field>

      {noFuel ? null : (
        <label className="check-row">
          <input
            type="checkbox"
            checked={allowPending}
            onChange={(event) => setAllowPending(event.target.checked)}
          />
          <span>
            Record the figure even if the store cannot cover it (held awaiting
            stock).
          </span>
        </label>
      )}

      {currentDraw && (
        <p className="hint">
          {currentDraw.no_consumption
            ? "Currently recorded as no fuel consumed for this leg."
            : `Currently ${currentDraw.quantity} ${
                currentDraw.source === "bulk" ? "L" : "drums"
              } (${Math.round(
                currentDraw.litres
              )} L) for ${legLabel}. Saving posts only the difference.`}
        </p>
      )}
      {error && <p className="field-message">{error}</p>}
    </Modal>
  );
}

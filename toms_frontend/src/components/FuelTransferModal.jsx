import { useMemo, useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { Badge, Button, Field, Modal, Select } from "./ui.jsx";
import { formatDate, number } from "../utils/format.js";

//: The order the picker groups the places a transfer may move fuel into.
//: Nothing else is offered, and the server narrows them to the mother Wing's
//: AOR: a store must stand somewhere the Wing answers for.
const TYPES = ["Friendly troops", "Airport", "Air strip", "Unit"];

function listOf(payload) {
  if (Array.isArray(payload)) return payload;
  return payload?.results ?? [];
}

/**
 * Transfer drum stock to another operational location.
 *
 * Each row is one drum batch, so batches of the same grade expiring on
 * different days stay apart: the officer says how many drums leave each one.
 * The destinations are the map's own locations - friendly troops, airports,
 * air strips and units - limited to the AOR the mother Wing covers.
 */
export default function FuelTransferModal({ batches, onClose, onDone }) {
  const places = useApi("/inventory/fuel/destinations/");
  const [destinationKey, setDestinationKey] = useState("");
  const [drums, setDrums] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const destinations = useMemo(
    () =>
      listOf(places.data?.results)
        .filter((row) => TYPES.includes(row.type))
        .map((row) => ({ ...row, key: row.id })),
    [places.data]
  );

  const grouped = useMemo(
    () =>
      TYPES.map((type) => ({
        type,
        rows: destinations.filter((row) => row.type === type),
      })).filter((group) => group.rows.length),
    [destinations]
  );

  const chosen = destinations.find((row) => row.key === destinationKey);
  const planned = batches.reduce(
    (total, batch) => total + (Number(drums[batch.id]) || 0),
    0
  );
  const loading = places.loading;
  // Named in the picker's hint so the officer knows which area limits it.
  const wing = places.data?.mother_unit || "";
  const aorScoped = places.data?.aor_scoped !== false;

  function setQuantity(batch, value) {
    const limit = Number(batch.quantity_available) || 0;
    const next = Math.max(0, Math.min(limit, Math.floor(Number(value) || 0)));
    setDrums((current) => ({ ...current, [batch.id]: next }));
  }

  async function submit() {
    const moves = batches
      .map((batch) => ({ item_id: batch.id, drums: Number(drums[batch.id]) || 0 }))
      .filter((move) => move.drums > 0);
    if (!moves.length) {
      setError("Enter how many drums leave at least one batch.");
      return;
    }
    if (!chosen) {
      setError("Choose the location the drums are going to.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.post("/inventory/fuel/transfer/", {
        moves,
        destination: chosen.label,
        latitude: chosen.latitude,
        longitude: chosen.longitude,
      });
      onDone?.(result?.detail || "Drums transferred.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Transfer drums"
      onClose={onClose}
      wide
      footer={
        <>
          <Button variant="ghost" type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" onClick={submit} disabled={busy || loading}>
            {busy ? "Transferring…" : `Transfer${planned ? ` ${planned} drum(s)` : ""}`}
          </Button>
        </>
      }
    >
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      <p className="hint">
        Each line is one drum batch, so drums that expire on different days are
        moved separately. Only the drums entered leave the batch; the rest stay
        where they are.
      </p>

      {!batches.length && (
        <p className="hint">No drum stock with drums on hand to transfer.</p>
      )}

      <div className="fuel-transfer-list">
        {batches.map((batch) => (
          <div className="fuel-transfer-row" key={batch.id}>
            <div className="fuel-transfer-main">
              <strong>{batch.storage_location || "No location recorded"}</strong>
              <span className="hint">
                {batch.grade_label || "Ungraded"} ·{" "}
                {batch.expiry_date
                  ? `expires ${formatDate(batch.expiry_date)}`
                  : "no expiry recorded"}{" "}
                · {number(batch.quantity_available)} drum(s) available
              </span>
            </div>
            <label className="fuel-transfer-qty">
              <span>Drums</span>
              <input
                className="input"
                type="number"
                min="0"
                max={Number(batch.quantity_available) || 0}
                step="1"
                value={drums[batch.id] ?? ""}
                onChange={(event) => setQuantity(batch, event.target.value)}
                placeholder="0"
              />
            </label>
          </div>
        ))}
      </div>

      <Field
        label="Destination"
        required
        hint={
          aorScoped
            ? `Airports, air strips, units and friendly troops inside ${wing || "the mother Wing"}'s AOR.`
            : `${wing || "The mother unit"} has no AOR recorded, so every location is offered.`
        }
      >
        <Select
          value={destinationKey}
          onChange={(event) => setDestinationKey(event.target.value)}
          disabled={loading}
        >
          <option value="">
            {loading ? "Loading locations…" : "Select a location…"}
          </option>
          {grouped.map((group) => (
            <optgroup key={group.type} label={group.type}>
              {group.rows.map((row) => (
                <option key={row.key} value={row.key}>
                  {row.label}
                </option>
              ))}
            </optgroup>
          ))}
        </Select>
      </Field>

      {chosen && (
        <p className="hint">
          {planned ? `${number(planned)} drum(s)` : "The drums"} move to{" "}
          <Badge tone="muted">{chosen.type}</Badge> {chosen.label} and appear on
          the board and the map there.
        </p>
      )}
    </Modal>
  );
}

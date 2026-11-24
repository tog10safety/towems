import { useState } from "react";
import { api } from "../api/client.js";
import { ABORT_DESTINATIONS } from "../utils/abort.js";
import { Button, ErrorBanner, Field, Modal, Select, Textarea } from "./ui.jsx";

/**
 * The Operations Center's abort order, in one place.
 *
 * Ground Operations reaches this from both the register and the live map, so
 * the form and its request live here rather than in either page: the order is
 * the same instruction whichever view it was given from.
 */
export default function AbortMissionDialog({ mission, onClose, onDone }) {
  const [destination, setDestination] = useState("station");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (!reason.trim()) {
      setError("Tell the drivers why the mission is being aborted.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.post(`/missions/${mission.id}/tracking/abort/`, {
        destination,
        reason: reason.trim(),
      });
      onDone?.();
    } catch (err) {
      setError(
        err.fieldErrors?.reason ||
          err.fieldErrors?.detail ||
          err.message ||
          "The mission could not be aborted."
      );
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Abort ${mission.mission_number}`}
      onClose={() => (busy ? null : onClose())}
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="danger" size="sm" onClick={submit} disabled={busy}>
            {busy ? "Aborting…" : "Abort mission"}
          </Button>
        </>
      }
    >
      <p className="hint">
        The drivers are notified immediately and the order is written into each vehicle&apos;s
        movement log. The mission keeps being watched until you close it.
      </p>
      <ErrorBanner message={error} />
      <Field
        label="Where do the vehicles finish?"
        required
        hint="The last stop means one the vehicles have already reached; the planned final stop may still be ahead of them."
      >
        <Select
          value={destination}
          onChange={(event) => setDestination(event.target.value)}
        >
          {ABORT_DESTINATIONS.map((row) => (
            <option key={row.value} value={row.value}>
              {row.label}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Reason for the abort" required hint="It is sent to the drivers as written.">
        <Textarea
          value={reason}
          rows={3}
          maxLength={500}
          placeholder="Why is this mission ending early?"
          onChange={(event) => setReason(event.target.value)}
        />
      </Field>
    </Modal>
  );
}

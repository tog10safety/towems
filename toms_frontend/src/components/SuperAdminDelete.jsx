import { useState } from "react";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { IconButton } from "./ui.jsx";

/**
 * Permanent deletion, and the only place in the app that offers it.
 *
 * The Super Administrator seat is the single account allowed to erase a record,
 * and every delete route checks that again server-side. For everyone else this
 * control renders nothing at all rather than a disabled button: the capability
 * is not theirs to see.
 */
export default function SuperAdminDelete({
  path,
  label = "Delete permanently",
  confirmText,
  reasonPrompt = "Reason for deleting (recorded in the audit trail):",
  disabled = false,
  onDone,
  onError,
}) {
  const { permissions } = useAuth();
  const [busy, setBusy] = useState(false);

  if (!permissions?.is_system_owner) return null;

  async function remove() {
    if (!window.confirm(confirmText || "Delete this record permanently? This cannot be undone.")) {
      return;
    }
    const reason = window.prompt(reasonPrompt, "");
    if (reason === null) return;
    if (reason.trim().length < 3) {
      onError?.("Record a reason of at least 3 characters.");
      return;
    }
    setBusy(true);
    try {
      await api.del(path, { reason: reason.trim() });
      onDone?.();
    } catch (err) {
      onError?.(err.message || "The record could not be deleted.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <IconButton
      name="trash"
      variant="danger"
      disabled={disabled || busy}
      label={busy ? "Deleting…" : label}
      onClick={remove}
    />
  );
}

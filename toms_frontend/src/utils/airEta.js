/**
 * Add minutes to a Philippine wall-clock datetime-local value.
 *
 * The app stores and displays naive Philippine time, so the calculation uses
 * UTC only as a calendar engine. This avoids the browser's local time zone and
 * handles a flight crossing midnight.
 */
export function addMinutes(value, minutes) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(
    String(value || "")
  );
  const amount = Number(minutes);
  if (!match || !Number.isFinite(amount)) return "";
  const [, year, month, day, hour, minute] = match.map(Number);
  const next = new Date(
    Date.UTC(year, month - 1, day, hour, minute + Math.round(amount))
  );
  const pad = (part) => String(part).padStart(2, "0");
  return `${next.getUTCFullYear()}-${pad(next.getUTCMonth() + 1)}-${pad(
    next.getUTCDate()
  )}T${pad(next.getUTCHours())}:${pad(next.getUTCMinutes())}`;
}

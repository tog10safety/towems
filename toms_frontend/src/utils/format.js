/**
 * One source of truth for dates and times (spec §7).
 *
 * TOWEMS operates on Philippine Standard Time (UTC+08:00) and displays the
 * 24-hour military clock: ``29 Oct 2026, 1430H (PST)``. Values reach the app in
 * two shapes:
 *
 * 1. Naive wall-clock strings from the API (``2026-10-29T14:30:00``). The
 *    backend stores Philippine local time and ``USE_TZ = False``, so these are
 *    printed exactly as stored - no device time zone is applied.
 * 2. Real instants (anything with ``Z`` or an offset, or a ``Date``). Those are
 *    converted to ``Asia/Manila`` explicitly, so a laptop set to another time
 *    zone still shows Philippine time.
 *
 * Nothing in the app should call ``toLocaleString`` directly: doing so would
 * make the displayed time depend on the operator's device.
 */

const MANILA_TIME_ZONE = "Asia/Manila";
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
const NAIVE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/;

function fromInstant(date) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: MANILA_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const read = (type) => Number(parts.find((part) => part.type === type)?.value);
  return { year: read("year"), month: read("month"), day: read("day"), hour: read("hour"), minute: read("minute") };
}

function manilaParts(value) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : fromInstant(value);
  }
  if (value === null || value === undefined || value === "") return null;

  const text = String(value).trim();
  const naive = NAIVE.exec(text);
  if (naive) {
    const [, year, month, day, hour = "00", minute = "00"] = naive;
    return {
      year: Number(year),
      month: Number(month),
      day: Number(day),
      // Some sources use 24 for midnight; normalise it.
      hour: Number(hour) % 24,
      minute: Number(minute),
    };
  }

  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : fromInstant(parsed);
}

const pad = (n) => String(n).padStart(2, "0");

/** ``29 Oct 2026`` */
export function formatDate(value) {
  const parts = manilaParts(value);
  if (!parts) return value === null || value === undefined || value === "" ? "—" : String(value);
  return `${pad(parts.day)} ${MONTHS[parts.month - 1]} ${parts.year}`;
}

/** ``1430H`` */
export function formatTime(value) {
  const parts = manilaParts(value);
  if (!parts) return value === null || value === undefined || value === "" ? "—" : String(value);
  return `${pad(parts.hour)}${pad(parts.minute)}H`;
}

/** ``29 Oct 2026, 1430H (PST)`` */
export function formatDateTime(value) {
  const parts = manilaParts(value);
  if (!parts) return value === null || value === undefined || value === "" ? "—" : String(value);
  return `${formatDate(value)}, ${formatTime(value)} (PST)`;
}

/** Current Philippine wall-clock date, ``YYYY-MM-DD``. */
export function todayManila() {
  const parts = fromInstant(new Date());
  return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}`;
}

/** Current Philippine wall-clock time, ``HH:MM``. */
export function currentTimeManila() {
  const parts = fromInstant(new Date());
  return `${pad(parts.hour)}:${pad(parts.minute)}`;
}

/** Normalise a typed 24-hour value to ``HH:MM``; ``""`` when invalid. */
export function normalizeTimeInput(value) {
  const match = /^(\d{1,2}):?(\d{1,2})$/.exec(String(value ?? "").trim());
  if (!match) return "";
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return "";
  return `${pad(hour)}:${pad(minute)}`;
}

/**
 * Value for an ``<input type="datetime-local">`` / ``DateTimeInput``:
 * ``2026-10-29T14:30`` in Philippine wall-clock time.
 */
export function toInputDateTime(value) {
  const parts = manilaParts(value);
  if (!parts) return "";
  return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`;
}

/**
 * What the API receives. The backend treats a naive value as Philippine local
 * time, so this stays a wall-clock string with seconds for Django's parser.
 */
export function toApiDateTime(value) {
  const parts = manilaParts(value);
  if (!parts) return null;
  return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}:00`;
}

/**
 * Parse a typed 24-hour entry. Accepts ``2026-10-29 14:30``, ``2026-10-29
 * 1430``, ``2026-10-29T14:30`` and ``29 Oct 2026 1430``. Returns
 * ``YYYY-MM-DDTHH:mm`` or ``null`` when the text is not a valid 24-hour value.
 */
export function parseDateTimeInput(text) {
  const trimmed = String(text ?? "").trim();
  if (!trimmed) return "";

  let day;
  let month;
  let year;
  let hour;
  let minute;

  const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):?(\d{2})(?::\d{2})?)?$/.exec(
    trimmed
  );
  const written = /^(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})(?:[ ,]+(\d{1,2}):?(\d{2})\s*H?)?$/.exec(
    trimmed
  );

  if (iso) {
    [, year, month, day, hour = "0", minute = "0"] = iso;
  } else if (written) {
    const monthIndex = MONTHS.findIndex(
      (name) => name.toLowerCase() === written[2].slice(0, 3).toLowerCase()
    );
    if (monthIndex < 0) return null;
    day = written[1];
    month = String(monthIndex + 1);
    year = written[3];
    hour = written[4] ?? "0";
    minute = written[5] ?? "0";
  } else {
    return null;
  }

  const pad2 = (value) => String(value).padStart(2, "0");
  const h = Number(hour);
  const m = Number(minute);
  if (!Number.isFinite(h) || h > 23 || !Number.isFinite(m) || m > 59) return null;

  return `${year}-${pad2(month)}-${pad2(day)}T${pad2(h)}:${pad2(m)}`;
}

export function titleCase(value) {
  return String(value ?? "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function number(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString();
}

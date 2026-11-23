import { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { formatDate, formatDateTime, toInputDateTime } from "../utils/format.js";

export function Card({ title, actions, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

/**
 * A card that folds away, for panels that are useful but not always needed.
 *
 * The collapsed state keeps a one-line summary so the panel is still
 * identifiable without expanding it.
 */
export function CollapsibleCard({
  title,
  actions,
  children,
  className = "",
  defaultOpen = false,
  summary = "",
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`card card-collapsible ${open ? "is-open" : ""} ${className}`}>
      <header className="card-head">
        <button
          type="button"
          className="card-toggle"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          <span className="card-chevron" aria-hidden="true" />
          <h2>{title}</h2>
          {!open && summary && <span className="card-summary">{summary}</span>}
        </button>
        {actions && <div className="card-actions">{actions}</div>}
      </header>
      {open && <div className="card-body">{children}</div>}
    </section>
  );
}

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  children,
  ...props
}) {
  return (
    <button
      className={`btn btn-${variant} btn-${size} ${className}`.trim()}
      {...props}
    >
      {children}
    </button>
  );
}

/**
 * An action that reads as an icon rather than a word.
 *
 * The label is not lost: it is the tooltip and the accessible name, so the
 * button is still announced and still explains itself on hover.
 */
export function IconButton({
  name,
  label,
  variant = "ghost",
  size = "sm",
  className = "",
  ...props
}) {
  return (
    <button
      type="button"
      className={`btn btn-${variant} btn-${size} btn-icon ${className}`.trim()}
      aria-label={label}
      title={label}
      {...props}
    >
      <Icon name={name} size={size === "sm" ? 16 : 18} />
    </button>
  );
}

export function Field({ label, error, hint, required, htmlFor, children, className = "" }) {
  return (
    <div className={`field ${error ? "field-error" : ""} ${className}`}>
      {label && (
        <label htmlFor={htmlFor}>
          {label}
          {required && <span className="req"> *</span>}
        </label>
      )}
      {children}
      {hint && !error && <p className="hint">{hint}</p>}
      {error && <p className="field-message">{error}</p>}
    </div>
  );
}

export function Input({ error, className = "", ...props }) {
  return (
    <input
      className={`${error ? "input invalid" : "input"} ${className}`.trim()}
      {...props}
    />
  );
}

export function Select({ error, className = "", children, ...props }) {
  return (
    <select
      className={`${error ? "input invalid" : "input"} ${className}`.trim()}
      {...props}
    >
      {children}
    </select>
  );
}

export function Textarea({ error, className = "", ...props }) {
  return (
    <textarea
      className={`${error ? "input invalid" : "input"} ${className}`.trim()}
      rows={3}
      {...props}
    />
  );
}

/** Today in Philippine time: the first day a plan may be scheduled for. */
function todayDate() {
  return toInputDateTime(new Date()).slice(0, 10);
}

const pad = (value) => String(value).padStart(2, "0");

/**
 * Date-and-time entry in Philippine time, on a 24-hour clock (spec §7).
 *
 * The day comes from a native calendar. The time is two number fields - hour
 * then minute - because a browser's own time control renders in the device
 * locale and brings AM/PM back for an operator whose machine is set to a
 * 12-hour clock; these fields always read 0000 to 2359. Each field has its own
 * spinner and accepts arrow keys, so the value can be clicked up or down
 * without typing. The parent receives a ``YYYY-MM-DDTHH:mm`` wall-clock
 * string, exactly what the backend stores.
 *
 * ``minDate`` raises the floor above today (a return cannot precede its
 * departure, for example); days before it are disabled in the calendar and
 * clamped if one arrives from the keyboard.
 *
 * ``minDateTime`` is that floor with a time on it. On the floor's own day a
 * time earlier than the floor is pulled up to it, so a mission starting at
 * 1430 cannot be given an end of 0900 on the same day.
 */
export function DateTimeInput({ value, onChange, error, id, minDate, minDateTime, allowPast = false, disabled = false, ...props }) {
  const floor = minDateTime ? toInputDateTime(minDateTime) : "";
  const floorDay = floor.slice(0, 10);
  const defaultEarliest = allowPast ? "" : todayDate();
  const earliest = floorDay > (minDate || defaultEarliest) ? floorDay : minDate || defaultEarliest;
  const normalised = toInputDateTime(value);
  const [datePart, timePart] = normalised ? normalised.split("T") : ["", ""];

  // The fields hold what is being typed; the parent only hears complete times,
  // so clearing the hour on the way to a new one does not blank the field.
  const [hourText, setHourText] = useState(timePart ? timePart.slice(0, 2) : "");
  const [minuteText, setMinuteText] = useState(timePart ? timePart.slice(3, 5) : "");

  // A value this control has just written is already on screen; copying it back
  // into the fields would fight the operator's typing ("1" becoming "01").
  const written = useRef(normalised);

  useEffect(() => {
    if (normalised === written.current) return;
    written.current = normalised;
    const [hour = "", minute = ""] = normalised
      ? (normalised.split("T")[1] || "").split(":")
      : ["", ""];
    setHourText(hour);
    setMinuteText(minute);
  }, [normalised]);

  // The time floor only applies to the day it belongs to; any later day may
  // use the whole clock.
  const floorTime = floorDay && datePart === floorDay ? floor.slice(11, 16) : "";

  const clamp = (value, highest) => Math.min(highest, Math.max(0, Math.trunc(Number(value)) || 0));

  /** Writes the value and returns the time actually stored, after clamping. */
  function emit(nextDate, nextTime) {
    if (!nextDate) {
      written.current = "";
      onChange("");
      return "";
    }
    const day = earliest && nextDate < earliest ? earliest : nextDate;
    const lowest = day === floorDay ? floor.slice(11, 16) : "";
    const time = nextTime || "08:00";
    const finalTime = lowest && time < lowest ? lowest : time;
    written.current = `${day}T${finalTime}`;
    onChange(written.current);
    return finalTime;
  }

  function changeTime(nextHour, nextMinute) {
    setHourText(nextHour);
    setMinuteText(nextMinute);
    if (nextHour === "" || nextMinute === "") return;
    const hour = clamp(nextHour, 23);
    const minute = clamp(nextMinute, 59);
    const requested = `${pad(hour)}:${pad(minute)}`;
    const stored = emit(datePart, requested);
    // A number out of range, or a time the floor overruled, is shown as what
    // was actually stored rather than as what was typed.
    if (
      stored &&
      (stored !== requested || Number(nextHour) !== hour || Number(nextMinute) !== minute)
    ) {
      setHourText(stored.slice(0, 2));
      setMinuteText(stored.slice(3, 5));
    }
  }

  // A new day keeps the time already entered, or fills in the start of the day.
  function changeDate(nextDate) {
    const requested =
      hourText !== "" && minuteText !== ""
        ? `${pad(clamp(hourText, 23))}:${pad(clamp(minuteText, 59))}`
        : "";
    const time = emit(nextDate, requested);
    setHourText(time ? time.slice(0, 2) : "");
    setMinuteText(time ? time.slice(3, 5) : "");
  }

  // Leaving a field pads it: "9" reads "09" without changing the value.
  function tidyTime() {
    if (hourText === "" || minuteText === "") return;
    const stored = emit(datePart, `${pad(clamp(hourText, 23))}:${pad(clamp(minuteText, 59))}`);
    if (stored) {
      setHourText(stored.slice(0, 2));
      setMinuteText(stored.slice(3, 5));
    }
  }

  const tone = error ? "input invalid" : "input";
  const timeTitle = !datePart
    ? "Pick a day first"
    : floorTime
      ? `PST, 24-hour: hour 00-23, minute 00-59. Earlier than ${floorTime.replace(":", "")}H is refused on this day.`
      : "PST, 24-hour: hour 00-23, minute 00-59";

  return (
    <div className="inline-row date-time-input">
      <input
        type="date"
        id={id}
        className={tone}
        value={datePart}
        min={earliest || undefined}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        title={
          earliest
            ? `Pick the day (PST). Earlier than ${earliest} is unavailable.`
            : "Pick the day (PST)."
        }
        onChange={(event) => changeDate(event.target.value)}
        {...props}
      />
      <div className="time-parts" role="group" aria-label="Time (PST, 24-hour)">
        <input
          type="number"
          inputMode="numeric"
          className={tone}
          value={hourText}
          min={0}
          max={23}
          step={1}
          disabled={disabled || !datePart}
          placeholder="HH"
          aria-label="Hour, 00 to 23"
          title={timeTitle}
          onChange={(event) => changeTime(event.target.value, minuteText)}
          onBlur={tidyTime}
        />
        <span className="time-colon" aria-hidden="true">
          :
        </span>
        <input
          type="number"
          inputMode="numeric"
          className={tone}
          value={minuteText}
          min={0}
          max={59}
          step={1}
          disabled={disabled || !datePart}
          placeholder="MM"
          aria-label="Minute, 00 to 59"
          title={timeTitle}
          onChange={(event) => changeTime(hourText, event.target.value)}
          onBlur={tidyTime}
        />
      </div>
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide = false }) {
  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape") onClose?.();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Closing on a click outside only counts when the press also started
  // outside: a click that begins inside the dialog and lands on the backdrop
  // (a layout shift, a dragged text selection) must not throw the form away.
  const pressedBackdrop = useRef(false);

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      onMouseDown={(event) => {
        pressedBackdrop.current = event.target === event.currentTarget;
      }}
      onClick={(event) => {
        const outside = pressedBackdrop.current;
        pressedBackdrop.current = false;
        if (outside && event.target === event.currentTarget) onClose?.();
      }}
    >
      <div
        className={`modal ${wide ? "modal-wide" : ""}`}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}

export function Table({
  columns,
  rows,
  rowKey = "id",
  empty = "No records found.",
  onRowClick,
  stack = false,
}) {
  if (!rows?.length) return <EmptyState message={empty} />;
  return (
    // ``stack`` turns each row into a small card on a phone: every cell keeps
    // its column label, so a wide table stays readable without sideways
    // scrolling. Used where a phone is a realistic device (the hazard library).
    <div className={`table-wrap ${stack ? "table-stack" : ""}`}>
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} style={column.width ? { width: column.width } : undefined}>
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row[rowKey]}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={onRowClick ? "clickable" : undefined}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`cell-${column.key}`}
                  data-label={column.label || undefined}
                >
                  {column.render ? column.render(row) : format(row[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;

function format(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") {
    // No table may show a raw ISO timestamp: every one is rendered in
    // Philippine time, 24-hour format (spec §7).
    if (ISO_DATETIME.test(value)) return formatDateTime(value);
    if (ISO_DATE.test(value)) return formatDate(value);
  }
  return String(value);
}

const TONES = {
  success: "badge-success",
  warning: "badge-warning",
  danger: "badge-danger",
  info: "badge-info",
  muted: "badge-muted",
};

export function Badge({ tone = "muted", children }) {
  return <span className={`badge ${TONES[tone] ?? TONES.muted}`}>{children}</span>;
}

export function StatusBadge({ status }) {
  const map = {
    draft: "muted",
    planned: "info",
    approved: "success",
    // A running mission, vehicle run or trip is in progress, not finished:
    // yellow distinguishes it from an approved plan (green).
    active: "warning",
    in_progress: "warning",
    completed: "success",
    cancelled: "danger",
    submitted: "info",
    reviewed: "success",
    pending: "warning",
    rejected: "danger",
    needs_revision: "warning",
    available: "success",
    scheduled: "info",
    in_flight: "info",
    on_trip: "info",
    maintenance: "warning",
    grounded: "danger",
    reserved: "warning",
    unserviceable: "danger",
    serviceable: "success",
    condemned: "danger",
  };
  const label = String(status ?? "").replace(/_/g, " ");
  return <Badge tone={map[status] ?? "muted"}>{label || "—"}</Badge>;
}

export function Loading({ label = "Loading…" }) {
  return (
    <div className="state">
      <span className="spinner" aria-hidden="true" /> {label}
    </div>
  );
}

export function ErrorBanner({ message, onRetry }) {
  if (!message) return null;
  return (
    <div className="alert alert-danger" role="alert">
      <span>{message}</span>
      {onRetry && (
        <Button variant="ghost" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ message }) {
  return <div className="state state-empty">{message}</div>;
}

export function Stat({ label, value, hint, tone, children }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ""}`}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value ?? "—"}</span>
      {hint && <span className="stat-hint">{hint}</span>}
      {children}
    </div>
  );
}

export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={active === tab.value}
          className={active === tab.value ? "tab active" : "tab"}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/**
 * The numbered pages to show for ``page`` of ``pages``.
 *
 * Always the first and last page, plus the neighbours of the current one, with
 * an ellipsis where the run breaks: ``1 … 4 5 6 … 12``. Seven numbers at most,
 * so the control never becomes a wrapping wall of buttons.
 */
function pageNumbers(page, pages) {
  if (pages <= 7) return Array.from({ length: pages }, (_, index) => index + 1);
  const current = Math.min(Math.max(page, 1), pages);
  const shown = new Set([1, pages, current]);
  [current - 1, current + 1].forEach((near) => {
    if (near > 1 && near < pages) shown.add(near);
  });
  // At either end the run is filled out, so the first click is always forward
  // (or back) and the gap reads as the middle of the list, not its edge.
  if (current <= 3) [2, 3, 4].forEach((near) => near < pages && shown.add(near));
  if (current >= pages - 2) {
    [pages - 1, pages - 2, pages - 3].forEach((near) => near > 1 && shown.add(near));
  }
  const numbers = [...shown].sort((a, b) => a - b);
  const output = [];
  numbers.forEach((number, index) => {
    if (index && number - numbers[index - 1] > 1) output.push("gap");
    output.push(number);
  });
  return output;
}

export function Pagination({ page, pageSize, count, onPage }) {
  const pages = Math.max(1, Math.ceil((count || 0) / pageSize));
  if (pages <= 1) return null;
  return (
    <nav className="pagination" aria-label="Pagination">
      <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Previous
      </Button>
      <div className="page-numbers">
        {pageNumbers(page, pages).map((number, index) =>
          number === "gap" ? (
            <span key={`gap-${index}`} className="page-gap" aria-hidden="true">
              …
            </span>
          ) : (
            <button
              key={number}
              type="button"
              className={number === page ? "page-number active" : "page-number"}
              aria-current={number === page ? "page" : undefined}
              onClick={() => onPage(number)}
            >
              {number}
            </button>
          )
        )}
      </div>
      <span className="page-count">
        Page {page} of {pages} · {count} records
      </span>
      <Button variant="ghost" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next
      </Button>
    </nav>
  );
}

export function Detail({ label, value, children }) {
  return (
    <div className="detail">
      <dt>{label}</dt>
      <dd>{children ?? format(value)}</dd>
    </div>
  );
}

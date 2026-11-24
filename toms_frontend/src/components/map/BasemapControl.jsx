import { useEffect, useMemo, useState } from "react";
import { availableBasemaps } from "../../map/config.js";

/**
 * Basemap / map style picker.
 *
 * Only providers the backend reports as available are listed; an unavailable
 * provider explains why instead of silently doing nothing. The selection itself
 * is persisted by the caller.
 */
export default function BasemapControl({
  config,
  value,
  onChange,
  compact = false,
  showLabel = true,
  iconOnly = false,
}) {
  const [open, setOpen] = useState(false);

  const basemaps = useMemo(() => availableBasemaps(config), [config]);
  const unavailable = (config?.basemaps ?? []).filter((item) => item.available === false);
  const current = basemaps.find((item) => item.id === value) || basemaps[0];
  const kind = current?.group?.toLowerCase() || "standard";

  useEffect(() => {
    if (!open) return undefined;
    function onKey(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  if (!basemaps.length) {
    return <p className="hint">No basemap provider is configured.</p>;
  }

  const options = (
    <div className="basemap-list" role="listbox" aria-label="Basemap">
      {basemaps.map((item) => (
        <button
          key={item.id}
          type="button"
          role="option"
          aria-selected={item.id === current?.id}
          className={`basemap-option ${item.id === current?.id ? "active" : ""}`}
          onClick={() => {
            setOpen(false);
            onChange(item.id);
          }}
        >
          <span className="basemap-thumb" data-kind={item.group?.toLowerCase() || "standard"} />
          <span>
            <strong>{item.label}</strong>
            <span className="hint">{item.provider}</span>
          </span>
        </button>
      ))}
      {unavailable.map((item) => (
        <div key={item.id} className="basemap-option disabled" aria-disabled="true">
          <span className="basemap-thumb" data-kind="unavailable" />
          <span>
            <strong>{item.label}</strong>
            <span className="hint">{item.unavailable_reason || "Not configured"}</span>
          </span>
        </div>
      ))}
    </div>
  );

  if (compact) {
    return (
      <label className="basemap-compact">
        {showLabel && <span className="hint">Map style</span>}
        <select value={current?.id || ""} onChange={(event) => onChange(event.target.value)}>
          {basemaps.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
    );
  }

  // Icon only: the current style reads as a swatch, and the list opens beside
  // it. Used where the map is the screen and the control is a floating button.
  if (iconOnly) {
    return (
      <div className="basemap-icon">
        <button
          type="button"
          className="basemap-icon-button"
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label={`Map style: ${current?.label || "choose"}`}
          title={`Map style: ${current?.label || "choose"}`}
          onClick={() => setOpen((state) => !state)}
        >
          <span className="basemap-thumb" data-kind={kind} />
        </button>
        {open && options}
      </div>
    );
  }

  return (
    <div className="basemap-control">
      <button
        type="button"
        className="basemap-current"
        aria-expanded={open}
        onClick={() => setOpen((state) => !state)}
      >
        <span className="basemap-thumb" data-kind={kind} />
        <span>
          <strong>{current?.label || "Map style"}</strong>
          <span className="hint">Change basemap</span>
        </span>
      </button>

      {open && options}
    </div>
  );
}

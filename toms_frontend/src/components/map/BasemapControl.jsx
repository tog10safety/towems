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
}) {
  const [open, setOpen] = useState(false);

  const basemaps = useMemo(() => availableBasemaps(config), [config]);
  const unavailable = (config?.basemaps ?? []).filter((item) => item.available === false);
  const current = basemaps.find((item) => item.id === value) || basemaps[0];

  if (!basemaps.length) {
    return <p className="hint">No basemap provider is configured.</p>;
  }

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

  return (
    <div className="basemap-control">
      <button
        type="button"
        className="basemap-current"
        aria-expanded={open}
        onClick={() => setOpen((state) => !state)}
      >
        <span className="basemap-thumb" data-kind={current?.group?.toLowerCase() || "standard"} />
        <span>
          <strong>{current?.label || "Map style"}</strong>
          <span className="hint">Change basemap</span>
        </span>
      </button>

      {open && (
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
      )}
    </div>
  );
}

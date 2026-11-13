/**
 * Basemap helpers.
 *
 * Provider definitions come from the backend (`/map/config/`) so a deployment can
 * change, add or remove a tile service (including a self-hosted one) without a
 * frontend rebuild. This module only knows how to *use* an entry.
 */

export const BASEMAP_STORAGE_KEY = "toms.map.basemap";
export const LAYERS_STORAGE_KEY = "toms.map.layers";

export function availableBasemaps(config) {
  return (config?.basemaps ?? []).filter((item) => item.available !== false);
}

/** Resolve the operator's choice, falling back to theme then server default. */
export function resolveBasemap(config, preferredId, theme) {
  const available = availableBasemaps(config);
  const byId = available.find((item) => item.id === preferredId);
  if (byId) return byId;
  const themed = available.find((item) => item.theme === theme);
  if (themed) return themed;
  const serverDefault = available.find((item) => item.id === config?.default);
  return serverDefault || available[0] || null;
}

export function readStored(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage disabled: the choice simply does not persist */
  }
}

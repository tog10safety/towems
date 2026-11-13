/**
 * Operational layer panel.
 *
 * Each toggle changes drawing only: no request is made that could alter AOR,
 * mission, hazard or vehicle records, and visibility never grants permissions -
 * the backend already scoped the data before it reached the browser.
 */
export default function MapLayersPanel({ layers, visible, onToggle, extra = null }) {
  if (!layers?.length) return <p className="hint">No map layers are configured.</p>;

  return (
    <div className="map-layers">
      {layers.map((layer) => (
        <label key={layer.id} className="map-layer-row">
          <input
            type="checkbox"
            checked={visible.includes(layer.id)}
            onChange={(event) => onToggle(layer.id, event.target.checked)}
          />
          <span>
            <strong>{layer.label}</strong>
            {layer.description && <span className="hint">{layer.description}</span>}
          </span>
        </label>
      ))}
      {extra}
    </div>
  );
}

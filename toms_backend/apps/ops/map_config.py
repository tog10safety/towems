"""Basemap and layer configuration served to the map UI.

Basemaps are configuration, not code: each entry names a licensed tile provider,
its attribution and the zoom range it supports. Adding a provider means adding
an entry here (or overriding it through the environment) - no AOR, hazard,
mission or vehicle code changes.

Every provider below is usable without an API key and is displayed with its
required attribution. Providers that need a key are declared with ``env_key``
and are reported as unavailable until that key is configured, so the UI never
offers a basemap that cannot load.

Nothing in this module talks to a provider. Operational data (missions,
personnel, positions, AOR geometry) is always fetched from the TOWEMS backend;
tile requests only contain the standard z/x/y tile coordinates.

See docs/AOR_GIS_ENHANCEMENT.md for licence, cost and data-flow notes.
"""

from __future__ import annotations

import os

DEFAULT_BASEMAP_ENV = "TOMS_DEFAULT_BASEMAP"

OSM_ATTRIBUTION = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
    "contributors"
)
#: Required attribution for the Esri Canvas dark basemap and its labels.
ESRI_DARK_ATTRIBUTION = (
    "Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS User Community"
)
ESRI_ATTRIBUTION = (
    "Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community"
)
OPENTOPO_ATTRIBUTION = (
    'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
    'contributors, SRTM | Map style &copy; <a href="https://opentopomap.org">OpenTopoMap</a> '
    "(CC-BY-SA)"
)

#: ``theme`` lets the UI pick a basemap matching light/dark without changing the
#: operator's explicit choice; ``overlays`` are label/reference tiles drawn on
#: top of the imagery (hybrid mode).
BASEMAPS: list[dict] = [
    {
        "id": "streets",
        "label": "Standard streets",
        "group": "Standard",
        "theme": "light",
        "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": OSM_ATTRIBUTION,
        "max_zoom": 19,
        "provider": "OpenStreetMap",
        "license": "ODbL 1.0",
    },
    {
        # CARTO's keyless basemaps now answer with an "API Key Required" tile,
        # so the night basemap is Esri's keyless dark canvas, the same provider
        # as the imagery layers. Its cache stops at zoom 16, which is declared
        # as the native zoom: Leaflet upscales those tiles instead of showing
        # blank ones when the operator zooms further in.
        "id": "streets_dark",
        "label": "Standard streets (night)",
        "group": "Standard",
        "theme": "dark",
        "url": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
        ),
        "attribution": ESRI_DARK_ATTRIBUTION,
        "max_zoom": 19,
        "max_native_zoom": 16,
        "provider": "Esri",
        "license": "Esri World Dark Gray Canvas - attribution required",
        "overlays": [
            {
                "url": (
                    "https://server.arcgisonline.com/ArcGIS/rest/services/"
                    "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"
                ),
                "attribution": ESRI_DARK_ATTRIBUTION,
            }
        ],
    },
    {
        "id": "satellite",
        "label": "Satellite imagery",
        "group": "Imagery",
        "url": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        "attribution": ESRI_ATTRIBUTION,
        "max_zoom": 19,
        "provider": "Esri",
        "license": "Esri World Imagery - attribution required",
    },
    {
        "id": "hybrid",
        "label": "Satellite with labels",
        "group": "Imagery",
        "url": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        "attribution": ESRI_ATTRIBUTION,
        "max_zoom": 19,
        "provider": "Esri",
        "license": "Esri World Imagery + reference labels",
        "overlays": [
            {
                "url": (
                    "https://server.arcgisonline.com/ArcGIS/rest/services/"
                    "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
                ),
                "attribution": "Labels &copy; Esri",
            }
        ],
    },
    {
        "id": "terrain",
        "label": "Terrain / topographic",
        "group": "Terrain",
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": OPENTOPO_ATTRIBUTION,
        "max_zoom": 17,
        "provider": "OpenTopoMap",
        "license": "CC-BY-SA 3.0",
    },
    {
        # Placeholder for an organisation-hosted tile server (internal network).
        # It is only offered when TOMS_INTERNAL_TILE_URL is configured, so no
        # dead option appears in the interface and operational deployments can
        # keep all map traffic inside their own network.
        "id": "internal",
        "label": "Organisation basemap",
        "group": "Organisation",
        # Resolved per request so a deployment can configure or remove the
        # internal tile service without a code change or restart-time import.
        "url_env": "TOMS_INTERNAL_TILE_URL",
        "attribution_env": (
            "TOMS_INTERNAL_TILE_ATTRIBUTION",
            "Organisation map service",
        ),
        "max_zoom_env": ("TOMS_INTERNAL_TILE_MAX_ZOOM", 19),
        "provider": "Self-hosted",
        "license": "Internal use",
    },
]

#: Operational layers the map can draw. ``source`` names the endpoint the data
#: already comes from; the UI never invents a layer whose data does not exist.
LAYERS: list[dict] = [
    {
        "id": "aor",
        "label": "Unit AOR boundaries",
        "description": "Active areas of responsibility for your unit scope.",
        "source": "/api/aor/",
        "default_on": True,
    },
    {
        "id": "units",
        "label": "Unit locations",
        "description": (
            "Each unit's own grid coordinate, pinned with the seal uploaded in "
            "Unit Management."
        ),
        "source": "/api/units/markers/",
        "default_on": True,
    },
    {
        "id": "admin_boundaries",
        "label": "Province / city boundaries",
        "description": "Official PSGC province and city outlines (reference geography).",
        "source": "/api/admin-areas/boundaries/",
        "default_on": False,
    },
    {
        "id": "missions",
        "label": "Mission locations",
        "description": "Ground and air mission locations with stored coordinates.",
        "source": "/api/map/picture/",
        "default_on": True,
    },
    {
        "id": "air",
        "label": "Air operations",
        "description": "Air missions and their flight information.",
        "source": "/api/map/picture/",
        "default_on": True,
    },
    {
        "id": "ground",
        "label": "Ground operations",
        "description": "Ground missions and their operational areas.",
        "source": "/api/map/picture/",
        "default_on": True,
    },
    {
        "id": "vehicles",
        "label": "Vehicle last known positions",
        "description": "Last reported vehicle positions with their timestamp; not live tracking.",
        "source": "/api/map/picture/",
        "default_on": True,
    },
    {
        "id": "trips",
        "label": "Vehicle routes",
        "description": "Planned itinerary points for vehicle trips.",
        "source": "/api/map/picture/",
        "default_on": True,
    },
    {
        "id": "hazards",
        "label": "Georeferenced hazards",
        "description": "Hazard registry entries that carry stored coordinates.",
        "source": "/api/map/picture/",
        "default_on": True,
    },
]

DATA_FLOW_NOTES = {
    "basemaps": (
        "Tile requests disclose only the requested tile coordinates, the client IP and "
        "browser metadata to the tile provider. No mission, personnel, vehicle, AOR or "
        "other operational attribute is ever added to a tile URL."
    ),
    "operational": (
        "Operational features are fetched from the authenticated TOWEMS API and drawn "
        "locally; they are never sent to a basemap provider."
    ),
    "self_hosted": (
        "Set TOMS_INTERNAL_TILE_URL to a self-hosted tile service to keep map traffic "
        "inside the organisation's network."
    ),
}


def _resolve(entry: dict) -> dict:
    """Copy one basemap entry, resolving environment-supplied fields."""
    item = {
        key: value
        for key, value in entry.items()
        if key not in {"url_env", "attribution_env", "max_zoom_env"}
    }
    url_env = entry.get("url_env")
    if url_env:
        item["url"] = os.environ.get(url_env, "")
    attribution_env = entry.get("attribution_env")
    if attribution_env:
        name, fallback = attribution_env
        item["attribution"] = os.environ.get(name, fallback)
    zoom_env = entry.get("max_zoom_env")
    if zoom_env:
        name, fallback = zoom_env
        try:
            item["max_zoom"] = int(os.environ.get(name, fallback))
        except (TypeError, ValueError):
            item["max_zoom"] = fallback

    if not item.get("url"):
        item["available"] = False
        item["unavailable_reason"] = f"{url_env or 'TOMS_INTERNAL_TILE_URL'} is not configured."
    else:
        item["available"] = True
    return item


def basemap_payload() -> dict:
    items = [_resolve(entry) for entry in BASEMAPS]

    default = os.environ.get(DEFAULT_BASEMAP_ENV, "").strip()
    ids = {item["id"] for item in items if item["available"]}
    if default not in ids:
        default = "streets"
    return {"basemaps": items, "default": default}


def map_config_payload() -> dict:
    payload = basemap_payload()
    return {
        **payload,
        "layers": LAYERS,
        "data_flow": DATA_FLOW_NOTES,
        "default_center": [12.8797, 121.774],
        "default_zoom": 6,
    }

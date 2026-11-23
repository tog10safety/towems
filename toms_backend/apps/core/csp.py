"""Report-only CSP construction and browser violation ingestion.

The policy is intentionally report-only for the rollout: browsers enforce
nothing, but every blocked resource is posted to ``CSP_REPORT_URI`` and logged
under ``towems.csp``. Once the reports are clean, set
``TOMS_CSP_REPORT_ONLY=false`` to send the same policy as an enforcing header.

Image sources are derived from :mod:`apps.ops.map_config`, so adding or
re-pointing a basemap (including ``TOMS_INTERNAL_TILE_URL``) updates the policy
without a second provider allow-list to maintain.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger("towems.csp")

#: Browser reports are capped before parsing. A violation report is small; the
#: limit prevents an unauthenticated reporter from making the endpoint allocate
#: an unbounded body.
MAX_REPORT_BYTES = 64 * 1024

#: Report strings are truncated before logging so a crafted document URL or
#: sample cannot flood the log.
MAX_FIELD_CHARS = 500


def _origin(value: str) -> str:
    """Return a CSP origin for a tile URL, or ``""`` when it is not usable.

    Leaflet's ``{s}`` placeholder is translated to a CSP host wildcard. Invalid
    values, non-HTTP(S) schemes, credentials and header-breaking whitespace are
    rejected rather than copied into the policy.
    """

    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = f"https:{value}"

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return ""

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return ""

    host = (parsed.hostname or "").replace("{s}", "*").lower()
    if not host or any(character in host for character in " ;\r\n\t"):
        return ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    origin = f"{scheme}://{host}"
    if port:
        origin = f"{origin}:{port}"
    return origin


def map_provider_origins() -> list[str]:
    """Origins of every available configured basemap and its label overlays."""

    # Imported lazily so Django's app registry is ready and environment changes
    # are picked up on the next request, exactly as /api/map/config/ does.
    from apps.ops import map_config

    payload = map_config.basemap_payload()
    origins: set[str] = set()
    for basemap in payload.get("basemaps", []):
        if basemap.get("available") is False:
            continue
        origins.add(_origin(basemap.get("url", "")))
        for overlay in basemap.get("overlays", []):
            origins.add(_origin(overlay.get("url", "")))
    origins.discard("")
    return sorted(origins)


def _settings_list(name: str) -> list[str]:
    values = getattr(settings, name, []) or []
    return [str(value).strip() for value in values if str(value).strip()]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_policy() -> str:
    """Build the CSP once per response from settings and map configuration."""

    report_uri = str(
        getattr(settings, "CSP_REPORT_URI", "/api/csp-report/") or "/api/csp-report/"
    ).strip()
    image_sources = _dedupe(
        [
            "'self'",
            "data:",
            "blob:",
            *map_provider_origins(),
            *_settings_list("CSP_EXTRA_IMG_SRC"),
        ]
    )
    connect_sources = _dedupe(
        ["'self'", *_settings_list("CSP_EXTRA_CONNECT_SRC")]
    )

    # 'unsafe-inline' is present for the first rollout because index.html boots
    # the saved theme with an inline script and Leaflet/React write inline
    # styles. The report stream will show when those can be removed; do not
    # tighten this until the reports are clean.
    directives: list[tuple[str, list[str]]] = [
        ("default-src", ["'self'"]),
        ("base-uri", ["'self'"]),
        ("object-src", ["'none'"]),
        ("frame-ancestors", ["'none'"]),
        ("form-action", ["'self'"]),
        ("script-src", ["'self'", "'unsafe-inline'"]),
        ("style-src", ["'self'", "'unsafe-inline'"]),
        ("img-src", image_sources),
        ("font-src", ["'self'", "data:"]),
        ("connect-src", connect_sources),
        ("worker-src", ["'self'", "blob:"]),
        ("manifest-src", ["'self'"]),
        ("media-src", ["'self'"]),
        ("report-uri", [report_uri]),
    ]
    return "; ".join(
        f"{name} {' '.join(values)}" for name, values in directives if values
    )


def _short(value):
    if not isinstance(value, str):
        return value
    return value[:MAX_FIELD_CHARS]


def _pick(report: dict, *names: str):
    for name in names:
        value = report.get(name)
        if value not in (None, ""):
            return value
    return None


def _normalise(report: dict) -> dict:
    """Accept both legacy ``csp-report`` and Reporting API field names."""

    fields = {
        "directive": _pick(
            report,
            "effective-directive",
            "effectiveDirective",
            "violated-directive",
            "violatedDirective",
        ),
        "blocked_uri": _pick(report, "blocked-uri", "blockedURL"),
        "document_uri": _pick(report, "document-uri", "documentURL"),
        "source_file": _pick(report, "source-file", "sourceFile"),
        "line_number": _pick(report, "line-number", "lineNumber"),
        "column_number": _pick(report, "column-number", "columnNumber"),
        "disposition": _pick(report, "disposition"),
        "status_code": _pick(report, "status-code", "statusCode"),
        "script_sample": _pick(report, "script-sample", "sample"),
    }
    return {
        key: _short(value)
        for key, value in fields.items()
        if value not in (None, "")
    }


def _reports(payload):
    if isinstance(payload, dict):
        legacy = payload.get("csp-report")
        if isinstance(legacy, dict):
            yield legacy
        else:
            yield payload
        return
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            body = item.get("body")
            if isinstance(body, dict):
                yield body
            elif item.get("type") in (None, "csp-violation"):
                yield item


@csrf_exempt
@require_POST
def csp_report(request):
    """Log browser CSP reports. Always 204 so browsers do not retry forever."""

    raw = request.body[:MAX_REPORT_BYTES]
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        logger.warning(
            "CSP report ignored: invalid JSON request_id=%s",
            getattr(request, "correlation_id", ""),
        )
        return HttpResponse(status=204)

    reports = [_normalise(report) for report in _reports(payload)]
    reports = [report for report in reports if report]
    for report in reports:
        logger.warning(
            "CSP violation %s",
            json.dumps(
                {
                    **report,
                    "request_id": getattr(request, "correlation_id", ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-store"
    return response

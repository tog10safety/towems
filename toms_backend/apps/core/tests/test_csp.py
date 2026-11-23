"""Report-only CSP header, map-provider calibration and report logging."""

from __future__ import annotations

import json
import logging

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.core import csp

pytestmark = pytest.mark.django_db


def test_health_response_carries_a_report_only_policy(api, monkeypatch):
    monkeypatch.delenv("TOMS_INTERNAL_TILE_URL", raising=False)
    response = api.get("/api/health/")

    assert response.status_code == 200
    assert "Content-Security-Policy" not in response
    policy = response["Content-Security-Policy-Report-Only"]
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "report-uri /api/csp-report/" in policy
    # These are the configured, keyless providers served by /api/map/config/.
    assert "img-src 'self' data: blob:" in policy
    assert "https://*.tile.openstreetmap.org" in policy
    assert "https://server.arcgisonline.com" in policy
    assert "https://*.tile.opentopomap.org" in policy


def test_internal_tile_service_is_added_when_configured(api, monkeypatch):
    monkeypatch.setenv(
        "TOMS_INTERNAL_TILE_URL", "https://tiles.internal:8443/{z}/{x}/{y}.png"
    )
    policy = api.get("/api/health/")["Content-Security-Policy-Report-Only"]

    assert "https://tiles.internal:8443" in policy


def test_unconfigured_internal_tile_service_is_not_allowed(api, monkeypatch):
    monkeypatch.delenv("TOMS_INTERNAL_TILE_URL", raising=False)
    policy = api.get("/api/health/")["Content-Security-Policy-Report-Only"]

    assert "tiles.internal" not in policy


@override_settings(
    CSP_EXTRA_IMG_SRC=["https://images.example.mil"],
    CSP_EXTRA_CONNECT_SRC=["wss://realtime.example.mil"],
)
def test_rollout_allow_list_can_be_extended_without_code_changes(api):
    policy = api.get("/api/health/")["Content-Security-Policy-Report-Only"]

    assert "https://images.example.mil" in policy
    assert "connect-src 'self' wss://realtime.example.mil" in policy


@override_settings(CSP_REPORT_ONLY=False)
def test_the_same_policy_can_be_enforced_after_the_rollout(api):
    response = api.get("/api/health/")

    assert "Content-Security-Policy-Report-Only" not in response
    assert "Content-Security-Policy" in response


def test_leaflet_subdomain_placeholder_becomes_a_csp_wildcard():
    assert (
        csp._origin("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png")
        == "https://*.tile.openstreetmap.org"
    )
    assert csp._origin("javascript:alert(1)") == ""
    assert csp._origin("https://user:password@tiles.example/{z}") == "https://tiles.example"


def test_legacy_csp_report_is_logged(api, caplog):
    payload = {
        "csp-report": {
            "document-uri": "https://toms.example.mil/missions/air",
            "effective-directive": "img-src",
            "blocked-uri": "https://unexpected.example/tile.png",
            "disposition": "report",
        }
    }

    with caplog.at_level(logging.WARNING, logger="towems.csp"):
        response = api.post(
            "/api/csp-report/",
            data=json.dumps(payload),
            content_type="application/csp-report",
        )

    assert response.status_code == 204
    assert response["Cache-Control"] == "no-store"
    assert "CSP violation" in caplog.text
    assert "img-src" in caplog.text
    assert "https://unexpected.example/tile.png" in caplog.text
    assert "document-uri" not in caplog.text  # keys are normalised, not echoed raw


def test_reporting_api_payload_is_logged(api, caplog):
    payload = [
        {
            "type": "csp-violation",
            "body": {
                "documentURL": "https://toms.example.mil/",
                "effectiveDirective": "script-src",
                "blockedURL": "https://unexpected.example/app.js",
                "disposition": "report",
            },
        }
    ]

    with caplog.at_level(logging.WARNING, logger="towems.csp"):
        response = api.post(
            "/api/csp-report/",
            data=json.dumps(payload),
            content_type="application/reports+json",
        )

    assert response.status_code == 204
    assert "script-src" in caplog.text
    assert "https://unexpected.example/app.js" in caplog.text


def test_report_endpoint_accepts_anonymous_posts_without_csrf():
    client = APIClient(enforce_csrf_checks=True)
    response = client.post(
        "/api/csp-report/",
        data=json.dumps({"csp-report": {"effective-directive": "style-src"}}),
        content_type="application/csp-report",
    )

    assert response.status_code == 204


def test_malformed_report_is_ignored_without_error(api, caplog):
    with caplog.at_level(logging.WARNING, logger="towems.csp"):
        response = api.post(
            "/api/csp-report/",
            data="{not-json",
            content_type="application/csp-report",
        )

    assert response.status_code == 204
    assert "invalid JSON" in caplog.text

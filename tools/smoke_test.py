"""End-to-end HTTP smoke test against a running TOWEMS backend.

Exercises every module over real HTTP (no Django test client), using a JWT so
the test does not depend on browser cookies. Read-only: it issues GETs plus a
login and an MGRS conversion.

    python tools/smoke_test.py --base http://127.0.0.1:8000/api \
        --username toms.smoke --password '...'
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def call(base, path, *, method="GET", data=None, token=None):
    request = urllib.request.Request(f"{base}{path}", method=method)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, body, timeout=30) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                # e.g. the OpenAPI schema, which drf-spectacular serves as YAML
                return response.status, {}
    except urllib.error.HTTPError as exc:
        return exc.code, {"detail": exc.read()[:300].decode(errors="replace")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000/api")
    parser.add_argument("--username", default="toms.smoke")
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    base = args.base.rstrip("/")
    failures = []

    status, payload = call(base, "/health/")
    print(f"health                       {status}")
    if status != 200:
        failures.append("health")

    status, payload = call(
        base, "/auth/token/", method="POST",
        data={"username": args.username, "password": args.password},
    )
    print(f"auth/token                   {status}")
    if status != 200:
        print(json.dumps(payload)[:300])
        return 1
    token = payload["access"]

    checks = [
        ("auth/me", "/auth/me/", None),
        ("dashboard", "/dashboard/", None),
        ("personnel", "/personnel/?page_size=5", None),
        ("personnel/assignable", "/personnel/assignable/", None),
        ("units", "/units/?page_size=5", None),
        ("units/tree", "/units/tree/", None),
        ("missions", "/missions/?page_size=5", None),
        ("risk-register", "/risk-register/?page_size=5", None),
        ("risk evaluators", "/risk-register/evaluators/", None),
        ("inventory/categories", "/inventory/categories/", None),
        ("inventory/items", "/inventory/items/?page_size=5", None),
        ("inventory/transactions", "/inventory/transactions/?page_size=5", None),
        ("vehicles", "/vehicles/", None),
        ("trips", "/trips/?page_size=5", None),
        ("aor", "/aor/?page_size=5", None),
        ("notifications", "/notifications/", None),
        ("audit", "/audit/?page_size=5", None),
        ("reports/missions", "/reports/missions/", None),
        ("reports/risk", "/reports/risk/", None),
        ("reports/inventory", "/reports/inventory/", None),
        ("gis/search", "/gis/search/?q=a", None),
        ("openapi schema", "/schema/", None),
    ]

    for label, path, _ in checks:
        status, payload = call(base, path, token=token)
        count = payload.get("count") if isinstance(payload, dict) else None
        suffix = f" count={count}" if count is not None else ""
        print(f"{label:28s} {status}{suffix}")
        if status != 200:
            failures.append(label)

    status, payload = call(
        base, "/gis/mgrs/", method="POST",
        data={"latitude": 48.8584, "longitude": 2.2945}, token=token,
    )
    print(f"{'gis/mgrs convert':28s} {status} {payload.get('mgrs', '')}")
    if status != 200 or not str(payload.get("mgrs", "")).startswith("31UDQ"):
        failures.append("gis/mgrs")

    status, payload = call(base, "/dashboard/", token=None)
    print(f"{'unauthenticated dashboard':28s} {status} (expected 401/403)")
    if status not in (401, 403):
        failures.append("auth boundary")

    print()
    if failures:
        print(f"FAILURES: {', '.join(failures)}")
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

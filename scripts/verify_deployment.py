"""Verify a RideOps deployment (local Docker stack or hosted).

    python scripts/verify_deployment.py --api http://localhost:8001 --web http://localhost:8080
    python scripts/verify_deployment.py --api https://rideops-api.onrender.com --web https://rideops.pages.dev
    python scripts/verify_deployment.py --api http://localhost:8000 --lens   # + Lens checks (LENS_ENABLED=true)

--lens signs up a throwaway account (lens-verify-<random>@example.com) and checks: liveness/readiness,
security headers, session + CSRF, the RideOps demo connection turning healthy with status history,
the SQL guard rejecting DELETE, and the Network Guard blocking the cloud metadata address.

Exits non-zero if any check fails. Uses only the standard library, so it runs anywhere.
A free Render service may be asleep: the first request can take ~1 minute (--timeout).
"""

import argparse
import http.cookiejar
import json
import secrets
import sys
import time
import urllib.error
import urllib.request


def fetch(url: str, timeout: float) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


class LensClient:
    """Cookie-keeping JSON client that echoes the CSRF cookie like the dashboard does."""

    def __init__(self, base: str, timeout: float) -> None:
        self.base, self.timeout = base, timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body: object = None) -> tuple[int, dict, dict]:
        headers = {"Content-Type": "application/json"}
        csrf = next((c.value for c in self.jar if c.name == "lens_csrf"), None)
        if csrf and method != "GET":
            headers["X-CSRF-Token"] = csrf
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:  # noqa: S310
                return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read() or b"{}"
            try:
                return exc.code, json.loads(raw), dict(exc.headers)
            except ValueError:
                return exc.code, {}, dict(exc.headers)


def lens_checks(api_root: str, timeout: float, check) -> None:
    status, body = fetch(f"{api_root}/healthz", timeout)
    check("Lens /healthz", status == 200, f"status={status}")
    status, body = fetch(f"{api_root}/readyz", timeout)
    ready = json.loads(body) if status in (200, 503) else {}
    check("Lens /readyz (db, migrations, keys)", status == 200, json.dumps(ready.get("checks", {})))

    c = LensClient(api_root + "/api/v1", timeout)
    email = f"lens-verify-{secrets.token_hex(4)}@example.com"
    status, me, headers = c.call(
        "POST",
        "/auth/signup",
        {"email": email, "password": secrets.token_urlsafe(18), "display_name": "Verify"},
    )
    check(
        "sign up creates a session",
        status == 201 and any(x.name == "lens_session" for x in c.jar),
        f"status={status}",
    )
    lower = {k.lower(): v for k, v in headers.items()}
    check(
        "security headers present",
        lower.get("x-frame-options") == "DENY" and "content-security-policy" in lower,
        ",".join(
            k
            for k in ("x-frame-options", "content-security-policy", "strict-transport-security")
            if k in lower
        ),
    )
    if status != 201:
        return
    ws = me["current_workspace_id"]
    req = urllib.request.Request(api_root + "/api/v1/workspaces", data=b'{"name":"x"}', method="POST",
                                 headers={"Content-Type": "application/json"})  # fmt: skip
    try:
        c.opener.open(req, timeout=timeout)  # noqa: S310
        csrf_status = 200
    except urllib.error.HTTPError as exc:
        csrf_status = exc.code
    check("mutations without X-CSRF-Token are refused", csrf_status == 403, f"status={csrf_status}")

    demo, deadline = None, time.monotonic() + 120
    while time.monotonic() < deadline:
        _, conns, _ = c.call("GET", f"/workspaces/{ws}/connections")
        demo = (
            next((x for x in conns if x.get("is_demo")), None) if isinstance(conns, list) else None
        )
        if demo and demo["current_state"] in ("healthy", "degraded"):
            break
        time.sleep(5)
    check("demo connection exists and is healthy", bool(demo) and demo["current_state"] in ("healthy", "degraded"),
          demo["current_state"] if demo else "no demo (set RIDEOPS_DEMO_URL)")  # fmt: skip
    if demo:
        _, hist, _ = c.call("GET", f"/workspaces/{ws}/connections/{demo['id']}/status/history")
        check(
            "status history is not empty",
            isinstance(hist, list) and len(hist) > 0,
            f"points={len(hist) if isinstance(hist, list) else 0}",
        )
        status, body, _ = c.call("POST", f"/workspaces/{ws}/connections/{demo['id']}/queries/run",
                                 {"statement": "DELETE FROM core.bookings"})  # fmt: skip
        code = body.get("error", {}).get("code")
        check(
            "query console rejects DELETE",
            status == 422 and code == "QUERY_NOT_READONLY",
            f"status={status}",
        )
    status, body, _ = c.call("POST", f"/workspaces/{ws}/connections/test",
                             {"url": "postgresql://x@169.254.169.254:5432/db"})  # fmt: skip
    last = (body.get("stages") or [{}])[-1] if isinstance(body, dict) else {}
    check(
        "SSRF to 169.254.169.254 is blocked",
        last.get("code") == "CONN_BLOCKED_NETWORK",
        str(last.get("code")),
    )
    c.call("POST", "/auth/logout")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", required=True, help="backend base URL (without /api/v1)")
    parser.add_argument("--web", help="frontend base URL")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument(
        "--lens", action="store_true", help="also run the Lens checks (creates a test account)"
    )
    args = parser.parse_args()
    api = args.api.rstrip("/") + "/api/v1"
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    status, body = fetch(f"{api}/health", args.timeout)
    health = json.loads(body) if status in (200, 503) else {}
    check("API /health returns 200", status == 200, f"status={status}")
    check("database reachable", health.get("database") == "ok", str(health.get("database")))

    for path, key in [
        ("/analytics/overview", "total_bookings"),
        ("/pipelines", None),
        ("/data-quality", "checks"),
        ("/costs/summary", "sources"),
        ("/monitoring/summary", "data_quality"),
    ]:
        status, body = fetch(api + path, args.timeout)
        data = json.loads(body) if status == 200 else None
        ok = status == 200 and (key is None or key in data) and bool(data)
        check(f"GET {path}", ok, f"status={status}")
        if path == "/costs/summary" and data:
            check(
                "cost data carries its source label",
                bool(data["sources"]),
                ",".join(data["sources"]),
            )

    status, body = fetch(f"{api}/runs/does-not-exist", args.timeout)
    check(
        "errors use the JSON error shape", status == 404 and '"error"' in body, f"status={status}"
    )

    if args.lens:
        lens_checks(args.api.rstrip("/"), args.timeout, check)

    if args.web:
        web = args.web.rstrip("/")
        status, body = fetch(web + "/", args.timeout)
        check(
            "landing page is served at /",
            status == 200 and 'id="hero-h"' in body,
            f"status={status}",
        )
        status, body = fetch(web + "/app/", args.timeout)
        check(
            "dashboard is served at /app/",
            status == 200 and '<div id="root">' in body,
            f"status={status}",
        )
        status, body = fetch(web + "/app/pipelines/ingest_payments", args.timeout)
        check(
            "SPA deep link works (no 404)",
            status == 200 and '<div id="root">' in body,
            f"status={status}",
        )
        status, _ = fetch(web + "/api/v1/health", args.timeout)
        if status != 404:  # only applies when the frontend proxies /api (Docker/nginx setup)
            check("frontend reaches the API through /api", status == 200, f"status={status}")

    width = max(len(name) for name, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = sum(not ok for _, ok, _ in results)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

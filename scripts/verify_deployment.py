"""Verify a RideOps deployment (local Docker stack or hosted).

    python scripts/verify_deployment.py --api http://localhost:8001 --web http://localhost:8080
    python scripts/verify_deployment.py --api https://rideops-api.onrender.com --web https://rideops.pages.dev

Exits non-zero if any check fails. Uses only the standard library, so it runs anywhere.
A free Render service may be asleep: the first request can take ~1 minute (--timeout).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def fetch(url: str, timeout: float) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", required=True, help="backend base URL (without /api/v1)")
    parser.add_argument("--web", help="frontend base URL")
    parser.add_argument("--timeout", type=float, default=90)
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

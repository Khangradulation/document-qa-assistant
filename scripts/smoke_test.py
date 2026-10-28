"""Post-deployment smoke tests. Standard library only, no AWS credentials needed.

Usage:  python scripts/smoke_test.py outputs.json [--attempts 8] [--delay 15]
        (outputs.json is `terraform output -json`; optional env SMOKE_ID_TOKEN enables
        one authenticated check.)

What this proves after a deploy: the site is up and configured for the REAL backend,
the API refuses anonymous callers, CORS only allows our own frontend, and neither
bucket is publicly readable. It does NOT prove uploads, ingestion or answers work
(that needs a signed-in user and paid model calls: see the integration tests).
Exit code 1 if any check fails, so the deploy job goes red.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Fetch = Callable[[str, str, dict[str, str]], tuple[int, dict[str, str], bytes]]
EVIL_ORIGIN = "https://evil.example"


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


def real_fetch(method: str, url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, method=method, headers=headers)  # noqa: S310 (https URLs from our own outputs)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as err:  # 4xx/5xx are answers, not crashes
        return err.code, {k.lower(): v for k, v in err.headers.items()}, err.read()


def _bucket_url(bucket: str, region: str, path: str = "") -> str:
    return f"https://{bucket}.s3.{region}.amazonaws.com/{path}"


def run_checks(out: dict[str, Any], fetch: Fetch, token: str | None = None) -> list[Result]:
    api = out["api_url"].rstrip("/")
    site = out["frontend_url"].rstrip("/")
    results: list[Result] = []

    def check(name: str, fn: Callable[[], str | None]) -> None:
        """fn returns None on success or a failure message."""
        try:
            problem = fn()
        except Exception as exc:  # a network error is a failed check, not a crash
            problem = f"{type(exc).__name__}"
        results.append(Result(name, problem is None, problem or "ok"))

    def frontend_serves_app() -> str | None:
        status, _, body = fetch("GET", f"{site}/", {})
        if status != 200:
            return f"GET / returned {status}"
        return None if b'id="root"' in body else "index.html has no app root"

    def frontend_config_is_live() -> str | None:
        status, _, body = fetch("GET", f"{site}/config.json", {})
        if status != 200:
            return f"config.json returned {status}"
        cfg = json.loads(body)
        if cfg.get("demo"):
            return "config.json still has demo=true"
        want = {
            "apiUrl": api,
            "userPoolId": out["cognito_user_pool_id"],
            "clientId": out["cognito_client_id"],
        }
        wrong = [k for k, v in want.items() if str(cfg.get(k, "")).rstrip("/") != v]
        return f"config.json mismatch: {wrong}" if wrong else None

    def frontend_security_headers() -> str | None:
        _, headers, _ = fetch("GET", f"{site}/", {})
        missing = [
            h for h in ("strict-transport-security", "x-content-type-options") if h not in headers
        ]
        return f"missing headers: {missing}" if missing else None

    def api_requires_auth() -> str | None:
        for method, path in (("GET", "/documents"), ("POST", "/query")):
            status, _, _ = fetch(method, f"{api}{path}", {})
            if status != 401:
                return f"{method} {path} without a token returned {status} (expected 401)"
        return None

    def cors_only_for_frontend() -> str | None:
        def preflight(origin: str) -> tuple[int, dict[str, str]]:
            s, h, _ = fetch(
                "OPTIONS",
                f"{api}/documents",
                {"Origin": origin, "Access-Control-Request-Method": "GET"},
            )
            return s, h

        status, headers = preflight(site)
        if headers.get("access-control-allow-origin") != site:
            got = headers.get("access-control-allow-origin")
            return f"preflight for our origin returned {status}, allow-origin={got!r}"
        _, evil = preflight(EVIL_ORIGIN)
        allowed = evil.get("access-control-allow-origin")
        return f"CORS allows foreign origin: {allowed!r}" if allowed in (EVIL_ORIGIN, "*") else None

    def buckets_not_public() -> str | None:
        for label, key in (("docs", "docs_bucket"), ("frontend", "frontend_bucket")):
            for path in ("", "uploads/anything.pdf", "index.html"):
                status, _, _ = fetch("GET", _bucket_url(out[key], out["region"], path), {})
                if status != 403:
                    return (
                        f"{label} bucket answered {status} for anonymous GET /{path} (expected 403)"
                    )
        return None

    check("frontend serves the app", frontend_serves_app)
    check("frontend config targets the real backend", frontend_config_is_live)
    check("frontend security headers present", frontend_security_headers)
    check("API rejects anonymous requests", api_requires_auth)
    check("CORS allows only our frontend", cors_only_for_frontend)
    check("buckets are not publicly readable", buckets_not_public)

    if token:

        def authed_list() -> str | None:
            status, _, body = fetch("GET", f"{api}/documents", {"Authorization": f"Bearer {token}"})
            if status != 200:
                return f"authenticated GET /documents returned {status}"
            return None if "documents" in json.loads(body) else "response has no 'documents'"

        check("authenticated API call works", authed_list)
    return results


def run_with_retries(
    run: Callable[[], list[Result]],
    attempts: int,
    delay: float,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Result]:
    """CloudFront and API changes take a little while to propagate, so retry before failing."""
    results = run()
    for _ in range(attempts - 1):
        if all(r.ok for r in results):
            break
        sleep(delay)
        results = run()
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("outputs", help="path to `terraform output -json` file")
    parser.add_argument("--attempts", type=int, default=8)
    parser.add_argument("--delay", type=float, default=15)
    args = parser.parse_args()

    with open(args.outputs) as f:
        out = {k: v["value"] for k, v in json.load(f).items()}
    token = os.environ.get("SMOKE_ID_TOKEN") or None  # never printed
    results = run_with_retries(
        lambda: run_checks(out, real_fetch, token), args.attempts, args.delay
    )

    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.name}" + ("" if r.ok else f"  -> {r.detail}"))
    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} smoke checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

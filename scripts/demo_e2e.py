"""Live end-to-end check of a deployed stack, used by the demo workflow.

    python scripts/demo_e2e.py infra/outputs.json [--questions q04 q11 u01]

What it does (standard library + the AWS CLI that GitHub runners already have):
  1. creates a throw-away Cognito user (random password, masked, never printed),
  2. signs in with the AWS-credentialed admin flow (needs enable_test_auth=true),
  3. uploads data/greenfield_hydroponics_guide.pdf through the real presigned-URL path,
  4. waits for the real ingestion Lambda to mark it READY,
  5. asks questions from eval/questions.json and checks each answer the same way the eval does:
     expected facts in the answer, only expected pages cited, and unanswerable ones refused.
Exit code 1 if anything is wrong. Optionally (DEMO_USER_EMAIL + DEMO_USER_PASSWORD set) it also
creates a permanent demo user so a person can sign in to the demo UI.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess  # noqa: S404 (runs the AWS CLI with fixed arguments)
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "data" / "greenfield_hydroponics_guide.pdf"
QUESTIONS = ROOT / "eval" / "questions.json"
DEFAULT_QUESTIONS = ["q04", "q11", "u01"]  # pH range, LED height, and one that must be refused

# (method, url, headers, body) -> (status, parsed JSON or {})
Http = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, Any]]]
Aws = Callable[[list[str], dict[str, Any] | None], str]


def make_password() -> str:
    """Meets the pool policy (12+, upper, lower, digit, symbol) with plenty of randomness."""
    return "Aa1!" + secrets.token_urlsafe(18)


def evaluate_answer(q: dict[str, Any], resp: dict[str, Any]) -> list[str]:
    """Problems with one answer (empty list = fine). Mirrors the checks in eval/run_eval.py."""
    problems: list[str] = []
    cited = {c.get("page") for c in resp.get("citations", [])}
    if not q["answerable"]:
        if resp.get("answered"):
            problems.append("answered a question that has no answer in the document")
        if cited:
            problems.append(f"cited pages {sorted(cited)} for an unanswerable question")
        return problems
    if not resp.get("answered"):
        return ["refused a question the document answers"]
    answer = str(resp.get("answer", "")).lower()
    missing = [k for k in q["expected_keywords"] if k.lower() not in answer]
    if missing:
        problems.append(f"answer is missing {missing}: {resp.get('answer')!r}")
    if not cited or not cited <= set(q["expected_pages"]):
        problems.append(f"cited pages {sorted(map(str, cited))}, expected {q['expected_pages']}")
    return problems


def describe(body: dict[str, Any]) -> str:
    """Safe-to-log summary of an API response: only the error code/message, never the body
    (a successful upload-url response contains a signed URL)."""
    err = body.get("error")
    if isinstance(err, dict):
        return f"{err.get('code')}: {err.get('message')}"
    return "(no error detail)"


def real_http(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 (https URLs from our own outputs)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as err:
        raw, status = err.read(), err.code
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def real_aws(args: list[str], stdin_json: dict[str, Any] | None = None) -> str:
    """Run `aws <args>`. Secrets go in a private (0600) temp file that is deleted right away, so
    they never appear in a process list. (`--cli-input-json file:///dev/stdin` was rejected by the
    CLI on GitHub runners, so a real file is used.)"""
    cmd = ["aws", *args]
    path: str | None = None
    try:
        if stdin_json is not None:
            fd, path = tempfile.mkstemp(prefix="demo-e2e-", suffix=".json")  # mode 0600
            with os.fdopen(fd, "w") as fh:
                json.dump(stdin_json, fh)
            cmd += ["--cli-input-json", f"file://{path}"]
        done = subprocess.run(  # noqa: S603 (fixed argv, no shell)
            cmd, capture_output=True, text=True, check=False
        )
    finally:
        if path is not None:
            Path(path).unlink(missing_ok=True)
    if done.returncode != 0:
        # stderr from the CLI does not echo request bodies, but keep it short anyway.
        raise RuntimeError(f"aws {args[1]} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def create_user(aws: Aws, pool: str, email: str, password: str) -> None:
    """Admin-create a confirmed user with a permanent password. No email is sent."""
    aws(
        [
            "cognito-idp",
            "admin-create-user",
            "--user-pool-id",
            pool,
            "--username",
            email,
            "--user-attributes",
            f"Name=email,Value={email}",
            "Name=email_verified,Value=true",
            "--message-action",
            "SUPPRESS",
        ],
        None,
    )
    aws(
        ["cognito-idp", "admin-set-user-password"],
        {"UserPoolId": pool, "Username": email, "Password": password, "Permanent": True},
    )


def sign_in(aws: Aws, pool: str, client: str, email: str, password: str) -> str:
    out = aws(
        ["cognito-idp", "admin-initiate-auth"],
        {
            "UserPoolId": pool,
            "ClientId": client,
            "AuthFlow": "ADMIN_USER_PASSWORD_AUTH",
            "AuthParameters": {"USERNAME": email, "PASSWORD": password},
        },
    )
    token = json.loads(out)["AuthenticationResult"]["IdToken"]
    print(f"::add-mask::{token}")
    return str(token)


def call(
    http: Http,
    token: str,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    tries: int = 4,
) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    status, parsed = 0, {}
    for attempt in range(tries):
        status, parsed = http(method, url, headers, data)
        if status not in (429, 502, 503, 504):
            break
        time.sleep(min(20, 3 * 2**attempt))  # throttled or cold: back off and retry
    return status, parsed


def run(
    outputs: dict[str, Any],
    question_ids: list[str],
    aws: Aws = real_aws,
    http: Http = real_http,
    sleep: Callable[[float], None] = time.sleep,
    ingest_timeout: float = 360,
) -> int:
    api = outputs["api_url"]["value"].rstrip("/")
    pool = outputs["cognito_user_pool_id"]["value"]
    client = outputs["cognito_client_id"]["value"]

    password = make_password()
    print(f"::add-mask::{password}")
    email = f"smoke-{secrets.token_hex(4)}@example.com"
    no_login = outputs.get("demo_mode", {}).get("value") is True
    if no_login:
        token = ""  # demo_mode stack: the API takes no token, exactly like the public demo
        print("demo_mode stack: calling the API without signing in")
    else:
        create_user(aws, pool, email, password)
        token = sign_in(aws, pool, client, email, password)

    all_questions = {q["id"]: q for q in json.loads(QUESTIONS.read_text())}
    questions = [all_questions[i] for i in question_ids]

    status, target = call(http, token, "POST", f"{api}/documents/upload-url", {
        "filename": PDF.name, "content_type": "application/pdf",
    })  # fmt: skip
    if status not in (200, 201):  # the API answers 201 Created
        print(f"FAIL upload-url -> {status} {describe(target)}")
        return 1
    status, _ = http("PUT", target["upload_url"], dict(target["headers"]), PDF.read_bytes())
    if status not in (200, 201):
        print(f"FAIL presigned upload -> {status}")
        return 1
    doc_id = target["document_id"]
    print(f"uploaded {PDF.name} as {doc_id}")

    deadline = time.monotonic() + ingest_timeout
    state = "UPLOADED"
    while time.monotonic() < deadline:
        status, listing = call(http, token, "GET", f"{api}/documents")
        if status != 200:
            print(f"FAIL listing documents -> {status} {describe(listing)}")
            return 1
        mine = [d for d in listing.get("documents", []) if d.get("document_id") == doc_id]
        state = mine[0]["status"] if mine else "MISSING"
        if state == "READY":
            break
        if state == "FAILED":
            print(
                f"FAIL ingestion failed: {mine[0].get('error_code')} {mine[0].get('error_detail')}"
            )
            return 1
        sleep(5)
    if state != "READY":
        print(f"FAIL document still {state} after {ingest_timeout:.0f}s")
        return 1
    print(
        f"document READY ({mine[0].get('page_count')} pages, {mine[0].get('chunk_count')} chunks)"
    )

    failures = 0
    for q in questions:
        status, resp = call(http, token, "POST", f"{api}/query", {
            "document_id": doc_id, "question": q["question"],
        })  # fmt: skip
        problems = (
            [f"HTTP {status}: {describe(resp)}"] if status != 200 else evaluate_answer(q, resp)
        )
        verdict = "PASS" if not problems else "FAIL"
        print(f"{verdict} {q['id']}: {q['question']}")
        print(f"     answer: {resp.get('answer')!r}")
        print(f"     cited pages: {sorted({c.get('page') for c in resp.get('citations', [])})}")
        for p in problems:
            print(f"     problem: {p}")
        failures += bool(problems)
        sleep(7)  # stay under the 10 requests/minute Haiku quota

    demo_email, demo_password = (
        os.environ.get("DEMO_USER_EMAIL"),
        os.environ.get("DEMO_USER_PASSWORD"),
    )
    if demo_email and demo_password and not no_login:
        create_user(aws, pool, demo_email, demo_password)  # the password is a masked secret
        print(f"demo user created: {demo_email}")

    print(f"\n{len(questions) - failures}/{len(questions)} questions correct")
    return 1 if failures else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("outputs", type=Path, help="`terraform output -json` file")
    ap.add_argument("--questions", nargs="+", default=DEFAULT_QUESTIONS)
    args = ap.parse_args()
    sys.exit(run(json.loads(args.outputs.read_text()), args.questions))


if __name__ == "__main__":
    main()

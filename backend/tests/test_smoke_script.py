"""The smoke tests gate every deployment, so the gate itself is tested: it must PASS a
healthy deployment and FAIL each way a deployment can be wrong."""

import json

import pytest
from smoke_test import EVIL_ORIGIN, run_checks, run_with_retries

SITE = "https://d111.cloudfront.net"
API = "https://abc.execute-api.us-east-1.amazonaws.com"
OUT = {
    "api_url": API, "frontend_url": SITE, "region": "us-east-1",
    "docs_bucket": "docs-b", "frontend_bucket": "web-b",
    "cognito_user_pool_id": "us-east-1_X", "cognito_client_id": "client1",
}  # fmt: skip
GOOD_CONFIG = {"demo": False, "apiUrl": API, "userPoolId": "us-east-1_X", "clientId": "client1"}


class Deployment:
    """A fake internet. Override attributes to break one thing at a time."""

    def __init__(self, **over):
        self.config = GOOD_CONFIG
        self.anon_status = 401
        self.reflect_any_origin = False
        self.bucket_status = 403
        self.html = b'<div id="root"></div>'
        self.sec_headers = {
            "strict-transport-security": "max-age=1",
            "x-content-type-options": "nosniff",
        }
        self.authed_status = 200
        self.__dict__.update(over)

    def __call__(self, method, url, headers):
        if ".s3." in url:
            return self.bucket_status, {}, b"AccessDenied"
        if url.startswith(SITE):
            if url.endswith("/config.json"):
                return 200, {}, json.dumps(self.config).encode()
            return 200, self.sec_headers, self.html
        if method == "OPTIONS":
            origin = headers["Origin"]
            ok = origin == SITE or self.reflect_any_origin
            return 204, ({"access-control-allow-origin": origin} if ok else {}), b""
        if headers.get("Authorization"):
            return self.authed_status, {}, b'{"documents": []}'
        return self.anon_status, {}, b"{}"


def failures(**over):
    jwt = over.pop("jwt", None)
    return [r.name for r in run_checks(OUT, Deployment(**over), jwt) if not r.ok]


def test_healthy_deployment_passes_everything():
    assert failures() == []
    assert failures(jwt="t") == []


@pytest.mark.parametrize(
    ("override", "expected_failure"),
    [
        ({"config": {**GOOD_CONFIG, "demo": True}}, "frontend config targets the real backend"),
        (
            {"config": {**GOOD_CONFIG, "apiUrl": "https://other"}},
            "frontend config targets the real backend",
        ),
        ({"html": b"<html>oops</html>"}, "frontend serves the app"),
        ({"sec_headers": {}}, "frontend security headers present"),
        ({"anon_status": 200}, "API rejects anonymous requests"),
        ({"reflect_any_origin": True}, "CORS allows only our frontend"),
        ({"bucket_status": 200}, "buckets are not publicly readable"),
        ({"jwt": "t", "authed_status": 500}, "authenticated API call works"),
    ],
)
def test_each_kind_of_broken_deployment_is_caught(override, expected_failure):
    assert expected_failure in failures(**override)


def test_network_errors_are_failed_checks_not_crashes():
    def boom(*_):
        raise OSError("down")

    results = run_checks(OUT, boom)
    assert results and not any(r.ok for r in results)


def test_retries_wait_for_propagation_then_stop():
    sleeps, calls = [], []
    healthy, broken = run_checks(OUT, Deployment()), run_checks(OUT, Deployment(anon_status=200))

    def flaky():
        calls.append(1)
        return broken if len(calls) < 3 else healthy

    results = run_with_retries(flaky, attempts=5, delay=7, sleep=sleeps.append)
    assert all(r.ok for r in results) and len(calls) == 3 and sleeps == [7, 7]


def test_retries_give_up_and_report_failure():
    broken = run_checks(OUT, Deployment(anon_status=200))
    results = run_with_retries(lambda: broken, attempts=3, delay=0, sleep=lambda _: None)
    assert not all(r.ok for r in results)


def test_evil_origin_constant_is_not_ours():
    assert EVIL_ORIGIN != SITE

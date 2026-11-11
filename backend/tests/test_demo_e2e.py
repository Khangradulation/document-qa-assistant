"""scripts/demo_e2e.py with fake AWS CLI / HTTP: logic only, no network."""

from __future__ import annotations

import json
import re
from typing import Any

import demo_e2e
import pytest

OUTPUTS = {
    "api_url": {"value": "https://api.example/"},
    "cognito_user_pool_id": {"value": "us-east-2_pool"},
    "cognito_client_id": {"value": "client123"},
}
Q_ANSWERABLE = {
    "id": "q04", "answerable": True, "expected_pages": [2], "expected_keywords": ["5.8", "6.2"],
}  # fmt: skip
Q_REFUSE = {"id": "u01", "answerable": False, "expected_pages": [], "expected_keywords": []}


def good(page: int = 2, answer: str = "Keep pH between 5.8 and 6.2.") -> dict[str, Any]:
    return {"answered": True, "answer": answer, "citations": [{"page": page}]}


# ---- evaluate_answer ------------------------------------------------------------------
def test_correct_answer_has_no_problems() -> None:
    assert demo_e2e.evaluate_answer(Q_ANSWERABLE, good()) == []


def test_wrong_fact_wrong_page_and_missing_citation_are_caught() -> None:
    assert demo_e2e.evaluate_answer(Q_ANSWERABLE, good(answer="About pH 7")) != []
    assert demo_e2e.evaluate_answer(Q_ANSWERABLE, good(page=3)) != []
    no_cite = {"answered": True, "answer": "5.8 to 6.2", "citations": []}
    assert demo_e2e.evaluate_answer(Q_ANSWERABLE, no_cite) != []


def test_refusing_an_answerable_question_is_a_problem() -> None:
    assert demo_e2e.evaluate_answer(Q_ANSWERABLE, {"answered": False, "citations": []}) != []


def test_unanswerable_must_be_refused_without_citations() -> None:
    assert demo_e2e.evaluate_answer(Q_REFUSE, {"answered": False, "citations": []}) == []
    assert demo_e2e.evaluate_answer(Q_REFUSE, good()) != []


def test_password_meets_the_cognito_policy_and_is_random() -> None:
    p = demo_e2e.make_password()
    assert len(p) >= 12 and re.search(r"[a-z]", p) and re.search(r"[A-Z]", p)
    assert re.search(r"\d", p) and re.search(r"[^A-Za-z0-9]", p)
    assert p != demo_e2e.make_password()


# ---- run() with fakes -----------------------------------------------------------------
class World:
    def __init__(self, statuses: list[str], answers: dict[str, dict[str, Any]]) -> None:
        self.statuses = statuses
        self.answers = answers
        self.aws_calls: list[tuple[list[str], dict[str, Any] | None]] = []
        self.passwords_seen: list[str] = []

    def aws(self, args: list[str], stdin_json: dict[str, Any] | None = None) -> str:
        self.aws_calls.append((args, stdin_json))
        if stdin_json and "Password" in stdin_json:
            self.passwords_seen.append(stdin_json["Password"])
        if args[1] == "admin-initiate-auth":
            return json.dumps({"AuthenticationResult": {"IdToken": "tok.en.value"}})
        return ""

    def http(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, dict[str, Any]]:
        if url.endswith("/documents/upload-url"):
            assert headers["Authorization"] == "Bearer tok.en.value"
            return 201, {"document_id": "d1", "upload_url": "https://s3.example/put", "headers": {}}
        if method == "PUT":
            return 200, {}
        if url.endswith("/documents"):
            st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            doc = {"document_id": "d1", "status": st, "page_count": 4, "chunk_count": 4}
            return 200, {"documents": [doc]}
        if url.endswith("/query"):
            question = json.loads(body or b"{}")["question"]
            return 200, self.answers[question]
        raise AssertionError(url)


def questions() -> dict[str, dict[str, Any]]:
    import demo_e2e as d

    return {q["id"]: q for q in json.loads(d.QUESTIONS.read_text())}


def run_world(world: World, ids: list[str]) -> int:
    return demo_e2e.run(OUTPUTS, ids, aws=world.aws, http=world.http, sleep=lambda _s: None)


def answers_for(ids: list[str], correct: bool = True) -> dict[str, dict[str, Any]]:
    qs = questions()
    out: dict[str, dict[str, Any]] = {}
    for i in ids:
        q = qs[i]
        if not q["answerable"]:
            out[q["question"]] = {"answered": False, "answer": "not found", "citations": []}
        else:
            ans = " ".join(q["expected_keywords"]) if correct else "I think it is blue"
            out[q["question"]] = {
                "answered": True, "answer": ans,
                "citations": [{"page": q["expected_pages"][0]}],
            }  # fmt: skip
    return out


def test_happy_path_passes_and_waits_for_ready() -> None:
    ids = ["q04", "q11", "u01"]
    world = World(["UPLOADED", "PROCESSING", "READY"], answers_for(ids))
    assert run_world(world, ids) == 0


def test_one_wrong_answer_fails_the_run() -> None:
    ids = ["q04", "q11", "u01"]
    answers = answers_for(ids)
    answers[questions()["q11"]["question"]] = good(page=3, answer="Place them somewhere")
    assert run_world(World(["READY"], answers), ids) == 1


def test_failed_ingestion_fails_the_run() -> None:
    world = World(["PROCESSING", "FAILED"], {})
    assert run_world(world, ["q04"]) == 1


def test_stuck_ingestion_times_out() -> None:
    world = World(["PROCESSING"], {})
    code = demo_e2e.run(
        OUTPUTS, ["q04"], aws=world.aws, http=world.http, sleep=lambda _s: None, ingest_timeout=0
    )
    assert code == 1


def test_password_is_masked_before_use_and_never_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ids = ["q04"]
    world = World(["READY"], answers_for(ids))
    run_world(world, ids)
    out = capsys.readouterr().out
    assert world.passwords_seen, "expected the script to set a password"
    for pw in world.passwords_seen:
        assert f"::add-mask::{pw}" in out  # masked ...
        assert out.replace(f"::add-mask::{pw}", "").count(pw) == 0  # ... and printed nowhere else
    assert "::add-mask::tok.en.value" in out


def test_demo_user_is_created_only_when_both_env_vars_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = ["q04"]
    monkeypatch.delenv("DEMO_USER_EMAIL", raising=False)
    monkeypatch.delenv("DEMO_USER_PASSWORD", raising=False)
    w1 = World(["READY"], answers_for(ids))
    run_world(w1, ids)
    created_without = [c for c in w1.aws_calls if c[0][1] == "admin-create-user"]
    monkeypatch.setenv("DEMO_USER_EMAIL", "me@example.com")
    monkeypatch.setenv("DEMO_USER_PASSWORD", "Sup3r-secret-pw!")
    w2 = World(["READY"], answers_for(ids))
    run_world(w2, ids)
    created_with = [c for c in w2.aws_calls if c[0][1] == "admin-create-user"]
    assert len(created_without) == 1 and len(created_with) == 2


# ---- real_aws against a stub `aws` executable (the layer the fake-based tests skip) ----------
def test_real_aws_passes_secrets_via_a_private_temp_file_that_is_removed(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    log = tmp_path / "seen.json"
    stub = tmp_path / "bin" / "aws"
    stub.parent.mkdir()
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, stat, sys\n"
        "i = sys.argv.index('--cli-input-json')\n"
        "path = sys.argv[i + 1].removeprefix('file://')\n"
        "mode = stat.S_IMODE(os.stat(path).st_mode)\n"
        f"out = open({str(log)!r}, 'w')\n"
        "json.dump({'argv': sys.argv[1:], 'body': json.load(open(path)),\n"
        "           'mode': mode, 'path': path}, out)\n"
        "out.close()\n"
        "print('ok')\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{stub.parent}{os.pathsep}{os.environ['PATH']}")

    out = demo_e2e.real_aws(["cognito-idp", "admin-set-user-password"], {"Password": "s3cret!"})

    seen = json.loads(log.read_text())
    assert out.strip() == "ok" and seen["body"] == {"Password": "s3cret!"}
    assert seen["mode"] == 0o600  # readable by the owner only
    assert "s3cret!" not in " ".join(seen["argv"])  # never on the command line
    assert not os.path.exists(seen["path"])  # deleted after the call


def test_real_aws_raises_with_the_cli_error_text(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat

    stub = tmp_path / "bin" / "aws"
    stub.parent.mkdir()
    stub.write_text("#!/bin/sh\necho 'boom' >&2\nexit 3\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{stub.parent}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(RuntimeError, match="boom"):
        demo_e2e.real_aws(["cognito-idp", "admin-create-user"], None)


def test_failures_never_log_response_bodies_or_signed_urls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    signed_url = "https://bucket.s3.amazonaws.com/x.pdf?X-Amz-Signature=deadbeef"

    class Failing(World):
        def http(
            self, method: str, url: str, headers: dict[str, str], body: bytes | None
        ) -> tuple[int, dict[str, Any]]:
            if url.endswith("/documents/upload-url"):
                return 500, {"upload_url": signed_url, "error": {"code": "BOOM", "message": "bad"}}
            return super().http(method, url, headers, body)

    world = Failing(["READY"], {})
    assert run_world(world, ["q04"]) == 1
    out = capsys.readouterr().out
    assert "BOOM: bad" in out and "deadbeef" not in out and signed_url not in out


def test_listing_error_fails_fast_instead_of_waiting_for_the_timeout() -> None:
    class NoListing(World):
        def http(
            self, method: str, url: str, headers: dict[str, str], body: bytes | None
        ) -> tuple[int, dict[str, Any]]:
            if url.endswith("/documents") and method == "GET":
                return 401, {"error": {"code": "UNAUTHORIZED", "message": "no"}}
            return super().http(method, url, headers, body)

    assert run_world(NoListing(["READY"], {}), ["q04"]) == 1

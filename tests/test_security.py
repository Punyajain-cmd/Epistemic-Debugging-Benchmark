"""Regression tests for the 2026-10 security review (C1-C2, H1-H4, M1-M3, low)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from epidebug.engine.engine import EpistemicDebuggingEngine
from epidebug.llm import _to_provider_messages
from epidebug.schema import TestCase
from web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cases():
    return TestCase.load_all(ROOT / "test_cases")


@pytest.fixture
def app(tmp_path, cases, monkeypatch):
    monkeypatch.setenv("EPIDEBUG_RATE_PER_MIN", "1000")
    monkeypatch.setenv("EPIDEBUG_RATE_PER_HOUR", "10000")
    return create_app(upload_dir=tmp_path, prefer_llm=False, cases=cases)


def _new_chat(client):
    res = client.post("/api/chat", json={"message": "Battery cell overheated during charge at 2C"})
    assert res.status_code == 200
    return res.json()


def test_session_list_removed(app):
    c = TestClient(app)
    _new_chat(c)
    assert c.get("/api/sessions").status_code == 404


def test_cross_session_access_denied(app):
    owner, attacker = TestClient(app), TestClient(app)
    body = _new_chat(owner)
    sid = body["session_id"]
    assert owner.get(f"/api/sessions/{sid}").status_code == 200
    assert attacker.get(f"/api/sessions/{sid}").status_code == 404
    assert attacker.post(f"/api/sessions/{sid}/chat", json={"message": "x"}).status_code == 404
    assert attacker.post("/api/chat", json={"message": "x", "session_id": sid}).status_code == 404
    assert attacker.post(f"/api/sessions/{sid}/add-info", json={"information": "x"}).status_code == 404
    wrong = attacker.get(f"/api/sessions/{sid}", headers={"x-session-token": "nope"})
    assert wrong.status_code == 404
    ok = attacker.get(f"/api/sessions/{sid}", headers={"x-session-token": body["session_token"]})
    assert ok.status_code == 200


def test_session_id_format_validated(app):
    c = TestClient(app)
    assert c.get("/api/sessions/..%2F..%2Fetc").status_code == 404
    assert c.get("/api/sessions/ZZZ").status_code == 404


def test_ground_truth_not_public(app):
    c = TestClient(app)
    body = c.get("/api/cases/RF-001?include_ground_truth=true").json()
    assert "ground_truth" not in body


def test_tool_errors_generic(app):
    c = TestClient(app)
    from epidebug.tools import TOOL_REGISTRY

    name = next(iter(TOOL_REGISTRY))
    res = c.post(f"/api/tools/{name}", json={"arguments": {"__bogus__": 1}})
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid tool arguments"



def _bundle_with_png(c, extra=None):
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    return c.post("/api/diagnose-bundle", data={"unexpected_outcome": "cell swelled", **(extra or {})},
                  files=[("files", ("shot.png", buf.getvalue(), "image/png"))])


def test_files_require_owning_session(app):
    owner = TestClient(app)
    body = _bundle_with_png(owner).json()
    art = body["experiment"]["artifacts"][0]["id"]
    assert owner.get(f"/api/files/{art}").status_code == 200
    stranger = TestClient(app)
    assert stranger.get(f"/api/files/{art}").status_code == 404
    other = _new_chat(stranger)
    assert stranger.get(f"/api/files/{art}", headers={"x-session-id": other["session_id"],
                        "x-session-token": other["session_token"]}).status_code == 404
    hdr = TestClient(app)
    assert hdr.get(f"/api/files/{art}", headers={"x-session-id": body["session_id"],
                   "x-session-token": body["session_token"]}).status_code == 200


def test_bundle_replace_keeps_owner_token(app):
    c = TestClient(app)
    first = _bundle_with_png(c).json()
    sid, tok = first["session_id"], first["session_token"]
    tab2 = TestClient(app)
    res = _bundle_with_png(c, {"session_id": sid})
    assert res.status_code == 200 and "session_token" not in res.json()
    assert tab2.get(f"/api/sessions/{sid}", headers={"x-session-token": tok}).status_code == 200


def test_token_server_side_expiry(tmp_path, monkeypatch):
    import time as _t

    from web.security import TokenStore

    store = TokenStore(tmp_path)
    tok = store.issue("abcdef012345")
    assert store.check("abcdef012345", tok)
    monkeypatch.setenv("EPIDEBUG_SESSION_TTL_S", "60")
    real = _t.time
    monkeypatch.setattr("web.security.time.time", lambda: real() + 120)
    assert not store.check("abcdef012345", tok)

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


def test_traversal_filename_neutralized(app, tmp_path):
    c = TestClient(app)
    files = {"files": ("../../sessions/evil.json", b"time,temp\n0,1\n", "text/csv")}
    res = c.post("/api/diagnose-bundle", data={"unexpected_outcome": "overheat"}, files=files)
    assert res.status_code == 200, res.text
    assert not (tmp_path / "sessions" / "evil.json").exists()
    stored = [p for p in tmp_path.rglob("*") if p.is_file() and p.parent.name not in {"sessions", "tokens"}]
    assert stored and all(p.name == "blob" for p in stored)
    names = [a["filename"] for a in res.json()["view"]["artifacts"]]
    assert all("/" not in n and ".." not in n.split("/")[0][:2] for n in names)


def test_html_svg_uploads_rejected(app):
    c = TestClient(app)
    for name, data in (("x.html", b"<html><script>alert(1)</script>"),
                       ("x.svg", b"<svg onload=alert(1)>"),
                       ("x.txt", b"<!doctype html><script>1</script>"),
                       ("x.png", b"not a png")):
        res = c.post("/api/diagnose-bundle", data={"unexpected_outcome": "x"},
                     files={"files": (name, data, "text/plain")})
        assert res.status_code == 415, name


def test_file_served_as_attachment(app):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buf, format="PNG")
    c = TestClient(app)
    res = c.post("/api/diagnose-bundle", data={"unexpected_outcome": "crack"},
                 files={"files": ("p.png", buf.getvalue(), "image/png")})
    url = res.json()["view"]["artifacts"][0].get("preview_url") or ""
    art = res.json()["experiment"]["artifacts"][0]["id"]
    f = c.get(f"/api/files/{art}")
    assert f.status_code == 200
    assert f.headers["content-type"] == "application/octet-stream"
    assert f.headers["content-disposition"].startswith("attachment")
    assert f.headers["x-content-type-options"] == "nosniff"
    assert url == "" or url.endswith(art)


def test_too_many_files(app):
    c = TestClient(app)
    files = [("files", (f"f{i}.csv", b"a,b\n1,2\n", "text/csv")) for i in range(13)]
    res = c.post("/api/diagnose-bundle", data={"unexpected_outcome": "x"}, files=files)
    assert res.status_code == 413


def test_security_headers_and_cors(app):
    c = TestClient(app)
    res = c.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in res.headers
    csp = res.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"]
    pre = c.options("/api/chat", headers={"Origin": "https://evil.example",
                                          "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in pre.headers


def test_rate_limit(tmp_path, cases, monkeypatch):
    monkeypatch.setenv("EPIDEBUG_RATE_PER_MIN", "2")
    app = create_app(upload_dir=tmp_path, prefer_llm=False, cases=cases)
    c = TestClient(app)
    codes = [c.post("/api/chat", json={"message": "pump cavitation noise"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_message_length_capped(app):
    c = TestClient(app)
    assert c.post("/api/chat", json={"message": "a" * 5000}).status_code == 413


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


def test_pil_pixel_cap():
    from PIL import Image

    import epidebug.engine.ingest  # noqa: F401

    assert Image.MAX_IMAGE_PIXELS == 40_000_000



def _slow(x):
    import time as _t

    _t.sleep(x)
    return x


def test_parse_timeout_kills_child():
    import time as _t

    from web.security import ParseTimeout, run_isolated

    assert run_isolated(_slow, 0, timeout=5) == 0
    t0 = _t.time()
    with pytest.raises(ParseTimeout):
        run_isolated(_slow, 30, timeout=0.5)
    assert _t.time() - t0 < 5


@pytest.mark.parametrize("name,data", [
    ("a.txt", b"\xef\xbb\xbf<html><body>x"),
    ("a.txt", b"<!-- c -->\n  <SvG onload=x>"),
    ("a.txt", b"log line\n" * 50 + b"<script>alert(1)</script>"),
    ("data.xml", b"<?xml version='1.0'?><root><svg/></root>"),
    ("data.xml", b"<root xmlns:h='http://www.w3.org/1999/xhtml'><h:HTML/></root>"),
    ("evil.html.", b"hello"),
    ("noext", b"   <!DOCTYPE HTML><p>"),
    ("u16.txt", "\ufeff<html>".encode("utf-16")),
])
def test_sniff_rejects_markup(name, data):
    from web.security import safe_display_name, validate_upload

    assert validate_upload(safe_display_name(name), data) is not None


def test_sniff_allows_plain_data():
    from web.security import validate_upload

    assert validate_upload("run.csv", b"t,od\n0,0.1\n") is None
    assert validate_upload("data.xml", b"<?xml version='1.0'?><plate><well id='A1'/></plate>") is None


def test_pdf_page_cap():
    from epidebug.engine import ingest

    data = b"%PDF-1.4\n" + b"<< /Type /Page >>\n" * (ingest.MAX_PDF_PAGES + 1)
    _, stats, summary = ingest._extract_pdf(data)
    assert stats.get("rejected") == "page_cap" and "not parsed" in summary


def test_run_isolated_falls_back_inline_without_main_file(monkeypatch):
    """forkserver cannot start a child when __main__ points at a missing file
    (or fn lives in __main__); run_isolated must fall back to inline, not crash."""
    import sys
    import types

    from web import security

    fake_main = types.ModuleType("__main__")
    fake_main.__file__ = "/nonexistent/launcher.py"
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    assert security._isolation_possible(_slow) is False
    assert security.run_isolated(_slow, 0, timeout=5) == 0  # ran inline

    def in_main(x):
        return x + 1
    in_main.__module__ = "__main__"
    monkeypatch.delitem(sys.modules, "__main__")
    assert security._isolation_possible(in_main) is False
    assert security.run_isolated(in_main, 1) == 2

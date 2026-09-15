"""Contract tests for the researcher-facing API.

These lock the JSON the frontend (including a parallel UI overhaul) can rely on:
raw session fields stay in place, and an additive ``view`` object is always present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from epidebug.schema import TestCase
from web.app import create_app


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cases():
    return TestCase.load_all(ROOT / "test_cases")


@pytest.fixture
def client(tmp_path, cases):
    app = create_app(upload_dir=tmp_path, prefer_llm=False, cases=cases)
    return TestClient(app)


def test_health_contract(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["cases"] >= 30
    assert body["contract_version"] == "0.6"
    assert "dump_demo" in body["fixtures"]
    assert "view" in body["session_payload"]
    assert "messages" in body["session_payload"]
    assert "messages" in body["view_fields"]
    assert "chat" in body["view_fields"]
    assert any("chat" in item for item in body["hitl"])
    assert body["chat"]["turn"] == "POST /api/sessions/{id}/chat"
    assert "ingest" in body["view_fields"]
    assert "gallery" in body["view_fields"]
    assert body["evidence_slots"]
    assert body["max_file_bytes"] == 25 * 1024 * 1024
    assert body["engine_mode"] in {"heuristic", "llm"}
    assert body["platform"] in {"local", "vercel"}
    assert "ephemeral_storage" in body


def test_index_and_static_assets(client):
    page = client.get("/")
    assert page.status_code == 200
    html = page.content
    assert b"EpiDebug" in html
    assert b"Competing causes are waiting" in html
    assert b"busy-pipeline" in html
    assert b"chat-panel" in html
    assert b"chatInput" in html
    assert b"chatCollapse" in html
    assert b"is-compact" in html
    assert b"Collapse" in html
    assert b"ico-expand" in html
    assert b"ico-sensors" in html
    css = client.get("/assets/styles.css")
    assert css.status_code == 200
    assert "text/css" in css.headers.get("content-type", "")
    assert b"busy-pipeline" in css.content
    assert b"gallery-mosaic" in css.content
    assert b"min-height: 148px" in css.content
    assert b"height: 36px" in css.content
    assert b"chat-log.is-idle" in css.content
    assert b"minmax(11rem" not in css.content
    js = client.get("/assets/app.js")
    assert js.status_code == 200
    assert b"GALLERY_GROUPS" in js.content
    assert b"startBusyStages" in js.content
    assert b"sendChat" in js.content
    assert b"/api/sessions/" in js.content
    assert b"/api/chat" in js.content
    assert b"expandComposer" in js.content
    assert b"has-thread" in js.content
    assert b"chat-idle-hint" in js.content
    assert b"is-idle" in js.content


def test_case_list_has_picker_fields(client):
    res = client.get("/api/cases")
    assert res.status_code == 200
    rows = res.json()
    sample = next(row for row in rows if row["id"] == "RF-001")
    assert sample["title"]
    assert sample["failure_category_label"]
    assert sample["objective_preview"]
    assert sample["information_regime"]


def test_diagnose_catalog_case_includes_view(client):
    res = client.post("/api/diagnose", json={"case_id": "RF-001"})
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"]
    assert body["diagnosis"]["hypotheses"]
    view = body["view"]
    assert view["session_id"] == body["session_id"]
    assert view["lead"]["cause"]
    assert view["hypotheses"]
    assert "posterior_pct" in view["hypotheses"][0]
    assert view["contract_version"] == "0.6"
    assert view["ingest"]["source"] == "catalog"
    assert view["gallery"]["images"] == []
    listed = client.get("/api/sessions").json()
    assert any(item["session_id"] == body["session_id"] for item in listed)


def test_diagnose_bundle_and_hitl(client):
    files = {"files": ("cell_temp.csv", b"time,temp_C\n0,25\n1,80\n2,110\n", "text/csv")}
    data = {
        "title": "Overheating cell",
        "domain": "batteries / energy",
        "unexpected_outcome": "Can got hot and capacity collapsed on 2C charge",
        "materials": "LiPF6 electrolyte from an opened bottle",
        "processing": "Filled in air then crimped",
        "roles": '["sensor"]',
        "captions": '["cell thermistor"]',
    }
    res = client.post("/api/diagnose-bundle", data=data, files=files)
    assert res.status_code == 200
    body = res.json()
    assert body["experiment"]["artifacts"][0]["filename"] == "cell_temp.csv"
    assert body["view"]["file_count"] == 1
    assert body["view"]["artifacts"][0]["summary"]
    assert body["view"]["ingest"]["summaries"][0]["filename"] == "cell_temp.csv"
    assert body["diagnosis"]["hypotheses"]

    session_id = body["session_id"]
    hyp_id = body["diagnosis"]["hypotheses"][0]["id"]
    rejected = client.post(
        f"/api/sessions/{session_id}/reject",
        json={"hypothesis_id": hyp_id, "reason": "Not this one"},
    )
    assert rejected.status_code == 200
    statuses = {h["id"]: h["status"] for h in rejected.json()["diagnosis"]["hypotheses"]}
    assert statuses[hyp_id] == "rejected"

    extra = client.post(
        f"/api/sessions/{session_id}/add-info",
        json={"information": "Karl Fischer water in electrolyte was 480 ppm"},
    )
    assert extra.status_code == 200
    assert "480 ppm" in extra.json()["view"]["history"][-1]["information"]

    more = client.post(
        f"/api/sessions/{session_id}/artifacts",
        data={"roles": '["log"]', "captions": '["charger console"]'},
        files={"files": ("charger.log", b"ERROR overheat pack_main\n", "text/plain")},
    )
    assert more.status_code == 200
    more_body = more.json()
    names = [a["filename"] for a in more_body["view"]["artifacts"]]
    assert "cell_temp.csv" in names
    assert "charger.log" in names
    assert more_body["view"]["gallery"]["logs"]
    assert more_body["view"]["ingest"]["kind_counts"]["log"] == 1
    hitl = more_body["view"]
    assert hitl["hypotheses"]
    assert {h["id"] for h in hitl["hypotheses"]}


def test_empty_bundle_rejected(client):
    res = client.post("/api/diagnose-bundle", data={"title": "Blank"})
    assert res.status_code == 400


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01"
    b"\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_multifile_dump_exposes_gallery_and_coverage(client):
    files = [
        ("files", ("pack_temp.csv", b"time_s,temp_C\n0,25\n1,70\n2,94\n", "text/csv")),
        ("files", ("charger.log", b"2024-08-12 ERROR overheat pack_main\n", "text/plain")),
        (
            "files",
            (
                "housing.step",
                b"ISO-10303-21;\nHEADER;\n#1=PRODUCT('6082-T6-housing','part','');\n",
                "application/step",
            ),
        ),
        ("files", ("setup_vise.png", PNG, "image/png")),
        ("files", ("bore_finish.nc", b"T4 M6\nS4200 M3\nG1 F180\nM30\n", "text/plain")),
    ]
    data = {
        "title": "Housing bore drift / pack venting",
        "domain": "machining",
        "unexpected_outcome": "Bore 0.18 mm undersize; three cells vented on 2C charge",
        "objective": "Machine 6082-T6 housings and assemble a 6S2P pack",
        "materials": "6082-T6 lot 24-081\nOpened LiPF6 bottle",
        "processing": "Finish 0.08 mm/rev; 2C CC-CV",
        "protocol": "1. Bore housing\n2. Assemble pack\n3. Charge",
        "setup_description": "Kurt vise, REV C STEP, 21 C / 62% RH",
        "telemetry": "Bore 11.82 mm vs 12.00",
        "logs": "ERROR overheat pack_main",
        "context": "New night-shift operator",
        "roles": '["sensor","log","cad","setup_photo","process_doc"]',
        "captions": '["thermistor","charger","REV C","vise","finish program"]',
    }
    res = client.post("/api/diagnose-bundle", data=data, files=files)
    assert res.status_code == 200
    view = res.json()["view"]
    assert view["contract_version"] == "0.6"
    assert view["file_count"] == 5
    assert view["ingest"]["file_count"] == 5
    assert view["ingest"]["completeness"] == 1.0
    assert view["ingest"]["missing"] == []
    labels = {slot["id"]: slot["present"] for slot in view["ingest"]["coverage"]}
    assert labels["cad"] and labels["sensors"] and labels["logs"] and labels["photos"]
    assert view["gallery"]["tables"]
    assert view["gallery"]["logs"]
    assert view["gallery"]["cad"]
    assert view["gallery"]["images"]
    summaries = {row["filename"]: row["summary"] for row in view["ingest"]["summaries"]}
    assert "pack_temp.csv" in summaries and summaries["pack_temp.csv"]
    cards = {a["filename"]: a for a in view["artifacts"]}
    assert cards["pack_temp.csv"]["stats"]["rows"] == 3
    assert cards["charger.log"]["flags"]
    assert cards["housing.step"]["extracted_preview"]
    assert cards["setup_vise.png"]["preview_url"]
    assert res.json()["diagnosis"]["hypotheses"]

    session_id = res.json()["session_id"]
    follow = client.post(
        f"/api/sessions/{session_id}/followup",
        json={"intervention": "Karl Fischer the electrolyte", "outcome": "480 ppm water"},
    )
    assert follow.status_code == 200
    assert follow.json()["view"]["file_count"] == 5
    assert follow.json()["view"]["followups"]


def test_dump_demo_fixtures_via_diagnose_bundle(client):
    demo = ROOT / "mock_data" / "dump_demo"
    assert demo.is_dir()
    files = [
        ("files", ("pack_temp.csv", (demo / "pack_temp.csv").read_bytes(), "text/csv")),
        ("files", ("charger.log", (demo / "charger.log").read_bytes(), "text/plain")),
        ("files", ("housing_revC.step", (demo / "housing_revC.step").read_bytes(), "application/step")),
        ("files", ("bore_finish.nc", (demo / "bore_finish.nc").read_bytes(), "text/plain")),
        ("files", ("setup_vise.png", (demo / "setup_vise.png").read_bytes(), "image/png")),
        ("files", ("vented_cells_result.png", (demo / "vented_cells_result.png").read_bytes(), "image/png")),
        ("files", ("mill_cert_lot24081.txt", (demo / "mill_cert_lot24081.txt").read_bytes(), "text/plain")),
    ]
    data = {
        "title": "Housing bore drift / pack venting",
        "domain": "machining",
        "unexpected_outcome": "Bore undersize; cells vented on 2C charge",
        "objective": "Machine housings and assemble a pack",
        "materials": "6082-T6 lot 24-081",
        "processing": "Finish 0.08 mm/rev; 2C charge",
        "setup_description": "Kurt vise, REV C STEP",
        "context": "Night shift",
        "roles": '["sensor","log","cad","process_doc","setup_photo","result_image","material_doc"]',
        "captions": '["thermistor","charger","fixture","gcode","vise","vent","cert"]',
    }
    res = client.post("/api/diagnose-bundle", data=data, files=files)
    assert res.status_code == 200
    view = res.json()["view"]
    assert view["contract_version"] == "0.6"
    assert view["file_count"] == 7
    assert view["ingest"]["core_missing"] == []
    kinds = {h["kind"] for h in view["ingest"]["anomaly_highlights"]}
    assert kinds & {"trend", "step", "outlier", "endpoint"}
    assert "log" in kinds
    csv_stats = next(a["stats"] for a in view["artifacts"] if a["filename"] == "pack_temp.csv")
    assert csv_stats["anomalies"]
    log_stats = next(a["stats"] for a in view["artifacts"] if a["filename"] == "charger.log")
    assert log_stats["error_samples"]["unique"]


def test_full_dump_mix_svg_stl_stub_markdown(client):
    """Coordinator full_dump_demo shape: csv + log + md + svg + STL stub."""
    files = [
        ("files", ("pack_temp.csv", b"time_s,temp_C\n0,25.1\n1,48.0\n2,94.0\n", "text/csv")),
        ("files", ("charger.log", b"2024-08-12 ERROR overheat pack_main\nALARM-401\n", "text/plain")),
        (
            "files",
            (
                "NOTES.md",
                b"# Traveler SOP\n\nFinish bore per protocol. Lot 24-081.\n",
                "text/markdown",
            ),
        ),
        (
            "files",
            (
                "setup_layout.svg",
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>Vise</title></svg>',
                "image/svg+xml",
            ),
        ),
        ("files", ("housing_stub.stl", b"solid stub\nendsolid stub\n", "model/stl")),
    ]
    data = {
        "title": "Full dump mix",
        "domain": "machining",
        "unexpected_outcome": "Bore undersize and pack vented",
        "objective": "Machine housing and charge pack",
        "roles": '["sensor","log","process_doc","setup_photo","cad"]',
    }
    res = client.post("/api/diagnose-bundle", data=data, files=files)
    assert res.status_code == 200
    view = res.json()["view"]
    assert view["contract_version"] == "0.6"
    assert view["file_count"] == 5
    cards = {a["filename"]: a for a in view["artifacts"]}
    assert cards["pack_temp.csv"]["kind"] == "sensor"
    assert cards["charger.log"]["kind"] == "log"
    assert cards["NOTES.md"]["kind"] == "document"
    assert "Traveler SOP" in (cards["NOTES.md"]["summary"] or "")
    assert cards["setup_layout.svg"]["kind"] == "image"
    assert cards["setup_layout.svg"]["mime_type"] == "image/svg+xml"
    assert cards["housing_stub.stl"]["kind"] == "cad"
    assert cards["housing_stub.stl"]["stats"].get("stub") is True
    assert view["gallery"]["images"]
    assert view["gallery"]["cad"]
    assert view["gallery"]["tables"]
    assert view["gallery"]["logs"]


@pytest.mark.skipif(
    not (ROOT / "mock_data" / "full_dump_demo").is_dir(),
    reason="coordinator local fixtures at mock_data/full_dump_demo/",
)
def test_full_dump_demo_fixtures_via_diagnose_bundle(client):
    demo = ROOT / "mock_data" / "full_dump_demo"
    files = [
        ("files", (path.name, path.read_bytes(), None))
        for path in sorted(demo.iterdir())
        if path.is_file() and not path.name.startswith(".")
    ]
    assert files
    res = client.post(
        "/api/diagnose-bundle",
        data={
            "title": "Coordinator full dump demo",
            "domain": "machining",
            "unexpected_outcome": "Multi-file dump smoke",
        },
        files=files,
    )
    assert res.status_code == 200
    view = res.json()["view"]
    assert view["contract_version"] == "0.6"
    assert view["file_count"] == len(files)
    assert not any(a.get("summary") is None for a in view["artifacts"])
    kinds = {a["kind"] for a in view["artifacts"]}
    names = {a["filename"].lower() for a in view["artifacts"]}
    if any(n.endswith(".csv") for n in names):
        assert "sensor" in kinds
    if any(n.endswith(".log") for n in names):
        assert "log" in kinds
    if any(n.endswith(".svg") for n in names):
        assert "image" in kinds
    if any(n.endswith(".stl") for n in names):
        assert "cad" in kinds
    if any(n.endswith(".md") for n in names):
        assert "document" in kinds


def test_oversized_file_rejected(client):
    huge = b"x" * (25 * 1024 * 1024 + 8)
    res = client.post(
        "/api/diagnose-bundle",
        data={"title": "Too big", "unexpected_outcome": "n/a"},
        files={"files": ("huge.csv", huge, "text/csv")},
    )
    assert res.status_code == 413

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
    assert body["contract_version"] == "0.4"
    assert "view" in body["session_payload"]
    assert body["engine_mode"] in {"heuristic", "llm"}


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
    names = [a["filename"] for a in more.json()["view"]["artifacts"]]
    assert "cell_temp.csv" in names
    assert "charger.log" in names


def test_empty_bundle_rejected(client):
    res = client.post("/api/diagnose-bundle", data={"title": "Blank"})
    assert res.status_code == 400

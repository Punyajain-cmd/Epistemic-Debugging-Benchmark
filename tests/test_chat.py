"""Contract tests for the conversational diagnostic loop."""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from epidebug.engine.session import SessionStore
from epidebug.schema import TestCase
from web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cases():
    return TestCase.load_all(ROOT / "test_cases")


@pytest.fixture
def client(tmp_path, cases):
    app = create_app(upload_dir=tmp_path, prefer_llm=False, cases=cases)
    return TestClient(app), tmp_path


def test_chat_create_persists_transcript(client):
    http, upload_dir = client
    res = http.post(
        "/api/chat",
        json={
            "message": (
                "The CNC bore is 0.18 mm undersize and the last three cells "
                "vented on 2C charge."
            )
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"]
    assert body["view"]["messages"]
    roles = [m["role"] for m in body["messages"]]
    assert "user" in roles
    assert "assistant" in roles
    user_text = next(m["content"] for m in body["messages"] if m["role"] == "user")
    assert "undersize" in user_text
    assistant = next(m for m in reversed(body["messages"]) if m["role"] == "assistant")
    assert assistant["content"]
    # Heuristic without keys should request missing evidence, not pretend it scraped the web.
    blob = assistant["content"].lower()
    assert "internet" not in blob
    cues = ("missing", "sensor", "log", "photo", "cad", "material", "still")
    assert any(word in blob for word in cues)

    session_id = body["session_id"]
    stored = SessionStore(upload_dir / "sessions").get(session_id)
    assert len(stored.messages) >= 2
    assert stored.messages[0].role == "user"


def test_chat_multi_turn_and_force_diagnose(client):
    http, upload_dir = client
    first = http.post(
        "/api/chat",
        json={"message": "Yield collapsed on Ni-NTA; target in the flow-through."},
    ).json()
    session_id = first["session_id"]
    n1 = len(first["messages"])

    second = http.post(
        f"/api/sessions/{session_id}/chat",
        json={
            "message": (
                "We used 3-week-old Tris buffer stored on the bench next "
                "to a CO2 incubator."
            )
        },
    )
    assert second.status_code == 200
    body = second.json()
    assert body["session_id"] == session_id
    assert len(body["messages"]) > n1
    users = [m for m in body["messages"] if m["role"] == "user"]
    assert len(users) == 2

    forced = http.post(
        f"/api/sessions/{session_id}/chat",
        json={"message": "", "force_diagnose": True},
    )
    assert forced.status_code == 200
    forced_body = forced.json()
    assert forced_body["diagnosis"]["hypotheses"]
    assert forced_body["view"]["lead"]["cause"]
    assert any(m["role"] == "system-tool" for m in forced_body["messages"])
    assert any(m.get("diagnosed") for m in forced_body["messages"] if m["role"] == "assistant")

    restored = SessionStore(upload_dir / "sessions").get(session_id)
    assert restored.diagnosis is not None
    assert len(restored.messages) == len(forced_body["messages"])


def test_chat_empty_and_unknown(client):
    http, _ = client
    blank = http.post("/api/chat", json={"message": "  "})
    assert blank.status_code == 400
    missing = http.post("/api/sessions/does-not-exist/chat", json={"message": "hello"})
    assert missing.status_code == 404


def test_diagnose_bundle_seeds_chat_and_keeps_view(client):
    http, _ = client
    res = http.post(
        "/api/diagnose-bundle",
        data={
            "title": "Overheating cell",
            "domain": "batteries / energy",
            "unexpected_outcome": "Can got hot and capacity collapsed on 2C charge",
            "materials": "Opened LiPF6 bottle",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["view"]["lead"]["cause"]
    assert body["view"]["messages"]
    assert body["view"]["chat"]["turn_count"] >= 0
    assert any(m["role"] == "assistant" for m in body["messages"])

    session_id = body["session_id"]
    chat = http.post(
        f"/api/sessions/{session_id}/chat",
        json={"message": "Karl Fischer water in electrolyte was 480 ppm. Please diagnose."},
    )
    assert chat.status_code == 200
    follow = chat.json()
    assert follow["session_id"] == session_id
    assert follow["view"]["hypotheses"]
    assert "480 ppm" in " ".join(m["content"] for m in follow["messages"] if m["role"] == "user")


def test_diagnose_bundle_with_session_id_keeps_transcript(client):
    http, _ = client
    started = http.post("/api/chat", json={"message": "Pack vented during 2C charge."}).json()
    session_id = started["session_id"]
    before = len(started["messages"])
    dumped = http.post(
        "/api/diagnose-bundle",
        data={
            "title": "Pack vent",
            "session_id": session_id,
            "unexpected_outcome": "Three 21700 cells vented on 2C charge",
            "materials": "Opened LiPF6 bottle",
            "processing": "Filled in air then crimped",
        },
    )
    assert dumped.status_code == 200
    body = dumped.json()
    assert body["session_id"] == session_id
    assert len(body["messages"]) >= before
    assert body["diagnosis"]["hypotheses"]
    assert body["view"]["ingest"]["source"] == "dump"


def test_chat_llm_path_when_key_mocked(tmp_path, cases):
    app = create_app(upload_dir=tmp_path, prefer_llm=True, cases=cases)

    class FakeLLM:
        available = True
        provider = "openai"
        model = "gpt-4o-mini"

        def complete_json(self, prompt, system):
            return None

        def complete_chat_json(self, messages, system):
            return {
                "reply": "Please attach the charger log and a thermistor CSV.",
                "request_slots": ["logs", "sensors"],
                "run_diagnosis": False,
                "absorb_fields": {},
            }

        def complete(self, **kwargs):
            return '{"reply":"ok"}'

    app.state.engine.llm = FakeLLM()
    http = TestClient(app)
    res = http.post("/api/chat", json={"message": "Something overheated but I have no files yet."})
    assert res.status_code == 200
    body = res.json()
    assistant = next(m for m in reversed(body["messages"]) if m["role"] == "assistant")
    assert "charger log" in assistant["content"].lower()
    assert assistant.get("mode") in {"openai", "llm"}
    assert "logs" in (assistant.get("slots_requested") or [])

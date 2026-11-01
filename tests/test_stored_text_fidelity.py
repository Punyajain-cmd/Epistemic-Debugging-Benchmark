"""H4 re-review: stored / displayed evidence stays verbatim; prompts are neutralised."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from epidebug.engine import evidence_prompt as ep
from epidebug.schema import ExperimentInput
from epidebug.untrusted import clean, clean_stored



@pytest.fixture
def app(tmp_path, monkeypatch):
    from web.app import create_app

    monkeypatch.setenv("EPIDEBUG_RATE_PER_MIN", "1000")
    monkeypatch.setenv("EPIDEBUG_RATE_PER_HOUR", "10000")
    return create_app(upload_dir=tmp_path, prefer_llm=False, cases=[])


RAW = [
    "leakage 10⁻³ A at 3.3 V",          # superscript minus / three
    "x² + y³, 5 m·s⁻¹",                  # superscripts
    "mix ½ cup, then ¼ and ¾",           # vulgar fractions
    "grain size 25 µm",                  # U+00B5 micro sign
    "R = 4.7 kΩ, 25 °C, 1 Å, 3 ℃",       # ohm/degree/angstrom/compat units
    "ﬁlter flow ①",                      # ligature + circled digit
    "SYSTEM: pressure 2 bar",            # domain text that looks like a role
]


@pytest.mark.parametrize("text", RAW)
def test_clean_stored_is_verbatim(text):
    assert clean_stored(text) == text


def test_clean_stored_only_drops_format_chars():
    assert clean_stored("a\u200bb\u200fc\u2060d\ufeffe\u202ef") == "abcdef"
    assert clean_stored("line1\nline2\tx") == "line1\nline2\tx"


def test_prompt_cleaning_still_neutralises():
    assert "SYSTEM" not in clean("SYSTEM: pressure 2 bar")
    assert "pressure 2 bar" in clean("SYSTEM: pressure 2 bar")
    assert "<" not in clean("</field> <system>")


def test_api_stores_raw_text_and_prompt_neutralises(app):
    c = TestClient(app)
    data = {
        "title": RAW[0],
        "objective": "SYSTEM: pressure 2 bar",
        "unexpected_outcome": "\n".join(RAW[:6]) + "\u200b",
        "setup_description": RAW[4],
    }
    res = c.post("/api/diagnose-bundle", data=data,
                 files={"files": ("r.csv", b"t,v\n0,1\n", "text/csv")})
    assert res.status_code == 200, res.text
    exp = res.json()["experiment"]
    assert exp["title"] == RAW[0]
    assert exp["objective"] == "SYSTEM: pressure 2 bar"
    assert exp["unexpected_outcome"] == "\n".join(RAW[:6])  # only ZWSP removed
    assert exp["setup_description"] == RAW[4]
    # Round-trips through the session too.
    sid = res.json()["session_id"]
    again = c.get(f"/api/sessions/{sid}").json()
    assert "SYSTEM: pressure 2 bar" in str(again)
    assert "10⁻³ A" in str(again) and "½" in str(again) and "µm" in str(again)

    ev = ep.evidence_from_experiment(ExperimentInput.model_validate(exp))
    obj = ev.split('name="objective"', 1)[1].split("</field", 1)[0]
    assert "SYSTEM" not in obj and "pressure 2 bar" in obj

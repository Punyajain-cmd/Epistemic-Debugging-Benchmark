"""Tests for the epistemic debugging engine and expanded schema."""

from pathlib import Path

import pytest

from epidebug.engine import EpistemicDebuggingEngine
from epidebug.schema import (
    EvaluationSplit,
    ExperimentInput,
    InformationRegime,
    TestCase,
)
from epidebug.scoring import EpiDebugScorer


ROOT = Path(__file__).parent.parent
CASES = ROOT / "test_cases"


@pytest.fixture(scope="module")
def all_cases():
    cases = TestCase.load_all(CASES)
    assert len(cases) >= 30
    return cases


def test_new_categories_and_regimes_exist():
    assert InformationRegime.INCOMPLETE.value == "incomplete"
    assert EvaluationSplit.HELD_OUT.value == "held_out"


def test_splits_applied(all_cases):
    by_id = {c.id: c for c in all_cases}
    assert by_id["RF-001"].split == EvaluationSplit.CORE
    assert by_id["RF-015"].information_regime == InformationRegime.INCOMPLETE
    assert by_id["FH-014"].information_regime == InformationRegime.AMBIGUOUS
    assert by_id["IN-016"].split == EvaluationSplit.HELD_OUT


def test_incomplete_prompt_withholds_clues(all_cases):
    case = next(c for c in all_cases if c.id == "RF-015")
    prompt = case.to_prompt()
    assert "CO2 incubator" not in prompt
    assert "withheld" in prompt.lower() or "not recorded" in prompt.lower()


def test_engine_diagnoses_buffer_case(all_cases):
    case = next(c for c in all_cases if c.id == "RF-001")
    engine = EpistemicDebuggingEngine(prefer_llm=False)
    diagnosis = engine.diagnose_case(case)
    assert diagnosis.hypotheses
    assert diagnosis.leading_cause
    assert diagnosis.recommended_intervention is not None
    assert diagnosis.uncertainty.competing_count >= 1
    blob = (diagnosis.leading_cause + " " + " ".join(diagnosis.leading_causal_chain)).lower()
    assert any(tok in blob for tok in ("ph", "buffer", "his", "ni-nta", "protonat"))


def test_engine_hitl_reject_and_add_info(all_cases):
    case = next(c for c in all_cases if c.id == "IN-001")
    engine = EpistemicDebuggingEngine(prefer_llm=False)
    session = engine.open_session(case=case)
    hyp_id = session.diagnosis.hypotheses[0].id
    session = engine.reject_hypothesis(session.session_id, hyp_id, "Researcher disagrees")
    rejected = [h for h in session.diagnosis.hypotheses if h.id == hyp_id]
    assert rejected and rejected[0].status == "rejected"
    session = engine.add_information(session.session_id, "Blank reading was 0.08 AU after a dirty pedestal")
    assert "Blank reading was 0.08 AU after a dirty pedestal" in session.extra_information


def test_catalog_session_uses_attached_artifacts(all_cases, tmp_path):
    from epidebug.engine.ingest import ingest_bytes
    from epidebug.engine.session import SessionStore

    case = next(c for c in all_cases if c.id == "RF-001")
    engine = EpistemicDebuggingEngine(
        prefer_llm=False,
        sessions=SessionStore(tmp_path / "sessions"),
        cases={c.id: c for c in all_cases},
    )
    session = engine.open_session(case=case)
    csv = b"time_s,ph\n0,8.0\n1,6.4\n2,6.2\n"
    artifact = ingest_bytes("bench_ph.csv", csv, caption="Aged Tris buffer pH probe")
    session = engine.add_artifacts(session.session_id, [artifact])
    blob = " ".join(h.statement for h in session.diagnosis.hypotheses).lower()
    assert session.experiment is not None
    assert session.experiment.artifacts[0].filename == "bench_ph.csv"
    assert any(tok in blob for tok in ("ph", "buffer", "probe", "tris"))
    restored = SessionStore(tmp_path / "sessions").get(session.session_id)
    assert restored.diagnosis is not None
    assert restored.experiment.artifacts[0].filename == "bench_ph.csv"


def test_engine_freeform_experiment():
    engine = EpistemicDebuggingEngine(prefer_llm=False)
    exp = ExperimentInput(
        title="Failed Ni-NTA",
        objective="Purify His-tagged kinase; yield collapsed",
        protocol=["Used 3-week-old Tris buffer stored on the bench", "Loaded Ni-NTA column"],
        telemetry_notes=["Target in flow-through", "Elution yield 0.3 mg vs expected 5 mg"],
        contextual_clues=["CO2 incubator on the same bench"],
        domain="molecular_biology",
    )
    diagnosis = engine.diagnose_experiment(exp)
    assert diagnosis.hypotheses
    assert diagnosis.anomalies


def test_engine_scores_as_model(all_cases):
    case = next(c for c in all_cases if c.id == "RF-001")
    engine = EpistemicDebuggingEngine(prefer_llm=False)
    response = engine.diagnose_case(case).to_model_response()
    score = EpiDebugScorer().score_deterministic(case, response)
    assert 0.0 <= score.final_score <= 1.0
    assert score.epistemic.evidence_grounding is not None


def test_tools_registry():
    from epidebug.tools import TOOL_REGISTRY, get_tool, list_tools

    assert len(TOOL_REGISTRY) >= 5
    assert get_tool("ph_calculator")(solution="Tris-HCl")["calculated_ph"] == 8.0
    assert "ph_calculator" in [t["name"] for t in list_tools()]

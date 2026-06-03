"""Unit tests for the EpiDebug schema and scoring modules."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from epidebug.schema import (
    ComponentScore,
    Domain,
    Difficulty,
    FailureCategory,
    GroundTruth,
    ModelResponse,
    ProtocolStep,
    ScoreResult,
    ScoringConfig,
    ScoringWeights,
    TelemetryEntry,
    TelemetryLog,
    TestCase,
    TrajectoryMetrics,
)
from epidebug.scoring import (
    CausalChainScorer,
    EpiDebugScorer,
    InterventionScorer,
    RootCauseScorer,
    TrajectoryAnalyzer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_test_case() -> TestCase:
    """Create a minimal valid test case for testing."""
    return TestCase(
        id="RF-001",
        title="Test Buffer pH Drift",
        domain=Domain.MOLECULAR_BIOLOGY,
        subdomain="protein_purification",
        failure_category=FailureCategory.REAGENT_MATERIAL_FLAW,
        difficulty=Difficulty.MEDIUM,
        objective="Purify protein via Ni-NTA chromatography",
        protocol=[
            ProtocolStep(step=1, action="Prepare lysis buffer"),
            ProtocolStep(step=2, action="Lyse cells"),
            ProtocolStep(step=3, action="Run column"),
        ],
        telemetry=TelemetryLog(entries={
            "yield": TelemetryEntry(
                description="Total protein yield",
                data_type="numeric",
                values=[0.3],
                units="mg",
            ),
        }),
        contextual_clues=["Buffer stored at room temperature for 3 weeks"],
        ground_truth=GroundTruth(
            root_cause="Buffer pH drifted from 8.0 to 6.5 due to CO2 absorption",
            root_cause_category=FailureCategory.REAGENT_MATERIAL_FLAW,
            causal_chain=[
                "Tris buffer left at room temperature",
                "CO2 dissolved into buffer forming carbonic acid",
                "pH dropped from 8.0 to 6.5",
                "His-tag protonated, lost Ni2+ affinity",
                "Protein flows through column",
            ],
            intervention="Re-prepare fresh buffer, verify pH, rerun purification",
            difficulty_justification="Requires connecting buffer age, CO2, and His-tag chemistry",
        ),
        scoring=ScoringConfig(
            root_cause_keywords=["pH", "buffer", "CO2", "drift", "histidine"],
            partial_credit_causes={"buffer degradation": 0.5},
        ),
        source="Test fixture",
    )


@pytest.fixture
def correct_response() -> ModelResponse:
    """A model response that correctly diagnoses RF-001."""
    return ModelResponse(
        model_name="test-model",
        test_case_id="RF-001",
        root_cause="The lysis buffer pH drifted from 8.0 to approximately 6.5 due to CO2 absorption over 3 weeks of room temperature storage",
        causal_chain=[
            "Tris-HCl buffer stored at room temperature for 3 weeks",
            "CO2 from atmosphere dissolved into the buffer",
            "Carbonic acid formed, lowering pH from 8.0 to ~6.5",
            "At pH 6.5, histidine residues in the His-tag become protonated",
            "Protonated His cannot coordinate Ni2+, so protein flows through",
        ],
        intervention="Prepare fresh lysis buffer, verify pH is 8.0 before use, and rerun the Ni-NTA purification",
    )


@pytest.fixture
def wrong_response() -> ModelResponse:
    """A model response that misdiagnoses RF-001."""
    return ModelResponse(
        model_name="test-model",
        test_case_id="RF-001",
        root_cause="The Ni-NTA resin has degraded and lost its nickel ions",
        causal_chain=[
            "The resin is old and has lost nickel",
            "Without nickel, the resin cannot bind His-tagged proteins",
        ],
        intervention="Order new Ni-NTA resin from the supplier",
    )


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------

class TestSchema:

    def test_failure_category_prefix(self):
        assert FailureCategory.REAGENT_MATERIAL_FLAW.prefix == "RF"
        assert FailureCategory.INSTRUMENTATION_ARTIFACT.prefix == "IN"
        assert FailureCategory.PROTOCOL_HUMAN_LOOPHOLE.prefix == "PL"
        assert FailureCategory.FLAWED_HYPOTHESIS.prefix == "FH"

    def test_expansion_domains_available(self):
        assert Domain.SOFTWARE_SYSTEMS.value == "software_systems"
        assert Domain.MANUFACTURING.value == "manufacturing"
        assert Domain.CIVIL_ENGINEERING.value == "civil_engineering"
        assert Domain.AEROSPACE.value == "aerospace"
        assert Domain.ENERGY.value == "energy"
        assert Domain.ENVIRONMENTAL_SCIENCE.value == "environmental_science"
        assert Domain.MATERIALS_SCIENCE.value == "materials_science"
        assert Domain.ROBOTICS.value == "robotics"

    def test_scoring_weights_sum(self):
        w = ScoringWeights()
        assert abs(w.root_cause + w.causal_chain + w.intervention - 1.0) < 0.01

    def test_scoring_weights_invalid(self):
        with pytest.raises(ValueError):
            ScoringWeights(root_cause=0.5, causal_chain=0.5, intervention=0.5)

    def test_test_case_id_validation(self):
        """ID prefix must match failure category."""
        with pytest.raises(ValueError):
            TestCase(
                id="IN-001",  # Wrong prefix for reagent flaw
                title="Test",
                domain=Domain.MOLECULAR_BIOLOGY,
                subdomain="test",
                failure_category=FailureCategory.REAGENT_MATERIAL_FLAW,
                difficulty=Difficulty.EASY,
                objective="Test",
                protocol=[ProtocolStep(step=1, action="Test")],
                telemetry=TelemetryLog(entries={"x": TelemetryEntry(description="x")}),
                contextual_clues=["clue"],
                ground_truth=GroundTruth(
                    root_cause="test", root_cause_category=FailureCategory.REAGENT_MATERIAL_FLAW,
                    causal_chain=["a", "b"], intervention="test", difficulty_justification="test",
                ),
                source="test",
            )

    def test_test_case_to_prompt(self, sample_test_case: TestCase):
        prompt = sample_test_case.to_prompt(mode="text")
        assert "Root Cause" in prompt
        assert "Causal Chain" in prompt
        assert "Intervention" in prompt
        assert "Buffer stored at room temperature" in prompt

    def test_test_case_agent_prompt_has_tools(self, sample_test_case: TestCase):
        sample_test_case.available_tools = ["ph_calculator", "gel_imager"]
        prompt = sample_test_case.to_prompt(mode="agent")
        assert "ph_calculator" in prompt
        assert "tool calls" in prompt.lower()

    def test_score_result_computes_final(self):
        result = ScoreResult(
            test_case_id="RF-001", model_name="test", mode="text",
            root_cause_score=ComponentScore(score=1.0, weight=0.3, weighted_score=0.3, rationale="ok"),
            causal_chain_score=ComponentScore(score=0.5, weight=0.4, weighted_score=0.2, rationale="ok"),
            intervention_score=ComponentScore(score=0.75, weight=0.3, weighted_score=0.225, rationale="ok"),
            final_score=0.725,
        )
        assert abs(result.final_score - 0.725) < 0.01


# ---------------------------------------------------------------------------
# Scoring Tests
# ---------------------------------------------------------------------------

class TestScoring:

    def test_root_cause_correct(self, sample_test_case, correct_response):
        scorer = RootCauseScorer()
        result = scorer.score(sample_test_case, correct_response)
        assert result.score >= 0.75, f"Expected high score for correct answer, got {result.score}"
        assert result.weight == 0.3

    def test_root_cause_wrong(self, sample_test_case, wrong_response):
        scorer = RootCauseScorer()
        result = scorer.score(sample_test_case, wrong_response)
        assert result.score <= 0.5, f"Expected low score for wrong answer, got {result.score}"

    def test_causal_chain_correct(self, sample_test_case, correct_response):
        scorer = CausalChainScorer()
        result = scorer.score(sample_test_case, correct_response)
        assert result.score >= 0.5, f"Expected good score for matching chain, got {result.score}"

    def test_causal_chain_empty(self, sample_test_case):
        response = ModelResponse(
            model_name="test", test_case_id="RF-001",
            root_cause="something", causal_chain=[], intervention="something",
        )
        scorer = CausalChainScorer()
        result = scorer.score(sample_test_case, response)
        assert result.score == 0.0

    def test_intervention_correct(self, sample_test_case, correct_response):
        scorer = InterventionScorer()
        result = scorer.score(sample_test_case, correct_response)
        assert result.score >= 0.5

    def test_intervention_empty(self, sample_test_case):
        response = ModelResponse(
            model_name="test", test_case_id="RF-001",
            root_cause="x", causal_chain=["x"], intervention="",
        )
        scorer = InterventionScorer()
        result = scorer.score(sample_test_case, response)
        assert result.score == 0.0

    def test_full_scorer_deterministic(self, sample_test_case, correct_response):
        scorer = EpiDebugScorer()
        result = scorer.score_deterministic(sample_test_case, correct_response)
        assert 0 <= result.final_score <= 1.0
        assert result.test_case_id == "RF-001"
        assert result.model_name == "test-model"

    def test_trajectory_empty(self, sample_test_case):
        response = ModelResponse(
            model_name="test", test_case_id="RF-001",
            root_cause="x", causal_chain=["x"], intervention="x",
            tool_calls=[],
        )
        traj = TrajectoryAnalyzer.analyze(response, sample_test_case)
        assert traj.tool_calls_made == 0
        assert traj.budget_compliant is True

    def test_trajectory_over_budget(self, sample_test_case):
        sample_test_case.max_tool_calls = 3
        calls = [{"tool": f"tool_{i}", "args": str(i)} for i in range(5)]
        response = ModelResponse(
            model_name="test", test_case_id="RF-001",
            root_cause="x", causal_chain=["x"], intervention="x",
            tool_calls=calls,
        )
        traj = TrajectoryAnalyzer.analyze(response, sample_test_case)
        assert traj.budget_compliant is False
        assert any("exceeded_budget" in p for p in traj.anti_patterns)

    def test_trajectory_redundant_calls(self, sample_test_case):
        calls = [{"tool": "ph_calculator", "args": "same"} for _ in range(4)]
        response = ModelResponse(
            model_name="test", test_case_id="RF-001",
            root_cause="x", causal_chain=["x"], intervention="x",
            tool_calls=calls,
        )
        traj = TrajectoryAnalyzer.analyze(response, sample_test_case)
        assert any("redundant" in p for p in traj.anti_patterns)
        assert any("single_tool" in p for p in traj.anti_patterns)


# ---------------------------------------------------------------------------
# YAML Loading Tests
# ---------------------------------------------------------------------------

class TestYAMLLoading:

    def test_load_rf001(self):
        path = Path(__file__).parent.parent / "test_cases" / "reagent_flaw" / "RF-001_buffer_ph_drift.yaml"
        if path.exists():
            case = TestCase.from_yaml(path)
            assert case.id == "RF-001"
            assert case.failure_category == FailureCategory.REAGENT_MATERIAL_FLAW
            assert len(case.protocol) >= 5
            assert len(case.ground_truth.causal_chain) >= 3

    def test_load_all_cases(self):
        test_dir = Path(__file__).parent.parent / "test_cases"
        if test_dir.exists():
            cases = TestCase.load_all(test_dir)
            assert len(cases) >= 1, "Should load at least 1 test case"
            for case in cases:
                assert case.id  # Every case has an ID
                assert len(case.ground_truth.causal_chain) >= 2  # Every case has a chain


# ---------------------------------------------------------------------------
# Tool Tests
# ---------------------------------------------------------------------------

class TestTools:

    def test_tool_registry_populated(self):
        from epidebug.tools import TOOL_REGISTRY
        assert len(TOOL_REGISTRY) >= 5

    def test_ph_calculator(self):
        from epidebug.tools import get_tool
        ph_calc = get_tool("ph_calculator")
        result = ph_calc(solution="Tris-HCl", temperature=25.0, co2_exposure_hours=500)
        assert result["calculated_ph"] < 7.5  # Should show significant drift
        assert result["co2_correction"] < -0.5

    def test_ph_calculator_no_co2(self):
        from epidebug.tools import get_tool
        ph_calc = get_tool("ph_calculator")
        result = ph_calc(solution="Tris-HCl", temperature=25.0, co2_exposure_hours=0)
        assert result["calculated_ph"] == 8.0

    def test_ni_nta_conditions(self):
        from epidebug.tools import get_tool
        ni_nta = get_tool("ni_nta_binding_conditions")
        result = ni_nta()
        assert "pH" in str(result)
        assert "6.5" in str(result) or "protonated" in str(result).lower()

    def test_unknown_tool_raises(self):
        from epidebug.tools import get_tool
        with pytest.raises(KeyError):
            get_tool("nonexistent_tool")

    def test_list_tools(self):
        from epidebug.tools import list_tools
        tools = list_tools()
        assert len(tools) >= 5
        names = [t["name"] for t in tools]
        assert "ph_calculator" in names

    def test_trace_log_analyzer_case_data(self):
        from epidebug.tools import get_tool
        trace = get_tool("trace_log_analyzer")
        result = trace(case_id="IN-009")
        assert result["host_clock_offsets_ms"]["checkout-canary-3"] > 1000
        assert "clock skew" in result["assessment"].lower()

    def test_machine_failure_analyzer_case_data(self):
        from epidebug.tools import get_tool
        analyzer = get_tool("machine_failure_analyzer")
        result = analyzer(case_id="RF-009")
        assert (
            result["tool_life"]["flank_wear_mm"]
            > result["tool_life"]["replacement_threshold_mm"]
        )
        assert "end mill" in result["assessment"].lower()

    def test_vision_dataset_auditor_case_data(self):
        from epidebug.tools import get_tool
        auditor = get_tool("vision_dataset_auditor")
        result = auditor(case_id="FH-009")
        assert (
            result["random_patch_split"]["validation_accuracy"]
            > result["site_holdout_split"]["validation_accuracy"]
        )
        assert "leakage" in result["assessment"].lower()

    def test_clinical_sample_checker_case_data(self):
        from epidebug.tools import get_tool
        checker = get_tool("clinical_sample_checker")
        result = checker(case_id="RF-010")
        assert max(result["specimen_quality"]["hemolysis_index"]) > 20
        assert "pseudohyperkalemia" in result["assessment"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

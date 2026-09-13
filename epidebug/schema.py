"""
Core data models for the Epistemic Debugging Benchmark.

Every test case follows the schema that mirrors what a human researcher receives:
    1. Objective — what the experiment aimed to achieve
    2. Protocol — exact step-by-step instructions followed
    3. Telemetry — raw sensor data, images, machine outputs
    4. Contextual Clues — hidden environmental variables

Ground truth includes root cause, causal chain, and intervention strategy.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FailureCategory(str, Enum):
    """The four fundamental categories of experimental failure."""

    REAGENT_MATERIAL_FLAW = "reagent_material_flaw"
    INSTRUMENTATION_ARTIFACT = "instrumentation_artifact"
    PROTOCOL_HUMAN_LOOPHOLE = "protocol_human_loophole"
    FLAWED_HYPOTHESIS = "flawed_hypothesis"

    @property
    def short_label(self) -> str:
        return {
            self.REAGENT_MATERIAL_FLAW: "Reagent/Material",
            self.INSTRUMENTATION_ARTIFACT: "Instrumentation",
            self.PROTOCOL_HUMAN_LOOPHOLE: "Protocol/Human",
            self.FLAWED_HYPOTHESIS: "Flawed Hypothesis",
        }[self]

    @property
    def prefix(self) -> str:
        """ID prefix for this category (e.g., RF, IN, PL, FH)."""
        return {
            self.REAGENT_MATERIAL_FLAW: "RF",
            self.INSTRUMENTATION_ARTIFACT: "IN",
            self.PROTOCOL_HUMAN_LOOPHOLE: "PL",
            self.FLAWED_HYPOTHESIS: "FH",
        }[self]


class Domain(str, Enum):
    """Scientific domains covered by the benchmark."""

    MOLECULAR_BIOLOGY = "molecular_biology"
    CHEMISTRY = "chemistry"
    PHYSICS = "physics"
    ENGINEERING = "engineering"
    BIOTECH = "biotech"
    CLINICAL = "clinical"
    SOFTWARE_SYSTEMS = "software_systems"
    MANUFACTURING = "manufacturing"
    CIVIL_ENGINEERING = "civil_engineering"
    AEROSPACE = "aerospace"
    ENERGY = "energy"
    ENVIRONMENTAL_SCIENCE = "environmental_science"
    MATERIALS_SCIENCE = "materials_science"
    ROBOTICS = "robotics"


class Difficulty(str, Enum):
    """Difficulty tiers for test cases."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


class InformationRegime(str, Enum):
    """How complete and trustworthy the presented evidence is."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    NOISY = "noisy"
    DISTRACTOR_HEAVY = "distractor_heavy"
    AMBIGUOUS = "ambiguous"


class EvaluationSplit(str, Enum):
    """Train/dev/held-out partition for evaluation."""

    CORE = "core"
    DEVELOPMENT = "development"
    HELD_OUT = "held_out"


# ---------------------------------------------------------------------------
# Protocol & Telemetry Models
# ---------------------------------------------------------------------------

class ProtocolStep(BaseModel):
    """A single step in an experimental protocol."""

    step: int = Field(..., description="Step number (1-indexed)")
    action: str = Field(..., description="What the researcher did")
    details: Optional[str] = Field(
        None, description="Additional details, timing, equipment used"
    )
    duration: Optional[str] = Field(None, description="How long this step takes")
    notes: Optional[str] = Field(
        None, description="Any observations or notes made during this step"
    )


class TelemetryEntry(BaseModel):
    """A single telemetry data point — sensor reading, image, machine output."""

    description: str = Field(..., description="What this measurement represents")
    data_type: Literal[
        "numeric", "timeseries", "image", "spectrum", "table", "text", "categorical"
    ] = Field("text", description="Type of telemetry data")
    observations: Optional[list[str]] = Field(
        None, description="Human-readable observations about this data"
    )
    values: Optional[Union[list[float], list[list[float]], dict[str, Any]]] = Field(
        None, description="Numeric data values"
    )
    units: Optional[str] = Field(None, description="Units of measurement")
    data_ref: Optional[str] = Field(
        None, description="Path to external data file (image, CSV, etc.)"
    )
    note: Optional[str] = Field(None, description="Additional note about this entry")
    expected_range: Optional[dict[str, float]] = Field(
        None, description="Expected min/max range for normal results"
    )


class TelemetryLog(BaseModel):
    """Collection of all telemetry data from the experiment."""

    entries: dict[str, TelemetryEntry] = Field(
        ..., description="Named telemetry entries (e.g., 'sds_page', 'od280')"
    )

    def __getattr__(self, name: str) -> TelemetryEntry:
        if name in self.entries:
            return self.entries[name]
        raise AttributeError(f"No telemetry entry named '{name}'")


# ---------------------------------------------------------------------------
# Ground Truth
# ---------------------------------------------------------------------------

class GroundTruth(BaseModel):
    """The verified root cause, causal chain, and intervention for a failure."""

    root_cause: str = Field(
        ..., description="The single variable or factor that caused the failure"
    )
    root_cause_category: FailureCategory = Field(
        ..., description="Which of the 4 failure categories this falls into"
    )
    causal_chain: list[str] = Field(
        ...,
        description="Step-by-step causal propagation from root cause to observed symptoms",
        min_length=2,
    )
    intervention: str = Field(
        ...,
        description="Counterfactual experiment that would confirm/refute the diagnosis",
    )
    key_evidence: list[str] = Field(
        default_factory=list,
        description="Specific pieces of evidence in telemetry/protocol that point to root cause",
    )
    alternative_diagnoses: list[str] = Field(
        default_factory=list,
        description="Plausible but incorrect alternative explanations",
    )
    why_alternatives_fail: Optional[dict[str, str]] = Field(
        None,
        description="For each alternative diagnosis, why it doesn't fit the evidence",
    )
    difficulty_justification: str = Field(
        ..., description="Why this test case has its assigned difficulty"
    )


# ---------------------------------------------------------------------------
# Scoring Rubric (embedded per test case for customization)
# ---------------------------------------------------------------------------

class ScoringWeights(BaseModel):
    """Weights for the three scoring components. Must sum to 1.0."""

    root_cause: float = Field(0.30, ge=0, le=1)
    causal_chain: float = Field(0.40, ge=0, le=1)
    intervention: float = Field(0.30, ge=0, le=1)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "ScoringWeights":
        total = self.root_cause + self.causal_chain + self.intervention
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Scoring weights must sum to 1.0, got {total:.2f}"
            )
        return self


class ScoringConfig(BaseModel):
    """Per-test-case scoring configuration."""

    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    root_cause_keywords: list[str] = Field(
        default_factory=list,
        description="Key terms that must appear in a correct root cause identification",
    )
    partial_credit_causes: Optional[dict[str, float]] = Field(
        None,
        description="Map of partially-correct root causes to their credit (0-1)",
    )
    evidence_keywords: list[str] = Field(
        default_factory=list,
        description="Terms that a well-grounded diagnosis should cite from the evidence",
    )


# ---------------------------------------------------------------------------
# Test Case (top-level)
# ---------------------------------------------------------------------------

class TestCase(BaseModel):
    """
    A complete Epistemic Debugging test case.

    Encodes everything needed to present the failure scenario to an AI model,
    evaluate its diagnosis, and report results.
    """

    __test__ = False

    # --- Identity ---
    id: str = Field(..., description="Unique ID, e.g. 'RF-001'", pattern=r"^[A-Z]{2}-\d{3}$")
    title: str = Field(..., description="Human-readable title")
    version: str = Field("1.0", description="Test case version")

    # --- Classification ---
    domain: Domain
    subdomain: str = Field(..., description="e.g., 'protein_purification', 'PCR'")
    failure_category: FailureCategory
    difficulty: Difficulty
    information_regime: InformationRegime = Field(
        InformationRegime.COMPLETE,
        description="Completeness/noise regime of the presented case",
    )
    split: EvaluationSplit = Field(
        EvaluationSplit.CORE,
        description="Evaluation split this case belongs to",
    )

    # --- The four layers of information (what the human gets) ---
    objective: str = Field(
        ..., description="What the researcher wanted to achieve"
    )
    protocol: list[ProtocolStep] = Field(
        ..., description="Step-by-step experimental protocol", min_length=1
    )
    telemetry: TelemetryLog = Field(
        ..., description="Raw sensor data, images, machine outputs"
    )
    contextual_clues: list[str] = Field(
        ...,
        description="Hidden environmental variables and background details",
        min_length=1,
    )
    hidden_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Fields withheld from the model in incomplete regimes. "
            "Use 'contextual_clues' or 'telemetry.<name>'."
        ),
    )
    red_herrings: list[str] = Field(
        default_factory=list,
        description="Explicit distractor clues that should not drive the diagnosis",
    )

    # --- Ground truth (for scoring) ---
    ground_truth: GroundTruth

    # --- Scoring customization ---
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)

    # --- Sandbox configuration ---
    available_tools: list[str] = Field(
        default_factory=list,
        description="Which mock tools the agent can use in agent mode",
    )
    mock_data_refs: list[str] = Field(
        default_factory=list, description="Paths to pre-recorded data files"
    )
    max_tool_calls: int = Field(
        20, ge=1, le=100, description="Maximum tool calls allowed (efficiency budget)"
    )

    # --- Metadata ---
    source: str = Field(
        ..., description="Where this case originated (paper, guide, composite)"
    )
    source_url: Optional[str] = Field(None, description="URL to original source")
    expert_validated: bool = Field(False, description="Has a domain expert reviewed this?")
    tags: list[str] = Field(default_factory=list)
    author: Optional[str] = Field(None, description="Who authored this test case")

    @model_validator(mode="after")
    def id_matches_category(self) -> "TestCase":
        """Validate that ID prefix matches the failure category."""
        expected_prefix = self.failure_category.prefix
        actual_prefix = self.id.split("-")[0]
        if actual_prefix != expected_prefix:
            raise ValueError(
                f"ID prefix '{actual_prefix}' doesn't match category "
                f"'{self.failure_category.value}' (expected '{expected_prefix}')"
            )
        return self

    # --- Serialization helpers ---

    def withheld_set(self) -> set[str]:
        return {field.lower() for field in self.hidden_fields}

    def visible_clues(self) -> list[str]:
        if "contextual_clues" in self.withheld_set():
            return []
        return list(self.contextual_clues)

    def visible_telemetry(self) -> dict[str, TelemetryEntry]:
        withheld = self.withheld_set()
        entries = {}
        for name, entry in self.telemetry.entries.items():
            if f"telemetry.{name.lower()}" in withheld or name.lower() in withheld:
                continue
            entries[name] = entry
        return entries

    def to_prompt(self, mode: str = "text") -> str:
        """
        Generate the prompt that will be presented to the AI model.

        Args:
            mode: 'text' for text-only mode, 'agent' for tool-augmented mode.
        """
        sections = []

        sections.append("# Experimental Failure Analysis\n")
        sections.append("You are a scientific researcher investigating why an experiment "
                        "produced unexpected results. Analyze the information below and provide:\n"
                        "1. **Root Cause**: Identify the single most likely variable that caused "
                        "the failure.\n"
                        "2. **Causal Chain**: Trace the step-by-step mechanism from root cause "
                        "to the observed symptoms.\n"
                        "3. **Intervention**: Propose a specific counterfactual experiment that "
                        "would confirm or refute your diagnosis.\n"
                        "Also list competing hypotheses, supporting and contradictory evidence, "
                        "missing information, and a confidence between 0 and 1.\n")

        sections.append(f"## Objective\n{self.objective}\n")

        sections.append("## Protocol")
        for step in self.protocol:
            line = f"**Step {step.step}**: {step.action}"
            if step.details:
                line += f"\n  - *Details*: {step.details}"
            if step.duration:
                line += f"\n  - *Duration*: {step.duration}"
            if step.notes:
                line += f"\n  - *Notes*: {step.notes}"
            sections.append(line)
        sections.append("")

        sections.append("## Experimental Results (Telemetry)")
        visible = self.visible_telemetry()
        if not visible:
            sections.append("*No telemetry was provided for this case.*")
        for name, entry in visible.items():
            entry_text = f"### {name}\n{entry.description}"
            if entry.observations:
                entry_text += "\n**Observations:**"
                for obs in entry.observations:
                    entry_text += f"\n- {obs}"
            if entry.values is not None:
                entry_text += f"\n**Data**: {entry.values}"
            if entry.units:
                entry_text += f" ({entry.units})"
            if entry.note:
                entry_text += f"\n*Note*: {entry.note}"
            if entry.expected_range:
                entry_text += (
                    f"\n*Expected range*: {entry.expected_range.get('min', '?')} – "
                    f"{entry.expected_range.get('max', '?')}"
                )
            sections.append(entry_text)
        sections.append("")

        clues = self.visible_clues()
        if clues:
            sections.append("## Background Context")
            for clue in clues:
                sections.append(f"- {clue}")
            sections.append("")
        elif self.information_regime in {
            InformationRegime.INCOMPLETE,
            InformationRegime.NOISY,
        }:
            sections.append("## Background Context")
            sections.append("- Contextual notes were not recorded or have been withheld.")
            sections.append("")

        if self.red_herrings and self.information_regime == InformationRegime.DISTRACTOR_HEAVY:
            sections.append("## Additional Lab Notes")
            for clue in self.red_herrings:
                sections.append(f"- {clue}")
            sections.append("")

        if mode == "agent":
            sections.append("## Available Tools")
            sections.append("You have access to the following tools to investigate further:")
            for tool_name in self.available_tools:
                sections.append(f"- `{tool_name}`")
            sections.append(f"\n*Efficiency budget*: You may make at most "
                            f"**{self.max_tool_calls}** tool calls.\n")

        sections.append("## Your Analysis")
        sections.append("Provide your analysis in the following format:\n")
        sections.append("### Root Cause\n[Identify the primary variable that caused the failure]\n")
        sections.append("### Causal Chain\n[Trace the step-by-step causal mechanism]\n")
        sections.append("### Intervention\n[Propose a counterfactual experiment to confirm]\n")
        sections.append("### Competing Hypotheses\n[Ranked alternative explanations]\n")
        sections.append("### Evidence\n[Supporting and contradictory observations]\n")
        sections.append("### Missing Information\n[What else would discriminate hypotheses]\n")
        sections.append("### Confidence\n[A number between 0 and 1]\n")

        return "\n".join(sections)

    def to_yaml(self) -> str:
        """Serialize to YAML for storage."""
        return yaml.dump(
            self.model_dump(mode="json"),
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "TestCase":
        """Load a test case from a YAML file."""
        path = Path(path)
        with open(path, "r") as f:
            data = yaml.safe_load(f)

        # Handle telemetry format: convert flat dict to nested entries format
        if "telemetry" in data and "entries" not in data["telemetry"]:
            data["telemetry"] = {"entries": data["telemetry"]}

        return cls.model_validate(data)

    @classmethod
    def load_all(cls, directory: Union[str, Path]) -> list["TestCase"]:
        """Load all test cases from a directory tree."""
        directory = Path(directory)
        cases = []
        skip_names = {"manifest.yaml", "splits.yaml", "catalog.yaml"}
        for yaml_file in sorted(directory.rglob("*.yaml")):
            if yaml_file.name in skip_names:
                continue
            try:
                cases.append(cls.from_yaml(yaml_file))
            except Exception as e:
                print(f"Failed to load {yaml_file}: {e}")
        splits_path = directory / "splits.yaml"
        if splits_path.exists():
            cls._apply_splits(cases, splits_path)
        return cases

    @staticmethod
    def _apply_splits(cases: list["TestCase"], splits_path: Path) -> None:
        with open(splits_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        mapping: dict[str, EvaluationSplit] = {}
        for split in EvaluationSplit:
            for case_id in data.get(split.value, []) or []:
                mapping[str(case_id)] = split
        for case in cases:
            if case.id in mapping:
                case.split = mapping[case.id]


# ---------------------------------------------------------------------------
# Model Response Schema (what the AI returns)
# ---------------------------------------------------------------------------

class ModelResponse(BaseModel):
    """Structured representation of the AI model's diagnosis."""

    model_name: str = Field(..., description="Name of the model being evaluated")
    test_case_id: str = Field(..., description="ID of the test case")
    mode: Literal["text", "agent"] = Field("text", description="Evaluation mode")

    # The three scored components
    root_cause: str = Field(..., description="Model's identified root cause")
    causal_chain: list[str] = Field(
        ..., description="Model's step-by-step causal chain"
    )
    intervention: str = Field(
        ..., description="Model's proposed counterfactual experiment"
    )

    # Optional metadata
    raw_response: Optional[str] = Field(
        None, description="Full raw text response from the model"
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list, description="Log of tool calls made (agent mode)"
    )
    total_tokens: Optional[int] = Field(None, description="Total tokens used")
    latency_seconds: Optional[float] = Field(None, description="Wall-clock time")
    confidence: Optional[float] = Field(
        None, ge=0, le=1, description="Model's stated confidence (for calibration)"
    )
    competing_hypotheses: list[str] = Field(
        default_factory=list, description="Ranked alternative explanations"
    )
    supporting_evidence: list[str] = Field(
        default_factory=list, description="Observations cited as supporting the diagnosis"
    )
    contradictory_evidence: list[str] = Field(
        default_factory=list, description="Observations that count against the diagnosis"
    )
    missing_information: list[str] = Field(
        default_factory=list, description="Information the model says is still needed"
    )


# ---------------------------------------------------------------------------
# Score Result
# ---------------------------------------------------------------------------

class ComponentScore(BaseModel):
    """Score for a single component (root cause, causal chain, or intervention)."""

    score: float = Field(..., ge=0, le=1, description="Score from 0.0 to 1.0")
    weight: float = Field(..., ge=0, le=1, description="Weight in final score")
    weighted_score: float = Field(
        ..., ge=0, le=1, description="score × weight"
    )
    rationale: str = Field(..., description="Why this score was assigned")


class TrajectoryMetrics(BaseModel):
    """Metrics about the reasoning process (not weighted into final score)."""

    tool_calls_made: int = Field(0, description="Total tool calls")
    tool_calls_budget: int = Field(20, description="Maximum allowed")
    tool_efficiency: Optional[float] = Field(
        None, description="Fraction of tool calls that were productive"
    )
    budget_compliant: bool = Field(True, description="Stayed within budget?")
    anti_patterns: list[str] = Field(
        default_factory=list,
        description="Detected epistemic anti-patterns (e.g., confirmation bias)",
    )


class EpistemicMetrics(BaseModel):
    """Separate evaluation of epistemic behaviour, not mixed into the 3-way score."""

    evidence_grounding: Optional[float] = Field(
        None, ge=0, le=1, description="How well claims are tied to case evidence"
    )
    calibration_error: Optional[float] = Field(
        None, ge=0, le=1, description="Absolute gap between confidence and accuracy"
    )
    confirmation_bias: Optional[float] = Field(
        None, ge=0, le=1, description="1.0 = strong bias toward a single untested hypothesis"
    )
    hypothesis_diversity: Optional[float] = Field(
        None, ge=0, le=1, description="Whether multiple competing explanations were considered"
    )
    missing_info_quality: Optional[float] = Field(
        None, ge=0, le=1, description="Whether the model asked for diagnostically useful information"
    )


class ScoreResult(BaseModel):
    """Complete scoring result for a single test case evaluation."""

    test_case_id: str
    model_name: str
    mode: Literal["text", "agent"]

    root_cause_score: ComponentScore
    causal_chain_score: ComponentScore
    intervention_score: ComponentScore

    final_score: float = Field(..., ge=0, le=1, description="Weighted composite score")

    trajectory: TrajectoryMetrics = Field(default_factory=TrajectoryMetrics)
    epistemic: EpistemicMetrics = Field(default_factory=EpistemicMetrics)

    @model_validator(mode="after")
    def compute_final(self) -> "ScoreResult":
        computed = (
            self.root_cause_score.weighted_score
            + self.causal_chain_score.weighted_score
            + self.intervention_score.weighted_score
        )
        # Allow the pre-set value but warn if mismatch
        if abs(self.final_score - computed) > 0.01:
            self.final_score = round(computed, 4)
        return self


# ---------------------------------------------------------------------------
# Researcher-facing diagnosis models
# ---------------------------------------------------------------------------

class ArtifactKind(str, Enum):
    IMAGE = "image"
    CAD = "cad"
    SENSOR = "sensor"
    LOG = "log"
    DOCUMENT = "document"
    NOTEBOOK = "notebook"
    OTHER = "other"


class ArtifactRole(str, Enum):
    SETUP_PHOTO = "setup_photo"
    RESULT_IMAGE = "result_image"
    CAD = "cad"
    SENSOR = "sensor"
    LOG = "log"
    MATERIAL_DOC = "material_doc"
    PROCESS_DOC = "process_doc"
    DATASHEET = "datasheet"
    OTHER = "other"


class ExperimentArtifact(BaseModel):
    """A file or extracted record from the experimental/engineering work."""

    id: str
    filename: str
    kind: ArtifactKind = ArtifactKind.OTHER
    role: ArtifactRole = ArtifactRole.OTHER
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0
    caption: str = ""
    extracted_text: str = ""
    summary: str = ""
    stats: dict[str, Any] = Field(default_factory=dict)
    preview_url: Optional[str] = None


class ExperimentInput(BaseModel):
    """Everything a researcher can dump about a failed experiment or build."""

    title: str = Field("Untitled experiment")
    domain: Optional[str] = None
    objective: str = ""
    unexpected_outcome: str = ""
    setup_description: str = ""
    materials: list[str] = Field(default_factory=list)
    processing: list[str] = Field(default_factory=list)
    protocol: list[str] = Field(default_factory=list)
    telemetry_notes: list[str] = Field(default_factory=list)
    contextual_clues: list[str] = Field(default_factory=list)
    extra_information: list[str] = Field(default_factory=list)
    artifacts: list[ExperimentArtifact] = Field(default_factory=list)

    def has_content(self) -> bool:
        return bool(
            self.objective.strip()
            or self.unexpected_outcome.strip()
            or self.setup_description.strip()
            or self.materials
            or self.processing
            or self.protocol
            or self.telemetry_notes
            or self.contextual_clues
            or self.artifacts
        )


class EvidenceItem(BaseModel):
    statement: str
    source: Literal[
        "objective", "protocol", "telemetry", "context", "tool", "researcher", "artifact"
    ] = "telemetry"
    polarity: Literal["supports", "contradicts", "missing", "neutral"] = "neutral"
    relevance: float = Field(0.5, ge=0, le=1)


class Anomaly(BaseModel):
    name: str
    description: str
    expected: Optional[str] = None
    observed: Optional[str] = None
    severity: float = Field(0.5, ge=0, le=1)
    source: str = "telemetry"


class Hypothesis(BaseModel):
    id: str
    statement: str
    category: Optional[str] = None
    score: float = Field(0.0, ge=0, le=1)
    prior: float = Field(0.2, ge=0, le=1)
    posterior: float = Field(0.0, ge=0, le=1)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    contradictory_evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    causal_chain: list[str] = Field(default_factory=list)
    status: Literal["active", "rejected", "confirmed"] = "active"
    rejected_reason: Optional[str] = None


class InterventionPlan(BaseModel):
    description: str
    target_hypothesis_id: str
    variables_changed: list[str] = Field(default_factory=list)
    predicted_if_true: str = ""
    predicted_if_false: str = ""
    diagnostic_power: float = Field(0.5, ge=0, le=1)
    feasibility: str = "routine lab or field procedure"


class UncertaintyReport(BaseModel):
    confidence: float = Field(0.0, ge=0, le=1)
    entropy: float = Field(0.0, ge=0)
    competing_count: int = 0
    note: str = ""
    missing_information: list[str] = Field(default_factory=list)


class Diagnosis(BaseModel):
    """Full epistemic diagnosis produced by the engine."""

    case_id: Optional[str] = None
    title: str = "Diagnosis"
    anomalies: list[Anomaly] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    leading_cause: str = ""
    leading_causal_chain: list[str] = Field(default_factory=list)
    uncertainty: UncertaintyReport = Field(default_factory=UncertaintyReport)
    recommended_intervention: Optional[InterventionPlan] = None
    alternative_interventions: list[InterventionPlan] = Field(default_factory=list)
    researcher_notes: list[str] = Field(default_factory=list)
    engine_mode: str = "heuristic"

    def to_model_response(self, model_name: str = "epidebug-engine") -> ModelResponse:
        leading = next((h for h in self.hypotheses if h.status == "active"), None)
        support = []
        contradict = []
        missing = list(self.uncertainty.missing_information)
        if leading:
            support = [e.statement for e in leading.supporting_evidence]
            contradict = [e.statement for e in leading.contradictory_evidence]
            missing = list(dict.fromkeys(missing + leading.missing_information))
        intervention = (
            self.recommended_intervention.description
            if self.recommended_intervention
            else ""
        )
        return ModelResponse(
            model_name=model_name,
            test_case_id=self.case_id or "FREEFORM",
            mode="text",
            root_cause=self.leading_cause or (leading.statement if leading else ""),
            causal_chain=self.leading_causal_chain or (leading.causal_chain if leading else ["No chain"]),
            intervention=intervention,
            confidence=self.uncertainty.confidence,
            competing_hypotheses=[h.statement for h in self.hypotheses if h.status == "active"],
            supporting_evidence=support,
            contradictory_evidence=contradict,
            missing_information=missing,
        )

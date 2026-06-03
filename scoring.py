"""
Multi-dimensional scoring engine for the Epistemic Debugging Benchmark.

Scores model responses on three components:
  1. Root Cause Identification (30%) — keyword + semantic matching
  2. Causal Chain (40%) — LLM-as-judge or heuristic evaluation
  3. Intervention Strategy (30%) — LLM-as-judge or heuristic evaluation

Also computes trajectory metrics (tool efficiency, anti-patterns) separately.
"""

from __future__ import annotations

import re
from typing import Optional

from epidebug.schema import (
    ComponentScore,
    ModelResponse,
    ScoreResult,
    TestCase,
    TrajectoryMetrics,
)


# ---------------------------------------------------------------------------
# Judge prompt templates (used by LLM-as-judge scoring)
# ---------------------------------------------------------------------------

CAUSAL_CHAIN_JUDGE_PROMPT = """You are an expert scientific evaluator. You are grading an AI model's
causal chain explanation for why a scientific experiment failed.

## Ground Truth Causal Chain
{ground_truth_chain}

## Model's Causal Chain
{model_chain}

## Evaluation Criteria
Score the model's causal chain from 0.0 to 1.0 on these four sub-criteria (0.25 each):

1. **Logical Ordering** (0-0.25): Are the causal steps in a logically valid sequence?
2. **Scientific Accuracy** (0-0.25): Are the intermediate mechanisms scientifically correct?
3. **Completeness** (0-0.25): Does the chain cover all critical intermediate steps?
4. **Symptom Connection** (0-0.25): Does the chain explain the observed experimental symptoms?

## Output Format
Respond with ONLY a JSON object:
{{"score": <float 0.0-1.0>, "rationale": "<brief explanation>"}}
"""

INTERVENTION_JUDGE_PROMPT = """You are an expert scientific evaluator. You are grading an AI model's
proposed intervention (counterfactual experiment) to confirm a failure diagnosis.

## Ground Truth Root Cause
{ground_truth_cause}

## Ground Truth Intervention
{ground_truth_intervention}

## Model's Proposed Intervention
{model_intervention}

## Evaluation Criteria
Score the model's intervention from 0.0 to 1.0 on these three sub-criteria:

1. **Counterfactual Validity** (0-0.33): Does the intervention change only the suspected variable?
2. **Diagnostic Power** (0-0.34): Would a positive result conclusively confirm the diagnosis?
3. **Feasibility** (0-0.33): Is the proposed experiment practically achievable?

## Output Format
Respond with ONLY a JSON object:
{{"score": <float 0.0-1.0>, "rationale": "<brief explanation>"}}
"""


# ---------------------------------------------------------------------------
# Root Cause Scorer (deterministic)
# ---------------------------------------------------------------------------

class RootCauseScorer:
    """Scores root cause identification via keyword matching and text overlap."""

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[^\w\s]", "", text.lower()).strip()

    @staticmethod
    def _token_overlap(text_a: str, text_b: str) -> float:
        tokens_a = set(RootCauseScorer._normalize(text_a).split())
        tokens_b = set(RootCauseScorer._normalize(text_b).split())
        stop = {"the", "a", "an", "is", "was", "were", "are", "in", "on", "at",
                "to", "for", "of", "and", "or", "but", "it", "its", "this", "that",
                "with", "from", "by", "as", "be", "has", "had", "have", "not", "no",
                "do", "did", "does", "will", "would", "can", "could", "may", "might"}
        tokens_a -= stop
        tokens_b -= stop
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)

    def score(self, test_case: TestCase, response: ModelResponse) -> ComponentScore:
        model_rc = self._normalize(response.root_cause)
        keywords = test_case.scoring.root_cause_keywords

        if keywords:
            matched = sum(1 for kw in keywords if kw.lower() in model_rc)
            keyword_score = matched / len(keywords)
        else:
            keyword_score = 0.0

        overlap_score = self._token_overlap(response.root_cause, test_case.ground_truth.root_cause)

        partial_score = 0.0
        if test_case.scoring.partial_credit_causes:
            for cause, credit in test_case.scoring.partial_credit_causes.items():
                if self._normalize(cause) in model_rc or self._token_overlap(cause, response.root_cause) > 0.3:
                    partial_score = max(partial_score, credit)

        if keywords:
            raw_score = max(keyword_score * 0.7 + overlap_score * 0.3, partial_score)
        else:
            raw_score = max(overlap_score, partial_score)

        if raw_score >= 0.6:
            final = 1.0
            rationale = "Root cause correctly identified — key terms match ground truth."
        elif raw_score >= 0.4:
            final = 0.75
            rationale = "Root cause partially identified — correct category but imprecise."
        elif raw_score >= 0.25:
            final = 0.5
            rationale = "Contributing factor mentioned but not the primary root cause."
        elif raw_score >= 0.1:
            final = 0.25
            rationale = "Related concept mentioned but root cause not identified."
        else:
            final = 0.0
            rationale = "Root cause not identified or completely incorrect."

        weight = test_case.scoring.weights.root_cause
        return ComponentScore(
            score=final, weight=weight,
            weighted_score=round(final * weight, 4), rationale=rationale,
        )


# ---------------------------------------------------------------------------
# Causal Chain Scorer (deterministic fallback)
# ---------------------------------------------------------------------------

class CausalChainScorer:

    @staticmethod
    def _step_similarity(model_step: str, gt_step: str) -> float:
        m_tokens = set(model_step.lower().split()) - {"the", "a", "an", "to", "of", "and", "in"}
        g_tokens = set(gt_step.lower().split()) - {"the", "a", "an", "to", "of", "and", "in"}
        if not m_tokens or not g_tokens:
            return 0.0
        return len(m_tokens & g_tokens) / max(len(m_tokens), len(g_tokens))

    def score(self, test_case: TestCase, response: ModelResponse) -> ComponentScore:
        gt_chain = test_case.ground_truth.causal_chain
        model_chain = response.causal_chain

        if not model_chain or (len(model_chain) == 1 and "no causal" in model_chain[0].lower()):
            weight = test_case.scoring.weights.causal_chain
            return ComponentScore(score=0.0, weight=weight, weighted_score=0.0,
                                  rationale="No causal chain provided.")

        step_scores = []
        for gt_step in gt_chain:
            best_match = max(self._step_similarity(ms, gt_step) for ms in model_chain) if model_chain else 0.0
            step_scores.append(best_match)

        coverage = sum(step_scores) / len(step_scores) if step_scores else 0.0
        length_ratio = min(len(model_chain) / len(gt_chain), 1.5)
        if length_ratio < 0.5:
            coverage *= 0.7

        if coverage >= 0.5:
            final = min(1.0, coverage * 1.5)
            rationale = "Causal chain covers most key mechanistic steps."
        elif coverage >= 0.3:
            final = 0.5
            rationale = "Causal chain captures some key steps but has significant gaps."
        elif coverage >= 0.15:
            final = 0.25
            rationale = "Causal chain has correct direction but misses most steps."
        else:
            final = 0.0
            rationale = "Causal chain does not match the expected failure mechanism."

        final = round(min(final, 1.0), 2)
        weight = test_case.scoring.weights.causal_chain
        return ComponentScore(score=final, weight=weight,
                              weighted_score=round(final * weight, 4), rationale=rationale)


# ---------------------------------------------------------------------------
# Intervention Scorer (deterministic fallback)
# ---------------------------------------------------------------------------

class InterventionScorer:

    def score(self, test_case: TestCase, response: ModelResponse) -> ComponentScore:
        model_intervention = response.intervention.lower()
        if not model_intervention.strip():
            weight = test_case.scoring.weights.intervention
            return ComponentScore(score=0.0, weight=weight, weighted_score=0.0,
                                  rationale="No intervention proposed.")

        gt_intervention = test_case.ground_truth.intervention.lower()
        gt_tokens = set(gt_intervention.split()) - {"the", "a", "an", "to", "of", "and", "in", "if", "is"}
        model_tokens = set(model_intervention.split()) - {"the", "a", "an", "to", "of", "and", "in", "if", "is"}
        overlap = len(gt_tokens & model_tokens) / len(gt_tokens) if gt_tokens else 0.0

        rc_tokens = set(test_case.ground_truth.root_cause.lower().split()) - {"the", "a", "an", "to", "of", "and", "in"}
        rc_mentioned = len(rc_tokens & model_tokens) / max(len(rc_tokens), 1)

        combined = overlap * 0.6 + rc_mentioned * 0.4
        if combined >= 0.4:
            final = min(1.0, combined * 2.0)
            rationale = "Intervention targets the correct variable and would test the diagnosis."
        elif combined >= 0.25:
            final = 0.5
            rationale = "Intervention is related but may not isolate the suspected variable."
        elif combined >= 0.1:
            final = 0.25
            rationale = "Intervention addresses the problem area but is too vague."
        else:
            final = 0.0
            rationale = "Intervention does not address the identified root cause."

        final = round(min(final, 1.0), 2)
        weight = test_case.scoring.weights.intervention
        return ComponentScore(score=final, weight=weight,
                              weighted_score=round(final * weight, 4), rationale=rationale)


# ---------------------------------------------------------------------------
# Trajectory Analyzer
# ---------------------------------------------------------------------------

class TrajectoryAnalyzer:

    @staticmethod
    def analyze(response: ModelResponse, test_case: TestCase) -> TrajectoryMetrics:
        tool_calls = response.tool_calls
        total = len(tool_calls)
        budget = test_case.max_tool_calls
        anti_patterns: list[str] = []

        if total == 0:
            return TrajectoryMetrics(tool_calls_made=0, tool_calls_budget=budget,
                                    tool_efficiency=None, budget_compliant=True)

        budget_ok = total <= budget
        if not budget_ok:
            anti_patterns.append(f"exceeded_budget ({total}/{budget})")

        seen_calls: list[str] = []
        redundant = 0
        for call in tool_calls:
            sig = f"{call.get('tool', '')}:{call.get('args', '')}"
            if sig in seen_calls:
                redundant += 1
            seen_calls.append(sig)
        if redundant > 0:
            anti_patterns.append(f"redundant_calls ({redundant})")

        tool_names = [call.get("tool", "") for call in tool_calls]
        if len(set(tool_names)) == 1 and total > 3:
            anti_patterns.append("single_tool_fixation")

        useful = total - redundant
        efficiency = useful / total if total > 0 else None

        return TrajectoryMetrics(
            tool_calls_made=total, tool_calls_budget=budget,
            tool_efficiency=round(efficiency, 3) if efficiency is not None else None,
            budget_compliant=budget_ok, anti_patterns=anti_patterns,
        )


# ---------------------------------------------------------------------------
# Main Scorer
# ---------------------------------------------------------------------------

class EpiDebugScorer:
    """
    Top-level scorer combining all sub-scorers.

    Modes:
      - score_deterministic(): keyword/heuristic only, no LLM needed
      - score_with_llm(): uses LLM judge for causal chain & intervention
    """

    def __init__(self):
        self.root_cause_scorer = RootCauseScorer()
        self.causal_chain_scorer = CausalChainScorer()
        self.intervention_scorer = InterventionScorer()
        self.trajectory_analyzer = TrajectoryAnalyzer()

    def score_deterministic(self, test_case: TestCase, response: ModelResponse) -> ScoreResult:
        rc = self.root_cause_scorer.score(test_case, response)
        cc = self.causal_chain_scorer.score(test_case, response)
        iv = self.intervention_scorer.score(test_case, response)
        traj = self.trajectory_analyzer.analyze(response, test_case)
        final = round(rc.weighted_score + cc.weighted_score + iv.weighted_score, 4)
        return ScoreResult(
            test_case_id=test_case.id, model_name=response.model_name, mode=response.mode,
            root_cause_score=rc, causal_chain_score=cc, intervention_score=iv,
            final_score=final, trajectory=traj,
        )

    def score_with_llm(
        self, test_case: TestCase, response: ModelResponse,
        judge_fn: Optional[callable] = None,
    ) -> ScoreResult:
        import json as json_module

        rc = self.root_cause_scorer.score(test_case, response)

        if judge_fn:
            try:
                cc_prompt = CAUSAL_CHAIN_JUDGE_PROMPT.format(
                    ground_truth_chain="\n".join(f"{i+1}. {s}" for i, s in enumerate(test_case.ground_truth.causal_chain)),
                    model_chain="\n".join(f"{i+1}. {s}" for i, s in enumerate(response.causal_chain)),
                )
                cc_raw = judge_fn(cc_prompt)
                json_match = re.search(r"\{.*\}", cc_raw, re.DOTALL)
                if json_match:
                    cc_result = json_module.loads(json_match.group())
                    cc_s = float(cc_result.get("score", 0))
                    cc_r = cc_result.get("rationale", "LLM judge evaluation.")
                else:
                    raise ValueError("No JSON in judge response")
                weight = test_case.scoring.weights.causal_chain
                cc = ComponentScore(score=round(cc_s, 2), weight=weight,
                                    weighted_score=round(cc_s * weight, 4), rationale=f"[LLM Judge] {cc_r}")
            except Exception:
                cc = self.causal_chain_scorer.score(test_case, response)
        else:
            cc = self.causal_chain_scorer.score(test_case, response)

        if judge_fn:
            try:
                iv_prompt = INTERVENTION_JUDGE_PROMPT.format(
                    ground_truth_cause=test_case.ground_truth.root_cause,
                    ground_truth_intervention=test_case.ground_truth.intervention,
                    model_intervention=response.intervention,
                )
                iv_raw = judge_fn(iv_prompt)
                json_match = re.search(r"\{.*\}", iv_raw, re.DOTALL)
                if json_match:
                    iv_result = json_module.loads(json_match.group())
                    iv_s = float(iv_result.get("score", 0))
                    iv_r = iv_result.get("rationale", "LLM judge evaluation.")
                else:
                    raise ValueError("No JSON in judge response")
                weight = test_case.scoring.weights.intervention
                iv = ComponentScore(score=round(iv_s, 2), weight=weight,
                                    weighted_score=round(iv_s * weight, 4), rationale=f"[LLM Judge] {iv_r}")
            except Exception:
                iv = self.intervention_scorer.score(test_case, response)
        else:
            iv = self.intervention_scorer.score(test_case, response)

        traj = self.trajectory_analyzer.analyze(response, test_case)
        final = round(rc.weighted_score + cc.weighted_score + iv.weighted_score, 4)
        return ScoreResult(
            test_case_id=test_case.id, model_name=response.model_name, mode=response.mode,
            root_cause_score=rc, causal_chain_score=cc, intervention_score=iv,
            final_score=final, trajectory=traj,
        )

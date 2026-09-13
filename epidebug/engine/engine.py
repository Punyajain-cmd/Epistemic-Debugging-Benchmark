"""Epistemic Debugging Engine.

Workflow:
    experiment information -> anomaly identification -> competing hypotheses
    -> evidence analysis -> causal chain -> uncertainty -> intervention

The engine stays in the researcher loop: hypotheses can be rejected, extra
information can be added, and follow-up outcomes can be fed back.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from epidebug.engine.anomalies import anomalies_from_case, anomalies_from_freeform
from epidebug.engine.ingest import artifact_blob
from epidebug.engine.patterns import FAILURE_PATTERNS
from epidebug.engine.session import DiagnosisSession, FollowUpRecord, SessionStore
from epidebug.llm import EngineLLM
from epidebug.schema import (
    Diagnosis,
    EvidenceItem,
    ExperimentInput,
    Hypothesis,
    InterventionPlan,
    TestCase,
    UncertaintyReport,
)
from epidebug.utils.trajectory import TrajectoryLogger

STOP = {
    "the", "a", "an", "is", "was", "were", "are", "in", "on", "at", "to", "for",
    "of", "and", "or", "but", "it", "its", "this", "that", "with", "from", "by",
    "as", "be", "has", "had", "have", "not", "no", "do", "did", "does", "will",
    "would", "can", "could", "may", "might", "into", "than", "then",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+\-]+", text.lower()) if t not in STOP and len(t) > 2}


def _blob_from_case(case: TestCase, extra: Iterable[str] = ()) -> str:
    parts = [case.objective]
    for step in case.protocol:
        parts.extend(filter(None, [step.action, step.details, step.notes]))
    for name, entry in case.visible_telemetry().items():
        parts.append(name)
        parts.append(entry.description)
        if entry.observations:
            parts.extend(entry.observations)
        if entry.note:
            parts.append(entry.note)
        if entry.values is not None:
            parts.append(str(entry.values))
    parts.extend(case.visible_clues())
    parts.extend(case.red_herrings)
    parts.extend(extra)
    return "\n".join(parts)


def _blob_from_experiment(exp: ExperimentInput, extra: Iterable[str] = ()) -> str:
    parts = [
        exp.title,
        exp.domain or "",
        f"Objective: {exp.objective}" if exp.objective else "",
        f"Unexpected outcome: {exp.unexpected_outcome}" if exp.unexpected_outcome else "",
        f"Setup: {exp.setup_description}" if exp.setup_description else "",
    ]
    if exp.materials:
        parts.append("Materials: " + "; ".join(exp.materials))
    if exp.processing:
        parts.append("Processing / how it was made: " + "; ".join(exp.processing))
    parts.extend(exp.protocol)
    parts.extend(exp.telemetry_notes)
    parts.extend(exp.contextual_clues)
    parts.extend(exp.extra_information)
    for artifact in exp.artifacts:
        parts.append(artifact_blob(artifact))
    parts.extend(extra)
    return "\n".join(p for p in parts if p)


def _softmax(scores: list[float], temperature: float = 0.35) -> list[float]:
    if not scores:
        return []
    scaled = [s / max(temperature, 1e-6) for s in scores]
    top = max(scaled)
    exps = [math.exp(s - top) for s in scaled]
    total = sum(exps) or 1.0
    return [e / total for e in exps]


def _entropy(probs: list[float]) -> float:
    return -sum(p * math.log(p + 1e-12) for p in probs if p > 0)


def _snippet_hits(blob: str, keywords: list[str], limit: int = 3) -> list[str]:
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip()]
    hits = []
    lowered_keys = [k.lower() for k in keywords]
    for line in lines:
        low = line.lower()
        if any(k in low for k in lowered_keys):
            hits.append(line[:240])
        if len(hits) >= limit:
            break
    return hits


class EpistemicDebuggingEngine:
    """Generate competing, evidence-grounded diagnoses of experimental failures."""

    def __init__(
        self,
        llm: EngineLLM | None = None,
        prefer_llm: bool = True,
        sessions: SessionStore | None = None,
        cases: dict[str, TestCase] | None = None,
    ):
        self.llm = llm or EngineLLM()
        self.prefer_llm = prefer_llm
        self.sessions = sessions or SessionStore()
        self._cases = cases

    def diagnose_case(
        self,
        case: TestCase,
        extra_information: list[str] | None = None,
        rejected: dict[str, str] | None = None,
        followups: list[FollowUpRecord] | None = None,
    ) -> Diagnosis:
        extra = list(extra_information or [])
        if followups:
            extra.extend(f"Follow-up: {item.intervention} -> {item.outcome}" for item in followups)
        blob = _blob_from_case(case, extra)
        anomalies = anomalies_from_case(case)
        diagnosis = self._diagnose_text(
            blob=blob,
            anomalies=anomalies,
            title=case.title,
            case_id=case.id,
            domain=case.domain.value,
            rejected=rejected or {},
        )
        return diagnosis

    def diagnose_experiment(
        self,
        experiment: ExperimentInput,
        extra_information: list[str] | None = None,
        rejected: dict[str, str] | None = None,
        followups: list[FollowUpRecord] | None = None,
    ) -> Diagnosis:
        extra = list(extra_information or []) + list(experiment.extra_information)
        if followups:
            extra.extend(f"Follow-up: {item.intervention} -> {item.outcome}" for item in followups)
        blob = _blob_from_experiment(experiment, extra)
        anomalies = anomalies_from_freeform(experiment)
        return self._diagnose_text(
            blob=blob,
            anomalies=anomalies,
            title=experiment.title,
            case_id=None,
            domain=experiment.domain,
            rejected=rejected or {},
        )

    def open_session(
        self,
        case: TestCase | None = None,
        experiment: ExperimentInput | None = None,
    ) -> DiagnosisSession:
        session = self.sessions.create(case=case, experiment=experiment)
        if case is not None:
            session.diagnosis = self.diagnose_case(case)
        elif experiment is not None:
            session.diagnosis = self.diagnose_experiment(experiment)
        else:
            raise ValueError("Provide a test case or a free-form experiment.")
        session.history.append({"event": "opened", "leading": session.diagnosis.leading_cause})
        return self.sessions.save(session)

    def reject_hypothesis(self, session_id: str, hypothesis_id: str, reason: str) -> DiagnosisSession:
        session = self.sessions.get(session_id)
        session.rejected[hypothesis_id] = reason
        session.history.append({"event": "reject", "hypothesis_id": hypothesis_id, "reason": reason})
        return self._rediagnose(session)

    def add_information(self, session_id: str, information: str) -> DiagnosisSession:
        session = self.sessions.get(session_id)
        session.extra_information.append(information)
        if session.experiment is not None:
            session.experiment.extra_information.append(information)
        session.history.append({"event": "add_information", "information": information})
        return self._rediagnose(session)

    def add_artifacts(self, session_id: str, artifacts: list) -> DiagnosisSession:
        session = self.sessions.get(session_id)
        if session.experiment is None:
            from epidebug.schema import ExperimentInput

            session.experiment = ExperimentInput(title=session.title, unexpected_outcome="Additional evidence attached.")
        session.experiment.artifacts.extend(artifacts)
        session.history.append({
            "event": "add_artifacts",
            "files": [a.filename for a in artifacts],
        })
        return self._rediagnose(session)

    def record_followup(self, session_id: str, intervention: str, outcome: str) -> DiagnosisSession:
        session = self.sessions.get(session_id)
        session.followups.append(
            FollowUpRecord(
                intervention=intervention,
                outcome=outcome,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        session.history.append({"event": "followup", "intervention": intervention, "outcome": outcome})
        return self._rediagnose(session)

    def _case_by_id(self, case_id: str) -> TestCase:
        if self._cases is None:
            root = Path(__file__).resolve().parents[2] / "test_cases"
            self._cases = {c.id: c for c in TestCase.load_all(root)}
        case = self._cases.get(case_id)
        if case is None:
            raise KeyError(case_id)
        return case

    def _extras_from_session(self, session: DiagnosisSession) -> list[str]:
        extra = list(session.extra_information)
        if session.experiment is None:
            return extra
        # Catalog sessions can grow an experiment sidecar when the researcher
        # attaches files or notes. Fold that evidence into the re-rank.
        exp = session.experiment
        if exp.unexpected_outcome and exp.unexpected_outcome != "Additional evidence attached.":
            extra.append(f"Unexpected outcome: {exp.unexpected_outcome}")
        extra.extend(exp.telemetry_notes)
        extra.extend(exp.contextual_clues)
        extra.extend(artifact_blob(artifact) for artifact in exp.artifacts)
        return extra

    def _rediagnose(self, session: DiagnosisSession) -> DiagnosisSession:
        extra = self._extras_from_session(session)
        if session.case_id:
            case = self._case_by_id(session.case_id)
            session.diagnosis = self.diagnose_case(
                case,
                extra_information=extra,
                rejected=session.rejected,
                followups=session.followups,
            )
        elif session.experiment is not None:
            session.diagnosis = self.diagnose_experiment(
                session.experiment,
                extra_information=session.extra_information,
                rejected=session.rejected,
                followups=session.followups,
            )
        return self.sessions.save(session)

    def _diagnose_text(
        self,
        blob: str,
        anomalies: list,
        title: str,
        case_id: str | None,
        domain: str | None,
        rejected: dict[str, str],
    ) -> Diagnosis:
        logger = TrajectoryLogger(case_id=case_id or "FREEFORM")
        llm_diagnosis = None
        if self.prefer_llm and self.llm.available:
            llm_diagnosis = self._diagnose_with_llm(blob, title, domain)
            logger.log_call("llm_complete_json", {"title": title})
        if llm_diagnosis is not None:
            diagnosis = llm_diagnosis
            diagnosis.case_id = case_id
            diagnosis.title = title
            diagnosis.anomalies = anomalies or diagnosis.anomalies
            diagnosis.engine_mode = "llm"
        else:
            diagnosis = self._diagnose_heuristic(blob, anomalies, title, case_id, domain)
            diagnosis.engine_mode = "heuristic"

        for hyp in diagnosis.hypotheses:
            if hyp.id in rejected:
                hyp.status = "rejected"
                hyp.rejected_reason = rejected[hyp.id]
                hyp.posterior = 0.0
                hyp.score = 0.0

        active = [h for h in diagnosis.hypotheses if h.status == "active"]
        if not active:
            active = diagnosis.hypotheses
        posts = _softmax([max(h.score, 0.05) for h in active])
        for hyp, post in zip(active, posts):
            hyp.posterior = round(post, 4)
        active.sort(key=lambda h: h.posterior, reverse=True)
        rejected_hyps = [h for h in diagnosis.hypotheses if h.status == "rejected"]
        diagnosis.hypotheses = active + rejected_hyps

        leading = active[0] if active else None
        if leading:
            diagnosis.leading_cause = leading.statement
            diagnosis.leading_causal_chain = leading.causal_chain
            diagnosis.recommended_intervention = InterventionPlan(
                description=self._intervention_for(leading),
                target_hypothesis_id=leading.id,
                variables_changed=list(self._pattern_by_id(leading.id).get("variables", [])),
                predicted_if_true=self._pattern_by_id(leading.id).get("if_true", ""),
                predicted_if_false=self._pattern_by_id(leading.id).get("if_false", ""),
                diagnostic_power=round(min(1.0, 0.45 + leading.posterior), 3),
            )
            diagnosis.alternative_interventions = [
                InterventionPlan(
                    description=self._intervention_for(h),
                    target_hypothesis_id=h.id,
                    diagnostic_power=round(0.3 + 0.4 * h.posterior, 3),
                )
                for h in active[1:3]
            ]
            missing = list(dict.fromkeys(
                leading.missing_information
                + [m for h in active[1:3] for m in h.missing_information]
            ))
            diagnosis.uncertainty = UncertaintyReport(
                confidence=round(leading.posterior, 3),
                entropy=round(_entropy(posts), 3),
                competing_count=len(active),
                note=self._uncertainty_note(leading.posterior, len(active), anomalies),
                missing_information=missing[:6],
            )
        diagnosis.researcher_notes = [
            f"Logged {len(logger.calls)} investigation step(s).",
        ]
        return diagnosis

    def _diagnose_heuristic(
        self,
        blob: str,
        anomalies: list,
        title: str,
        case_id: str | None,
        domain: str | None,
    ) -> Diagnosis:
        text_tokens = _tokens(blob)
        scored: list[Hypothesis] = []
        for pattern in FAILURE_PATTERNS:
            keys = [k.lower() for k in pattern["keywords"]]
            key_tokens = _tokens(" ".join(keys))
            overlap = len(text_tokens & key_tokens) / max(len(key_tokens), 1)
            phrase_hits = sum(1 for k in keys if k in blob.lower()) / max(len(keys), 1)
            domain_bonus = 0.12 if domain and domain in pattern.get("domains", []) else 0.0
            neg = sum(1 for n in pattern.get("negative", []) if n.lower() in blob.lower())
            raw = 0.55 * phrase_hits + 0.35 * overlap + domain_bonus - 0.15 * neg
            if anomalies:
                raw += 0.08 * min(len(anomalies), 3) / 3
            raw = max(0.0, min(1.0, raw))
            if raw < 0.12:
                continue
            support_lines = _snippet_hits(blob, pattern["keywords"])
            contradict_lines = _snippet_hits(blob, pattern.get("negative", []))
            cues = [k for k in keys if k in blob.lower()][:5]
            statement = pattern["statement"]
            if cues:
                statement = f"{statement}. Observed cues: {', '.join(cues)}."
            scored.append(Hypothesis(
                id=pattern["id"],
                statement=statement,
                category=pattern["category"],
                score=round(raw, 4),
                prior=0.2,
                supporting_evidence=[
                    EvidenceItem(statement=line, source="telemetry", polarity="supports", relevance=0.7)
                    for line in support_lines
                ],
                contradictory_evidence=[
                    EvidenceItem(statement=line, source="context", polarity="contradicts", relevance=0.6)
                    for line in contradict_lines
                ],
                missing_information=list(pattern.get("missing", [])),
                causal_chain=list(pattern["causal_chain"]),
            ))
        scored.sort(key=lambda h: h.score, reverse=True)
        hypotheses = scored[:6] or [
            Hypothesis(
                id="unspecified",
                statement="The failure is underdetermined from the supplied evidence.",
                score=0.2,
                missing_information=["additional telemetry", "a successful historical control"],
                causal_chain=["Observed outcome diverged from the objective", "Available evidence does not isolate a single cause"],
            )
        ]
        return Diagnosis(
            case_id=case_id,
            title=title,
            anomalies=anomalies,
            hypotheses=hypotheses,
            leading_cause=hypotheses[0].statement,
            leading_causal_chain=hypotheses[0].causal_chain,
        )

    def _diagnose_with_llm(self, blob: str, title: str, domain: str | None) -> Diagnosis | None:
        system = (
            "You are an epistemic debugging engine. Distinguish observations, "
            "inferences, competing hypotheses, supporting evidence, contradictory "
            "evidence, and missing information. Do not collapse to a single story "
            "if the evidence is incomplete. Return ONLY JSON."
        )
        prompt = f"""Diagnose this failed experiment.

Title: {title}
Domain: {domain or "unspecified"}

Evidence:
{blob[:8000]}

Return JSON with this shape:
{{
  "leading_cause": str,
  "hypotheses": [
    {{
      "id": str,
      "statement": str,
      "category": str,
      "score": float,
      "causal_chain": [str],
      "supporting_evidence": [str],
      "contradictory_evidence": [str],
      "missing_information": [str]
    }}
  ],
  "intervention": {{
    "description": str,
    "predicted_if_true": str,
    "predicted_if_false": str
  }},
  "confidence": float
}}
Generate 3-5 competing hypotheses. Rank them.
"""
        data = self.llm.complete_json(prompt, system)
        if not data:
            return None
        hyps = []
        for i, item in enumerate(data.get("hypotheses") or []):
            hyps.append(Hypothesis(
                id=str(item.get("id") or f"h{i+1}"),
                statement=item.get("statement") or "",
                category=item.get("category"),
                score=float(item.get("score") or 0.3),
                causal_chain=list(item.get("causal_chain") or []),
                supporting_evidence=[
                    EvidenceItem(statement=s, polarity="supports")
                    for s in item.get("supporting_evidence") or []
                ],
                contradictory_evidence=[
                    EvidenceItem(statement=s, polarity="contradicts")
                    for s in item.get("contradictory_evidence") or []
                ],
                missing_information=list(item.get("missing_information") or []),
            ))
        if not hyps:
            return None
        intervention = data.get("intervention") or {}
        return Diagnosis(
            title=title,
            hypotheses=hyps,
            leading_cause=data.get("leading_cause") or hyps[0].statement,
            leading_causal_chain=hyps[0].causal_chain,
            recommended_intervention=InterventionPlan(
                description=intervention.get("description") or "Run a discriminating follow-up experiment.",
                target_hypothesis_id=hyps[0].id,
                predicted_if_true=intervention.get("predicted_if_true") or "",
                predicted_if_false=intervention.get("predicted_if_false") or "",
            ),
            uncertainty=UncertaintyReport(
                confidence=float(data.get("confidence") or hyps[0].score),
                competing_count=len(hyps),
            ),
            engine_mode="llm",
        )

    @staticmethod
    def _pattern_by_id(pattern_id: str) -> dict:
        for pattern in FAILURE_PATTERNS:
            if pattern["id"] == pattern_id:
                return pattern
        return {}

    def _intervention_for(self, hyp: Hypothesis) -> str:
        pattern = self._pattern_by_id(hyp.id)
        return pattern.get("intervention") or (
            f"Design a follow-up that changes only the factor in: {hyp.statement}"
        )

    @staticmethod
    def _uncertainty_note(confidence: float, n: int, anomalies: list) -> str:
        if confidence >= 0.7 and n <= 2:
            return "One hypothesis is strongly preferred, but a discriminating follow-up is still warranted."
        if confidence < 0.45 or n >= 4:
            return "Several explanations remain consistent with the evidence; gather the missing measurements before committing."
        if not anomalies:
            return "Anomalies are weak; treat the ranking as provisional."
        return "Top hypothesis is ahead, but competing accounts have not been ruled out."

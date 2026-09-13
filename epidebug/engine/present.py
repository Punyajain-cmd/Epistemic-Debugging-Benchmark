"""UI-oriented view of a diagnosis session.

The raw session JSON stays stable for the existing prototype. The additive
``view`` object is a flatter rendering contract for a redesigned frontend:
ranked hypotheses, evidence strings, artifact gallery, and timeline.
"""

from __future__ import annotations

from typing import Any

from epidebug.engine.session import DiagnosisSession
from epidebug.schema import Diagnosis, ExperimentInput


def session_payload(session: DiagnosisSession) -> dict[str, Any]:
    """Serialize a session plus an additive ``view`` block for the web UI."""
    payload = session.model_dump(mode="json")
    payload["view"] = build_view(session)
    return payload


def build_view(session: DiagnosisSession) -> dict[str, Any]:
    diagnosis = session.diagnosis or Diagnosis()
    experiment = session.experiment
    uncertainty = diagnosis.uncertainty
    confidence = float(uncertainty.confidence or 0.0)
    intervention = diagnosis.recommended_intervention
    artifacts = _artifact_cards(experiment)

    active = [h for h in diagnosis.hypotheses if h.status == "active"]
    return {
        "session_id": session.session_id,
        "title": session.title,
        "case_id": session.case_id,
        "created_at": session.created_at,
        "engine_mode": diagnosis.engine_mode,
        "file_count": len(artifacts),
        "confidence": round(confidence, 3),
        "confidence_pct": int(round(confidence * 100)),
        "entropy": round(float(uncertainty.entropy or 0.0), 3),
        "competing_count": uncertainty.competing_count or len(active),
        "uncertainty_note": uncertainty.note,
        "lead": {
            "cause": diagnosis.leading_cause,
            "chain": list(diagnosis.leading_causal_chain),
            "intervention": {
                "description": intervention.description if intervention else "",
                "predicted_if_true": intervention.predicted_if_true if intervention else "",
                "predicted_if_false": intervention.predicted_if_false if intervention else "",
                "diagnostic_power": intervention.diagnostic_power if intervention else None,
                "target_hypothesis_id": intervention.target_hypothesis_id if intervention else None,
            },
            "alternatives": [
                {
                    "description": plan.description,
                    "target_hypothesis_id": plan.target_hypothesis_id,
                    "diagnostic_power": plan.diagnostic_power,
                }
                for plan in diagnosis.alternative_interventions
            ],
        },
        "anomalies": [
            {
                "name": item.name,
                "description": item.description,
                "severity": item.severity,
                "source": item.source,
                "expected": item.expected,
                "observed": item.observed,
            }
            for item in diagnosis.anomalies
        ],
        "missing": list(uncertainty.missing_information),
        "hypotheses": [_hypothesis_card(h) for h in diagnosis.hypotheses],
        "artifacts": artifacts,
        "history": list(session.history),
        "followups": [item.model_dump(mode="json") for item in session.followups],
        "rejected": dict(session.rejected),
    }


def _hypothesis_card(hyp) -> dict[str, Any]:
    posterior = float(hyp.posterior or 0.0)
    return {
        "id": hyp.id,
        "statement": hyp.statement,
        "category": hyp.category,
        "status": hyp.status,
        "score": hyp.score,
        "posterior": round(posterior, 4),
        "posterior_pct": int(round(posterior * 100)),
        "rejected_reason": hyp.rejected_reason,
        "supporting": [e.statement for e in hyp.supporting_evidence],
        "contradicting": [e.statement for e in hyp.contradictory_evidence],
        "missing": list(hyp.missing_information),
        "chain": list(hyp.causal_chain),
    }


def _artifact_cards(experiment: ExperimentInput | None) -> list[dict[str, Any]]:
    if experiment is None:
        return []
    cards = []
    for art in experiment.artifacts:
        cards.append(
            {
                "id": art.id,
                "filename": art.filename,
                "kind": art.kind.value if hasattr(art.kind, "value") else art.kind,
                "role": art.role.value if hasattr(art.role, "value") else art.role,
                "caption": art.caption,
                "summary": art.summary,
                "preview_url": art.preview_url,
                "size_bytes": art.size_bytes,
            }
        )
    return cards

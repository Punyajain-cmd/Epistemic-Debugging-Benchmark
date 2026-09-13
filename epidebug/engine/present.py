"""UI-oriented view of a diagnosis session.

The raw session JSON stays stable for the existing prototype. The additive
``view`` object is a flatter rendering contract for a redesigned frontend:
ranked hypotheses, evidence strings, artifact gallery, ingest summaries,
and a dump-completeness checklist.
"""

from __future__ import annotations

from typing import Any

from epidebug.engine.session import DiagnosisSession
from epidebug.schema import ArtifactKind, ArtifactRole, Diagnosis, ExperimentInput

VIEW_CONTRACT = "0.5"

# Slots a complete machining / robotics / battery / lab dump should cover.
EVIDENCE_SLOTS: list[dict[str, str]] = [
    {
        "id": "failure",
        "label": "What went wrong",
        "hint": "Unexpected outcome, scrap, vent, crash, or off-spec measurement",
    },
    {
        "id": "objective",
        "label": "Intent / objective",
        "hint": "What the run, cut, charge, or assay was supposed to produce",
    },
    {
        "id": "materials",
        "label": "Materials / lots",
        "hint": "Alloy, cell chemistry, reagent lot, CoA, SDS, mill cert",
    },
    {
        "id": "process",
        "label": "Process / protocol",
        "hint": "Feeds and speeds, charge recipe, SOP, traveler, G-code",
    },
    {
        "id": "setup",
        "label": "Setup description",
        "hint": "Fixture, CAD revision, sensors, room conditions",
    },
    {
        "id": "cad",
        "label": "CAD / drawing",
        "hint": "STEP, STL, DXF, IGES, or drawing PDF",
    },
    {
        "id": "sensors",
        "label": "Sensor / table data",
        "hint": "CSV/JSON thermistor, current, IMU, scope, or cycle table",
    },
    {
        "id": "logs",
        "label": "Machine / console logs",
        "hint": "CNC alarms, charger console, robot / ROS, serial",
    },
    {
        "id": "photos",
        "label": "Setup or result photos",
        "hint": "Bench, fixture, failed part, gel, burn, vent",
    },
    {
        "id": "context",
        "label": "Background / context",
        "hint": "New operator, humidity, reused tool, leftover bottle",
    },
]


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
    ingest = build_ingest_view(
        experiment,
        catalog=bool(session.case_id and experiment is None),
    )

    active = [h for h in diagnosis.hypotheses if h.status == "active"]
    return {
        "contract_version": VIEW_CONTRACT,
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
        "gallery": ingest["gallery"],
        "ingest": ingest,
        "history": list(session.history),
        "followups": [item.model_dump(mode="json") for item in session.followups],
        "rejected": dict(session.rejected),
    }


def build_ingest_view(
    experiment: ExperimentInput | None,
    *,
    catalog: bool = False,
) -> dict[str, Any]:
    if experiment is None and catalog:
        return {
            "file_count": 0,
            "note_fields": _note_presence(None),
            "role_counts": {},
            "kind_counts": {},
            "summaries": [],
            "coverage": [],
            "missing": [],
            "completeness": None,
            "source": "catalog",
            "gallery": _gallery([]),
        }
    cards = _artifact_cards(experiment)
    coverage = coverage_from_experiment(experiment)
    present_slots = [slot for slot in coverage if slot["present"]]
    missing_slots = [slot for slot in coverage if not slot["present"]]
    role_counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    for card in cards:
        role_counts[card["role"]] = role_counts.get(card["role"], 0) + 1
        kind_counts[card["kind"]] = kind_counts.get(card["kind"], 0) + 1
    notes = _note_presence(experiment)
    return {
        "file_count": len(cards),
        "note_fields": notes,
        "role_counts": role_counts,
        "kind_counts": kind_counts,
        "summaries": [
            {
                "id": card["id"],
                "filename": card["filename"],
                "kind": card["kind"],
                "role": card["role"],
                "summary": card["summary"],
            }
            for card in cards
        ],
        "coverage": coverage,
        "missing": [slot["label"] for slot in missing_slots],
        "completeness": round(len(present_slots) / max(len(coverage), 1), 3),
        "source": "dump",
        "gallery": _gallery(cards),
    }


def coverage_from_experiment(experiment: ExperimentInput | None) -> list[dict[str, Any]]:
    notes = _note_presence(experiment)
    arts = list(experiment.artifacts) if experiment else []
    kinds = {a.kind.value if hasattr(a.kind, "value") else a.kind for a in arts}
    roles = {a.role.value if hasattr(a.role, "value") else a.role for a in arts}

    present_map = {
        "failure": notes["unexpected_outcome"],
        "objective": notes["objective"],
        "materials": notes["materials"] or bool(roles & {ArtifactRole.MATERIAL_DOC.value, ArtifactRole.DATASHEET.value}),
        "process": notes["process"] or ArtifactRole.PROCESS_DOC.value in roles,
        "setup": notes["setup"],
        "cad": ArtifactKind.CAD.value in kinds or ArtifactRole.CAD.value in roles,
        "sensors": notes["telemetry"] or ArtifactKind.SENSOR.value in kinds or ArtifactRole.SENSOR.value in roles,
        "logs": ArtifactKind.LOG.value in kinds or ArtifactRole.LOG.value in roles,
        "photos": ArtifactKind.IMAGE.value in kinds,
        "context": notes["context"],
    }
    return [
        {
            "id": slot["id"],
            "label": slot["label"],
            "hint": slot["hint"],
            "present": bool(present_map.get(slot["id"], False)),
        }
        for slot in EVIDENCE_SLOTS
    ]


def _note_presence(experiment: ExperimentInput | None) -> dict[str, bool]:
    if experiment is None:
        return {
            "unexpected_outcome": False,
            "objective": False,
            "materials": False,
            "process": False,
            "setup": False,
            "telemetry": False,
            "context": False,
        }
    return {
        "unexpected_outcome": bool((experiment.unexpected_outcome or "").strip()),
        "objective": bool((experiment.objective or "").strip()),
        "materials": bool(experiment.materials),
        "process": bool(experiment.processing or experiment.protocol),
        "setup": bool((experiment.setup_description or "").strip()),
        "telemetry": bool(experiment.telemetry_notes),
        "context": bool(experiment.contextual_clues),
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


def _enum_val(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def _artifact_flags(art) -> list[str]:
    stats = art.stats or {}
    flags: list[str] = []
    for key in ("flagged", "flags", "alarm_codes"):
        raw = stats.get(key) or []
        if isinstance(raw, list):
            flags.extend(str(item) for item in raw[:4])
    severity = stats.get("severity") or {}
    if isinstance(severity, dict) and severity.get("error"):
        flags.append(f"{severity['error']} error/fault lines")
    return flags[:6]


def _artifact_cards(experiment: ExperimentInput | None) -> list[dict[str, Any]]:
    if experiment is None:
        return []
    cards = []
    for art in experiment.artifacts:
        extracted = art.extracted_text or ""
        cards.append(
            {
                "id": art.id,
                "filename": art.filename,
                "kind": _enum_val(art.kind),
                "role": _enum_val(art.role),
                "suggested_role": _enum_val(art.suggested_role) if art.suggested_role else _enum_val(art.role),
                "caption": art.caption,
                "summary": art.summary,
                "preview_url": art.preview_url,
                "size_bytes": art.size_bytes,
                "mime_type": art.mime_type,
                "stats": art.stats or {},
                "extracted_preview": extracted[:360],
                "flags": _artifact_flags(art),
            }
        )
    return cards


def _gallery(cards: list[dict[str, Any]]) -> dict[str, Any]:
    by_role: dict[str, list[str]] = {}
    by_kind: dict[str, list[str]] = {}
    groups = {
        "images": [],
        "tables": [],
        "logs": [],
        "cad": [],
        "documents": [],
        "other": [],
    }
    group_for = {
        "image": "images",
        "sensor": "tables",
        "log": "logs",
        "cad": "cad",
        "document": "documents",
        "notebook": "documents",
    }
    for card in cards:
        by_role.setdefault(card["role"], []).append(card["id"])
        by_kind.setdefault(card["kind"], []).append(card["id"])
        groups[group_for.get(card["kind"], "other")].append(card)
    return {
        "by_role": by_role,
        "by_kind": by_kind,
        **groups,
    }

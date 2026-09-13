"""Anomaly identification from protocol, telemetry, and context."""

from __future__ import annotations

from typing import Iterable

from epidebug.schema import Anomaly, ExperimentInput, TestCase

FAILURE_WORDS = (
    "fail", "faint", "unexpected", "low yield", "no band", "error", "drift",
    "leak", "skew", "artifact", "false", "below", "poor", "noisy", "bimodal",
    "overheat", "hemolys", "carryover", "alias", "contamination", "did not bind",
)


def _severity_from_range(values, expected: dict | None) -> float:
    if not expected or not values:
        return 0.4
    numeric = []
    for item in values if isinstance(values, list) else [values]:
        if isinstance(item, (int, float)):
            numeric.append(float(item))
    if not numeric:
        return 0.4
    lo, hi = expected.get("min"), expected.get("max")
    mean = sum(numeric) / len(numeric)
    if lo is not None and mean < lo:
        span = max(abs(lo), 1e-9)
        return min(1.0, 0.55 + abs(mean - lo) / span)
    if hi is not None and mean > hi:
        span = max(abs(hi), 1e-9)
        return min(1.0, 0.55 + abs(mean - hi) / span)
    return 0.15


def anomalies_from_case(case: TestCase) -> list[Anomaly]:
    found: list[Anomaly] = []
    for name, entry in case.visible_telemetry().items():
        expected = entry.expected_range
        sev = _severity_from_range(entry.values, expected)
        observed = None
        if entry.values is not None:
            observed = str(entry.values)
        elif entry.observations:
            observed = "; ".join(entry.observations[:3])
        text = " ".join(
            [entry.description, entry.note or "", " ".join(entry.observations or [])]
        ).lower()
        if expected and sev >= 0.5:
            found.append(Anomaly(
                name=name,
                description=f"{entry.description} is outside the expected range.",
                expected=str(expected),
                observed=observed,
                severity=round(sev, 3),
                source="telemetry",
            ))
        elif any(word in text for word in FAILURE_WORDS):
            found.append(Anomaly(
                name=name,
                description=entry.note or (entry.observations[0] if entry.observations else entry.description),
                expected=str(expected) if expected else None,
                observed=observed,
                severity=0.7,
                source="telemetry",
            ))
    for step in case.protocol:
        blob = " ".join(filter(None, [step.action, step.details, step.notes])).lower()
        if any(tok in blob for tok in ("not ", "skip", "forgot", "omitted", "without", "old", "weeks ago")):
            found.append(Anomaly(
                name=f"protocol_step_{step.step}",
                description=step.notes or step.details or step.action,
                severity=0.55,
                source="protocol",
            ))
    return _dedupe(found)


def anomalies_from_freeform(experiment: ExperimentInput) -> list[Anomaly]:
    found: list[Anomaly] = []
    for i, note in enumerate(experiment.telemetry_notes):
        text = note.lower()
        if any(word in text for word in FAILURE_WORDS):
            found.append(Anomaly(
                name=f"observation_{i+1}",
                description=note,
                severity=0.65,
                source="telemetry",
            ))
    for i, step in enumerate(experiment.protocol + experiment.processing):
        if any(tok in step.lower() for tok in ("not ", "skip", "forgot", "without", "old", "hand-tight", "eyeball")):
            found.append(Anomaly(
                name=f"protocol_{i+1}",
                description=step,
                severity=0.5,
                source="protocol",
            ))
    if experiment.unexpected_outcome:
        found.append(Anomaly(
            name="stated_failure",
            description=experiment.unexpected_outcome,
            severity=0.8,
            source="objective",
        ))
    for artifact in experiment.artifacts:
        flags = artifact.stats.get("flags") or artifact.stats.get("flagged") or []
        if flags:
            found.append(Anomaly(
                name=f"artifact_{artifact.id}",
                description=f"{artifact.filename}: {'; '.join(str(f) for f in flags[:3])}",
                severity=0.7,
                source="artifact",
            ))
        elif any(w in (artifact.filename + artifact.summary + artifact.caption).lower()
                 for w in ("crack", "fail", "burn", "leak", "corrosion", "overheat", "error")):
            found.append(Anomaly(
                name=f"artifact_{artifact.id}",
                description=artifact.summary or artifact.filename,
                severity=0.55,
                source="artifact",
            ))
    if not found:
        found.append(Anomaly(
            name="unexpected_outcome",
            description="The experiment did not match the stated objective.",
            severity=0.4,
            source="objective",
        ))
    return _dedupe(found)


def _dedupe(items: Iterable[Anomaly]) -> list[Anomaly]:
    seen: set[str] = set()
    out: list[Anomaly] = []
    for item in items:
        key = item.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out

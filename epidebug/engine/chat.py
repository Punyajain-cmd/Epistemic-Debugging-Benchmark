"""Turn-by-turn conversational diagnostic loop.

Heuristic replies work with no API keys. When a key is present the engine
prefers an LLM for the assistant turn and for ranking hypotheses.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from epidebug.engine.present import EVIDENCE_SLOTS, coverage_from_experiment
from epidebug.engine.session import ChatMessage, DiagnosisSession
from epidebug.schema import ExperimentInput

FAILURE_CUES = (
    "fail", "failed", "wrong", "vent", "crash", "undersize", "yield", "error",
    "broke", "leak", "scrap", "off-spec", "unexpected", "alarm", "overheat",
    "drift", "contaminat", "collapse", "smear", "crack", "short", "fault",
)

GREETINGS = {
    "hi", "hello", "hey", "help", "start", "yo", "sup", "good morning",
    "good afternoon", "good evening",
}

DIAGNOSE_CUES = (
    "diagnose", "re-diagnose", "rediagnose", "rank", "hypothes",
    "what's wrong", "whats wrong", "what is wrong", "run the engine",
    "figure it out", "root cause", "competing cause",
)

SLOT_PROMPTS = {slot["id"]: slot for slot in EVIDENCE_SLOTS}

CHAT_SYSTEM = (
    "You are EpiDebug, a conversational epistemic debugger for failed "
    "experiments and builds. Talk turn-by-turn with the researcher. Do not "
    "dump a single essay unless they asked for a full diagnosis. Ask "
    "clarifying questions and request missing evidence slots (photos, CAD, "
    "sensors, logs, materials, process, setup, context). When there is "
    "enough to rank competing causes, set run_diagnosis true so the "
    "heuristic/LLM engine can refresh hypotheses. Do not invent "
    "measurements, files, or web results. Do not scrape the internet. Do "
    "not claim you trained a model. Keep competing hypotheses alive; do "
    "not collapse to one story if evidence is incomplete. Return ONLY JSON "
    "with this shape:\n"
    "{\n"
    '  "reply": "conversational assistant text",\n'
    '  "request_slots": ["sensors", "logs"],\n'
    '  "run_diagnosis": false,\n'
    '  "absorb_fields": {\n'
    '    "unexpected_outcome": "",\n'
    '    "objective": "",\n'
    '    "setup_description": "",\n'
    '    "materials": "",\n'
    '    "processing": "",\n'
    '    "telemetry": "",\n'
    '    "logs": "",\n'
    '    "context": "",\n'
    '    "title": ""\n'
    "  }\n"
    "}\n"
    "Leave absorb_fields keys empty unless the latest user turn clearly "
    "supplied that information.\n"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_message(
    role: str,
    content: str,
    *,
    kind: str = "message",
    slots_requested: list[str] | None = None,
    diagnosed: bool = False,
    mode: str | None = None,
) -> ChatMessage:
    return ChatMessage(
        role=role,  # type: ignore[arg-type]
        content=content,
        timestamp=utc_now(),
        kind=kind,
        slots_requested=list(slots_requested or []),
        diagnosed=diagnosed,
        mode=mode,
    )


def _split_lines(value: str) -> list[str]:
    return [ln.strip() for ln in value.replace("\r", "").split("\n") if ln.strip()]


def _title_from(message: str) -> str:
    line = message.strip().splitlines()[0] if message.strip() else "Untitled session"
    line = re.sub(r"\s+", " ", line)
    return (line[:72] + "…") if len(line) > 72 else (line or "Untitled session")


def _looks_like_failure(text: str) -> bool:
    low = text.lower()
    if len(text.strip()) < 12:
        return False
    if low.strip() in GREETINGS:
        return False
    if any(cue in low for cue in FAILURE_CUES):
        return True
    return len(text.strip()) >= 80


def _wants_diagnosis(text: str) -> bool:
    low = text.lower()
    return any(cue in low for cue in DIAGNOSE_CUES)


def missing_slots(session: DiagnosisSession) -> list[dict[str, str]]:
    coverage = coverage_from_experiment(session.experiment)
    return [slot for slot in coverage if not slot["present"]]


def present_slot_ids(session: DiagnosisSession) -> list[str]:
    return [slot["id"] for slot in coverage_from_experiment(session.experiment) if slot["present"]]


def evidence_ready(session: DiagnosisSession) -> bool:
    present = set(present_slot_ids(session))
    files = bool(session.experiment and session.experiment.artifacts)
    if session.case_id:
        return True
    if "failure" in present and (files or len(present) >= 3):
        return True
    if "failure" in present and (
        "process" in present or "materials" in present or "sensors" in present
    ):
        return True
    return False


def ensure_experiment(session: DiagnosisSession, message: str) -> ExperimentInput:
    if session.experiment is None:
        outcome = message.strip() if _looks_like_failure(message) else ""
        session.experiment = ExperimentInput(
            title=session.title if session.title != "Untitled session" else _title_from(message),
            unexpected_outcome=outcome,
        )
        if session.title in {"Untitled session", "Untitled experiment"}:
            session.title = session.experiment.title
    return session.experiment


def absorb_message(session: DiagnosisSession, message: str) -> None:
    """Fold a user turn into the experiment / extra_information used by diagnosis."""
    text = message.strip()
    if not text:
        return
    exp = ensure_experiment(session, text)
    if text not in session.extra_information:
        session.extra_information.append(text)
    if text not in exp.extra_information:
        exp.extra_information.append(text)
    if _looks_like_failure(text) and not (exp.unexpected_outcome or "").strip():
        exp.unexpected_outcome = text[:4000]
    if session.title in {"Untitled session", "Untitled experiment"}:
        session.title = _title_from(text)
        exp.title = session.title


def apply_absorb_fields(session: DiagnosisSession, fields: dict[str, Any] | None) -> None:
    if not fields or not isinstance(fields, dict):
        return
    exp = ensure_experiment(session, "")
    mapping = {
        "unexpected_outcome": "unexpected_outcome",
        "objective": "objective",
        "setup_description": "setup_description",
        "title": "title",
    }
    for src, dest in mapping.items():
        value = str(fields.get(src) or "").strip()
        if value and not str(getattr(exp, dest) or "").strip():
            setattr(exp, dest, value)
            if dest == "title":
                session.title = value
    list_fields = {
        "materials": "materials",
        "processing": "processing",
        "telemetry": "telemetry_notes",
        "logs": "telemetry_notes",
        "context": "contextual_clues",
        "protocol": "protocol",
    }
    for src, dest in list_fields.items():
        value = fields.get(src)
        if not value:
            continue
        if isinstance(value, str):
            lines = _split_lines(value)
        else:
            lines = [str(v) for v in value if str(v).strip()]
        bucket: list[str] = getattr(exp, dest)
        for line in lines:
            if line not in bucket:
                bucket.append(line)


def diagnosis_snapshot(session: DiagnosisSession) -> str:
    diagnosis = session.diagnosis
    if diagnosis is None:
        return "No diagnosis yet."
    hyps = [h for h in diagnosis.hypotheses if h.status == "active"][:3]
    lines = [
        f"Diagnosis refreshed ({diagnosis.engine_mode}).",
        f"Lead: {diagnosis.leading_cause or 'underdetermined'}",
    ]
    if diagnosis.uncertainty and diagnosis.uncertainty.confidence is not None:
        lines.append(f"Confidence: {round(diagnosis.uncertainty.confidence * 100)}%.")
    if hyps:
        others = "; ".join(h.statement[:140] for h in hyps[1:3])
        if others:
            lines.append(f"Also in play: {others}")
    missing = list(diagnosis.uncertainty.missing_information) if diagnosis.uncertainty else []
    if missing:
        lines.append("Still missing: " + "; ".join(missing[:4]))
    return "\n".join(lines)


def seed_diagnosis_turn(session: DiagnosisSession, *, event: str = "opened") -> None:
    """Add a system-tool snapshot plus a conversational assistant opener."""
    if session.diagnosis is None:
        return
    mode = session.diagnosis.engine_mode or "heuristic"
    session.messages.append(
        new_message(
            "system-tool",
            diagnosis_snapshot(session),
            kind="diagnosis",
            diagnosed=True,
            mode=mode,
        )
    )
    session.messages.append(
        new_message(
            "assistant",
            heuristic_after_diagnosis(session),
            kind="diagnosis",
            slots_requested=[s["id"] for s in missing_slots(session)[:4]],
            diagnosed=True,
            mode=mode,
        )
    )
    session.history.append({"event": "chat_seed", "source": event})


def note_rediagnose(session: DiagnosisSession, event: str) -> None:
    if not session.messages or session.diagnosis is None:
        return
    session.messages.append(
        new_message(
            "system-tool",
            f"Re-ranked after {event}. {diagnosis_snapshot(session)}",
            kind="diagnosis",
            diagnosed=True,
            mode=session.diagnosis.engine_mode,
        )
    )


def heuristic_after_diagnosis(session: DiagnosisSession) -> str:
    diagnosis = session.diagnosis
    gaps = missing_slots(session)
    mode = (diagnosis.engine_mode if diagnosis else "heuristic") or "heuristic"
    parts: list[str] = []
    if diagnosis and diagnosis.leading_cause:
        parts.append(
            f"I ranked competing causes from the record you have ({mode} engine).\n\n"
            f"**Lead:** {diagnosis.leading_cause}"
        )
        active = [h for h in diagnosis.hypotheses if h.status == "active"]
        if len(active) > 1:
            alts = "\n".join(f"- {h.statement}" for h in active[1:3])
            parts.append("Still in play:\n" + alts)
        plan = diagnosis.recommended_intervention
        if plan and plan.description:
            parts.append(f"A discriminating follow-up: {plan.description}")
    else:
        parts.append(
            "I don't have a stable ranking yet — the evidence still "
            "underdetermines the cause."
        )
    if gaps:
        asks = "\n".join(f"- **{g['label']}** — {g['hint']}" for g in gaps[:4])
        parts.append("I still don't have these evidence slots:\n" + asks)
        parts.append(
            "Paste a measurement, attach files on the left, or tell me "
            "what you can check next."
        )
    else:
        parts.append(
            "The dump looks complete. Tell me if a hypothesis is wrong, "
            "or feed a follow-up outcome."
        )
    return "\n\n".join(parts)


def heuristic_clarify(session: DiagnosisSession, message: str) -> tuple[str, list[str]]:
    gaps = missing_slots(session)
    present = present_slot_ids(session)
    heard = message.strip()
    opener = "I heard you."
    if _looks_like_failure(heard):
        opener = "That's a usable failure description — I'll keep it as the unexpected outcome."
    elif heard.lower().strip() in GREETINGS:
        opener = "Happy to walk this through with you."
    else:
        opener = "Noted. I folded that into the session notes."

    if not present and not gaps:
        gaps = list(EVIDENCE_SLOTS)

    if not present or "failure" not in present:
        reply = (
            f"{opener} To start ranking competing causes I need what actually went wrong, "
            "then whatever else you have — intent, materials, process, "
            "sensors, logs, photos, CAD.\n\n"
            "**What failed?** A sentence is enough to begin. Dump files "
            "on the left whenever they're ready."
        )
        return reply, ["failure"]

    if gaps:
        asks = "\n".join(f"- **{g['label']}** — {g['hint']}" for g in gaps[:4])
        slot_ids = [g["id"] for g in gaps[:4]]
        ready_note = ""
        if evidence_ready(session):
            ready_note = (
                "\n\nThere's already enough to run a provisional ranking. "
                "Say **diagnose** (or use Force re-diagnose) when you want the hypotheses."
            )
        reply = (
            f"{opener} I still need a few evidence slots before the ranking is trustworthy:\n{asks}"
            f"{ready_note}"
        )
        return reply, slot_ids

    reply = (
        f"{opener} The dump looks complete. I can rank competing causes now — "
        "say **diagnose** or hit Force re-diagnose."
    )
    return reply, []


def should_run_diagnosis(
    session: DiagnosisSession,
    message: str,
    *,
    force: bool,
    llm_flag: bool | None,
) -> bool:
    if force:
        return True
    if llm_flag is True:
        return True
    if llm_flag is False and not force and not _wants_diagnosis(message):
        if not evidence_ready(session):
            return False
    if _wants_diagnosis(message):
        return True
    if session.diagnosis is None and evidence_ready(session):
        return True
    if session.diagnosis is not None and evidence_ready(session) and _looks_like_failure(message):
        return True
    return False


def _refresh_diagnosis(engine, session: DiagnosisSession) -> DiagnosisSession:
    if session.case_id:
        return engine._rediagnose(session)
    exp = session.experiment
    if exp is None or not (exp.has_content() or session.extra_information):
        return engine.sessions.save(session)
    return engine._rediagnose(session)


def replace_dump(
    engine,
    session: DiagnosisSession,
    experiment: ExperimentInput,
) -> DiagnosisSession:
    """Swap the dossier on an existing session and refresh diagnosis, keeping chat."""
    session.experiment = experiment
    if experiment.title:
        session.title = experiment.title
    session.history.append({"event": "dump_replaced", "title": experiment.title})
    session = engine._rediagnose(session)
    had_chat = any(m.role == "assistant" for m in session.messages)
    if had_chat:
        mode = session.diagnosis.engine_mode if session.diagnosis else None
        session.messages.append(
            new_message(
                "system-tool",
                diagnosis_snapshot(session),
                kind="diagnosis",
                diagnosed=True,
                mode=mode,
            )
        )
        session.messages.append(
            new_message(
                "assistant",
                "Dossier updated and re-ranked. The lead and competing causes "
                "are in the diagnosis panel. Ask a follow-up, reject a "
                "hypothesis, or tell me the next measurement.",
                kind="diagnosis",
                slots_requested=[s["id"] for s in missing_slots(session)[:4]],
                diagnosed=True,
                mode=mode,
            )
        )
        return engine.sessions.save(session)
    seed_diagnosis_turn(session, event="dump")
    return engine.sessions.save(session)


def _llm_turn(engine, session: DiagnosisSession, user_text: str) -> dict[str, Any] | None:
    llm = engine.llm
    if not (engine.prefer_llm and llm.available):
        return None
    gaps = missing_slots(session)
    present = present_slot_ids(session)
    dump = ""
    if session.experiment:
        from epidebug.engine.engine import _blob_from_experiment

        dump = _blob_from_experiment(session.experiment, session.extra_information)[:6000]
    lead = session.diagnosis.leading_cause if session.diagnosis else ""
    hyps = []
    if session.diagnosis:
        hyps = [
            {"id": h.id, "statement": h.statement, "posterior": h.posterior, "status": h.status}
            for h in session.diagnosis.hypotheses[:5]
        ]
    transcript = []
    for msg in session.messages[-12:]:
        transcript.append({"role": msg.role, "content": msg.content[:2000]})
    context = (
        f"Session title: {session.title}\n"
        f"Present evidence slots: {', '.join(present) or 'none'}\n"
        f"Missing slots: {', '.join(s['id'] for s in gaps) or 'none'}\n"
        f"Has diagnosis: {session.diagnosis is not None}\n"
        f"Lead: {lead or 'n/a'}\n"
        f"Hypotheses: {hyps}\n\n"
        f"Dossier excerpt:\n{dump or '(empty)'}\n\n"
        f"Latest user turn:\n{user_text}"
    )
    payload = list(transcript) + [{"role": "user", "content": context}]
    return llm.complete_chat_json(payload, CHAT_SYSTEM)


def start_chat_session(
    engine,
    message: str,
    *,
    title: str = "",
    domain: str | None = None,
    force_diagnose: bool = False,
) -> DiagnosisSession:
    text = message.strip()
    outcome = text if _looks_like_failure(text) else ""
    experiment = ExperimentInput(
        title=title.strip() or _title_from(text),
        domain=domain,
        unexpected_outcome=outcome,
        extra_information=[text] if text else [],
    )
    session = engine.sessions.create(experiment=experiment)
    if text:
        session.extra_information.append(text)
    session.history.append({"event": "chat_opened"})
    engine.sessions.save(session)
    return run_chat_turn(
        engine,
        session.session_id,
        text,
        force_diagnose=force_diagnose,
        already_absorbed=True,
    )


def run_chat_turn(
    engine,
    session_id: str,
    message: str,
    *,
    force_diagnose: bool = False,
    already_absorbed: bool = False,
) -> DiagnosisSession:
    session = engine.sessions.get(session_id)
    text = (message or "").strip()
    if text:
        session.messages.append(new_message("user", text, kind="message"))
        if not already_absorbed:
            absorb_message(session, text)
        session.history.append({"event": "chat_user", "information": text[:500]})

    llm_plan: dict[str, Any] | None = None
    llm_flag: bool | None = None
    if text or force_diagnose:
        llm_plan = _llm_turn(engine, session, text or "(force re-diagnose)")
    if llm_plan:
        apply_absorb_fields(session, llm_plan.get("absorb_fields"))
        if "run_diagnosis" in llm_plan:
            llm_flag = bool(llm_plan.get("run_diagnosis"))

    diagnosed = should_run_diagnosis(session, text, force=force_diagnose, llm_flag=llm_flag)
    if diagnosed:
        session = _refresh_diagnosis(engine, session)
        session.messages.append(
            new_message(
                "system-tool",
                diagnosis_snapshot(session),
                kind="diagnosis",
                diagnosed=True,
                mode=session.diagnosis.engine_mode if session.diagnosis else None,
            )
        )

    mode = "heuristic"
    llm_reply = str((llm_plan or {}).get("reply") or "").strip()
    if engine.prefer_llm and engine.llm.available and llm_reply:
        provider = getattr(engine.llm, "provider", None)
        mode = provider if provider not in {None, "none"} else "llm"
        reply = llm_reply
        slots = [str(s) for s in ((llm_plan or {}).get("request_slots") or []) if s]
        if diagnosed and session.diagnosis and "lead" not in reply.lower():
            reply = reply + "\n\n" + heuristic_after_diagnosis(session)
    else:
        if diagnosed:
            reply = heuristic_after_diagnosis(session)
            slots = [s["id"] for s in missing_slots(session)[:4]]
        else:
            reply, slots = heuristic_clarify(session, text or "Let's keep going.")
        if session.diagnosis:
            mode = session.diagnosis.engine_mode or mode

    session.messages.append(
        new_message(
            "assistant",
            reply,
            kind="diagnosis" if diagnosed else "question",
            slots_requested=slots,
            diagnosed=diagnosed,
            mode=mode,
        )
    )
    return engine.sessions.save(session)

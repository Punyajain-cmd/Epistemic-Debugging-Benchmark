# Epistemic Debugging Engine

The engine implements the workflow from the research plan:

`experiment information → anomaly identification → competing hypotheses → evidence analysis → causal chain → uncertainty → intervention`

## Modes

- **Heuristic** (default, no API key): scores a domain pattern library against the presented text. It does **not** read ground truth. Chat still asks for missing dump slots.
- **LLM** (if `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set): asks a model for structured JSON with ranked hypotheses, evidence, and an intervention, and uses the same router for conversational turns. Falls back to heuristic if parsing fails. Default model: `EPIDEBUG_MODEL=gpt-4o-mini`.

## Human-in-the-loop

`EpistemicDebuggingEngine.open_session` returns a session that supports:

- `chat(session_id, message, force_diagnose=False)` / `open_chat_session(message)`
- `reject_hypothesis(session_id, hypothesis_id, reason)`
- `add_information(session_id, text)`
- `record_followup(session_id, intervention, outcome)`

Each HITL action re-ranks remaining hypotheses with the new evidence. Chat turns persist on `session.messages` (`user` / `assistant` / `system-tool`).

## Evaluation

```bash
python scripts/evaluate_engine.py --split core
python scripts/evaluate_engine.py --split held_out
```

The diagnosis is converted to a `ModelResponse` and scored with `EpiDebugScorer`, including epistemic metrics.

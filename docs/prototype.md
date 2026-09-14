# Researcher-facing prototype

```bash
python -m pip install -e ".[web]"
python scripts/serve.py
```

Open `http://127.0.0.1:8000`. Same app: `python -m uvicorn web.app:app --host 127.0.0.1 --port 8000`.

Vercel deploy (no Hugging Face / Docker): [vercel.md](vercel.md).

Dump the actual experimental record, not a three-box summary:

- What failed, and what you intended
- Materials and how the part/sample was processed
- Setup notes (fixture, CAD revision, environment)
- Files: setup photos, result images, CAD (STEP/STL/DXF), sensor CSV/JSON, machine logs, PDFs, notebooks

The engine extracts text, table statistics, CAD product names, and filename cues, then ranks competing hypotheses with supporting and contradictory evidence.

You can reject a hypothesis, type extra measurements, attach more files, **chat turn-by-turn**, and feed a follow-up outcome back.

Session JSON is stable for the existing page and also includes an additive `view` object (ranked hypotheses, evidence strings, artifact gallery, ingest summaries, dump coverage, `messages` / `chat`) for the researcher UI.

API:

- `GET /api/health` — engine mode, catalog size, `contract_version: "0.6"`, `evidence_slots`, `fixtures`, chat routes
- `GET /api/cases` — picker rows (`objective_preview`, `failure_category_label`, regime)
- `POST /api/diagnose-bundle` multipart form + `files` (optional `logs`, `protocol`, optional `session_id` to refresh an existing thread)
- `POST /api/diagnose` JSON `{ "case_id" }` or `{ "experiment" }`
- `GET /api/sessions` — compact session list
- `POST /api/chat` — start (or continue) a conversation `{ "message", "session_id?", "force_diagnose?" }`
- `POST /api/sessions/{id}/chat` — next user turn; assistant may ask for missing slots and/or refresh diagnosis
- `POST /api/sessions/{id}/artifacts` attach more files
- `POST /api/sessions/{id}/reject` | `add-info` | `followup`
- `GET /api/files/{artifact_id}` preview (survives server restart)

Transcript roles: `user`, `assistant`, `system-tool`. Heuristic replies work with no API key. When `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) is set, chat and diagnosis prefer the LLM and fall back to heuristic if the call fails.

`view.ingest` includes per-file summaries, `core_missing` (photo/CAD/sensor/log/material/process), `anomaly_highlights`, and a completeness checklist. `view.gallery` groups images / tables / logs / CAD / documents. See [TEST.md](../TEST.md) for exact curl.

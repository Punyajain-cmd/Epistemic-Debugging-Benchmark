# Researcher-facing prototype

```bash
python -m pip install -e ".[web]"
python scripts/serve.py
```

Open `http://127.0.0.1:8000`.

Dump the actual experimental record, not a three-box summary:

- What failed, and what you intended
- Materials and how the part/sample was processed
- Setup notes (fixture, CAD revision, environment)
- Files: setup photos, result images, CAD (STEP/STL/DXF), sensor CSV/JSON, machine logs, PDFs, notebooks

The engine extracts text, table statistics, CAD product names, and filename cues, then ranks competing hypotheses with supporting and contradictory evidence.

You can reject a hypothesis, type extra measurements, attach more files, and feed a follow-up outcome back.

API:

- `POST /api/diagnose-bundle` multipart form + `files`
- `POST /api/diagnose` JSON `{ "case_id" }` or `{ "experiment" }`
- `POST /api/sessions/{id}/artifacts` attach more files
- `POST /api/sessions/{id}/reject` | `add-info` | `followup`
- `GET /api/files/{artifact_id}` preview

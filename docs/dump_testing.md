# How to test a multi-file dump

Coordinator / reviewer notes for the “dump everything → diagnose” path.

## Start the prototype

```bash
python -m pip install -e ".[dev,web]"
python scripts/serve.py
```

Open `http://127.0.0.1:8000`. Health should report `contract_version: "0.6"`. Exact curl: see [`TEST.md`](../TEST.md).

## Fastest path: sample fill

1. Click **Sample fill** (or “Fill a machining / battery example” on the empty diagnosis pane).
2. Confirm the **Dump checklist** goes to **10 / 10**: failure, objective, materials, process, setup, CAD, sensors, logs, photos, context.
3. Confirm six sample files are grouped by role (`pack_temp.csv`, `charger.log`, `housing_revC.step`, `bore_finish.nc`, setup photo, result photo) and that roles can be changed.
4. Click **Run epistemic debugging**.
5. On the right, **What was ingested** should show a coverage list plus a gallery card per file with the engine summary (table units / rising temp, log error lines, STEP product name, etc.).
6. HITL still works: Reject a hypothesis, **Add evidence** as text, attach another file with a role, or feed a follow-up outcome.

## Manual multi-file dump (preferred independent check)

Create a small folder (any machining / robotics / battery / lab mix):

| File | Example | Expected role |
|------|---------|----------------|
| Sensor table | `pack_temp.csv` with `time_s,temp_C,current_A` | Sensor / table |
| Machine log | `charger.log` or `cnc.out` with `ERROR` / `ALARM` | Log / console |
| CAD | `housing.step` (ASCII STEP) or a tiny STL | CAD / drawing |
| Process | `bore_finish.nc` G-code or a traveler `.md` | Process sheet |
| Photo | any PNG/JPEG/SVG of the bench or failed part | Setup or result photo |
| Markdown notes | `NOTES.md` with a `# heading` | Process / other document |
| STL stub | tiny `solid … endsolid` or empty `.stl` | CAD (summarized as stub) |
| Optional PDF | SDS / datasheet | Datasheet or material cert |

Coordinator-local `mock_data/full_dump_demo/` (csv, log, md, svg, STL stub) is listed by `/api/fixtures` when present; see [`TEST.md`](../TEST.md) §5.

In the dossier:

1. Fill **What went wrong** and **Intent**.
2. Use the guided sections (Materials, Process, Setup & CAD, Sensors, Logs) — either type notes or use the section **Attach** buttons (they pre-assign roles).
3. Drop leftover files on **Photos & all files** and correct roles if the guess is wrong.
4. Watch the checklist: missing types stay listed until you add them. Incomplete dumps are allowed.
5. Diagnose. Confirm `view.ingest.summaries` and `view.gallery` in the network response (`POST /api/diagnose-bundle`) match the gallery.

## API smoke (no browser)

```bash
python -m pytest tests/test_ingest.py tests/test_present.py tests/test_web_api.py -q
```

`test_multifile_dump_exposes_gallery_and_coverage` posts CSV + log + STEP + PNG + G-code and checks:

- `view.contract_version == "0.6"`
- artifact summaries / stats / flags / `extracted_preview`
- `view.gallery` buckets and `view.ingest.completeness == 1.0`
- HITL `POST /api/sessions/{id}/followup` still returns the same gallery

HITL endpoints are unchanged: `/reject`, `/add-info`, `/artifacts`, `/followup`.

## Limits

Files are still capped at **25 MB** each (`413` if larger). Binary tables (xlsx/parquet) are accepted but only summarized; CSV/JSON get numeric stats.

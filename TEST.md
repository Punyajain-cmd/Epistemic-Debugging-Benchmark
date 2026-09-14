# Independent multi-file dump test

Contract: `0.6`. Fixtures: `mock_data/dump_demo/`.

## 1. Start the server

```bash
python -m pip install -e ".[dev,web]"
python scripts/serve.py
# http://127.0.0.1:8000
```

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -c "import sys,json; h=json.load(sys.stdin); print(h['contract_version'], h.get('fixtures'))"
```

Expect `0.6` and a `dump_demo` file list.

## 2. UI (sample fill)

1. Open `http://127.0.0.1:8000`.
2. Click **Sample fill**. Dump checklist should reach **10 / 10**. Core types present: photo, CAD, sensor, log, material, process.
3. File list should include `pack_temp.csv`, `charger.log`, `housing_revC.step`, `bore_finish.nc`, `setup_vise.png`, `vented_cells_result.png`, `mill_cert_lot24081.txt`.
4. Click **Run epistemic debugging**.
5. **What was ingested** should show coverage + gallery summaries:
   - CSV: `temp_C` step/trend (25 °C → ~94 °C)
   - Log: sampled `ERROR overheat pack_main` / `ALARM-401`
   - STEP: product `6082-T6-housing`
6. HITL still works: Reject, Add evidence, attach another file, Follow-up.

## 3. curl smoke (coordinator)

From the repo root, with the server running:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/diagnose-bundle \
  -F "title=Housing bore drift / pack venting" \
  -F "domain=machining" \
  -F "unexpected_outcome=The CNC bore is 0.18 mm undersize and smeared; three 21700 cells vented on 2C charge." \
  -F "objective=Machine 6082-T6 housings to REV C and assemble a 6S2P pack." \
  -F "materials=6082-T6 bar, lot 24-081" \
  -F "processing=Finish 0.08 mm/rev; 2C CC-CV to 4.20 V" \
  -F "setup_description=Kurt vise, REV C STEP, 21 C / 62% RH" \
  -F "context=New night-shift operator; electrolyte bottle left uncapped" \
  -F 'roles=["sensor","log","cad","process_doc","setup_photo","result_image","material_doc"]' \
  -F 'captions=["thermistor","charger","REV C fixture","finish program","vise","vented cans","mill cert"]' \
  -F "files=@mock_data/dump_demo/pack_temp.csv" \
  -F "files=@mock_data/dump_demo/charger.log" \
  -F "files=@mock_data/dump_demo/housing_revC.step" \
  -F "files=@mock_data/dump_demo/bore_finish.nc" \
  -F "files=@mock_data/dump_demo/setup_vise.png" \
  -F "files=@mock_data/dump_demo/vented_cells_result.png" \
  -F "files=@mock_data/dump_demo/mill_cert_lot24081.txt" \
  | python3 -c '
import json,sys
body=json.load(sys.stdin)
v=body["view"]
print("contract", v["contract_version"])
print("files", v["file_count"], "core_missing", v["ingest"]["core_missing"])
print("highlights:")
for h in v["ingest"]["anomaly_highlights"][:8]:
    print(" -", h["filename"], h["kind"], h["text"][:100])
print("lead:", (v["lead"]["cause"] or "")[:160])
assert v["contract_version"]=="0.6"
assert v["file_count"]==7
assert v["ingest"]["core_missing"]==[]
assert any(h["kind"] in {"trend","step","outlier","endpoint"} for h in v["ingest"]["anomaly_highlights"])
assert any(h["kind"]=="log" for h in v["ingest"]["anomaly_highlights"])
print("SMOKE OK")
'
```

Chat (heuristic, no API key):

```bash
curl -sS -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"The CNC bore is 0.18 mm undersize and three cells vented on 2C charge."}' \
  | python3 -c 'import json,sys; b=json.load(sys.stdin); print(b["session_id"]); print(b["view"]["chat"]["last_reply"][:200])'
```

HITL (replace `SESSION`):

```bash
curl -sS -X POST http://127.0.0.1:8000/api/sessions/SESSION/add-info \
  -H "Content-Type: application/json" \
  -d '{"information":"Karl Fischer water in electrolyte was 480 ppm"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['view']['history'][-1])"
```

## 4. Pytest

```bash
python -m pytest tests/test_ingest.py tests/test_present.py tests/test_web_api.py tests/test_chat.py tests/test_llm.py -q
```

`test_dump_demo_fixtures_via_diagnose_bundle` posts the files on disk in `mock_data/dump_demo/`.

## 5. Optional `full_dump_demo` (coordinator local fixtures)

If you have `mock_data/full_dump_demo/` (csv, log, md, svg, STL stub), `/api/health` lists it automatically. Ingest must classify those types without crashing:

| File | Expected |
|------|----------|
| `*.csv` | sensor + numeric stats / anomalies |
| `*.log` | log + `error_samples` |
| `*.md` | document, first heading in summary |
| `*.svg` | image (`image/svg+xml`), viewBox/title/labels |
| `*.stl` stub | cad, `stats.stub`, 0 facets/triangles |

```bash
# from repo root, server running
python3 - <<'PY'
from pathlib import Path
demo = Path("mock_data/full_dump_demo")
assert demo.is_dir(), "place coordinator fixtures at mock_data/full_dump_demo/"
print("files", sorted(p.name for p in demo.iterdir() if p.is_file()))
PY

curl -sS -X POST http://127.0.0.1:8000/api/diagnose-bundle \
  -F "title=Coordinator full dump" \
  -F "domain=machining" \
  -F "unexpected_outcome=Multi-file dump smoke" \
  $(for f in mock_data/full_dump_demo/*; do
      [ -f "$f" ] || continue
      printf ' -F files=@%s' "$f"
    done) \
  | python3 -c '
import json,sys
body=json.load(sys.stdin)
v=body["view"]
print("contract", v["contract_version"], "files", v["file_count"])
for a in v["artifacts"]:
    print(" -", a["filename"], a["kind"], (a.get("summary") or "")[:90])
assert v["contract_version"]=="0.6"
assert v["file_count"]>=1
print("FULL_DUMP SMOKE OK")
'
```

`test_full_dump_mix_svg_stl_stub_markdown` covers the same mix in-repo. `test_full_dump_demo_fixtures_via_diagnose_bundle` runs only when that folder exists.

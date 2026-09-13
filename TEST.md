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

HITL (replace `SESSION`):

```bash
curl -sS -X POST http://127.0.0.1:8000/api/sessions/SESSION/add-info \
  -H "Content-Type: application/json" \
  -d '{"information":"Karl Fischer water in electrolyte was 480 ppm"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['view']['history'][-1])"
```

## 4. Pytest

```bash
python -m pytest tests/test_ingest.py tests/test_present.py tests/test_web_api.py -q
```

`test_dump_demo_fixtures_via_diagnose_bundle` posts the files on disk in `mock_data/dump_demo/`.

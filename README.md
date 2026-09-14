# EpiDebug — Epistemic Debugging Benchmark and Diagnostic System

**Can AI systems diagnose why experiments fail — and show their uncertainty?**

EpiDebug is both an evaluation benchmark and a researcher-facing diagnostic engine for *epistemic debugging*: combining experimental objectives, protocols, telemetry, and context to identify why a scientific or engineering process produced unexpected results, reconstruct the causal chain, and propose a follow-up that distinguishes competing explanations.

This repository implements the plan in the EpiDebug research proposal: expand the benchmark, build an epistemic debugging engine, ship a human-in-the-loop prototype, and evaluate diagnosis quality beyond a single answer string.

---

## What the system does

A researcher (or an evaluated model) sees the same four layers a human would:

| Layer | Contents |
|-------|----------|
| **Objective** | What the experiment aimed to achieve |
| **Protocol** | Step-by-step instructions that were followed |
| **Telemetry** | Sensor readings, spectra, yields, logs |
| **Contextual clues** | Environment and history — some relevant, some red herrings |

The engine does **not** jump to one story. It:

1. Identifies anomalies
2. Generates competing hypotheses
3. Grounds each hypothesis in supporting and contradictory evidence
4. Reconstructs a causal chain
5. Reports confidence, entropy, and missing information
6. Recommends a discriminating intervention

The researcher can reject a hypothesis, add measurements, and feed a follow-up outcome back into the session.

---

## Failure categories and information regimes

**Mechanisms**

| Category | Example |
|----------|---------|
| Reagent / material flaw | Buffer pH drifted from CO₂ absorption |
| Instrumentation artifact | Nanodrop pedestal contamination |
| Protocol / human loophole | Glycerol stock sampled without vortexing |
| Flawed hypothesis | Assumed SN2 when E2 dominated |

**Information regimes** (new): `complete`, `incomplete`, `noisy`, `distractor_heavy`, `ambiguous`.

**Splits**: `core`, `development`, `held_out` — see `test_cases/splits.yaml`.

---

## Quick start

```bash
cd Epistemic-Debugging-Benchmark-main
python -m pip install -e ".[dev,web]"

# Validate cases
python scripts/validate_cases.py --all -v

# Unit tests
python -m pytest tests -q

# Heuristic engine vs the benchmark (no API key)
python scripts/evaluate_engine.py --split all

# Reference baselines (oracle / partial / distractor / category_only)
python scripts/run_reference_models.py --all

Dump the full experimental record — photos, CAD, logs, sensor files, materials, process — then run epistemic debugging. The UI checklist and `view.ingest` / `view.gallery` show what was actually read. Independent smoke steps: `TEST.md` (fixtures in `mock_data/dump_demo/`).

```bash
python scripts/serve.py
# open http://127.0.0.1:8000
# equivalent: python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Deploy the same FastAPI UI on Vercel (`web.app:app`). See [docs/vercel.md](docs/vercel.md).

Optional LLM diagnosis (engine + benchmark runner):

```bash
set OPENAI_API_KEY=...
python scripts/evaluate_engine.py --llm
python scripts/run_benchmark.py --model gpt-4o --case RF-001
```

---

## Project structure

```
epidebug/                 Core package
  schema.py               Cases, diagnoses, scores
  scoring.py              Root cause / chain / intervention + epistemic metrics
  engine/                 Hypothesis generation, evidence, HITL sessions
  tools/                  Mock instruments and databases
  utils/                  Trajectory logging
test_cases/               YAML benchmark (core + incomplete + ambiguous)
scripts/                  CLI: validate, run, evaluate, serve, report
web/                      FastAPI prototype
docs/                     Authoring, scoring, data sources, engine notes
paper/                    Manuscript draft
results/reference/        Deterministic baseline outputs
```

---

## Scoring

Primary score (unchanged weights):

| Component | Weight |
|-----------|--------|
| Root cause | 30% |
| Causal chain | 40% |
| Intervention | 30% |

Reported separately: evidence grounding, calibration error, confirmation bias, hypothesis diversity, missing-information quality, tool efficiency, budget compliance.

---

## License

Apache 2.0

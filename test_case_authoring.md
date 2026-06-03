# Writing EpiDebug Test Cases

This guide explains how to author new test cases for the Epistemic Debugging Benchmark.

## Overview

Each test case simulates a real-world experimental failure. The AI model receives exactly
what a human researcher would see, and must diagnose the root cause.

## File Naming Convention

```
test_cases/<category>/<ID>_<short_description>.yaml
```

- **Category directories**: `reagent_flaw/`, `instrumentation/`, `protocol_loophole/`, `flawed_hypothesis/`
- **ID format**: `XX-NNN` where `XX` is the category prefix:
  - `RF` = Reagent/Material Flaw
  - `IN` = Instrumentation Artifact
  - `PL` = Protocol/Human Loophole
  - `FH` = Flawed Hypothesis
- **Example**: `test_cases/reagent_flaw/RF-001_buffer_ph_drift.yaml`

## YAML Schema

```yaml
# --- Required fields ---
id: "RF-001"                          # Must match XX-NNN pattern
title: "Buffer pH Drift"             # Human-readable title
domain: "molecular_biology"           # See supported domain values below
subdomain: "protein_purification"     # Free-form subdomain
failure_category: "reagent_material_flaw"  # Must match enum value
difficulty: "medium"                  # easy | medium | hard | expert

# --- The Four Layers ---

objective: |
  What the researcher was trying to achieve.
  Be specific about expected outcomes.

protocol:
  - step: 1
    action: "Description of what was done"
    details: "Additional context (optional)"
    duration: "30 minutes (optional)"
    notes: "Observations during step (optional)"
  - step: 2
    action: "Next step..."

telemetry:
  measurement_name:          # Use descriptive snake_case names
    description: "What this measurement represents"
    data_type: "numeric"     # numeric | timeseries | image | spectrum | table | text
    observations:
      - "Human-readable observation 1"
      - "Human-readable observation 2"
    values: [1.0, 2.0, 3.0]  # Numeric data (optional)
    units: "mL"               # Units (optional)
    data_ref: "mock_data/..."  # Path to external data file (optional)
    note: "Additional note"    # Extra context (optional)
    expected_range:            # What normal would look like (optional)
      min: 0.5
      max: 2.0

contextual_clues:
  - "Relevant environmental detail (the actual clue)"
  - "A detail that seems relevant but is a red herring"
  - "Another background detail"

# --- Ground Truth ---
ground_truth:
  root_cause: "Clear, specific description of what went wrong"
  root_cause_category: "reagent_material_flaw"
  causal_chain:
    - "Step 1: Initial cause"
    - "Step 2: Intermediate effect"
    - "Step 3: Next consequence"
    - "Step 4: Observable symptom"
  intervention: "Specific experiment to confirm the diagnosis"
  key_evidence:
    - "Specific telemetry/protocol detail that points to root cause"
  alternative_diagnoses:
    - "Plausible but wrong explanation 1"
    - "Plausible but wrong explanation 2"
  why_alternatives_fail:
    "Plausible but wrong explanation 1": "Why the evidence doesn't support this"
  difficulty_justification: "Why this case has its difficulty rating"

# --- Scoring (optional customization) ---
scoring:
  weights:
    root_cause: 0.30
    causal_chain: 0.40
    intervention: 0.30
  root_cause_keywords:
    - "keyword1"
    - "keyword2"

# --- Sandbox (for agent mode) ---
available_tools:
  - "tool_name_1"
  - "tool_name_2"
max_tool_calls: 15

# --- Metadata ---
source: "Where this case came from"
source_url: "https://..."
expert_validated: false
tags: ["tag1", "tag2"]
author: "Your Name"
```

## Quality Checklist

Before submitting a test case, verify:

- [ ] **Scientific accuracy**: All protocols, data, and chemistry/biology/physics are correct
- [ ] **Solvable**: A domain expert should be able to diagnose this from the given information
- [ ] **Not trivially searchable**: Can't be solved by googling the title
- [ ] **Realistic telemetry**: Data values are realistic, not round numbers
- [ ] **Red herrings**: Include 1-2 contextual clues that are plausible but irrelevant
- [ ] **Complete causal chain**: Every step connects logically
- [ ] **Testable intervention**: The proposed experiment would actually confirm the diagnosis
- [ ] **Alternative diagnoses**: Include 3-4 plausible alternatives with explanations
- [ ] **Schema validates**: Run `python scripts/validate_cases.py --case <path>`

## Difficulty Guidelines

| Level | Description | Expected Expert Success Rate |
|-------|-------------|------------------------------|
| Easy | Single obvious failure, clear telemetry | 90%+ |
| Medium | Requires connecting 2-3 pieces of information | 70-90% |
| Hard | Subtle failure, misleading evidence, domain expertise needed | 40-70% |
| Expert | Requires deep domain knowledge, multiple competing hypotheses | <40% |

## Supported Domains

Use these domain values in `domain`:

| Value | Example Scope |
|-------|---------------|
| `molecular_biology` | PCR, protein purification, cell culture |
| `chemistry` | Organic synthesis, catalysis, analytical chemistry |
| `physics` | Measurement, signal processing, optics |
| `engineering` | Mechanical, electrical, materials, FEA |
| `biotech` | Flow cytometry, assays, bioprocessing |
| `clinical` | Lab medicine, biomarkers, sample handling |
| `software_systems` | Distributed systems, observability, cloud incidents |
| `manufacturing` | CNC, equipment wear, process control, metrology |
| `civil_engineering` | Structural inspection, infrastructure monitoring |
| `aerospace` | Engines, flight systems, spacecraft operations |
| `energy` | Batteries, fuel cells, solar, power systems |
| `environmental_science` | Field sampling, sensors, ecology, geochemistry |
| `materials_science` | Mechanical testing, microscopy, metallurgy |
| `robotics` | Controls, perception, actuation, autonomy |

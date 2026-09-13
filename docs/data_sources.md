# Data Sources and Curation Strategy

EpiDebug should grow from high-quality, reviewable cases rather than raw failure
records copied directly into prompts. The benchmark case is the unit of value:
objective, protocol, telemetry, context, root cause, causal chain, intervention,
and plausible alternatives.

## Source Lanes

| Lane | Useful Source Types | Typical Failure Categories |
|------|---------------------|----------------------------|
| Wet lab and chemistry | Troubleshooting guides, open protocols, synthetic composites, instrument logs | Reagent/material flaw, instrumentation artifact, protocol loophole, flawed hypothesis |
| Software and cloud systems | Fault-injection logs, incident postmortems, distributed traces, architecture regression reports | Instrumentation artifact, protocol loophole, flawed hypothesis |
| Manufacturing and machinery | Equipment-failure prediction tables, maintenance logs, SPC charts, metrology reports | Reagent/material flaw, instrumentation artifact, protocol loophole |
| Structural and civil engineering | Concrete crack datasets, inspection reports, load tests, NDE data | Flawed hypothesis, instrumentation artifact, material flaw |
| Clinical and biotech | Sample-quality records, assay validation reports, biomarker QC logs | Reagent/material flaw, instrumentation artifact, protocol loophole |

## Candidate Public Resources

- Fault-injection datasets for cloud platforms, especially records with injected
  fault, workload, logs, and observed error effect.
- NASA-style software defect metrics datasets, useful for converting code or
  system failures into diagnosis cases when the root cause is known.
- Equipment failure prediction datasets with machine telemetry and labeled
  failure modes such as tool wear, heat dissipation, power, or overstrain.
- ASM-style failure-analysis case studies for materials and fracture examples,
  when licensing permits summary-based synthetic cases rather than copied text.
- Infrastructure project datasets and civil inspection image sets, especially
  where a model or engineering workflow failed because assumptions did not hold.
- Concrete crack image datasets such as SDNET-like collections, used carefully
  to build validation-leakage or field-generalization cases.
- EuRoC-style visual-inertial flight logs and MEMS Allan-deviation plots, used
  for robotics time-offset and IMU-model cases.
- NIST dimensional-metrology and Physical Measurement Laboratory practice notes
  for CMM thermal/probe errors, lock-in setup, detector saturation, and RF
  near-field versus Friis mistakes.
- USGS water-quality monitoring and EPA Clean Water Act method hold-time /
  blank tables for environmental sampling cases.
- ASM-style materials failure-analysis pattern families (hydrogen embrittlement,
  weld interpass, overaging) summarized into original travelers only.
- Kubernetes HPA, Prometheus `rate()` / counter-reset, and payments
  idempotency incident families for software/cloud cases.
- CALCE / NASA battery aging tables for formation-rest, anode-moisture,
  calendar-versus-cycle, and BMS shunt-temperature cases.
- NASA C-MAPSS jet-engine simulated data
  (https://data.nasa.gov/dataset/cmapss-jet-engine-simulated-data) for
  sensor-bias versus true-degradation aerospace cases.
- MetroPT-3 compressor dataset
  (https://archive.ics.uci.edu/dataset/791/metropt+3+dataset) for analog
  pressure / load-unload leak-detector artifacts.
- SCANIA Component X PdM (https://doi.org/10.5878/jvb5-d390,
  https://www.nature.com/articles/s41597-025-04802-6) for oil-chemistry
  versus mechanical-wear manufacturing cases.
- Kubernetes RCA labs (https://github.com/coroot/rca-lab), Chaos Mesh
  (https://github.com/chaos-mesh/chaos-mesh), and TaskFlow incident lab
  (https://github.com/OnyiGlobal2025/taskflow-incident-lab) for
  software/cloud metric, timeout, and retry patterns.

## Coordinator-requested public pattern sources

These lanes were named for the 2026-09 expansion. Use them as **pattern
families only**: summarize mechanisms into original YAML travelers. Do not
copy dataset rows, lab runbooks, or proprietary text.

| Source | License / access | URL(s) | Synthetic cases |
|--------|------------------|--------|-----------------|
| NASA PCoE repository | NASA public; verify per file | https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/ | RF-012, RF-021, IN-019, PL-012, FH-016 |
| NASA C-MAPSS turbofan | NASA public | https://data.nasa.gov/dataset/cmapss-jet-engine-simulated-data | IN-012, IN-016 (and PCoE sister notes) |
| AI4I 2020 Predictive Maintenance | CC BY 4.0 | https://archive.ics.uci.edu/dataset/601/ai4i and https://archive.ics.uci.edu/ml/datasets/AI4I+2020+Predictive+Maintenance+Dataset | RF-009, RF-011, RF-016, PL-011 |
| MetroPT-3 compressor | UCI public research set | https://archive.ics.uci.edu/dataset/791/metropt+3+dataset | IN-024 |
| SCANIA Component X PdM | SND / Scientific Data; verify redistribution | https://doi.org/10.5878/jvb5-d390 and https://www.nature.com/articles/s41597-025-04802-6 | RF-023 |
| Coroot RCA lab | Pattern inspiration | https://github.com/coroot/rca-lab | IN-021 |
| Chaos Mesh | Apache-2.0 project; pattern inspiration | https://github.com/chaos-mesh/chaos-mesh | IN-020, PL-014 |
| TaskFlow incident lab | Pattern inspiration | https://github.com/OnyiGlobal2025/taskflow-incident-lab | IN-020, IN-021, PL-014, FH-018 |

## Curation Rules

1. Prefer open, public-domain, permissively licensed, or fully synthetic composite
   cases for anything intended to ship with the benchmark.
2. Do not copy proprietary postmortems, journal figures, paid database text, or
   restricted protocols into YAML cases. Summarize patterns into synthetic cases.
3. Every case must have a single primary root cause, even if the real-world source
   involved multiple contributing factors.
4. Include red herrings, but make the correct diagnosis recoverable from the
   provided evidence.
5. Record source provenance in `source` and set `expert_validated: false` until a
   relevant domain reviewer has checked the scientific or engineering details.

## Conversion Recipe

1. Extract the failure narrative: what was attempted, what was expected, what
   failed, and what finally explained it.
2. Separate observations from interpretation. Put raw facts in `protocol`,
   `telemetry`, and `contextual_clues`; put the explanation only in `ground_truth`.
3. Write the causal chain from initial failed variable to final observed symptom.
4. Add at least three plausible alternative diagnoses and why each fails.
5. Add a confirmatory intervention that changes one variable and predicts a clear
   outcome.
6. Add scoring keywords and partial-credit causes.
7. Run `python scripts/validate_cases.py --case <path>` and then seek expert review.

## Initial Expansion Targets

- 10 additional software/system cases from fault injection and incident patterns.
- 10 manufacturing/equipment cases from labeled machinery-failure scenarios.
- 10 civil/structural cases from inspection, materials, and model-validation failures.
- 10 clinical/biotech cases focused on sample handling, assay artifacts, and protocol gaps.
- 10 expert-level cross-domain cases where the obvious explanation is wrong.

## 2026-09 domain-expansion batch

A first synthetic batch toward those targets is now in-tree (30 new cases).
Remaining gaps worth a later pass: clinical/biotech sample-handling volume
and additional civil NDE composites. Each new case records a public
pattern-family URL in `source_url` and remains `expert_validated: false`
until domain review.

### Case-to-source-lane map (new IDs)

| Lane | Cases that cite it |
|------|--------------------|
| UCI AI4I 2020 (CC BY 4.0) | RF-016, PL-011 (plus existing RF-009, RF-011) |
| MetroPT-3 compressor | IN-024 |
| SCANIA Component X PdM | RF-023 |
| NASA PCoE / C-MAPSS | RF-021, IN-019, PL-012, FH-016 (plus existing IN-012, IN-016, RF-012) |
| NASA lessons learned | RF-018, PL-013, FH-017 |
| EuRoC MAV / Kalibr | RF-017, IN-018, FH-015 |
| Coroot rca-lab / Chaos Mesh / TaskFlow incident lab | IN-020, IN-021, PL-014, FH-018 |
| NASA MDP / PROMISE (secondary frame on FH-018) | FH-010 (existing), FH-018 |
| USGS / EPA CWA methods | RF-022, PL-016 |
| SDNET-style civil field campaigns | RF-019, IN-022, FH-019 (plus existing FH-009, PL-007) |
| ASM failure-analysis patterns | RF-020, PL-015, FH-020 |
| NIST dimensional metrology | IN-017 |
| NIST PML instrumentation / RF | IN-023, PL-017, FH-021 |

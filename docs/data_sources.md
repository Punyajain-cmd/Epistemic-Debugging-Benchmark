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

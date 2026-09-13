# EpiDebug: Evidence-Grounded Diagnosis of Scientific and Engineering Failures

## Abstract

Scientific work routinely fails for reasons that are not in the protocol as written: a reagent drifted, an instrument lied, an unwritten step was skipped, or the working hypothesis was wrong. Existing AI evaluations emphasize recall, code, or question answering, and therefore miss this capability. We present EpiDebug, a benchmark and a researcher-facing diagnostic engine for *epistemic debugging*: combining heterogeneous experimental information to produce competing, evidence-grounded causal diagnoses, explicit uncertainty, and follow-up experiments that discriminate remaining hypotheses. The system is evaluated on root-cause identification, causal-chain quality, intervention quality, evidence grounding, calibration, and robustness to incomplete or distracting information.

## 1. Introduction

Research is iterative. After an unexpected result, a competent investigator does not emit a single narrative. They list anomalies, keep more than one explanation alive, ask what would kill each explanation, and run the cheapest discriminating test. EpiDebug asks whether AI systems can do that, and provides a prototype that keeps the researcher in the loop.

The research questions are:

1. **Diagnosis.** Can a system identify the underlying cause of an unexpected experimental result?
2. **Epistemic reasoning.** Can it distinguish observations, inferences, hypotheses, supporting evidence, contradictory evidence, and uncertainty?
3. **Intervention.** Can it propose follow-ups that efficiently distinguish competing explanations?

## 2. Related work

EpiDebug sits between scientific question answering, root-cause analysis in operations, and tool-using agents. Unlike SWE-bench, the artifact is not a patch; it is a causal account of a physical, biological, or socio-technical experiment. Unlike MMLU, the required knowledge is local to the case file: telemetry, protocol notes, and context, some of which are red herrings.

## 3. Benchmark

Each case is a YAML object with objective, protocol, telemetry, contextual clues, ground-truth cause, causal chain, intervention, alternatives, and scoring keywords. Four mechanism categories are retained: reagent/material flaw, instrumentation artifact, protocol/human loophole, and flawed hypothesis.

The expansion adds:

- **Information regimes:** complete, incomplete, noisy, distractor-heavy, ambiguous.
- **Splits:** core, development, held-out (`test_cases/splits.yaml`).
- **Epistemic metrics** scored separately from the 30/40/30 primary rubric.

## 4. Engine

The engine follows a fixed pipeline. A heuristic pattern library scores domain failure modes against the presented text without reading ground truth. When an API key is present, an LLM is asked for the same structured diagnosis and the heuristic path remains the fallback. Sessions support rejection, added information, and follow-up outcomes.

## 5. Prototype

A local web application (FastAPI) lets a researcher load a catalog case or paste a new experiment, inspect ranked hypotheses with evidence for and against, and remain in the decision loop.

## 6. Evaluation protocol

Compare:

- Deterministic reference models (oracle, partial, category-only, distractor)
- The EpiDebug engine (heuristic and optional LLM)
- External LLMs in text and agent modes via `scripts/run_benchmark.py`

Report mean primary score by category, split, and information regime, plus evidence grounding, calibration error, confirmation bias, and hypothesis diversity.

## 7. Broader impact

Failed experiments are a large, poorly captured part of the scientific record. A system that exposes evidence and uncertainty can help laboratories reuse that knowledge instead of burying it in notebooks. The same interface is a testbed for studying premature commitment and confirmation bias in AI assistants.

## 8. Limitations

Heuristic patterns cannot cover every domain. Expert validation of ground-truth chains is still incomplete (`expert_validated: false` on starter cases). LLM evaluation requires API spend and a frozen judge prompt. Ambiguous cases have a designated scoring target even when a second mechanism remains scientifically live.

## References

Clayden, Greeves, Warren. *Organic Chemistry*.  
NASA PCoE data set repository; AI4I 2020; SDNET2018; OpenStack fault-injection traces (see `docs/catalog.yaml`).

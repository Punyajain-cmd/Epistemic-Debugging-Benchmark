# EpiDebug Scoring Rubric

## Overview

Each test case is scored on three dimensions with fixed weights:

| Component | Weight | What It Measures |
|-----------|--------|-----------------|
| **Root Cause Identification** | 30% | Did the model correctly name the variable that failed? |
| **Causal Chain** | 40% | Did the model trace the failure mechanism step-by-step? |
| **Intervention Strategy** | 30% | Did the model propose a valid confirmatory experiment? |

**Final Score** = (RC × 0.30) + (CC × 0.40) + (IV × 0.30)

Each component scores 0.0 – 1.0.

---

## Root Cause Identification (30%)

### Scoring Method
1. **Keyword match**: Check if the model's response contains key terms from `scoring.root_cause_keywords`
2. **Semantic similarity**: Compute cosine similarity between model response and ground truth using sentence embeddings
3. **Partial credit**: If `scoring.partial_credit_causes` is defined, check for partially correct answers

### Score Levels

| Score | Criteria |
|-------|----------|
| 1.0 | Correctly identifies the specific root cause variable |
| 0.75 | Identifies the correct category but is imprecise about the specific variable |
| 0.5 | Identifies a contributing factor but not the root cause |
| 0.25 | Mentions a related concept but doesn't identify the actual failure |
| 0.0 | Completely wrong or no answer |

---

## Causal Chain (40%)

### Scoring Method
LLM-as-judge evaluates the model's causal chain against the ground truth chain.

### Rubric for Judge

The judge evaluates on 4 sub-criteria:

1. **Logical ordering** (0-0.25): Are the steps in a logical causal sequence?
2. **Scientific accuracy** (0-0.25): Are the intermediate mechanisms scientifically correct?
3. **Completeness** (0-0.25): Does the chain cover all critical intermediate steps?
4. **Symptom connection** (0-0.25): Does the chain explain the observed symptoms?

### Score Levels

| Score | Criteria |
|-------|----------|
| 1.0 | Complete, accurate chain from root cause to symptoms |
| 0.75 | Mostly correct with minor gaps or imprecisions |
| 0.5 | Correct general direction but missing key intermediate steps |
| 0.25 | Some correct elements but fundamentally flawed logic |
| 0.0 | Completely wrong chain |

---

## Intervention Strategy (30%)

### Scoring Method
LLM-as-judge evaluates the proposed intervention.

### Rubric for Judge

The judge evaluates on 3 sub-criteria:

1. **Counterfactual validity** (0-0.33): Does the intervention change only the suspected variable?
2. **Diagnostic power** (0-0.34): Would a positive result confirm the diagnosis?
3. **Feasibility** (0-0.33): Is the experiment practically achievable?

### Score Levels

| Score | Criteria |
|-------|----------|
| 1.0 | Perfect counterfactual experiment that would conclusively test the diagnosis |
| 0.75 | Good experiment but with minor confounds or imprecisions |
| 0.5 | Reasonable experiment but doesn't isolate the suspected variable |
| 0.25 | An experiment related to the problem but wouldn't confirm the specific diagnosis |
| 0.0 | Irrelevant or infeasible experiment |

---

## Trajectory Metrics (Not Scored, Reported Separately)

### Tool Efficiency
```
efficiency = useful_tool_calls / total_tool_calls
```
A "useful" tool call is one that yields information relevant to the diagnosis.

### Budget Compliance
Binary: did the agent stay within `max_tool_calls`?

### Anti-Pattern Detection
- **Confirmation bias**: Agent only seeks evidence for its first hypothesis
- **Premature commitment**: Agent declares a diagnosis before examining all evidence
- **Redundant queries**: Agent makes the same or very similar tool calls repeatedly
- **Tool misuse**: Agent uses the wrong tool for the task

### Calibration
Across all test cases, how well does the model's stated confidence correlate with accuracy?
```
calibration_error = mean(|confidence_i - accuracy_i|) across bins
```

## Epistemic Metrics (Not Mixed Into the Primary Score)

These are computed by `EpistemicScorer` and stored on `ScoreResult.epistemic`.

| Metric | Meaning |
|--------|---------|
| Evidence grounding | Cited evidence overlaps the case text |
| Calibration error | `|confidence - root_cause_score|` |
| Confirmation bias | High if only one hypothesis and no contradictory evidence |
| Hypothesis diversity | Scales with the number of competing explanations |
| Missing-info quality | For incomplete cases, whether the model asked about withheld fields |


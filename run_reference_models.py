#!/usr/bin/env python3
"""
Run deterministic reference models over EpiDebug.

These are not LLM results. They are calibration baselines used to verify that the
case corpus, scorer, result schema, and report generator behave sensibly before
running paid/provider-backed model evaluations.

Usage:
    python scripts/run_reference_models.py --all
    python scripts/run_reference_models.py --models oracle partial distractor
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from rich.console import Console
from rich.table import Table

from epidebug.schema import FailureCategory, ModelResponse, TestCase
from epidebug.scoring import EpiDebugScorer

console = Console()

REFERENCE_MODELS = ("oracle", "partial", "category_only", "distractor")


def _first_sentence(text: str) -> str:
    for sep in (". ", "\n"):
        if sep in text:
            return text.split(sep, 1)[0].strip() + "."
    return text.strip()


def _partial_root_cause(case: TestCase) -> str:
    if case.scoring.partial_credit_causes:
        cause, _credit = max(
            case.scoring.partial_credit_causes.items(),
            key=lambda item: item[1],
        )
        return cause
    if case.ground_truth.key_evidence:
        return case.ground_truth.key_evidence[0]
    return _first_sentence(case.ground_truth.root_cause)


def build_reference_response(case: TestCase, model_name: str) -> ModelResponse:
    """Construct a deterministic pseudo-response for a named reference model."""
    if model_name == "oracle":
        root_cause = case.ground_truth.root_cause
        causal_chain = list(case.ground_truth.causal_chain)
        intervention = case.ground_truth.intervention
    elif model_name == "partial":
        root_cause = _partial_root_cause(case)
        keep = max(2, min(4, len(case.ground_truth.causal_chain) // 2))
        causal_chain = list(case.ground_truth.causal_chain[:keep])
        intervention = _first_sentence(case.ground_truth.intervention)
    elif model_name == "category_only":
        root_cause = (
            f"This looks like a {case.failure_category.short_label.lower()} problem, "
            "but the specific variable is uncertain."
        )
        causal_chain = [
            "A problem in the broad failure category affected the experiment",
            "That category-level problem propagated into the observed symptoms",
        ]
        intervention = "Repeat the experiment while checking the broad suspected category."
    elif model_name == "distractor":
        alternative = (
            case.ground_truth.alternative_diagnoses[0]
            if case.ground_truth.alternative_diagnoses
            else "an unrelated operator or setup mistake"
        )
        root_cause = alternative
        causal_chain = [
            f"{alternative} occurred",
            "The suspected alternative caused the observed failure",
        ]
        intervention = f"Change or replace the factor associated with: {alternative}."
    else:
        raise ValueError(f"Unknown reference model: {model_name}")

    return ModelResponse(
        model_name=model_name,
        test_case_id=case.id,
        mode="text",
        root_cause=root_cause,
        causal_chain=causal_chain,
        intervention=intervention,
        raw_response=(
            f"### Root Cause\n{root_cause}\n\n"
            f"### Causal Chain\n"
            + "\n".join(f"{i + 1}. {step}" for i, step in enumerate(causal_chain))
            + f"\n\n### Intervention\n{intervention}\n"
        ),
    )


def load_cases(
    test_cases_dir: Path,
    run_all: bool,
    case_ids: tuple[str, ...],
    category: str | None,
) -> list[TestCase]:
    all_cases = TestCase.load_all(test_cases_dir)
    if case_ids:
        requested = set(case_ids)
        return [case for case in all_cases if case.id in requested]
    if category:
        return [case for case in all_cases if case.failure_category.value == category]
    if run_all:
        return all_cases
    return all_cases


@click.command()
@click.option("--all", "run_all", is_flag=True, help="Run all cases")
@click.option("--case", "case_ids", multiple=True, help="Specific case IDs")
@click.option("--category", type=click.Choice([c.value for c in FailureCategory]))
@click.option(
    "--models",
    multiple=True,
    default=REFERENCE_MODELS,
    help=f"Reference models to run. Choices: {', '.join(REFERENCE_MODELS)}",
)
@click.option("--output-dir", "-o", type=click.Path(), default="results/reference")
def main(
    run_all: bool,
    case_ids: tuple[str, ...],
    category: str | None,
    models: tuple[str, ...],
    output_dir: str,
):
    """Run deterministic reference baselines and save runner-compatible JSON."""
    project_root = Path(__file__).parent.parent
    test_cases_dir = project_root / "test_cases"
    cases = load_cases(test_cases_dir, run_all, case_ids, category)
    if not cases:
        console.print("[red]No matching test cases found.[/red]")
        sys.exit(1)

    unknown = sorted(set(models) - set(REFERENCE_MODELS))
    if unknown:
        console.print(f"[red]Unknown reference model(s): {', '.join(unknown)}[/red]")
        sys.exit(1)

    scorer = EpiDebugScorer()
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = project_root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_table = Table(title="Reference Baseline Results")
    summary_table.add_column("Model")
    summary_table.add_column("Cases", justify="right")
    summary_table.add_column("Root Cause", justify="right")
    summary_table.add_column("Causal Chain", justify="right")
    summary_table.add_column("Intervention", justify="right")
    summary_table.add_column("Final", justify="right")

    for model_name in models:
        result_entries: list[dict] = []
        for case in cases:
            response = build_reference_response(case, model_name)
            score = scorer.score_deterministic(case, response)
            result_entries.append(
                {
                    "test_case_id": case.id,
                    "model": model_name,
                    "mode": "text",
                    "run_index": 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "response": response.model_dump(mode="json"),
                    "score": score.model_dump(mode="json"),
                    "metadata": {
                        "reference_model": True,
                        "total_tokens": None,
                        "latency_seconds": 0.0,
                    },
                }
            )

        def avg(path: tuple[str, ...]) -> float:
            vals = []
            for entry in result_entries:
                value = entry["score"]
                for key in path:
                    value = value[key]
                vals.append(float(value))
            return sum(vals) / len(vals)

        output = {
            "benchmark": "epidebug",
            "version": "0.1.0",
            "model": model_name,
            "mode": "text",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_cases": len(cases),
            "reference_model": True,
            "results": result_entries,
        }
        output_path = out_dir / f"{model_name}.json"
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)

        summary_table.add_row(
            model_name,
            str(len(cases)),
            f"{avg(('root_cause_score', 'score')):.3f}",
            f"{avg(('causal_chain_score', 'score')):.3f}",
            f"{avg(('intervention_score', 'score')):.3f}",
            f"{avg(('final_score',)):.3f}",
        )

    console.print(summary_table)
    console.print(f"[green]Saved reference results to {out_dir}[/green]")


if __name__ == "__main__":
    main()

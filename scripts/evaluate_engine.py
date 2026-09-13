#!/usr/bin/env python3
"""Evaluate the Epistemic Debugging Engine against the benchmark.

The engine is treated as a model: it never reads ground truth. Results are
scored with the same multidimensional rubric used for LLM baselines.
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

from epidebug.engine import EpistemicDebuggingEngine
from epidebug.schema import EvaluationSplit, TestCase
from epidebug.scoring import EpiDebugScorer

console = Console()


@click.command()
@click.option("--split", type=click.Choice([s.value for s in EvaluationSplit] + ["all"]), default="all")
@click.option("--output", "-o", type=click.Path(), default="results/engine_heuristic.json")
@click.option("--llm/--heuristic", default=False, help="Use LLM engine if API key is configured")
def main(split: str, output: str, llm: bool):
    project_root = Path(__file__).parent.parent
    cases = TestCase.load_all(project_root / "test_cases")
    if split != "all":
        cases = [c for c in cases if c.split.value == split]
    if not cases:
        console.print("[red]No cases in the requested split.[/red]")
        sys.exit(1)

    engine = EpistemicDebuggingEngine(prefer_llm=llm)
    scorer = EpiDebugScorer()
    results = []

    for case in cases:
        diagnosis = engine.diagnose_case(case)
        response = diagnosis.to_model_response(model_name="epidebug-engine")
        score = scorer.score_deterministic(case, response)
        results.append({
            "test_case_id": case.id,
            "model": response.model_name,
            "mode": "text",
            "split": case.split.value,
            "information_regime": case.information_regime.value,
            "run_index": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "response": response.model_dump(mode="json"),
            "score": score.model_dump(mode="json"),
            "diagnosis": diagnosis.model_dump(mode="json"),
            "metadata": {"engine_mode": diagnosis.engine_mode, "reference_model": False},
        })

    out = Path(output)
    if not out.is_absolute():
        out = project_root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "benchmark": "epidebug",
        "version": "0.2.0",
        "model": "epidebug-engine",
        "mode": "text",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(cases),
        "results": results,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    table = Table(title="Engine evaluation")
    table.add_column("Case")
    table.add_column("Split")
    table.add_column("Root")
    table.add_column("Chain")
    table.add_column("Intervention")
    table.add_column("Final")
    table.add_column("Grounding")
    for row in results:
        s = row["score"]
        epi = s.get("epistemic") or {}
        table.add_row(
            row["test_case_id"],
            row["split"],
            f"{s['root_cause_score']['score']:.2f}",
            f"{s['causal_chain_score']['score']:.2f}",
            f"{s['intervention_score']['score']:.2f}",
            f"{s['final_score']:.2f}",
            f"{epi.get('evidence_grounding', 0):.2f}",
        )
    console.print(table)
    finals = [r["score"]["final_score"] for r in results]
    console.print(f"[green]Mean final score: {sum(finals)/len(finals):.3f}  ({out})[/green]")


if __name__ == "__main__":
    main()

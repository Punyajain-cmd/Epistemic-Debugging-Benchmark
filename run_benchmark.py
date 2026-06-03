#!/usr/bin/env python3
"""
EpiDebug Benchmark Runner

Run the Epistemic Debugging Benchmark against AI models.

Supports two modes:
  - text:  Present all information upfront, ask for diagnosis (no tools)
  - agent: Give the model tools to investigate (mock APIs, instruments)

Usage:
    python scripts/run_benchmark.py --model gpt-4o --all
    python scripts/run_benchmark.py --model claude-sonnet --category reagent_material_flaw
    python scripts/run_benchmark.py --model gemini-2.5-pro --case RF-001
    python scripts/run_benchmark.py --model gpt-4o --mode agent --case RF-001
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from epidebug.schema import (
    FailureCategory,
    ModelResponse,
    ScoreResult,
    TestCase,
)

console = Console()

# ---------------------------------------------------------------------------
# LLM Client abstraction
# ---------------------------------------------------------------------------

class LLMClient:
    """Unified interface for calling different LLM providers."""

    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self.provider = self._detect_provider(model)
        self.api_key = api_key or self._get_api_key()

    @staticmethod
    def _detect_provider(model: str) -> str:
        model_lower = model.lower()
        if any(x in model_lower for x in ("gpt", "o1", "o3", "o4")):
            return "openai"
        elif any(x in model_lower for x in ("claude", "sonnet", "opus", "haiku")):
            return "anthropic"
        elif any(x in model_lower for x in ("gemini", "gemma")):
            return "google"
        else:
            return "openai"  # Default fallback

    def _get_api_key(self) -> str:
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
        }
        key = os.environ.get(env_map.get(self.provider, "OPENAI_API_KEY"), "")
        if not key:
            console.print(
                f"[yellow]Warning: No API key found for {self.provider}. "
                f"Set {env_map.get(self.provider, 'OPENAI_API_KEY')} env var.[/yellow]"
            )
        return key

    def chat(self, prompt: str, system: str = "") -> tuple[str, dict]:
        """
        Send a prompt and return (response_text, metadata).
        metadata includes tokens, latency, etc.
        """
        start = time.time()

        if self.provider == "openai":
            return self._call_openai(prompt, system, start)
        elif self.provider == "anthropic":
            return self._call_anthropic(prompt, system, start)
        elif self.provider == "google":
            return self._call_google(prompt, system, start)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    def _call_openai(self, prompt: str, system: str, start: float) -> tuple[str, dict]:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Install openai: pip install 'epidebug[eval]'")

        client = OpenAI(api_key=self.api_key)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.2,
            max_tokens=4096,
        )
        latency = time.time() - start
        text = response.choices[0].message.content or ""
        metadata = {
            "total_tokens": response.usage.total_tokens if response.usage else None,
            "latency_seconds": round(latency, 2),
        }
        return text, metadata

    def _call_anthropic(self, prompt: str, system: str, start: float) -> tuple[str, dict]:
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("Install anthropic: pip install 'epidebug[eval]'")

        client = Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system or "You are a scientific researcher.",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        latency = time.time() - start
        text = response.content[0].text if response.content else ""
        metadata = {
            "total_tokens": (response.usage.input_tokens + response.usage.output_tokens)
            if response.usage
            else None,
            "latency_seconds": round(latency, 2),
        }
        return text, metadata

    def _call_google(self, prompt: str, system: str, start: float) -> tuple[str, dict]:
        try:
            from google import genai
        except ImportError:
            raise ImportError("Install google-genai: pip install 'epidebug[eval]'")

        client = genai.Client(api_key=self.api_key)
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        response = client.models.generate_content(
            model=self.model,
            contents=full_prompt,
        )
        latency = time.time() - start
        text = response.text or ""
        metadata = {
            "total_tokens": None,  # Gemini doesn't always expose this directly
            "latency_seconds": round(latency, 2),
        }
        return text, metadata


# ---------------------------------------------------------------------------
# Response Parser
# ---------------------------------------------------------------------------

def parse_model_response(raw: str, model_name: str, case_id: str, mode: str) -> ModelResponse:
    """Parse the model's free-text response into structured ModelResponse."""

    def extract_section(text: str, header: str) -> str:
        """Extract content under a markdown header."""
        pattern = rf"###?\s*{re.escape(header)}\s*\n(.*?)(?=\n###?\s|\Z)"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # Fallback: try bold markers
        pattern = rf"\*\*{re.escape(header)}\*\*[:\s]*(.*?)(?=\*\*|\Z)"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""

    root_cause = extract_section(raw, "Root Cause") or raw[:500]
    causal_chain_raw = extract_section(raw, "Causal Chain") or ""
    intervention = extract_section(raw, "Intervention") or ""

    # Parse causal chain into list (split on numbered items, bullet points, or arrows)
    chain_items = []
    if causal_chain_raw:
        # Try numbered list first
        items = re.split(r"\n\s*\d+[\.\)]\s*", causal_chain_raw)
        if len(items) <= 1:
            # Try bullet points
            items = re.split(r"\n\s*[-•→→]\s*", causal_chain_raw)
        if len(items) <= 1:
            # Try arrow notation
            items = re.split(r"\s*[→→]\s*", causal_chain_raw)
        chain_items = [item.strip() for item in items if item.strip()]

    if not chain_items:
        chain_items = [causal_chain_raw or "No causal chain provided"]

    return ModelResponse(
        model_name=model_name,
        test_case_id=case_id,
        mode=mode,
        root_cause=root_cause,
        causal_chain=chain_items,
        intervention=intervention,
        raw_response=raw,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--model", required=True, help="Model name (e.g., gpt-4o, claude-sonnet, gemini-2.5-pro)")
@click.option("--all", "run_all", is_flag=True, help="Run all test cases")
@click.option("--case", "case_ids", multiple=True, help="Specific case IDs (e.g., RF-001)")
@click.option("--category", type=click.Choice([c.value for c in FailureCategory]), help="Run a category")
@click.option("--mode", type=click.Choice(["text", "agent"]), default="text", help="Evaluation mode")
@click.option("--output", "-o", type=click.Path(), help="Output JSON file path")
@click.option("--repeat", type=int, default=1, help="Repeat each case N times")
@click.option("--dry-run", is_flag=True, help="Show prompts without calling the model")
@click.option("--api-key", type=str, help="API key (or set via env var)")
def main(
    model: str,
    run_all: bool,
    case_ids: tuple[str, ...],
    category: str | None,
    mode: str,
    output: str | None,
    repeat: int,
    dry_run: bool,
    api_key: str | None,
):
    """Run the Epistemic Debugging Benchmark."""

    project_root = Path(__file__).parent.parent
    test_cases_dir = project_root / "test_cases"

    # Load test cases
    all_cases = TestCase.load_all(test_cases_dir)
    if not all_cases:
        console.print("[red]No test cases found. Run scripts/validate_cases.py first.[/red]")
        sys.exit(1)

    # Filter
    cases_to_run: list[TestCase] = []
    if case_ids:
        id_set = set(case_ids)
        cases_to_run = [c for c in all_cases if c.id in id_set]
        missing = id_set - {c.id for c in cases_to_run}
        if missing:
            console.print(f"[yellow]Warning: Cases not found: {missing}[/yellow]")
    elif category:
        cases_to_run = [c for c in all_cases if c.failure_category.value == category]
    elif run_all:
        cases_to_run = all_cases
    else:
        console.print("[yellow]Specify --all, --case, or --category[/yellow]")
        sys.exit(1)

    if not cases_to_run:
        console.print("[red]No matching test cases found.[/red]")
        sys.exit(1)

    # Banner
    console.print(Panel.fit(
        f"[bold cyan]EpiDebug Benchmark Runner[/bold cyan]\n"
        f"Model: [green]{model}[/green]  |  Mode: [yellow]{mode}[/yellow]  |  "
        f"Cases: [white]{len(cases_to_run)}[/white]  |  Repeat: {repeat}",
        border_style="cyan",
    ))

    if dry_run:
        for case in cases_to_run:
            prompt = case.to_prompt(mode=mode)
            console.print(f"\n[bold]--- {case.id}: {case.title} ---[/bold]")
            console.print(Panel(prompt[:2000] + ("..." if len(prompt) > 2000 else ""),
                                title=f"Prompt ({len(prompt)} chars)"))
        return

    # Initialize LLM client
    client = LLMClient(model=model, api_key=api_key)

    # Import scorer
    try:
        from epidebug.scoring import EpiDebugScorer
        scorer = EpiDebugScorer()
    except ImportError:
        console.print("[yellow]Scoring module not available, will save raw responses only.[/yellow]")
        scorer = None

    results: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        for case in cases_to_run:
            for run_idx in range(repeat):
                run_label = f" (run {run_idx + 1}/{repeat})" if repeat > 1 else ""
                task = progress.add_task(
                    f"[cyan]{case.id}[/cyan] {case.title}{run_label}...", total=None
                )

                prompt = case.to_prompt(mode=mode)

                system_msg = (
                    "You are a world-class experimental scientist. You are analyzing "
                    "a failed experiment and must identify the root cause, trace the "
                    "causal chain, and propose a confirmatory intervention. "
                    "Be precise, scientific, and thorough."
                )

                raw_response, metadata = client.chat(prompt, system=system_msg)

                # Parse response
                model_response = parse_model_response(
                    raw_response, model, case.id, mode
                )
                model_response.total_tokens = metadata.get("total_tokens")
                model_response.latency_seconds = metadata.get("latency_seconds")

                # Score
                score_result = None
                if scorer:
                    try:
                        score_result = scorer.score_deterministic(case, model_response)
                    except Exception as e:
                        console.print(f"[yellow]Scoring error for {case.id}: {e}[/yellow]")

                result_entry = {
                    "test_case_id": case.id,
                    "model": model,
                    "mode": mode,
                    "run_index": run_idx,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "response": model_response.model_dump(mode="json"),
                    "score": score_result.model_dump(mode="json") if score_result else None,
                    "metadata": metadata,
                }
                results.append(result_entry)

                progress.update(task, completed=True)

    # Display results table
    if any(r.get("score") for r in results):
        table = Table(title="Results Summary")
        table.add_column("Case", style="cyan")
        table.add_column("Root Cause", justify="center")
        table.add_column("Causal Chain", justify="center")
        table.add_column("Intervention", justify="center")
        table.add_column("Final", style="bold", justify="center")
        table.add_column("Tokens", justify="right")
        table.add_column("Latency", justify="right")

        for r in results:
            if r.get("score"):
                s = r["score"]
                final = s["final_score"]
                color = "green" if final >= 0.7 else "yellow" if final >= 0.4 else "red"
                table.add_row(
                    r["test_case_id"],
                    f"{s['root_cause_score']['score']:.2f}",
                    f"{s['causal_chain_score']['score']:.2f}",
                    f"{s['intervention_score']['score']:.2f}",
                    f"[{color}]{final:.2f}[/{color}]",
                    str(r["metadata"].get("total_tokens", "?")),
                    f"{r['metadata'].get('latency_seconds', '?')}s",
                )

        console.print("\n")
        console.print(table)

        # Category breakdown
        if len(results) > 1:
            cat_scores: dict[str, list[float]] = {}
            for r in results:
                if r.get("score"):
                    case = next((c for c in cases_to_run if c.id == r["test_case_id"]), None)
                    if case:
                        cat = case.failure_category.short_label
                        cat_scores.setdefault(cat, []).append(r["score"]["final_score"])

            if cat_scores:
                console.print("\n[bold]Per-Category Averages:[/bold]")
                for cat, scores in sorted(cat_scores.items()):
                    avg = sum(scores) / len(scores)
                    bar = "█" * int(avg * 20) + "░" * (20 - int(avg * 20))
                    console.print(f"  {cat:<20} {bar} {avg:.2f}")

    # Save results
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(
                {
                    "benchmark": "epidebug",
                    "version": "0.1.0",
                    "model": model,
                    "mode": mode,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_cases": len(cases_to_run),
                    "results": results,
                },
                f,
                indent=2,
            )
        console.print(f"\n[green]Results saved to {output_path}[/green]")
    else:
        # Default save location
        default_output = project_root / "results" / f"{model}_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        default_output.parent.mkdir(parents=True, exist_ok=True)
        with open(default_output, "w") as f:
            json.dump(
                {
                    "benchmark": "epidebug",
                    "version": "0.1.0",
                    "model": model,
                    "mode": mode,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total_cases": len(cases_to_run),
                    "results": results,
                },
                f,
                indent=2,
            )
        console.print(f"\n[dim]Results auto-saved to {default_output}[/dim]")


if __name__ == "__main__":
    main()

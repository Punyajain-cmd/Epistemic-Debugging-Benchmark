#!/usr/bin/env python3
"""
Generate an HTML report from EpiDebug benchmark results.

Usage:
    python scripts/generate_report.py --input results/ --output report.html
    python scripts/generate_report.py --input results/gpt4o.json --output report.html
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from rich.console import Console

console = Console()

REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EpiDebug Benchmark Report</title>
    <style>
        :root {
            --bg-primary: #0f0f1a;
            --bg-secondary: #1a1a2e;
            --bg-card: #16213e;
            --accent: #00d4ff;
            --accent-secondary: #7b2ff7;
            --text-primary: #e0e0ff;
            --text-secondary: #8888aa;
            --success: #00e676;
            --warning: #ffab00;
            --error: #ff5252;
            --border: rgba(255, 255, 255, 0.08);
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
        }

        .container { max-width: 1200px; margin: 0 auto; padding: 2rem; }

        header {
            text-align: center;
            padding: 3rem 2rem;
            background: linear-gradient(135deg, var(--bg-secondary), var(--bg-card));
            border-bottom: 1px solid var(--border);
            margin-bottom: 2rem;
        }

        header h1 {
            font-size: 2.5rem;
            background: linear-gradient(135deg, var(--accent), var(--accent-secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
        }

        header .subtitle { color: var(--text-secondary); font-size: 1.1rem; }

        .meta-bar {
            display: flex;
            gap: 2rem;
            justify-content: center;
            margin-top: 1.5rem;
            flex-wrap: wrap;
        }

        .meta-item {
            background: rgba(255, 255, 255, 0.05);
            padding: 0.5rem 1.2rem;
            border-radius: 8px;
            font-size: 0.9rem;
        }

        .meta-item span { color: var(--accent); font-weight: 600; }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0, 212, 255, 0.1);
        }

        .card h3 {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            margin-bottom: 0.5rem;
        }

        .card .value {
            font-size: 2.5rem;
            font-weight: 700;
        }

        .score-good { color: var(--success); }
        .score-mid { color: var(--warning); }
        .score-low { color: var(--error); }

        table {
            width: 100%;
            border-collapse: collapse;
            margin: 1.5rem 0;
            background: var(--bg-card);
            border-radius: 12px;
            overflow: hidden;
        }

        th {
            background: var(--bg-secondary);
            padding: 1rem;
            text-align: left;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
        }

        td {
            padding: 0.8rem 1rem;
            border-bottom: 1px solid var(--border);
        }

        tr:last-child td { border-bottom: none; }
        tr:hover td { background: rgba(255, 255, 255, 0.02); }

        .bar-container {
            width: 100%;
            height: 8px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
            overflow: hidden;
        }

        .bar {
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s ease;
        }

        .category-section {
            margin: 2rem 0;
            padding: 1.5rem;
            background: var(--bg-card);
            border-radius: 12px;
            border: 1px solid var(--border);
        }

        .category-section h2 {
            font-size: 1.3rem;
            margin-bottom: 1rem;
            color: var(--accent);
        }

        .category-bar {
            display: flex;
            align-items: center;
            gap: 1rem;
            margin: 0.8rem 0;
        }

        .category-bar .label {
            min-width: 180px;
            font-size: 0.9rem;
        }

        .category-bar .bar-container { flex: 1; }
        .category-bar .score-value { min-width: 50px; text-align: right; font-weight: 600; }

        footer {
            text-align: center;
            padding: 2rem;
            color: var(--text-secondary);
            font-size: 0.85rem;
            border-top: 1px solid var(--border);
            margin-top: 3rem;
        }

        @media (max-width: 768px) {
            .container { padding: 1rem; }
            header h1 { font-size: 1.8rem; }
            .grid { grid-template-columns: 1fr; }
        }
    </style>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
</head>
<body>
    <header>
        <h1>🔬 EpiDebug Benchmark Report</h1>
        <p class="subtitle">Epistemic Debugging — Can AI diagnose why experiments fail?</p>
        <div class="meta-bar">
            {{meta_items}}
        </div>
    </header>

    <div class="container">
        <!-- Overview Cards -->
        <div class="grid">
            {{overview_cards}}
        </div>

        <!-- Category Breakdown -->
        <div class="category-section">
            <h2>📊 Performance by Failure Category</h2>
            {{category_bars}}
        </div>

        <!-- Detailed Results Table -->
        <div class="category-section">
            <h2>📋 Detailed Results</h2>
            <table>
                <thead>
                    <tr>
                        <th>Case ID</th>
                        <th>Title</th>
                        <th>Category</th>
                        <th>Root Cause</th>
                        <th>Causal Chain</th>
                        <th>Intervention</th>
                        <th>Final Score</th>
                    </tr>
                </thead>
                <tbody>
                    {{result_rows}}
                </tbody>
            </table>
        </div>

        <!-- Component Score Breakdown -->
        <div class="category-section">
            <h2>🎯 Component Score Distribution</h2>
            {{component_bars}}
        </div>
    </div>

    <footer>
        <p>Generated by EpiDebug v0.1.0 on {{timestamp}}</p>
    </footer>
</body>
</html>
"""


def score_class(score: float) -> str:
    if score >= 0.7:
        return "score-good"
    elif score >= 0.4:
        return "score-mid"
    return "score-low"


def bar_color(score: float) -> str:
    if score >= 0.7:
        return "linear-gradient(90deg, #00e676, #00c853)"
    elif score >= 0.4:
        return "linear-gradient(90deg, #ffab00, #ff9100)"
    return "linear-gradient(90deg, #ff5252, #d50000)"


def generate_report(result_files: list[Path]) -> str:
    """Generate HTML report from result JSON files."""

    all_results = []
    model_name = "Unknown"
    mode = "text"

    for rf in result_files:
        with open(rf) as f:
            data = json.load(f)
        model_name = data.get("model", model_name)
        mode = data.get("mode", mode)
        for r in data.get("results", []):
            if r.get("score"):
                all_results.append(r)

    if not all_results:
        return "<html><body><h1>No scored results found.</h1></body></html>"

    # Compute aggregates
    scores = [r["score"]["final_score"] for r in all_results]
    avg_score = sum(scores) / len(scores) if scores else 0
    rc_scores = [r["score"]["root_cause_score"]["score"] for r in all_results]
    cc_scores = [r["score"]["causal_chain_score"]["score"] for r in all_results]
    iv_scores = [r["score"]["intervention_score"]["score"] for r in all_results]
    avg_rc = sum(rc_scores) / len(rc_scores) if rc_scores else 0
    avg_cc = sum(cc_scores) / len(cc_scores) if cc_scores else 0
    avg_iv = sum(iv_scores) / len(iv_scores) if iv_scores else 0

    # Meta items
    meta_items = (
        f'<div class="meta-item">Model: <span>{model_name}</span></div>'
        f'<div class="meta-item">Mode: <span>{mode}</span></div>'
        f'<div class="meta-item">Cases: <span>{len(all_results)}</span></div>'
        f'<div class="meta-item">Avg Score: <span>{avg_score:.2f}</span></div>'
    )

    # Overview cards
    overview_cards = f"""
        <div class="card">
            <h3>Overall Score</h3>
            <div class="value {score_class(avg_score)}">{avg_score:.2f}</div>
        </div>
        <div class="card">
            <h3>Root Cause ID (30%)</h3>
            <div class="value {score_class(avg_rc)}">{avg_rc:.2f}</div>
        </div>
        <div class="card">
            <h3>Causal Chain (40%)</h3>
            <div class="value {score_class(avg_cc)}">{avg_cc:.2f}</div>
        </div>
        <div class="card">
            <h3>Intervention (30%)</h3>
            <div class="value {score_class(avg_iv)}">{avg_iv:.2f}</div>
        </div>
    """

    # Category breakdown
    cat_scores: dict[str, list[float]] = {}
    for r in all_results:
        cat = r.get("test_case_id", "??")[:2]
        cat_map = {"RF": "Reagent/Material Flaw", "IN": "Instrumentation Artifact",
                    "PL": "Protocol/Human Loophole", "FH": "Flawed Hypothesis"}
        cat_name = cat_map.get(cat, cat)
        cat_scores.setdefault(cat_name, []).append(r["score"]["final_score"])

    category_bars = ""
    for cat_name, cat_s in sorted(cat_scores.items()):
        avg = sum(cat_s) / len(cat_s)
        category_bars += f"""
        <div class="category-bar">
            <div class="label">{cat_name}</div>
            <div class="bar-container">
                <div class="bar" style="width: {avg * 100}%; background: {bar_color(avg)};"></div>
            </div>
            <div class="score-value {score_class(avg)}">{avg:.2f}</div>
        </div>
        """

    # Result rows
    result_rows = ""
    for r in all_results:
        s = r["score"]
        final = s["final_score"]
        result_rows += f"""
        <tr>
            <td><strong>{r['test_case_id']}</strong></td>
            <td>{r.get('test_case_id', '')}</td>
            <td>{r['test_case_id'][:2]}</td>
            <td class="{score_class(s['root_cause_score']['score'])}">{s['root_cause_score']['score']:.2f}</td>
            <td class="{score_class(s['causal_chain_score']['score'])}">{s['causal_chain_score']['score']:.2f}</td>
            <td class="{score_class(s['intervention_score']['score'])}">{s['intervention_score']['score']:.2f}</td>
            <td class="{score_class(final)}"><strong>{final:.2f}</strong></td>
        </tr>
        """

    # Component score bars
    component_bars = ""
    for label, avg_val in [("Root Cause Identification", avg_rc),
                            ("Causal Chain Reasoning", avg_cc),
                            ("Intervention Strategy", avg_iv)]:
        component_bars += f"""
        <div class="category-bar">
            <div class="label">{label}</div>
            <div class="bar-container">
                <div class="bar" style="width: {avg_val * 100}%; background: {bar_color(avg_val)};"></div>
            </div>
            <div class="score-value {score_class(avg_val)}">{avg_val:.2f}</div>
        </div>
        """

    # Fill template
    html = REPORT_TEMPLATE
    html = html.replace("{{meta_items}}", meta_items)
    html = html.replace("{{overview_cards}}", overview_cards)
    html = html.replace("{{category_bars}}", category_bars)
    html = html.replace("{{result_rows}}", result_rows)
    html = html.replace("{{component_bars}}", component_bars)
    html = html.replace("{{timestamp}}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    return html


@click.command()
@click.option("--input", "-i", "input_path", required=True, type=click.Path(exists=True),
              help="Path to results JSON file or directory")
@click.option("--output", "-o", "output_path", required=True, type=click.Path(),
              help="Output HTML file path")
def main(input_path: str, output_path: str):
    """Generate an HTML report from EpiDebug benchmark results."""

    input_p = Path(input_path)

    if input_p.is_dir():
        result_files = sorted(input_p.glob("*.json"))
    else:
        result_files = [input_p]

    if not result_files:
        console.print("[red]No JSON result files found.[/red]")
        sys.exit(1)

    console.print(f"[cyan]Generating report from {len(result_files)} result file(s)...[/cyan]")

    html = generate_report(result_files)

    output_p = Path(output_path)
    output_p.parent.mkdir(parents=True, exist_ok=True)
    with open(output_p, "w") as f:
        f.write(html)

    console.print(f"[green]✅ Report saved to {output_p}[/green]")


if __name__ == "__main__":
    main()

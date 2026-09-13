#!/usr/bin/env python3
"""
Validate all test case YAML files against the EpiDebug schema.

Usage:
    python scripts/validate_cases.py --all
    python scripts/validate_cases.py --case test_cases/reagent_flaw/RF-001_buffer_ph_drift.yaml
    python scripts/validate_cases.py --category reagent_flaw
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from rich.console import Console
from rich.table import Table

from epidebug.schema import TestCase

console = Console()


def validate_file(path: Path) -> tuple[bool, str, str | None]:
    """Validate a single YAML file. Returns (success, case_id_or_filename, error_msg)."""
    try:
        case = TestCase.from_yaml(path)
        return True, case.id, None
    except Exception as e:
        return False, path.name, str(e)


@click.command()
@click.option("--all", "validate_all", is_flag=True, help="Validate all test cases")
@click.option("--case", "case_path", type=click.Path(exists=True), help="Validate a single file")
@click.option("--category", type=str, help="Validate all cases in a category subdirectory")
@click.option("--verbose", "-v", is_flag=True, help="Show details for passing cases too")
def main(validate_all: bool, case_path: str | None, category: str | None, verbose: bool):
    """Validate EpiDebug test case YAML files against the schema."""

    project_root = Path(__file__).parent.parent
    test_cases_dir = project_root / "test_cases"

    files_to_validate: list[Path] = []

    if case_path:
        files_to_validate = [Path(case_path)]
    elif category:
        cat_dir = test_cases_dir / category
        if not cat_dir.exists():
            console.print(f"[red]Category directory not found: {cat_dir}[/red]")
            sys.exit(1)
        files_to_validate = sorted(cat_dir.glob("*.yaml"))
    elif validate_all:
        files_to_validate = sorted(test_cases_dir.rglob("*.yaml"))
        # Exclude manifest
        files_to_validate = [f for f in files_to_validate if f.name not in {"manifest.yaml", "splits.yaml", "catalog.yaml"}]
    else:
        console.print("[yellow]Specify --all, --case, or --category[/yellow]")
        sys.exit(1)

    if not files_to_validate:
        console.print("[yellow]No YAML files found to validate.[/yellow]")
        sys.exit(0)

    console.print(f"\n[bold]Validating {len(files_to_validate)} test case(s)...[/bold]\n")

    table = Table(title="Validation Results")
    table.add_column("Status", style="bold", width=8)
    table.add_column("ID / File", style="cyan")
    table.add_column("Path", style="dim")
    table.add_column("Error", style="red", max_width=60)

    passed = 0
    failed = 0

    for filepath in files_to_validate:
        success, case_id, error = validate_file(filepath)
        rel_path = str(filepath.relative_to(project_root))

        if success:
            passed += 1
            if verbose:
                table.add_row("PASS", case_id, rel_path, "")
        else:
            failed += 1
            table.add_row("FAIL", case_id, rel_path, error or "Unknown error")

    if verbose or failed > 0:
        console.print(table)

    # Summary
    console.print(f"\n[bold]Results: {passed} passed, {failed} failed "
                  f"out of {len(files_to_validate)} total[/bold]")

    if failed == 0:
        console.print("[green]All test cases are valid.[/green]\n")
    else:
        console.print("[red]Some test cases have validation errors.[/red]\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

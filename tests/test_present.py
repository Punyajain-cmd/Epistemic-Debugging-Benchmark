"""Tests for the additive session view contract."""

from epidebug.engine.ingest import ingest_bytes
from epidebug.engine.present import VIEW_CONTRACT, build_ingest_view, coverage_from_experiment
from epidebug.schema import ExperimentInput


def test_coverage_marks_missing_cad_and_logs():
    exp = ExperimentInput(
        title="Thin dump",
        unexpected_outcome="Part out of spec",
        objective="Machine a bore",
        materials=["6082-T6"],
    )
    slots = {row["id"]: row["present"] for row in coverage_from_experiment(exp)}
    assert slots["failure"] is True
    assert slots["materials"] is True
    assert slots["cad"] is False
    assert slots["logs"] is False
    assert slots["photos"] is False


def test_ingest_view_gallery_and_summaries():
    csv = ingest_bytes("pack_temp.csv", b"t,temp_C\n0,20\n1,40\n2,90\n")
    log = ingest_bytes("charger.log", b"ERROR overheat pack_main\n")
    exp = ExperimentInput(
        title="Pack",
        unexpected_outcome="Vented",
        artifacts=[csv, log],
    )
    ingest = build_ingest_view(exp)
    assert ingest["file_count"] == 2
    assert ingest["kind_counts"]["sensor"] == 1
    assert ingest["kind_counts"]["log"] == 1
    assert ingest["gallery"]["tables"][0]["filename"] == "pack_temp.csv"
    assert ingest["gallery"]["logs"][0]["summary"]
    assert ingest["source"] == "dump"


def test_catalog_ingest_is_empty_not_false_missing():
    ingest = build_ingest_view(None, catalog=True)
    assert ingest["coverage"] == []
    assert ingest["completeness"] is None
    assert ingest["source"] == "catalog"
    assert VIEW_CONTRACT == "0.5"
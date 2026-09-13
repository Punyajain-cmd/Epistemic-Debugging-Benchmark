"""Tests for heterogeneous experiment ingest (files + notes)."""

from pathlib import Path

from epidebug.engine import EpistemicDebuggingEngine
from epidebug.engine.ingest import ingest_bytes
from epidebug.schema import ExperimentInput


def test_ingest_sensor_csv():
    csv = b"time_s,temp_C,current_A\n0,25.1,1.02\n1,48.8,3.4\n2,71.2,3.5\n3,94.0,3.6\n"
    art = ingest_bytes("pack_temp.csv", csv, caption="Thermistor on cell 4")
    assert art.kind.value == "sensor"
    assert "temp_C" in art.extracted_text
    assert art.stats["rows"] == 4
    assert "Thermistor" in art.summary


def test_ingest_log_flags_errors():
    log = b"boot ok\nERROR overheat on motor_2\nWARN encoder skip\n"
    art = ingest_bytes("drive.log", log)
    assert art.kind.value == "log"
    assert art.stats["flagged"]


def test_ingest_cad_step_names():
    step = b"ISO-10303-21;\nHEADER;\nFILE_NAME('housing.step','');\nENDSEC;\nDATA;\n#1=PRODUCT('6082-T6-housing','part','');\nENDSEC;"
    art = ingest_bytes("housing.step", step)
    assert art.kind.value == "cad"
    assert "6082-T6-housing" in art.summary or "6082" in art.extracted_text


def test_ingest_image_uses_filename_cues():
    # Minimal 1x1 PNG
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01"
        b"\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    art = ingest_bytes("corrosion_coupon_setup.png", png, caption="After salt spray")
    assert art.kind.value == "image"
    assert "corrosion" in art.extracted_text.lower()
    assert "salt spray" in art.summary.lower()


def test_engine_uses_uploaded_artifacts():
    csv = b"cycle,capacity_mAh,temp_C\n1,2400,28\n2,2310,41\n3,1800,78\n4,900,94\n"
    art = ingest_bytes("cell_cycle.csv", csv)
    engine = EpistemicDebuggingEngine(prefer_llm=False)
    exp = ExperimentInput(
        title="Coin cell fade",
        domain="energy",
        unexpected_outcome="Capacity collapsed and the can is warm after 2C charge",
        materials=["LiPF6 electrolyte from an opened bottle", "NMC532 cathode"],
        processing=["Cells filled in dry room then crimped; electrolyte sat uncapped 40 min"],
        artifacts=[art],
    )
    diagnosis = engine.diagnose_experiment(exp)
    blob = (diagnosis.leading_cause + " " + " ".join(h.statement for h in diagnosis.hypotheses)).lower()
    assert diagnosis.hypotheses
    assert diagnosis.anomalies
    assert any(tok in blob for tok in ("electrolyte", "water", "moisture", "heat", "battery", "sensor"))

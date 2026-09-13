"""Tests for heterogeneous experiment ingest (files + notes)."""

import struct

from epidebug.engine import EpistemicDebuggingEngine
from epidebug.engine.ingest import classify, ingest_bytes, suggest_role
from epidebug.schema import ArtifactKind, ArtifactRole, ExperimentInput


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01"
    b"\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_ingest_sensor_csv():
    csv = b"time_s,temp_C,current_A\n0,25.1,1.02\n1,48.8,3.4\n2,71.2,3.5\n3,94.0,3.6\n"
    art = ingest_bytes("pack_temp.csv", csv, caption="Thermistor on cell 4")
    assert art.kind.value == "sensor"
    assert "temp_C" in art.extracted_text
    assert art.stats["rows"] == 4
    assert "Thermistor" in art.summary
    assert art.stats["units"]["temp_C"] == "C"
    assert art.stats["time_column"] == "time_s"
    assert art.stats["numeric"]["temp_C"]["last"] > art.stats["numeric"]["temp_C"]["first"]
    assert any("rising" in note for note in art.stats["flags"])
    assert art.stats["anomalies"]
    assert any(item["kind"] == "trend" for item in art.stats["anomalies"])


def test_ingest_log_flags_errors():
    log = (
        b"2024-08-12 02:14:01 INFO boot ok\n"
        b"ERROR overheat on motor_2\n"
        b"WARN encoder skip\n"
        b"ALARM-401 spindle\n"
    )
    art = ingest_bytes("drive.log", log)
    assert art.kind.value == "log"
    assert art.stats["flagged"]
    assert art.stats["severity"]["error"] >= 1
    assert art.stats["severity"]["warn"] >= 1
    assert art.stats["alarm_codes"]
    assert "error" in art.summary.lower() or "fault" in art.summary.lower()
    assert art.stats["error_samples"]["count"] >= 2
    assert art.stats["error_samples"]["first"]
    assert any("overheat" in line.lower() for line in art.stats["error_samples"]["unique"])


def test_ingest_cad_step_names():
    step = (
        b"ISO-10303-21;\nHEADER;\nFILE_NAME('housing.step','');\n"
        b"FILE_DESCRIPTION(('REV C bore fixture'),'2;1');\n"
        b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\n"
        b"#1=PRODUCT('6082-T6-housing','part','');\nENDSEC;"
    )
    art = ingest_bytes("housing.step", step)
    assert art.kind.value == "cad"
    assert "6082-T6-housing" in art.summary or "6082" in art.extracted_text
    assert art.stats.get("schema")
    assert art.role == ArtifactRole.CAD


def test_ingest_image_uses_filename_cues():
    art = ingest_bytes("corrosion_coupon_setup.png", PNG, caption="After salt spray")
    assert art.kind.value == "image"
    assert "corrosion" in art.extracted_text.lower()
    assert "salt spray" in art.summary.lower()
    assert art.preview_url


def test_classify_uses_magic_bytes_without_extension():
    kind, mime = classify("untitled", PNG)
    assert kind == ArtifactKind.IMAGE
    assert mime == "image/png"

    kind, mime = classify("dump", b"%PDF-1.4\n1 0 obj\n")
    assert kind == ArtifactKind.DOCUMENT
    assert mime == "application/pdf"

    kind, mime = classify("part", b"ISO-10303-21;\nHEADER;\n")
    assert kind == ArtifactKind.CAD

    kind, mime = classify(
        "table",
        b"time,temp_C\n0,21\n1,22\n2,40\n",
        content_type="text/csv",
    )
    assert kind == ArtifactKind.SENSOR


def test_ingest_binary_stl_triangle_count():
    header = b"fixture" + b"\x00" * 73
    payload = header + struct.pack("<I", 2) + (b"\x00" * 100)
    art = ingest_bytes("fixture.stl", payload)
    assert art.kind == ArtifactKind.CAD
    assert art.stats["triangles"] == 2
    assert "2 triangles" in art.summary


def test_ingest_json_sensor_records():
    blob = (
        b'[{"t":0,"temp_C":25.0,"A":1.1},'
        b'{"t":1,"temp_C":40.2,"A":1.2},'
        b'{"t":2,"temp_C":88.0,"A":1.3}]'
    )
    art = ingest_bytes("cell_pack.json", blob)
    assert art.kind == ArtifactKind.SENSOR
    assert art.stats["records"] == 3
    assert "temp_C" in art.stats["numeric"]
    assert "88" in art.summary


def test_ingest_gcode_process_role():
    nc = b"(REV C bore)\nT4 M6\nS4200 M3\nG1 Z-12.0 F180\nM30\n"
    art = ingest_bytes("bore_finish.nc", nc)
    assert art.kind == ArtifactKind.DOCUMENT
    assert art.role == ArtifactRole.PROCESS_DOC
    assert art.suggested_role == ArtifactRole.PROCESS_DOC
    assert "T4" in art.summary
    assert art.stats["feed"]["max"] == 180


def test_role_suggestions_for_docs():
    assert (
        suggest_role("mill_cert_lot24081.pdf", ArtifactKind.DOCUMENT)
        == ArtifactRole.MATERIAL_DOC
    )
    assert suggest_role("electrolyte_sds.pdf", ArtifactKind.DOCUMENT) == ArtifactRole.DATASHEET
    assert suggest_role("traveler_sop.md", ArtifactKind.DOCUMENT) == ArtifactRole.PROCESS_DOC
    assert suggest_role("failed_vent_can.jpg", ArtifactKind.IMAGE) == ArtifactRole.RESULT_IMAGE
    art = ingest_bytes("housing.step", b"ISO-10303-21;\n", role="cad")
    assert art.role == ArtifactRole.CAD
    assert art.suggested_role == ArtifactRole.CAD


def test_ingest_pdf_fallback_or_text():
    art = ingest_bytes("mystery.pdf", b"%PDF-1.4\n% not a real document\n")
    assert art.kind == ArtifactKind.DOCUMENT
    assert "PDF" in art.summary or art.extracted_text is not None


def test_ingest_notebook_heading():
    nb = (
        b'{"nbformat":4,"nbformat_minor":5,"cells":['
        b'{"cell_type":"markdown","source":["# Cell fade notes\\n"]},'
        b'{"cell_type":"code","source":["print(1)"],"outputs":[{"text":"1\\n"}]}'
        b"]}"
    )
    art = ingest_bytes("notes.ipynb", nb)
    assert art.kind == ArtifactKind.NOTEBOOK
    assert art.stats["cells"] == 2
    assert "Cell fade" in art.summary


def test_ingest_svg_diagram_and_role():
    svg = (
        b'<?xml version="1.0"?>\n'
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40" width="120" height="40">'
        b"<title>Vise layout</title>"
        b'<text x="4" y="22">jaw gap 12 mm</text>'
        b"</svg>"
    )
    art = ingest_bytes("setup_vise_layout.svg", svg)
    assert art.kind == ArtifactKind.IMAGE
    assert art.mime_type == "image/svg+xml"
    assert art.stats["title"] == "Vise layout"
    assert art.stats["viewBox"] == "0 0 120 40"
    assert "jaw gap 12 mm" in art.stats["text_labels"]
    assert "Vise layout" in art.summary
    assert art.role == ArtifactRole.SETUP_PHOTO
    assert art.preview_url

    failed = ingest_bytes("failed_bore.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    assert failed.kind == ArtifactKind.IMAGE
    assert failed.role == ArtifactRole.RESULT_IMAGE

    kind, mime = classify("untitled", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    assert kind == ArtifactKind.IMAGE
    assert mime == "image/svg+xml"


def test_ingest_stl_stubs_do_not_crash():
    ascii_stub = ingest_bytes("housing_stub.stl", b"solid stub\nendsolid stub\n")
    assert ascii_stub.kind == ArtifactKind.CAD
    assert ascii_stub.stats.get("facets_seen", 0) == 0
    assert ascii_stub.stats.get("stub") is True
    assert "stub" in ascii_stub.summary.lower()

    empty = ingest_bytes("empty.stl", b"")
    assert empty.kind == ArtifactKind.CAD
    assert empty.stats.get("stub") is True

    tiny = ingest_bytes("tiny.stl", b"\x00" * 20)
    assert tiny.kind == ArtifactKind.CAD
    assert tiny.stats.get("stub") is True
    assert tiny.stats.get("triangles", 0) == 0

    zero_tri = ingest_bytes("zero.stl", b"fixture" + b"\x00" * 73 + struct.pack("<I", 0))
    assert zero_tri.kind == ArtifactKind.CAD
    assert zero_tri.stats["triangles"] == 0
    assert zero_tri.stats.get("stub") is True


def test_ingest_markdown_heading_and_process_role():
    md = (
        b"# Traveler SOP\n\n"
        b"Mill 6082-T6 housing per protocol.\n"
        b"Lot 24-081 material cert is attached.\n"
    )
    art = ingest_bytes("NOTES.md", md)
    assert art.kind == ArtifactKind.DOCUMENT
    assert art.mime_type == "text/markdown"
    assert art.stats["heading"] == "Traveler SOP"
    assert "Traveler SOP" in art.summary
    assert art.role == ArtifactRole.PROCESS_DOC


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

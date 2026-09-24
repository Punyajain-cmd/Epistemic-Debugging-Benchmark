"""Compact multimodal extracts must stay inside hard caps and out of LLM prompts."""

import base64
import struct

from epidebug.engine import EpistemicDebuggingEngine
from epidebug.engine.engine import _blob_from_experiment, build_diagnosis_prompt
from epidebug.engine.ingest import artifact_blob, classify, ingest_bytes
from epidebug.engine.preprocess import (
    MAX_EXCERPT_CHARS,
    MAX_EXCERPT_TOTAL,
    MAX_EXCERPTS,
    MAX_PROMPT_CHARS,
    MAX_SUMMARY_CHARS,
    build_evidence,
    prompt_from_artifact,
)
from epidebug.engine.present import session_payload
from epidebug.engine.session import SessionStore
from epidebug.schema import ArtifactKind, ExperimentArtifact, ExperimentInput


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01"
    b"\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

MIDDLE = "UNIQUE_MIDDLE_CELL_ZZQ9"
TAIL = "TAIL_ROW_SHOULD_NOT_ENTER_PROMPT"
JSON_BODY = "JSON_BODY_SENTINEL_QQ"
LOG_BODY = "LOG_BODY_NOT_IN_PROMPT_77"


def _large_csv() -> bytes:
    rows = ["i,temp_C,note"]
    for i in range(2000):
        note = "ok"
        if i == 1000:
            note = MIDDLE
        if i == 1999:
            note = TAIL
        rows.append(f"{i},{20 + i * 0.01:.4f},{note}")
    return ("\n".join(rows) + "\n").encode()


def _assert_bounded(art):
    evidence = art.evidence
    assert evidence is not None
    assert len(evidence.summary) <= MAX_SUMMARY_CHARS
    assert len(evidence.excerpts) <= MAX_EXCERPTS
    assert all(len(item) <= MAX_EXCERPT_CHARS for item in evidence.excerpts)
    assert sum(len(item) for item in evidence.excerpts) <= MAX_EXCERPT_TOTAL
    assert len(prompt_from_artifact(art)) <= MAX_PROMPT_CHARS
    assert len(artifact_blob(art)) <= MAX_PROMPT_CHARS
    assert evidence.omitted


def test_csv_prompt_excludes_full_table():
    raw = _large_csv()
    art = ingest_bytes("pack_sensors.csv", raw)
    _assert_bounded(art)
    assert art.evidence.modality == "csv"
    assert art.stats["rows"] == 2000
    assert "temp_C" in art.evidence.stats["numeric"]
    blob = artifact_blob(art)
    exp = ExperimentInput(
        title="Pack vent",
        unexpected_outcome="Cells vented on charge",
        artifacts=[art],
    )
    dossier = _blob_from_experiment(exp)
    prompt = build_diagnosis_prompt(dossier, exp.title, exp.domain)
    for leaked in (MIDDLE, TAIL, raw.decode()):
        assert leaked not in blob
        assert leaked not in dossier
        assert leaked not in prompt
        assert leaked not in art.extracted_text
    assert "full table omitted" in blob
    assert "compact extracts" in prompt
    assert art.evidence.stats["numeric"]["temp_C"]["max"] > art.evidence.stats["numeric"]["temp_C"]["min"]


def test_json_prompt_excludes_record_bodies():
    rows = [{"t": i, "temp_C": 20 + i, "note": "ok"} for i in range(400)]
    rows[250]["note"] = JSON_BODY
    import json

    raw = json.dumps(rows).encode()
    art = ingest_bytes("cells.json", raw)
    _assert_bounded(art)
    assert art.evidence.modality == "json"
    blob = artifact_blob(art)
    assert JSON_BODY not in blob
    assert JSON_BODY not in art.extracted_text
    assert "full JSON payload omitted" in blob
    assert art.evidence.stats["numeric"]["temp_C"]["max"] >= 400


def test_log_and_text_keep_faults_not_the_transcript():
    lines = ["2024-08-12 02:14:01 INFO boot ok"]
    lines.append("ERROR overheat on motor_2")
    lines.extend(f"INFO filler {i} nominal" for i in range(400))
    lines.append(f"INFO {LOG_BODY}")
    lines.append("WARN encoder skip")
    raw = ("\n".join(lines) + "\n").encode()
    art = ingest_bytes("drive.log", raw)
    _assert_bounded(art)
    assert art.evidence.modality == "log"
    blob = artifact_blob(art)
    assert "overheat" in blob
    assert LOG_BODY not in blob
    assert raw.decode() not in blob
    assert "full transcript omitted" in blob

    prose = ("Intro line about the fixture.\n" + ("padding sentence. " * 80) + "\nTEXT_TAIL_SENTINEL_42\n").encode()
    doc = ingest_bytes("notes.md", prose)
    assert doc.evidence.modality == "text"
    assert "TEXT_TAIL_SENTINEL_42" not in artifact_blob(doc)
    assert "rest of the text omitted" in artifact_blob(doc)


def test_image_has_no_base64_or_pixel_payload():
    art = ingest_bytes("corrosion_coupon_setup.png", PNG)
    _assert_bounded(art)
    assert art.evidence.modality == "image"
    blob = artifact_blob(art)
    assert base64.b64encode(PNG).decode() not in blob
    assert "IDATx" not in blob
    assert "pixel buffer omitted" in blob
    assert "corrosion" in art.extracted_text.lower()


def test_pdf_excerpt_is_capped():
    hint = "PAGEONE opening hazard temperature. " + ("word " * 200) + "PDF_TAIL_SENTINEL"
    evidence = build_evidence(
        filename="sds.pdf",
        kind="document",
        data=b"%PDF-1.4\n",
        stats={"pages": 12},
        summary="PDF with 12 pages.",
        mime="application/pdf",
        text_hint=hint,
    )
    assert evidence.modality == "pdf"
    assert evidence.excerpts
    assert "PDF_TAIL_SENTINEL" not in " ".join(evidence.excerpts)
    assert all(len(item) <= MAX_EXCERPT_CHARS for item in evidence.excerpts)
    assert "rest of the PDF omitted" in evidence.omitted[0]
    art = ingest_bytes("mystery.pdf", b"%PDF-1.4\n% not a real document\n")
    assert art.evidence.modality == "pdf"
    assert art.evidence.omitted


def test_stl_reports_bbox_without_dumping_mesh():
    def tri(a, b, c):
        return struct.pack("<12fH", 0, 0, 1, *a, *b, *c, 0)

    mesh = tri((0, 0, 0), (1, 0, 0), (0, 1, 0))
    mesh += tri((1, 0, 0), (0, 1, 0), (0, 0, 1))
    payload = b"fixture" + b"\x00" * 73 + struct.pack("<I", 2) + mesh
    art = ingest_bytes("fixture.stl", payload)
    _assert_bounded(art)
    assert art.evidence.modality == "stl"
    bbox = art.evidence.stats["bbox"]
    assert bbox[0] == 0 and bbox[3] == 1
    assert bbox[5] == 1
    assert art.evidence.stats["triangles"] == 2
    assert art.evidence.stats["volume_stl_units"] > 0
    assert "raw mesh omitted" in artifact_blob(art)
    assert payload not in artifact_blob(art).encode("utf-8", errors="replace")


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def test_video_is_metadata_stub_only():
    mvhd = bytes(4) + struct.pack(">II", 0, 0) + struct.pack(">II", 1000, 2500) + bytes(80)
    data = _box(b"ftyp", b"isom" + struct.pack(">I", 0) + b"isom")
    data += _box(b"moov", _box(b"mvhd", mvhd))
    data += b"FRAMEPAYLOAD_SHOULD_NOT_LEAK" + b"\x00" * 64
    art = ingest_bytes("setup_bench.mp4", data)
    _assert_bounded(art)
    assert art.kind == ArtifactKind.VIDEO
    assert art.evidence.modality == "video"
    assert art.evidence.stats["container"] == "mp4"
    assert art.evidence.stats["duration_s"] == 2.5
    blob = artifact_blob(art)
    assert "FRAMEPAYLOAD_SHOULD_NOT_LEAK" not in blob
    assert "frame payloads omitted" in blob
    kind, mime = classify("untitled", data[:32])
    assert kind == ArtifactKind.VIDEO
    assert mime == "video/mp4"


def test_legacy_artifact_without_extract_does_not_leak_text():
    art = ExperimentArtifact(
        id="legacy",
        filename="huge.csv",
        kind=ArtifactKind.SENSOR,
        extracted_text="FULL_ROW_SHOULD_NOT_LEAK," * 50,
        summary="Table with many rows.",
    )
    blob = artifact_blob(art)
    assert "FULL_ROW_SHOULD_NOT_LEAK" not in blob
    assert "raw text withheld" in blob
    assert len(blob) <= MAX_PROMPT_CHARS


def test_session_view_exposes_extracts_and_chat_says_what_was_omitted(tmp_path):
    art = ingest_bytes("pack_sensors.csv", _large_csv(), caption="Thermistor")
    engine = EpistemicDebuggingEngine(
        prefer_llm=False,
        sessions=SessionStore(tmp_path),
    )
    session = engine.open_session(experiment=ExperimentInput(
        title="Pack vent",
        unexpected_outcome="Cells vented during 2C charge",
        artifacts=[art],
    ))
    view = session_payload(session)["view"]
    assert view["extracts"]
    assert view["extracts"][0]["modality"] == "csv"
    assert view["artifacts"][0]["evidence"]["modality"] == "csv"
    assert "full table omitted" in view["extracts"][0]["omitted"][0]
    transcript = "\n".join(message.content for message in session.messages)
    assert "What reached the model" in transcript
    assert "pack_sensors.csv" in transcript
    assert "Omitted:" in transcript
    assert MIDDLE not in transcript
    assert TAIL not in transcript

"""Turn dumped lab/shop files into text the diagnostic engine can reason over.

Researchers should be able to drop whatever they have: setup photos, CAD,
sensor CSVs, machine logs, datasheets, process notes. This module classifies
each file (extension + magic bytes + optional Content-Type) and extracts a
compact, lossy summary — not a perfect reconstruction.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import struct
import uuid
from pathlib import Path
from typing import Any

from epidebug.schema import ArtifactKind, ArtifactRole, ExperimentArtifact

MAX_EXTRACT_CHARS = 12000
MAX_READ_BYTES = 400_000
MAX_SUMMARY_CHARS = 500

IMAGE_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff",
    ".bmp", ".heic", ".heif", ".avif",
}
CAD_EXT = {
    ".stl", ".step", ".stp", ".iges", ".igs", ".dxf", ".obj", ".3mf",
    ".sldprt", ".sldasm", ".ipt", ".fcstd", ".ply", ".dae", ".dwg",
    ".prt", ".catpart", ".3dxml", ".x_t", ".x_b",
}
SENSOR_EXT = {
    ".csv", ".tsv", ".xlsx", ".xls", ".json", ".parquet",
    ".dat", ".h5", ".hdf5", ".tdms", ".npz",
}
LOG_EXT = {
    ".log", ".txt", ".out", ".err", ".serial", ".nmea", ".ulog",
    ".blf", ".asc", ".syslog", ".dmesg",
}
PROCESS_EXT = {".nc", ".gcode", ".tap", ".cnc", ".ngc", ".mpf", ".pgm"}
DOC_EXT = {
    ".pdf", ".md", ".rst", ".docx", ".html", ".htm", ".xml",
    ".yaml", ".yml", ".rtf", ".tex", ".csvx",
}
NOTEBOOK_EXT = {".ipynb"}
MATERIAL_EXT = {".bom", ".coa"}

ERROR_WORDS = (
    "error", "fail", "alarm", "fault", "warn", "crash", "overheat",
    "leak", "nan", "estop", "e-stop", "abort", "interlock", "undervolt",
    "overcurrent", "thermal",
)
SEVERITY_PATTERNS = (
    ("error", re.compile(r"\b(error|err|fail|fault|alarm|crash|abort|estop|e-stop)\b", re.I)),
    ("warn", re.compile(r"\b(warn|warning|caution)\b", re.I)),
    ("info", re.compile(r"\b(info|notice|ok|ready)\b", re.I)),
)
TIMESTAMP_RE = re.compile(
    r"(?:"
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"
    r"|\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"|\[\d+(?:\.\d+)?\]"
    r")"
)
UNIT_RE = re.compile(
    r"(?P<name>.+?)(?:[_\s\[\(]+(?P<unit>C|F|K|A|V|mV|mA|Ah|mAh|W|kW|rpm|mm|um|µm|deg|Hz|kHz|psi|bar|Pa|s|ms|%|pct))[\s\]\)]*$",
    re.I,
)
TIME_HEADER_RE = re.compile(r"^(t|time|timestamp|cycle|step|sample|epoch|elapsed)", re.I)

CAD_TOKS = ("cad", "step", "stl", "fixture", "assembly", "drawing", "dxf", "iges", "model")
SENSOR_TOKS = (
    "sensor", "telemetry", "scope", "temp", "volt", "current", "cycle",
    "imu", "encoder", "thermistor", "strain", "pressure", "scope",
)
LOG_TOKS = ("log", "serial", "console", "dmesg", "syslog", "alarm", "pendant", "cnc")
SETUP_TOKS = ("setup", "bench", "rig", "lab", "photo", "vise", "fixture-photo")
RESULT_TOKS = (
    "gel", "result", "fail", "crack", "burn", "corrosion", "vent", "scrap",
    "defect", "undersize", "fracture", "smear", "after", "failed",
)
DATASHEET_TOKS = ("sds", "datasheet", "spec", "msds", "tds", "datasheet")
MATERIAL_TOKS = ("material", "alloy", "lot", "coa", "cert", "mill", "bom", "heat", "stock")
PROCESS_TOKS = (
    "protocol", "sop", "traveler", "router", "workorder", "gcode", "process",
    "recipe", "procedure", "runsheet", "ncprogram", "feeds",
)

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".xml": "application/xml",
    ".html": "text/html",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".ipynb": "application/x-ipynb+json",
    ".step": "model/step",
    ".stp": "model/step",
    ".stl": "model/stl",
    ".iges": "model/iges",
    ".igs": "model/iges",
    ".dxf": "image/vnd.dxf",
    ".obj": "model/obj",
    ".nc": "text/x-gcode",
    ".gcode": "text/x-gcode",
}


def classify(
    filename: str,
    data: bytes | None = None,
    content_type: str | None = None,
) -> tuple[ArtifactKind, str]:
    """Classify a dump file from name, magic bytes, and optional Content-Type."""
    ext = Path(filename).suffix.lower()
    mime_hint = (content_type or "").split(";")[0].strip().lower()
    magic_kind, magic_mime = _sniff_magic(data) if data else (None, None)

    if ext in NOTEBOOK_EXT or (magic_kind is None and _looks_like_notebook(data)):
        return ArtifactKind.NOTEBOOK, "application/x-ipynb+json"
    if ext in IMAGE_EXT:
        return ArtifactKind.IMAGE, magic_mime or MIME_BY_EXT.get(ext, "image/" + ext.lstrip("."))
    if ext in CAD_EXT:
        return ArtifactKind.CAD, magic_mime or MIME_BY_EXT.get(ext, "model/" + ext.lstrip("."))
    if ext in PROCESS_EXT:
        return ArtifactKind.DOCUMENT, MIME_BY_EXT.get(ext, "text/x-gcode")
    if ext in SENSOR_EXT:
        if ext == ".json" and _looks_like_notebook(data):
            return ArtifactKind.NOTEBOOK, "application/x-ipynb+json"
        if ext in {".csv", ".tsv"}:
            return ArtifactKind.SENSOR, MIME_BY_EXT.get(ext, "text/csv")
        if ext == ".json":
            return ArtifactKind.SENSOR, "application/json"
        return ArtifactKind.SENSOR, MIME_BY_EXT.get(ext, "application/octet-stream")
    if ext in MATERIAL_EXT:
        return ArtifactKind.DOCUMENT, "text/plain"
    if ext in DOC_EXT:
        default_mime = "application/pdf" if ext == ".pdf" else "text/plain"
        return ArtifactKind.DOCUMENT, MIME_BY_EXT.get(ext, default_mime)
    if ext in LOG_EXT:
        return ArtifactKind.LOG, "text/plain"

    if magic_kind is not None:
        return magic_kind, magic_mime or "application/octet-stream"

    if mime_hint.startswith("image/"):
        return ArtifactKind.IMAGE, mime_hint
    if mime_hint == "application/pdf":
        return ArtifactKind.DOCUMENT, mime_hint
    if mime_hint in {"text/csv", "application/json"} or mime_hint.endswith("spreadsheet"):
        return ArtifactKind.SENSOR, mime_hint
    if mime_hint in {"model/step", "model/stl", "image/vnd.dxf"}:
        return ArtifactKind.CAD, mime_hint
    if mime_hint.startswith("text/") and data:
        text = _decode(data[:8000])
        if _looks_like_csv(text):
            return ArtifactKind.SENSOR, "text/csv"
        if _looks_like_log(text):
            return ArtifactKind.LOG, "text/plain"
        return ArtifactKind.DOCUMENT, mime_hint or "text/plain"

    if data:
        text_head = _decode(data[:8000])
        if _looks_like_csv(text_head):
            return ArtifactKind.SENSOR, "text/csv"
        if _looks_like_log(text_head):
            return ArtifactKind.LOG, "text/plain"
        if _printable_ratio(data[:2000]) > 0.85:
            return ArtifactKind.DOCUMENT, "text/plain"

    return ArtifactKind.OTHER, mime_hint or "application/octet-stream"


def guess_role(
    filename: str,
    kind: ArtifactKind,
    suggested: str | None = None,
    stats: dict[str, Any] | None = None,
    text: str | None = None,
) -> ArtifactRole:
    if suggested:
        try:
            return ArtifactRole(suggested)
        except ValueError:
            pass
    return suggest_role(filename, kind, stats=stats, text=text)


def suggest_role(
    filename: str,
    kind: ArtifactKind,
    stats: dict[str, Any] | None = None,
    text: str | None = None,
) -> ArtifactRole:
    """Best-guess role from filename, kind, and extracted cues."""
    name = filename.lower()
    stem = Path(filename).stem.lower()
    blob = f"{name} {stem} {(text or '')[:400].lower()}"
    ext = Path(filename).suffix.lower()

    if ext in PROCESS_EXT or any(tok in name for tok in PROCESS_TOKS):
        return ArtifactRole.PROCESS_DOC
    if kind == ArtifactKind.CAD or any(tok in name for tok in CAD_TOKS):
        return ArtifactRole.CAD
    if any(tok in name for tok in DATASHEET_TOKS):
        return ArtifactRole.DATASHEET
    if any(tok in name for tok in MATERIAL_TOKS) or ext in MATERIAL_EXT:
        return ArtifactRole.MATERIAL_DOC
    if kind == ArtifactKind.SENSOR or any(tok in name for tok in SENSOR_TOKS):
        return ArtifactRole.SENSOR
    if kind == ArtifactKind.LOG or any(tok in name for tok in LOG_TOKS):
        return ArtifactRole.LOG
    if any(tok in name for tok in RESULT_TOKS):
        return ArtifactRole.RESULT_IMAGE if kind == ArtifactKind.IMAGE else ArtifactRole.OTHER
    if any(tok in name for tok in SETUP_TOKS):
        return ArtifactRole.SETUP_PHOTO if kind == ArtifactKind.IMAGE else ArtifactRole.OTHER
    if kind == ArtifactKind.IMAGE:
        return ArtifactRole.SETUP_PHOTO
    if kind == ArtifactKind.DOCUMENT:
        if any(tok in blob for tok in DATASHEET_TOKS):
            return ArtifactRole.DATASHEET
        if any(tok in blob for tok in PROCESS_TOKS):
            return ArtifactRole.PROCESS_DOC
        if any(tok in blob for tok in MATERIAL_TOKS):
            return ArtifactRole.MATERIAL_DOC
        if ext in {".pdf", ".md", ".docx"}:
            if "sheet" in name or "spec" in name:
                return ArtifactRole.DATASHEET
            return ArtifactRole.OTHER
    if stats:
        numeric = stats.get("numeric") or {}
        if numeric and any(re.search(r"temp|volt|current|rpm|pressure", k, re.I) for k in numeric):
            return ArtifactRole.SENSOR
        if stats.get("flagged") or stats.get("severity"):
            return ArtifactRole.LOG
    return ArtifactRole.OTHER


def ingest_bytes(
    filename: str,
    data: bytes,
    role: str | None = None,
    caption: str = "",
    artifact_id: str | None = None,
    content_type: str | None = None,
) -> ExperimentArtifact:
    kind, mime = classify(filename, data, content_type)
    art_id = artifact_id or uuid.uuid4().hex[:16]
    extracted, stats, summary = _extract(filename, kind, data)
    auto_role = suggest_role(filename, kind, stats=stats, text=extracted)
    assigned = guess_role(filename, kind, role, stats=stats, text=extracted)
    if caption:
        summary = f"{caption}. {summary}".strip()
    preview = None
    if kind == ArtifactKind.IMAGE:
        preview = f"/api/files/{art_id}"
    stats = dict(stats)
    stats.setdefault("kind", kind.value)
    stats.setdefault("mime", mime)
    return ExperimentArtifact(
        id=art_id,
        filename=filename,
        kind=kind,
        role=assigned,
        suggested_role=auto_role,
        mime_type=mime,
        size_bytes=len(data),
        caption=caption,
        extracted_text=extracted[:MAX_EXTRACT_CHARS],
        summary=summary[:MAX_SUMMARY_CHARS],
        stats=stats,
        preview_url=preview,
    )


def artifact_blob(artifact: ExperimentArtifact) -> str:
    parts = [
        f"Artifact {artifact.filename} ({artifact.kind.value}, role={artifact.role.value})",
        artifact.caption,
        artifact.summary,
        artifact.extracted_text,
    ]
    if artifact.stats:
        parts.append("Stats: " + json.dumps(artifact.stats, default=str)[:1500])
    return "\n".join(p for p in parts if p)


def _extract(filename: str, kind: ArtifactKind, data: bytes) -> tuple[str, dict[str, Any], str]:
    ext = Path(filename).suffix.lower()
    if kind == ArtifactKind.SENSOR:
        return _extract_tabular(filename, data)
    if kind == ArtifactKind.LOG:
        return _extract_log(data)
    if kind == ArtifactKind.CAD:
        return _extract_cad(filename, data)
    if kind == ArtifactKind.IMAGE:
        return _extract_image(filename, data)
    if kind == ArtifactKind.NOTEBOOK:
        return _extract_notebook(data)
    if ext in PROCESS_EXT:
        return _extract_gcode(filename, data)
    if ext == ".pdf" or (data[:5] == b"%PDF-"):
        return _extract_pdf(data)
    if ext in {".json"}:
        return _extract_json(data)
    if ext in {".yaml", ".yml"}:
        return _extract_yaml(data)
    if ext in {".xml", ".html", ".htm"}:
        return _extract_markup(filename, data)
    return _extract_text(data)


def _decode(data: bytes) -> str:
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    printable = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(data)


def _looks_like_notebook(data: bytes | None) -> bool:
    if not data:
        return False
    head = data.lstrip()[:800]
    return b'"nbformat"' in head or (b'"cells"' in head and b'"cell_type"' in data[:4000])


def _looks_like_csv(text: str) -> bool:
    sample = "\n".join(text.splitlines()[:8])
    if sample.count(",") < 2 and sample.count("\t") < 2 and sample.count(";") < 2:
        return False
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        rows = list(csv.reader(io.StringIO(sample), dialect))
        return len(rows) >= 2 and len(rows[0]) >= 2
    except csv.Error:
        return False


def _looks_like_log(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    hits = sum(
        1
        for ln in lines[:80]
        if any(w in ln.lower() for w in ERROR_WORDS) or TIMESTAMP_RE.search(ln)
    )
    return hits >= 2


def _looks_like_binary_stl(data: bytes) -> bool:
    if len(data) < 84:
        return False
    if data[:5].lower() == b"solid" and _printable_ratio(data[:80]) > 0.9:
        return False
    triangles = struct.unpack_from("<I", data, 80)[0]
    expected = 84 + triangles * 50
    return triangles > 0 and (expected == len(data) or abs(expected - len(data)) <= 50)


def _sniff_magic(data: bytes) -> tuple[ArtifactKind | None, str | None]:
    if not data:
        return None, None
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ArtifactKind.IMAGE, "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return ArtifactKind.IMAGE, "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ArtifactKind.IMAGE, "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ArtifactKind.IMAGE, "image/webp"
    if head.startswith(b"BM") and len(data) > 30:
        return ArtifactKind.IMAGE, "image/bmp"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return ArtifactKind.IMAGE, "image/tiff"
    if head.startswith(b"%PDF"):
        return ArtifactKind.DOCUMENT, "application/pdf"
    stripped = data.lstrip()[:120]
    if stripped.startswith(b"ISO-10303-21"):
        return ArtifactKind.CAD, "model/step"
    low4k = data[:4000].lower()
    ascii_stl = data[:80].lower().lstrip().startswith(b"solid")
    if ascii_stl and (b"facet" in low4k or b"vertex" in low4k):
        return ArtifactKind.CAD, "model/stl"
    if _looks_like_binary_stl(data):
        return ArtifactKind.CAD, "model/stl"
    if re.match(br"\s*0\s*\r?\n\s*(SECTION|HEADER)\b", data[:240], re.I):
        return ArtifactKind.CAD, "image/vnd.dxf"
    if stripped.startswith(b"IGES") or b"IGES" in data[:80]:
        return ArtifactKind.CAD, "model/iges"
    if _looks_like_notebook(data):
        return ArtifactKind.NOTEBOOK, "application/x-ipynb+json"
    if stripped[:1] in (b"{", b"["):
        return ArtifactKind.SENSOR, "application/json"
    return None, None


def _extract_text(data: bytes) -> tuple[str, dict[str, Any], str]:
    text = _decode(data[:MAX_READ_BYTES])
    lines = [ln for ln in text.splitlines() if ln.strip()]
    flags = [ln.strip() for ln in lines if any(w in ln.lower() for w in ERROR_WORDS)][:8]
    summary = f"Text file with {len(lines)} lines."
    if flags:
        summary += " Flagged lines: " + "; ".join(flags[:3])
    return text[:MAX_EXTRACT_CHARS], {"lines": len(lines), "flagged": flags}, summary


def _extract_log(data: bytes) -> tuple[str, dict[str, Any], str]:
    text, stats, _summary = _extract_text(data)
    stats["kind"] = "log"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    severity = {name: 0 for name, _ in SEVERITY_PATTERNS}
    for ln in lines:
        for name, pat in SEVERITY_PATTERNS:
            if pat.search(ln):
                severity[name] += 1
                break
    stamps = [m.group(0) for ln in lines[:400] if (m := TIMESTAMP_RE.search(ln))]
    flags = list(stats.get("flagged") or [])
    stats["severity"] = severity
    stats["timestamp_samples"] = stamps[:6]
    if stamps:
        stats["time_span"] = {"first": stamps[0], "last": stamps[-1]}
    alarms = sorted(set(re.findall(r"\b(?:ALM|ALARM|ERR)[-_ ]?[A-Z0-9]{1,8}\b", text, re.I)))
    if alarms:
        stats["alarm_codes"] = alarms[:12]
    bits = [f"Log with {len(lines)} lines"]
    if severity["error"]:
        bits.append(f"{severity['error']} error/fault lines")
    if severity["warn"]:
        bits.append(f"{severity['warn']} warnings")
    if alarms:
        bits.append("codes " + ", ".join(alarms[:4]))
    if flags:
        bits.append("e.g. " + flags[0][:120])
    elif stamps:
        bits.append(f"timestamps {stamps[0]} → {stamps[-1]}")
    return text, stats, ". ".join(bits) + "."


def _extract_tabular(filename: str, data: bytes) -> tuple[str, dict[str, Any], str]:
    ext = Path(filename).suffix.lower()
    if ext == ".json":
        return _extract_json(data)
    if ext in {".xlsx", ".xls", ".parquet", ".h5", ".hdf5", ".tdms", ".npz"}:
        return (
            f"Binary table {filename} ({len(data)} bytes). Export CSV for numeric summaries.",
            {"format": ext.lstrip("."), "bytes": len(data), "binary_table": True},
            f"Binary table {filename} ({len(data)} bytes); CSV/JSON preferred for stats.",
        )
    text = _decode(data[:MAX_READ_BYTES])
    sample = io.StringIO(text)
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(sample, dialect)
    rows = list(reader)
    if not rows:
        return text, {}, "Empty table."
    header = [h.strip() or f"col{i}" for i, h in enumerate(rows[0])]
    body = rows[1:]
    empty_cells = sum(1 for row in body for cell in row if not str(cell).strip())
    col_stats: dict[str, Any] = {}
    units: dict[str, str] = {}
    notes = []
    time_col = next((h for h in header if TIME_HEADER_RE.match(h)), None)
    for idx, name in enumerate(header[:12]):
        unit_match = UNIT_RE.match(name)
        if unit_match and unit_match.group("unit"):
            units[name] = unit_match.group("unit")
        vals = []
        for row in body:
            if idx >= len(row):
                continue
            raw = row[idx].strip()
            if not raw or raw.lower() in {"nan", "na", "null", "#n/a"}:
                continue
            try:
                vals.append(float(raw))
            except ValueError:
                continue
        if len(vals) < 3:
            continue
        mean = sum(vals) / len(vals)
        vmin, vmax = min(vals), max(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        stdev = math.sqrt(var)
        entry = {
            "n": len(vals),
            "min": round(vmin, 5),
            "max": round(vmax, 5),
            "mean": round(mean, 5),
            "stdev": round(stdev, 5),
            "first": round(vals[0], 5),
            "last": round(vals[-1], 5),
        }
        if name in units:
            entry["unit"] = units[name]
        col_stats[name] = entry
        span = abs(vmax - vmin)
        if mean != 0 and span / max(abs(mean), 1e-9) > 3:
            notes.append(f"{name} varies widely ({vmin:.4g} to {vmax:.4g})")
        if any(abs(v) > 1e6 for v in vals[:200]):
            notes.append(f"{name} has extreme magnitudes")
        drifted = abs(vals[-1] - vals[0]) > max(abs(mean) * 0.25, 1e-6)
        if time_col != name and drifted and len(vals) >= 4:
            direction = "rising" if vals[-1] > vals[0] else "falling"
            notes.append(f"{name} {direction} {vals[0]:.4g} → {vals[-1]:.4g}")
    if time_col and time_col in col_stats:
        times = col_stats[time_col]
        if times["n"] >= 3 and times["last"] < times["first"]:
            notes.append(f"{time_col} is not increasing")
    summary = f"Table {filename}: {len(body)} rows, columns {', '.join(header[:8])}."
    if units:
        summary += " Units: " + ", ".join(f"{k}={v}" for k, v in list(units.items())[:6]) + "."
    if notes:
        summary += " " + "; ".join(notes[:4])
    if empty_cells:
        summary += f" {empty_cells} empty cells."
    excerpt = " | ".join(header) + "\n"
    preview_rows = body[:6]
    if len(body) > 8:
        preview_rows = body[:4] + body[-2:]
    for row in preview_rows:
        excerpt += " | ".join(row[:12]) + "\n"
    if notes:
        excerpt += "\n".join(notes)
    stats = {
        "columns": header,
        "rows": len(body),
        "numeric": col_stats,
        "flags": notes,
        "empty_cells": empty_cells,
        "units": units,
    }
    if time_col:
        stats["time_column"] = time_col
    return excerpt, stats, summary


def _extract_json(data: bytes) -> tuple[str, dict[str, Any], str]:
    try:
        parsed = json.loads(_decode(data[:MAX_READ_BYTES]))
    except json.JSONDecodeError:
        return _extract_text(data)
    dumped = json.dumps(parsed, indent=2)[:MAX_EXTRACT_CHARS]
    stats: dict[str, Any] = {"json_type": type(parsed).__name__}
    if isinstance(parsed, list):
        summary = f"JSON list with {len(parsed)} records."
        stats["records"] = len(parsed)
        if parsed and isinstance(parsed[0], dict):
            keys = list(parsed[0])[:16]
            stats["columns"] = keys
            numeric: dict[str, Any] = {}
            for key in keys[:8]:
                vals = []
                for row in parsed:
                    if not isinstance(row, dict):
                        continue
                    try:
                        vals.append(float(row[key]))
                    except (KeyError, TypeError, ValueError):
                        continue
                if len(vals) >= 3:
                    numeric[key] = {
                        "n": len(vals),
                        "min": round(min(vals), 5),
                        "max": round(max(vals), 5),
                        "mean": round(sum(vals) / len(vals), 5),
                    }
            if numeric:
                stats["numeric"] = numeric
                summary += " Numeric fields: " + ", ".join(
                    f"{k} {v['min']}–{v['max']}" for k, v in list(numeric.items())[:4]
                ) + "."
            else:
                summary += f" Keys: {', '.join(keys[:8])}."
    elif isinstance(parsed, dict):
        keys = list(parsed)[:16]
        stats["keys"] = keys
        summary = f"JSON object keys: {', '.join(keys[:12])}."
        if any(isinstance(parsed.get(k), list) for k in parsed):
            lists = {k: len(v) for k, v in parsed.items() if isinstance(v, list)}
            if lists:
                stats["list_lengths"] = lists
                listed = ", ".join(f"{k}[{n}]" for k, n in list(lists.items())[:6])
                summary += f" Lists: {listed}."
    else:
        summary = "JSON scalar."
    return dumped, stats, summary


def _extract_yaml(data: bytes) -> tuple[str, dict[str, Any], str]:
    text = _decode(data[:MAX_READ_BYTES])
    try:
        import yaml

        parsed = yaml.safe_load(text)
    except Exception:
        return _extract_text(data)
    if isinstance(parsed, dict):
        keys = list(parsed)[:16]
        summary = f"YAML mapping keys: {', '.join(str(k) for k in keys[:12])}."
        return text[:MAX_EXTRACT_CHARS], {"yaml_type": "dict", "keys": keys}, summary
    if isinstance(parsed, list):
        summary = f"YAML list with {len(parsed)} items."
        return text[:MAX_EXTRACT_CHARS], {"yaml_type": "list", "items": len(parsed)}, summary
    return _extract_text(data)


def _extract_markup(filename: str, data: bytes) -> tuple[str, dict[str, Any], str]:
    text = _decode(data[:MAX_READ_BYTES])
    stripped = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    stripped = re.sub(r"<style[\s\S]*?</style>", " ", stripped, flags=re.I)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    title = ""
    found = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if found:
        title = re.sub(r"\s+", " ", found.group(1)).strip()
    summary = f"Markup {filename} ({len(stripped)} chars of text)."
    if title:
        summary = f"{title}. " + summary
    return (stripped or text)[:MAX_EXTRACT_CHARS], {"title": title, "chars": len(stripped)}, summary


def _extract_gcode(filename: str, data: bytes) -> tuple[str, dict[str, Any], str]:
    text = _decode(data[:MAX_READ_BYTES])
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("(")]
    codes = re.findall(r"\b[GM]\d+\.?\d*\b", text, re.I)
    tools = sorted(set(re.findall(r"\bT0*\d+\b", text, re.I)))
    feeds = [float(m) for m in re.findall(r"\bF(\d+(?:\.\d+)?)\b", text)]
    speeds = [float(m) for m in re.findall(r"\bS(\d+(?:\.\d+)?)\b", text)]
    stats: dict[str, Any] = {
        "format": "gcode",
        "lines": len(lines),
        "codes": sorted(set(c.upper() for c in codes))[:24],
        "tools": tools[:12],
    }
    if feeds:
        stats["feed"] = {"min": min(feeds), "max": max(feeds)}
    if speeds:
        stats["spindle"] = {"min": min(speeds), "max": max(speeds)}
    bits = [f"G-code {filename}: {len(lines)} lines"]
    if tools:
        bits.append("tools " + ", ".join(tools[:6]))
    if feeds:
        bits.append(f"F {min(feeds):.4g}–{max(feeds):.4g}")
    if speeds:
        bits.append(f"S {min(speeds):.4g}–{max(speeds):.4g}")
    return text[:MAX_EXTRACT_CHARS], stats, "; ".join(bits) + "."


def _extract_cad(filename: str, data: bytes) -> tuple[str, dict[str, Any], str]:
    ext = Path(filename).suffix.lower()
    head = data[:MAX_READ_BYTES]
    text = ""
    stats: dict[str, Any] = {"format": ext.lstrip(".") or "cad", "bytes": len(data)}
    if ext in {".stl"} or _looks_like_binary_stl(data) or (
        head[:80].lower().lstrip().startswith(b"solid") and b"facet" in head[:2000].lower()
    ):
        ascii_stl = head[:80].isascii() and b"solid" in head[:80].lower()
        if ascii_stl and not _looks_like_binary_stl(data):
            text = _decode(head)
            facets = len(re.findall(r"facet normal", text, re.I))
            stats["facets_seen"] = facets
            name = ""
            m = re.match(r"solid\s+(\S+)", text, re.I)
            if m:
                name = m.group(1)
                stats["solid_name"] = name
            summary = f"ASCII STL with at least {facets} facets ({len(data)} bytes)."
            if name:
                summary = f"ASCII STL '{name}' with at least {facets} facets ({len(data)} bytes)."
        else:
            header = head[:80].decode("latin-1", errors="replace").strip("\x00 ").strip()
            stats["binary_header"] = header
            if len(data) >= 84:
                triangles = struct.unpack_from("<I", data, 80)[0]
                stats["triangles"] = triangles
                summary = f"Binary STL ({triangles} triangles, {len(data)} bytes)."
            else:
                summary = f"Binary STL ({len(data)} bytes)."
            if header:
                summary += f" Header: {header[:80]}"
            text = header
        return text[:MAX_EXTRACT_CHARS], stats, summary

    text = _decode(head)
    products = re.findall(r"PRODUCT\s*\(\s*'([^']+)'", text)
    file_names = re.findall(r"FILE_NAME\s*\(\s*'([^']+)'", text)
    schemas = re.findall(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text)
    descriptions = re.findall(r"FILE_DESCRIPTION\s*\(\s*\(\s*'([^']+)'", text)
    layers = re.findall(r"(?m)^LAYER\s*$|(?:2\n)([A-Za-z0-9_\-]+)", text)
    materials = re.findall(r"(?i)material[^a-z]{0,8}([A-Za-z0-9_\-]+)", text)
    units = re.findall(r"(?i)\b(MILLI?METRE|INCH|SI_UNIT|MM|INCHES)\b", text)
    entities = {}
    for ent in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE", "INSERT", "TEXT", "DIMENSION"):
        n = len(re.findall(rf"(?m)^{ent}$", text))
        if n:
            entities[ent] = n
    stats["product_names"] = products[:12]
    stats["layers"] = layers[:12]
    stats["material_mentions"] = materials[:12]
    if file_names:
        stats["file_name"] = file_names[:6]
    if schemas:
        stats["schema"] = schemas[:4]
    if descriptions:
        stats["description"] = descriptions[:4]
    if units:
        stats["units"] = sorted(set(u.upper() for u in units))[:8]
    if entities:
        stats["entities"] = entities
    bits = []
    if products:
        bits.append("products " + ", ".join(products[:6]))
    if descriptions:
        bits.append("desc " + ", ".join(descriptions[:3]))
    if materials:
        bits.append("materials " + ", ".join(materials[:6]))
    if schemas:
        bits.append("schema " + ", ".join(schemas[:2]))
    if entities:
        bits.append("entities " + ", ".join(f"{k}×{v}" for k, v in list(entities.items())[:5]))
    summary = f"CAD file {filename} ({len(data)} bytes)"
    if bits:
        summary += ": " + "; ".join(bits)
    return text[:MAX_EXTRACT_CHARS], stats, summary


def _extract_image(filename: str, data: bytes) -> tuple[str, dict[str, Any], str]:
    stats: dict[str, Any] = {"bytes": len(data)}
    tokens = re.findall(r"[a-z0-9]+", Path(filename).stem.lower())
    stats["filename_tokens"] = tokens
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS

        img = Image.open(io.BytesIO(data))
        stats["width"], stats["height"] = img.size
        stats["mode"] = img.mode
        stats["format"] = img.format
        summary = f"Image {filename} {img.size[0]}x{img.size[1]} {img.mode}."
        exif_raw = getattr(img, "getexif", lambda: None)()
        if exif_raw:
            interesting = {}
            for tag_id, value in exif_raw.items():
                tag = TAGS.get(tag_id, str(tag_id))
                if tag in {"DateTime", "DateTimeOriginal", "Make", "Model", "Software"}:
                    interesting[tag] = str(value)[:80]
            if interesting:
                stats["exif"] = interesting
                if "DateTimeOriginal" in interesting or "DateTime" in interesting:
                    shot = interesting.get("DateTimeOriginal") or interesting.get("DateTime")
                    summary += f" Shot {shot}."
    except Exception:
        summary = f"Image {filename} ({len(data)} bytes)."
    if tokens:
        summary += " Filename cues: " + ", ".join(tokens[:8])
    text = f"Photograph or instrument image named {filename}. Tokens: {' '.join(tokens)}"
    return text, stats, summary


def _extract_notebook(data: bytes) -> tuple[str, dict[str, Any], str]:
    try:
        nb = json.loads(_decode(data[: MAX_READ_BYTES * 2]))
    except json.JSONDecodeError:
        return _extract_text(data)
    chunks = []
    heading = ""
    n_code = 0
    n_md = 0
    for cell in nb.get("cells", [])[:40]:
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        ctype = cell.get("cell_type")
        if ctype == "code":
            n_code += 1
        elif ctype == "markdown":
            n_md += 1
            if not heading:
                m = re.search(r"^#+\s+(.+)$", src, re.M)
                if m:
                    heading = m.group(1).strip()
        chunks.append(src)
        if ctype == "code":
            for out in cell.get("outputs", [])[:2]:
                if "text" in out:
                    t = out["text"]
                    chunks.append("".join(t) if isinstance(t, list) else str(t))
    text = "\n\n".join(chunks)
    n_cells = len(nb.get("cells", []))
    summary = f"Notebook with {n_cells} cells ({n_code} code, {n_md} markdown)."
    if heading:
        summary = f"{heading}. " + summary
    return text[:MAX_EXTRACT_CHARS], {
        "cells": n_cells,
        "code_cells": n_code,
        "markdown_cells": n_md,
        "heading": heading,
    }, summary


def _extract_pdf(data: bytes) -> tuple[str, dict[str, Any], str]:
    try:
        import pdfplumber

        buf = io.BytesIO(data)
        with pdfplumber.open(buf) as pdf:
            pages = []
            for page in pdf.pages[:8]:
                pages.append(page.extract_text() or "")
            text = "\n".join(pages)
            summary = f"PDF with {len(pdf.pages)} pages; extracted {len(pages)}."
            summary = _pdf_summary_bits(text, summary)
            return text[:MAX_EXTRACT_CHARS], {
                "pages": len(pdf.pages),
                "extracted_pages": len(pages),
            }, summary
    except Exception:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:8])
        info = getattr(reader, "metadata", None)
        title = ""
        if info is not None:
            title = str(getattr(info, "title", "") or "")
        summary = f"PDF with {len(reader.pages)} pages."
        if title:
            summary = f"{title}. " + summary
        summary = _pdf_summary_bits(text, summary)
        stats: dict[str, Any] = {"pages": len(reader.pages)}
        if title:
            stats["title"] = title
        return text[:MAX_EXTRACT_CHARS], stats, summary
    except Exception:
        head = _decode(data[:2000])
        cues = re.findall(r"/Title\s*\(([^)]+)\)", head)
        extra = f" Title cue: {cues[0]}." if cues else ""
        return "", {"pages": None}, f"PDF could not be parsed; filename still used as a cue.{extra}"


def _pdf_summary_bits(text: str, summary: str) -> str:
    if not text.strip():
        return summary + " No extractable text (likely scanned)."
    first = " ".join(text.splitlines()[:4])
    first = re.sub(r"\s+", " ", first).strip()
    if first:
        summary += " Opening: " + first[:160]
    keywords = ("material", "sds", "hazard", "temperature", "voltage", "protocol", "lot")
    hits = [w for w in keywords if w in text.lower()]
    if hits:
        summary += " Mentions " + ", ".join(hits[:5]) + "."
    return summary

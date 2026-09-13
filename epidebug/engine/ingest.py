"""Turn dumped lab/shop files into text the diagnostic engine can reason over.

Researchers should be able to drop whatever they have: setup photos, CAD,
sensor CSVs, machine logs, datasheets, process notes. This module classifies
each file and extracts a compact, lossy summary — not a perfect reconstruction.
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any

from epidebug.schema import ArtifactKind, ArtifactRole, ExperimentArtifact

MAX_EXTRACT_CHARS = 12000
MAX_READ_BYTES = 400_000

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".heic"}
CAD_EXT = {".stl", ".step", ".stp", ".iges", ".igs", ".dxf", ".obj", ".3mf", ".sldprt", ".sldasm", ".ipt", ".fcstd"}
SENSOR_EXT = {".csv", ".tsv", ".xlsx", ".json", ".parquet"}
LOG_EXT = {".log", ".txt", ".out", ".err", ".serial", ".nmea"}
DOC_EXT = {".pdf", ".md", ".rst", ".docx", ".html", ".xml", ".yaml", ".yml"}
NOTEBOOK_EXT = {".ipynb"}

ERROR_WORDS = ("error", "fail", "alarm", "fault", "warn", "crash", "overheat", "leak", "nan")


def classify(filename: str) -> tuple[ArtifactKind, str]:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXT:
        return ArtifactKind.IMAGE, "image/" + ext.lstrip(".")
    if ext in CAD_EXT:
        return ArtifactKind.CAD, "model/" + ext.lstrip(".")
    if ext in SENSOR_EXT:
        return ArtifactKind.SENSOR, "text/csv" if ext in {".csv", ".tsv"} else "application/json"
    if ext in NOTEBOOK_EXT:
        return ArtifactKind.NOTEBOOK, "application/x-ipynb+json"
    if ext in DOC_EXT:
        return ArtifactKind.DOCUMENT, "application/pdf" if ext == ".pdf" else "text/plain"
    if ext in LOG_EXT:
        return ArtifactKind.LOG, "text/plain"
    return ArtifactKind.OTHER, "application/octet-stream"


def guess_role(filename: str, kind: ArtifactKind, suggested: str | None = None) -> ArtifactRole:
    if suggested:
        try:
            return ArtifactRole(suggested)
        except ValueError:
            pass
    name = filename.lower()
    if kind == ArtifactKind.CAD or any(tok in name for tok in ("cad", "step", "stl", "fixture", "assembly")):
        return ArtifactRole.CAD
    if kind == ArtifactKind.SENSOR or any(tok in name for tok in ("sensor", "telemetry", "scope", "temp", "volt", "current")):
        return ArtifactRole.SENSOR
    if kind == ArtifactKind.LOG or any(tok in name for tok in ("log", "serial", "console", "dmesg")):
        return ArtifactRole.LOG
    if any(tok in name for tok in ("setup", "bench", "rig", "lab", "photo")):
        return ArtifactRole.SETUP_PHOTO
    if any(tok in name for tok in ("gel", "result", "fail", "crack", "burn", "corrosion")):
        return ArtifactRole.RESULT_IMAGE
    if any(tok in name for tok in ("sds", "datasheet", "spec", "msds")):
        return ArtifactRole.DATASHEET
    if any(tok in name for tok in ("material", "alloy", "lot", "coa")):
        return ArtifactRole.MATERIAL_DOC
    if kind == ArtifactKind.IMAGE:
        return ArtifactRole.SETUP_PHOTO
    return ArtifactRole.OTHER


def ingest_bytes(
    filename: str,
    data: bytes,
    role: str | None = None,
    caption: str = "",
    artifact_id: str | None = None,
) -> ExperimentArtifact:
    kind, mime = classify(filename)
    art_id = artifact_id or uuid.uuid4().hex[:16]
    extracted, stats, summary = _extract(filename, kind, data)
    if caption:
        summary = f"{caption}. {summary}".strip()
    preview = None
    if kind == ArtifactKind.IMAGE:
        preview = f"/api/files/{art_id}"
    return ExperimentArtifact(
        id=art_id,
        filename=filename,
        kind=kind,
        role=guess_role(filename, kind, role),
        mime_type=mime,
        size_bytes=len(data),
        caption=caption,
        extracted_text=extracted[:MAX_EXTRACT_CHARS],
        summary=summary[:500],
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
    if filename.lower().endswith(".pdf"):
        return _extract_pdf(data)
    if filename.lower().endswith((".json",)):
        return _extract_json(data)
    return _extract_text(data)


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_text(data: bytes) -> tuple[str, dict[str, Any], str]:
    text = _decode(data[:MAX_READ_BYTES])
    lines = [ln for ln in text.splitlines() if ln.strip()]
    flags = [ln.strip() for ln in lines if any(w in ln.lower() for w in ERROR_WORDS)][:8]
    summary = f"Text file with {len(lines)} lines."
    if flags:
        summary += " Flagged lines: " + "; ".join(flags[:3])
    return text[:MAX_EXTRACT_CHARS], {"lines": len(lines), "flagged": flags}, summary


def _extract_log(data: bytes) -> tuple[str, dict[str, Any], str]:
    text, stats, summary = _extract_text(data)
    stats["kind"] = "log"
    return text, stats, summary.replace("Text file", "Log")


def _extract_tabular(filename: str, data: bytes) -> tuple[str, dict[str, Any], str]:
    if filename.lower().endswith(".json"):
        return _extract_json(data)
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
    col_stats: dict[str, Any] = {}
    notes = []
    for idx, name in enumerate(header[:12]):
        vals = []
        for row in body:
            if idx >= len(row):
                continue
            try:
                vals.append(float(row[idx]))
            except ValueError:
                continue
        if len(vals) < 3:
            continue
        mean = sum(vals) / len(vals)
        vmin, vmax = min(vals), max(vals)
        col_stats[name] = {
            "n": len(vals),
            "min": round(vmin, 5),
            "max": round(vmax, 5),
            "mean": round(mean, 5),
        }
        span = abs(vmax - vmin)
        if mean != 0 and span / max(abs(mean), 1e-9) > 3:
            notes.append(f"{name} varies widely ({vmin:.4g} to {vmax:.4g})")
        if any(abs(v) > 1e6 for v in vals[:200]):
            notes.append(f"{name} has extreme magnitudes")
    summary = f"Table {filename}: {len(body)} rows, columns {', '.join(header[:8])}."
    if notes:
        summary += " " + "; ".join(notes[:4])
    excerpt = " | ".join(header) + "\n"
    for row in body[:8]:
        excerpt += " | ".join(row[:12]) + "\n"
    return excerpt + ("\n".join(notes)), {"columns": header, "rows": len(body), "numeric": col_stats, "flags": notes}, summary


def _extract_json(data: bytes) -> tuple[str, dict[str, Any], str]:
    try:
        parsed = json.loads(_decode(data[:MAX_READ_BYTES]))
    except json.JSONDecodeError:
        return _extract_text(data)
    dumped = json.dumps(parsed, indent=2)[:MAX_EXTRACT_CHARS]
    if isinstance(parsed, list):
        summary = f"JSON list with {len(parsed)} records."
    elif isinstance(parsed, dict):
        summary = f"JSON object keys: {', '.join(list(parsed)[:12])}."
    else:
        summary = "JSON scalar."
    return dumped, {"json_type": type(parsed).__name__}, summary


def _extract_cad(filename: str, data: bytes) -> tuple[str, dict[str, Any], str]:
    ext = Path(filename).suffix.lower()
    head = data[:MAX_READ_BYTES]
    text = ""
    stats: dict[str, Any] = {"format": ext.lstrip("."), "bytes": len(data)}
    if ext in {".stl"}:
        if head[:80].isascii() and b"solid" in head[:80].lower():
            text = _decode(head)
            facets = len(re.findall(r"facet normal", text, re.I))
            stats["facets_seen"] = facets
            summary = f"ASCII STL with at least {facets} facets ({len(data)} bytes)."
        else:
            header = head[:80].decode("latin-1", errors="replace").strip()
            stats["binary_header"] = header
            summary = f"Binary STL ({len(data)} bytes). Header: {header[:80]}"
            text = header
        return text[:MAX_EXTRACT_CHARS], stats, summary
    # STEP / IGES / DXF are mostly ASCII
    text = _decode(head)
    products = re.findall(r"PRODUCT\s*\(\s*'([^']+)'", text)
    layers = re.findall(r"(?m)^LAYER\s*$|(?:2\n)([A-Za-z0-9_\-]+)", text)
    materials = re.findall(r"(?i)material[^a-z]{0,8}([A-Za-z0-9_\-]+)", text)
    stats["product_names"] = products[:12]
    stats["layers"] = layers[:12]
    stats["material_mentions"] = materials[:12]
    bits = []
    if products:
        bits.append("products " + ", ".join(products[:6]))
    if materials:
        bits.append("materials " + ", ".join(materials[:6]))
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

        img = Image.open(io.BytesIO(data))
        stats["width"], stats["height"] = img.size
        stats["mode"] = img.mode
        summary = f"Image {filename} {img.size[0]}x{img.size[1]} {img.mode}."
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
    for cell in nb.get("cells", [])[:40]:
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        chunks.append(src)
        if cell.get("cell_type") == "code":
            for out in cell.get("outputs", [])[:2]:
                if "text" in out:
                    t = out["text"]
                    chunks.append("".join(t) if isinstance(t, list) else str(t))
    text = "\n\n".join(chunks)
    summary = f"Notebook with {len(nb.get('cells', []))} cells."
    return text[:MAX_EXTRACT_CHARS], {"cells": len(nb.get("cells", []))}, summary


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
            return text[:MAX_EXTRACT_CHARS], {"pages": len(pdf.pages)}, summary
    except Exception:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:8])
        summary = f"PDF with {len(reader.pages)} pages."
        return text[:MAX_EXTRACT_CHARS], {"pages": len(reader.pages)}, summary
    except Exception:
        return "", {"pages": None}, "PDF could not be parsed; filename still used as a cue."

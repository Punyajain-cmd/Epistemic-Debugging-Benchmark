"""Modality-specific preprocess: raw dump → bounded EvidenceExtract.

Diagnose and chat must not receive raw multimodal payloads. Each extractor
returns a compact record (summary, numeric cues, short excerpts, an explicit
omission list). Hard caps apply even if a caller passes a huge text hint.

Vercel Functions reject request bodies above about 4.5 MB before this code
runs. Local uploads may be larger (25 MB). ``/tmp`` on Vercel is ephemeral,
so the raw file is only a gallery artifact for the life of that instance.
"""

from __future__ import annotations

import math
import re
import struct
from pathlib import Path
from typing import Any

from epidebug.schema import EvidenceExtract, ExperimentArtifact, ExperimentInput

# Hard caps. The prompt renderer also truncates to MAX_PROMPT_CHARS so a
# hand-built extract cannot bypass the budget.
MAX_SUMMARY_CHARS = 320
MAX_EXCERPT_CHARS = 160
MAX_EXCERPTS = 3
MAX_EXCERPT_TOTAL = 400
MAX_ANOMALIES = 6
MAX_ANOMALY_CHARS = 160
MAX_PROMPT_CHARS = 1100
MAX_DOSSIER_ARTIFACT_CHARS = 6000
MAX_CITATIONS = 3
MAX_COLUMNS = 12
MAX_NUMERIC_COLS = 8
TEXT_HINT_CHARS = 2000
STL_TRIANGLE_SCAN_CAP = 8000
VIDEO_SCAN_BYTES = 2_000_000

VIDEO_EXT = {
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mpg", ".mpeg", ".wmv", ".ogv",
}
BINARY_TABLE_EXT = {".xlsx", ".xls", ".parquet", ".h5", ".hdf5", ".tdms", ".npz"}
GCODE_EXT = {".nc", ".gcode", ".tap", ".cnc", ".ngc", ".mpf", ".pgm"}

_WS = re.compile(r"\s+")


def _clip(text: str, limit: int) -> str:
    cleaned = _WS.sub(" ", (text or "").replace("\x00", " ")).strip()
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return ""
    return cleaned[: limit - 1].rstrip() + "…"


def _kind_value(kind: Any) -> str:
    return kind.value if hasattr(kind, "value") else str(kind or "")


def modality_of(
    filename: str,
    kind: Any,
    mime: str = "",
    stats: dict[str, Any] | None = None,
    data: bytes = b"",
) -> str:
    """Stable modality label used by extracts, the gallery, and docs."""
    ext = Path(filename).suffix.lower()
    stats = stats or {}
    mime_l = (mime or "").lower()
    kind_s = _kind_value(kind)
    if kind_s == "video" or ext in VIDEO_EXT or mime_l.startswith("video/"):
        return "video"
    if kind_s == "log":
        return "log"
    if kind_s == "image":
        return "image"
    if kind_s == "notebook":
        return "notebook"
    if kind_s == "sensor":
        if ext == ".json" or mime_l == "application/json" or stats.get("json_type"):
            return "json"
        if ext in BINARY_TABLE_EXT or stats.get("binary_table"):
            return "table"
        return "csv"
    if kind_s == "cad":
        if (
            ext == ".stl"
            or mime_l.endswith("stl")
            or "triangles" in stats
            or "facets_seen" in stats
        ):
            return "stl"
        return "cad"
    if ext in GCODE_EXT or stats.get("format") == "gcode":
        return "gcode"
    if ext == ".pdf" or mime_l == "application/pdf" or (data[:5] == b"%PDF-" if data else False):
        return "pdf"
    if kind_s == "document":
        return "text"
    return "other"


def _parse_window() -> int:
    from epidebug.engine.ingest import MAX_READ_BYTES

    return MAX_READ_BYTES


def _bound_numeric(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for name, entry in list(raw.items())[:MAX_NUMERIC_COLS]:
        label = _clip(str(name), 40)
        if isinstance(entry, dict):
            kept = {}
            for key in ("n", "min", "max", "mean", "stdev", "unit", "first", "last"):
                if key in entry and entry[key] is not None:
                    value = entry[key]
                    if isinstance(value, str):
                        value = _clip(value, 24)
                    kept[key] = value
            if kept:
                out[label] = kept
        else:
            out[label] = _clip(str(entry), 40)
    return out


def _bound_stats(stats: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only small, JSON-friendly cues. Never copy raw rows or bytes."""
    stats = stats or {}
    keep_scalars = (
        "rows", "records", "pages", "extracted_pages", "lines", "width", "height",
        "triangles", "facets_seen", "stub", "duration_s", "bytes", "empty_cells",
        "container", "brand", "format", "mode", "video_brand", "triangles_scanned",
        "degenerate_triangles", "volume_stl_units", "header_scanned_bytes",
        "header_truncated", "cells", "code_cells", "markdown_cells",
    )
    out: dict[str, Any] = {}
    for key in keep_scalars:
        if key in stats and stats[key] is not None:
            value = stats[key]
            if isinstance(value, str):
                value = _clip(value, 80)
            elif isinstance(value, float):
                value = round(value, 5)
            out[key] = value
    columns = stats.get("columns") or stats.get("keys")
    if isinstance(columns, list) and columns:
        out["columns"] = [_clip(str(col), 40) for col in columns[:MAX_COLUMNS]]
    numeric = _bound_numeric(stats.get("numeric"))
    if numeric:
        out["numeric"] = numeric
    for key in ("units", "severity", "time_span", "feed", "spindle"):
        raw = stats.get(key)
        if isinstance(raw, dict) and raw:
            clipped = {}
            for name, value in list(raw.items())[:8]:
                if isinstance(value, str):
                    clipped[_clip(str(name), 32)] = _clip(value, 40)
                else:
                    clipped[_clip(str(name), 32)] = value
            out[key] = clipped
    for key in ("alarm_codes", "tools", "product_names", "filename_tokens", "text_labels", "headings"):
        raw = stats.get(key)
        if isinstance(raw, list) and raw:
            out[key] = [_clip(str(item), 60) for item in raw[:8]]
    bbox = stats.get("bbox")
    if isinstance(bbox, list) and bbox:
        out["bbox"] = [round(float(v), 5) for v in bbox[:6]]
    if stats.get("time_column"):
        out["time_column"] = _clip(str(stats["time_column"]), 40)
    if stats.get("title"):
        out["title"] = _clip(str(stats["title"]), 120)
    if stats.get("solid_name"):
        out["solid_name"] = _clip(str(stats["solid_name"]), 80)
    return out


def _anomaly_cues(stats: dict[str, Any]) -> list[str]:
    cues: list[str] = []
    for item in stats.get("anomalies") or []:
        if isinstance(item, dict):
            text = str(item.get("detail") or item.get("kind") or "")
        else:
            text = str(item)
        if text.strip():
            cues.append(_clip(text, MAX_ANOMALY_CHARS))
    for item in stats.get("flags") or []:
        text = str(item).strip()
        if text:
            cues.append(_clip(text, MAX_ANOMALY_CHARS))
    samples = stats.get("error_samples") or {}
    if isinstance(samples, dict):
        for line in samples.get("unique") or []:
            cues.append(_clip(str(line), MAX_ANOMALY_CHARS))
    alarms = stats.get("alarm_codes") or []
    if isinstance(alarms, list) and alarms:
        cues.append(_clip("alarm codes " + ", ".join(str(a) for a in alarms[:6]), MAX_ANOMALY_CHARS))
    deduped: list[str] = []
    seen: set[str] = set()
    for cue in cues:
        if cue and cue not in seen:
            seen.add(cue)
            deduped.append(cue)
        if len(deduped) >= MAX_ANOMALIES:
            break
    return deduped


def _log_excerpts(stats: dict[str, Any]) -> list[str]:
    samples = stats.get("error_samples") or {}
    lines: list[str] = []
    if isinstance(samples, dict):
        for line in (samples.get("unique") or samples.get("first") or [])[:MAX_EXCERPTS]:
            clipped = _clip(str(line), MAX_EXCERPT_CHARS)
            if clipped:
                lines.append(clipped)
    if not lines:
        for line in stats.get("flagged") or []:
            clipped = _clip(str(line), MAX_EXCERPT_CHARS)
            if clipped:
                lines.append(clipped)
            if len(lines) >= MAX_EXCERPTS:
                break
    return lines[:MAX_EXCERPTS]


def _text_excerpts(stats: dict[str, Any], hint: str) -> list[str]:
    excerpts: list[str] = []
    heading = stats.get("heading") or ""
    headings = stats.get("headings") or []
    if heading:
        excerpts.append(_clip(str(heading), MAX_EXCERPT_CHARS))
    elif isinstance(headings, list) and headings:
        excerpts.append(_clip(str(headings[0]), MAX_EXCERPT_CHARS))
    for line in stats.get("flagged") or []:
        clipped = _clip(str(line), MAX_EXCERPT_CHARS)
        if clipped and clipped not in excerpts:
            excerpts.append(clipped)
        if len(excerpts) >= MAX_EXCERPTS:
            return excerpts[:MAX_EXCERPTS]
    if not excerpts and hint:
        first = next((ln.strip() for ln in hint.splitlines() if ln.strip()), "")
        if first:
            excerpts.append(_clip(first, MAX_EXCERPT_CHARS))
    return excerpts[:MAX_EXCERPTS]


def stl_mesh_stats(data: bytes) -> dict[str, Any]:
    """Triangle count, bounding box, and optional volume. No mesh dump."""
    stats: dict[str, Any] = {"bytes": len(data)}
    if not data:
        stats["stub"] = True
        stats["triangles"] = 0
        return stats
    if len(data) >= 84 and not (
        data[:5].lower() == b"solid" and sum(32 <= b < 127 or b in (9, 10, 13) for b in data[:80]) / 80 > 0.9
    ):
        try:
            declared = struct.unpack_from("<I", data, 80)[0]
        except struct.error:
            declared = 0
        if 0 < declared <= 5_000_000:
            return _binary_stl_stats(data, declared, stats)
    text = data[: _parse_window()].decode("utf-8", errors="replace")
    return _ascii_stl_stats(text, stats)


def _binary_stl_stats(data: bytes, declared: int, stats: dict[str, Any]) -> dict[str, Any]:
    stats["triangles"] = int(declared)
    stats["format"] = "binary-stl"
    scan = min(declared, STL_TRIANGLE_SCAN_CAP)
    expected = 84 + declared * 50
    stats["complete"] = expected == len(data) or abs(expected - len(data)) <= 2
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    volume = 0.0
    degenerate = 0
    seen = 0
    offset = 84
    for _ in range(scan):
        if offset + 50 > len(data):
            break
        nums = struct.unpack_from("<12f", data, offset)
        verts = (nums[3:6], nums[6:9], nums[9:12])
        offset += 50
        seen += 1
        if _degenerate(verts):
            degenerate += 1
        volume += _tet_volume(verts)
        for vert in verts:
            for axis, value in enumerate(vert):
                if value < mins[axis]:
                    mins[axis] = value
                if value > maxs[axis]:
                    maxs[axis] = value
    stats["triangles_scanned"] = seen
    stats["degenerate_triangles"] = degenerate
    if seen:
        bbox = _finite_bbox(*mins, *maxs)
        if bbox:
            stats["bbox"] = bbox
    full = seen == declared and stats.get("complete") and declared <= STL_TRIANGLE_SCAN_CAP
    if full and math.isfinite(volume):
        stats["volume_stl_units"] = round(abs(volume), 5)
    elif seen < declared:
        stats["mesh_scan_capped"] = True
    if declared == 0:
        stats["stub"] = True
    return stats


def _ascii_stl_stats(text: str, stats: dict[str, Any]) -> dict[str, Any]:
    stats["format"] = "ascii-stl"
    name = ""
    match = re.match(r"solid\s+(\S+)", text.lstrip(), re.I)
    if match:
        name = match.group(1)
        stats["solid_name"] = name[:80]
    verts: list[tuple[float, float, float]] = []
    capped = False
    for found in re.finditer(
        r"vertex\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)",
        text,
    ):
        if len(verts) >= STL_TRIANGLE_SCAN_CAP * 3:
            capped = True
            break
        try:
            verts.append(tuple(float(part) for part in found.groups()))  # type: ignore[arg-type]
        except ValueError:
            continue
    triangles = len(verts) // 3
    stats["facets_seen"] = triangles
    stats["triangles_scanned"] = triangles
    stats["stub"] = triangles == 0
    if capped:
        stats["mesh_scan_capped"] = True
    if not verts:
        return stats
    mins = [min(v[i] for v in verts) for i in range(3)]
    maxs = [max(v[i] for v in verts) for i in range(3)]
    bbox = _finite_bbox(*mins, *maxs)
    if bbox:
        stats["bbox"] = bbox
    degenerate = 0
    volume = 0.0
    for i in range(triangles):
        tri = verts[i * 3 : i * 3 + 3]
        if _degenerate(tri):
            degenerate += 1
        volume += _tet_volume(tri)
    stats["degenerate_triangles"] = degenerate
    if not capped and math.isfinite(volume):
        stats["volume_stl_units"] = round(abs(volume), 5)
    return stats


def _finite_bbox(*values: float) -> list[float] | None:
    if not values or any(not math.isfinite(v) for v in values):
        return None
    return [round(float(v), 5) for v in values]


def _degenerate(verts: tuple | list) -> bool:
    a, b, c = verts
    return _close(a, b) or _close(b, c) or _close(a, c)


def _close(p: tuple, q: tuple, tol: float = 1e-9) -> bool:
    return all(abs(p[i] - q[i]) <= tol for i in range(3))


def _tet_volume(verts: tuple | list) -> float:
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = verts
    return (
        x1 * (y2 * z3 - z2 * y3)
        + y1 * (z2 * x3 - x2 * z3)
        + z1 * (x2 * y3 - y2 * x3)
    ) / 6.0


def video_metadata(data: bytes) -> dict[str, Any]:
    """Container header only. Does not decode frames or audio."""
    scan = data[:VIDEO_SCAN_BYTES]
    meta: dict[str, Any] = {
        "bytes": len(data),
        "container": "unknown",
        "header_scanned_bytes": len(scan),
        "header_truncated": len(data) > len(scan),
    }
    if len(scan) >= 12 and scan[4:8] == b"ftyp":
        meta["container"] = "mp4"
        meta["brand"] = scan[8:12].decode("latin-1", errors="replace").strip()
        duration = _mp4_duration_seconds(scan)
        if duration is not None:
            meta["duration_s"] = round(duration, 3)
    elif scan.startswith(b"\x1aE\xdf\xa3"):
        meta["container"] = "webm/mkv"
    elif len(scan) >= 12 and scan[:4] == b"RIFF" and scan[8:12] == b"AVI ":
        meta["container"] = "avi"
        duration = _avi_duration_seconds(scan)
        if duration is not None:
            meta["duration_s"] = round(duration, 3)
    return meta


def _iter_boxes(buf: bytes, start: int, end: int):
    pos = start
    seen = 0
    while pos + 8 <= end and seen < 400:
        size = struct.unpack_from(">I", buf, pos)[0]
        typ = buf[pos + 4 : pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield typ, pos + header, pos + size
        pos += size
        seen += 1


def _mp4_duration_seconds(buf: bytes) -> float | None:
    for typ, start, end in _iter_boxes(buf, 0, len(buf)):
        if typ != b"moov":
            continue
        for inner, payload, payload_end in _iter_boxes(buf, start, end):
            if inner != b"mvhd" or payload_end - payload < 20:
                continue
            version = buf[payload]
            if version == 0 and payload_end - payload >= 20:
                timescale = struct.unpack_from(">I", buf, payload + 12)[0]
                duration = struct.unpack_from(">I", buf, payload + 16)[0]
            elif version == 1 and payload_end - payload >= 32:
                timescale = struct.unpack_from(">I", buf, payload + 20)[0]
                duration = struct.unpack_from(">Q", buf, payload + 24)[0]
            else:
                return None
            if timescale:
                return duration / float(timescale)
    return None


def _avi_duration_seconds(buf: bytes) -> float | None:
    idx = buf.find(b"avih")
    if idx < 0 or idx + 28 > len(buf):
        return None
    micro, _, _, _, frames = struct.unpack_from("<IIIII", buf, idx + 8)
    if micro <= 0 or frames <= 0:
        return None
    return (micro / 1_000_000.0) * frames


def _window_note(data: bytes) -> str | None:
    window = _parse_window()
    if len(data) > window:
        return f"bytes after {window} were not parsed"
    return None


def build_evidence(
    *,
    filename: str,
    kind: Any,
    data: bytes,
    stats: dict[str, Any] | None = None,
    summary: str = "",
    mime: str = "",
    text_hint: str = "",
) -> EvidenceExtract:
    """Return a hard-capped extract. ``text_hint`` is clipped and never stored whole."""
    stats = dict(stats or {})
    hint = _clip(text_hint or "", TEXT_HINT_CHARS)
    modality = modality_of(filename, kind, mime, stats, data)
    if modality == "stl":
        mesh = stl_mesh_stats(data)
        for key, value in mesh.items():
            stats.setdefault(key, value)
    if modality == "video":
        for key, value in video_metadata(data).items():
            stats.setdefault(key, value)

    omitted, excerpts, citations = _modality_payload(modality, filename, stats, hint)
    window = _window_note(data) if modality in {"csv", "json", "log", "text", "gcode", "notebook"} else None
    if window:
        omitted.append(window)
    if stats.get("mesh_scan_capped"):
        omitted.append(f"mesh scan capped at {STL_TRIANGLE_SCAN_CAP} triangles")
    if modality == "video" and stats.get("header_truncated"):
        omitted.append(
            f"container scan capped at {VIDEO_SCAN_BYTES} bytes; moov-at-end duration may be missing"
        )

    extract = EvidenceExtract(
        modality=modality,
        filename=filename,
        summary=_clip(summary, MAX_SUMMARY_CHARS),
        stats=_bound_stats(stats),
        anomalies=_anomaly_cues(stats),
        excerpts=excerpts,
        omitted=[_clip(item, 180) for item in omitted[:6]],
        citations=citations[:MAX_CITATIONS],
        size_bytes=len(data),
        truncated=bool(window or stats.get("mesh_scan_capped") or stats.get("header_truncated")),
        char_budget=MAX_PROMPT_CHARS,
    )
    return enforce_bounds(extract)


def _modality_payload(
    modality: str,
    filename: str,
    stats: dict[str, Any],
    hint: str,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    rows = stats.get("rows")
    records = stats.get("records")
    if modality == "csv":
        columns = stats.get("columns") or []
        header = ", ".join(str(col) for col in columns[:MAX_COLUMNS])
        excerpts = [_clip("header: " + header, MAX_EXCERPT_CHARS)] if header else []
        count = rows if isinstance(rows, int) else "unknown"
        return (
            [f"full table omitted ({count} data rows); model sees schema, ranges, and spike cues only"],
            excerpts,
            [{"label": "schema", "locator": "header"}],
        )
    if modality == "json":
        keys = stats.get("columns") or stats.get("keys") or []
        listing = ", ".join(str(key) for key in keys[:MAX_COLUMNS])
        excerpts = [_clip("keys: " + listing, MAX_EXCERPT_CHARS)] if listing else []
        count = records if isinstance(records, int) else (rows if isinstance(rows, int) else "unknown")
        return (
            [f"full JSON payload omitted ({count} records); model sees keys and numeric ranges only"],
            excerpts,
            [{"label": "schema", "locator": "top-level keys"}],
        )
    if modality == "table":
        return (
            ["binary table omitted; export CSV or JSON for ranges and spike cues"],
            [],
            [{"label": "binary table", "locator": filename}],
        )
    if modality == "log":
        lines = stats.get("lines")
        return (
            [f"full transcript omitted ({lines if lines is not None else 'unknown'} lines); model sees severity counts and short fault excerpts"],
            _log_excerpts(stats),
            [{"label": "fault excerpt", "locator": "sampled error lines"}],
        )
    if modality == "text":
        return (
            ["rest of the text omitted; model sees a short summary and at most a few lines"],
            _text_excerpts(stats, hint),
            [{"label": "excerpt", "locator": "opening or flagged line"}],
        )
    if modality == "image":
        fmt = str(stats.get("format") or "").lower()
        if fmt == "svg" or filename.lower().endswith(".svg"):
            labels = stats.get("text_labels") or []
            excerpts = []
            if stats.get("title"):
                excerpts.append(_clip(str(stats["title"]), MAX_EXCERPT_CHARS))
            if labels:
                excerpts.append(_clip("labels: " + ", ".join(str(x) for x in labels[:4]), MAX_EXCERPT_CHARS))
            return (
                ["raw SVG markup and any embedded images omitted"],
                excerpts[:MAX_EXCERPTS],
                [{"label": "svg labels", "locator": filename}],
            )
        tokens = stats.get("filename_tokens") or []
        excerpts = [_clip(" ".join(str(tok) for tok in tokens), MAX_EXCERPT_CHARS)] if tokens else []
        return (
            ["pixel buffer omitted (no base64 in the model context)"],
            excerpts,
            [{"label": "filename cues", "locator": filename}],
        )
    if modality == "pdf":
        pages = stats.get("pages")
        excerpt = _clip(hint, MAX_EXCERPT_CHARS) if hint else ""
        excerpts = [excerpt] if excerpt else []
        page_note = f"{pages} pages" if pages else "page count unknown"
        return (
            [f"rest of the PDF omitted ({page_note}); model sees a short opening excerpt only"],
            excerpts,
            [{"label": "opening", "locator": "first extracted page"}],
        )
    if modality == "stl":
        scanned = stats.get("triangles_scanned", stats.get("triangles", stats.get("facets_seen")))
        return (
            ["raw mesh omitted; model sees triangle count, bounding box, and volume when the scan is complete"],
            [_clip(f"triangles scanned {scanned}", MAX_EXCERPT_CHARS)] if scanned is not None else [],
            [{"label": "mesh stats", "locator": "header + scanned triangles"}],
        )
    if modality == "cad":
        names = stats.get("product_names") or []
        excerpts = [_clip("products: " + ", ".join(str(n) for n in names[:6]), MAX_EXCERPT_CHARS)] if names else []
        return (
            ["raw CAD body omitted; model sees header names, schema, and entity counts"],
            excerpts,
            [{"label": "header", "locator": "STEP/IGES/DXF header"}],
        )
    if modality == "video":
        container = stats.get("container") or "unknown"
        return (
            [f"frame payloads omitted ({container}); metadata stub only, no decode"],
            [],
            [{"label": "container", "locator": "file header"}],
        )
    if modality == "notebook":
        heading = stats.get("heading") or ""
        excerpts = [_clip(str(heading), MAX_EXCERPT_CHARS)] if heading else []
        return (
            ["notebook source and outputs omitted; model sees cell counts and a heading"],
            excerpts,
            [{"label": "heading", "locator": "first markdown heading"}],
        )
    if modality == "gcode":
        tools = stats.get("tools") or []
        excerpts = [_clip("tools " + ", ".join(str(t) for t in tools[:8]), MAX_EXCERPT_CHARS)] if tools else []
        return (
            ["toolpath body omitted; model sees tool, feed, and spindle ranges"],
            excerpts,
            [{"label": "tools", "locator": "T/F/S words"}],
        )
    return (
        ["raw bytes omitted; model sees only a short filename summary"],
        [],
        [{"label": "file", "locator": filename}],
    )


def enforce_bounds(extract: EvidenceExtract) -> EvidenceExtract:
    """Re-apply caps. Drops excerpts before it drops the omission list."""
    summary = _clip(extract.summary, MAX_SUMMARY_CHARS)
    anomalies = [_clip(item, MAX_ANOMALY_CHARS) for item in extract.anomalies[:MAX_ANOMALIES]]
    excerpts = [_clip(item, MAX_EXCERPT_CHARS) for item in extract.excerpts[:MAX_EXCERPTS]]
    truncated = bool(extract.truncated)
    while excerpts and sum(len(item) for item in excerpts) > MAX_EXCERPT_TOTAL:
        excerpts.pop()
        truncated = True
    omitted = [_clip(item, 180) for item in extract.omitted[:6]]
    citations: list[dict[str, str]] = []
    for item in extract.citations[:MAX_CITATIONS]:
        if not isinstance(item, dict):
            continue
        citations.append({
            "label": _clip(str(item.get("label") or ""), 40),
            "locator": _clip(str(item.get("locator") or ""), 80),
        })
    bounded = extract.model_copy(update={
        "summary": summary,
        "anomalies": anomalies,
        "excerpts": excerpts,
        "omitted": omitted,
        "citations": citations,
        "stats": _bound_stats(extract.stats),
        "truncated": truncated,
        "char_budget": MAX_PROMPT_CHARS,
    })
    for _ in range(8):
        if len(render_extract(bounded)) <= MAX_PROMPT_CHARS:
            break
        if bounded.excerpts:
            bounded = bounded.model_copy(update={"excerpts": bounded.excerpts[:-1], "truncated": True})
            continue
        if len(bounded.anomalies) > 3:
            bounded = bounded.model_copy(update={"anomalies": bounded.anomalies[:3], "truncated": True})
            continue
        if len(bounded.summary) > 160:
            bounded = bounded.model_copy(update={"summary": _clip(bounded.summary, 160), "truncated": True})
            continue
        stats = dict(bounded.stats)
        numeric = dict(stats.get("numeric") or {})
        if len(numeric) > 3:
            for key in list(numeric)[3:]:
                numeric.pop(key, None)
            stats["numeric"] = numeric
            bounded = bounded.model_copy(update={"stats": _bound_stats(stats), "truncated": True})
            continue
        if len(bounded.omitted) > 2:
            bounded = bounded.model_copy(update={"omitted": bounded.omitted[:2], "truncated": True})
            continue
        break
    return bounded


def render_extract(extract: EvidenceExtract) -> str:
    lines = [f"[{extract.modality}] {extract.filename} ({extract.size_bytes} bytes)"]
    if extract.summary:
        lines.append(extract.summary)
    stats_line = _stats_line(extract.stats)
    if stats_line:
        lines.append("stats: " + stats_line)
    if extract.anomalies:
        lines.append("anomalies: " + "; ".join(extract.anomalies))
    if extract.omitted:
        lines.append("omitted: " + "; ".join(extract.omitted))
    if extract.excerpts:
        lines.append("excerpts: " + " | ".join(extract.excerpts))
    if extract.citations:
        cites = ", ".join(
            f"{item.get('label')}@{item.get('locator')}" for item in extract.citations
        )
        lines.append("citations: " + cites)
    return "\n".join(lines)


def _stats_line(stats: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "rows", "records", "columns", "pages", "lines", "width", "height",
        "triangles", "facets_seen", "bbox", "duration_s", "container", "brand",
        "severity", "alarm_codes", "volume_stl_units", "stub",
    ):
        if key not in stats or stats[key] in (None, "", [], {}):
            continue
        value = stats[key]
        if isinstance(value, list):
            value = ",".join(str(item) for item in value[:8])
        elif isinstance(value, dict):
            value = ",".join(f"{name}:{item}" for name, item in list(value.items())[:6])
        parts.append(f"{key}={value}")
    numeric = stats.get("numeric") or {}
    if isinstance(numeric, dict):
        for name, entry in list(numeric.items())[:MAX_NUMERIC_COLS]:
            if isinstance(entry, dict):
                parts.append(f"{name}[{entry.get('min')}..{entry.get('max')}]")
    return _clip("; ".join(parts), 360)


def prompt_from_artifact(artifact: ExperimentArtifact) -> str:
    """LLM-facing text for one file. Never includes raw extracted_text."""
    if artifact.evidence is not None:
        text = render_extract(artifact.evidence)
    else:
        kind = _kind_value(artifact.kind)
        text = (
            f"[{kind}] {artifact.filename} ({artifact.size_bytes} bytes)\n"
            f"{_clip(artifact.summary or '', MAX_SUMMARY_CHARS)}\n"
            "omitted: raw text withheld (no compact extract stored)"
        )
    if len(text) > MAX_PROMPT_CHARS:
        return text[: MAX_PROMPT_CHARS - 1].rstrip() + "…"
    return text


def prompt_from_artifacts(artifacts: list[ExperimentArtifact]) -> str:
    """Join per-file prompts and stop at the dossier budget."""
    chunks: list[str] = []
    total = 0
    for artifact in artifacts:
        piece = prompt_from_artifact(artifact)
        if total + len(piece) > MAX_DOSSIER_ARTIFACT_CHARS:
            chunks.append("[further artifact extracts omitted — dossier budget]")
            break
        chunks.append(piece)
        total += len(piece) + 1
    return "\n".join(chunks)


def preview_text(extract: EvidenceExtract) -> str:
    """Short text persisted on the artifact for the gallery, not the raw file."""
    parts = list(extract.excerpts) or ([extract.summary] if extract.summary else [])
    return "\n".join(parts)[:800]


def extraction_brief(experiment: ExperimentInput | None) -> str:
    """User-facing note: what the model received vs what was left out."""
    artifacts = list(experiment.artifacts) if experiment is not None else []
    evidenced = [art for art in artifacts if art.evidence is not None]
    if not evidenced:
        return ""
    lines = [
        "**What reached the model** — compact extracts only. Raw files stay in the gallery and are not pasted into the context window.",
    ]
    for art in evidenced:
        evidence = art.evidence
        assert evidence is not None
        lines.append(f"- **{art.filename}** ({evidence.modality}): {_clip(evidence.summary, 180)}")
        if evidence.omitted:
            lines.append("  Omitted: " + "; ".join(evidence.omitted[:3]))
    return "\n".join(lines)

"""Security helpers for the EpiDebug web API (session tokens, rate limits,
upload validation, response headers)."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

SESSION_ID_RE = re.compile(r"^[0-9a-f]{12}$")
ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
TOKEN_HEADER = "x-session-token"
COOKIE_PREFIX = "epi_s_"

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_REQUEST = 12
MAX_MESSAGE_CHARS = 4000
MAX_INFO_CHARS = 4000
MAX_SESSION_MESSAGES = 60
PARSE_TIMEOUT_S = 15.0

ALLOWED_EXT = {
    # images
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic",
    # cad
    ".step", ".stp", ".stl", ".dxf", ".iges", ".igs", ".obj", ".3mf", ".ply",
    # sensor / data
    ".csv", ".tsv", ".json", ".jsonl", ".parquet", ".xlsx", ".xls", ".h5", ".hdf5", ".mat", ".npy",
    # logs / text
    ".log", ".txt", ".out", ".err", ".md", ".rst", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".toml",
    # process
    ".nc", ".gcode", ".tap", ".cnc", ".ngc", ".mpf", ".pgm",
    # docs
    ".pdf", ".docx", ".doc", ".ipynb", ".bom", ".coa",
    # video (metadata only)
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
}
BLOCKED_EXT = {".html", ".htm", ".svg", ".svgz", ".js", ".mjs", ".xhtml", ".xht", ".php", ".exe", ".sh", ".bat"}

_IMAGE_MAGIC = {
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".webp": [b"RIFF"],
    ".bmp": [b"BM"],
    ".tif": [b"II*\x00", b"MM\x00*"],
    ".tiff": [b"II*\x00", b"MM\x00*"],
    ".pdf": [b"%PDF-"],
}
_HTML_SNIFF = re.compile(rb"^\s*(<!doctype html|<html|<svg|<script|<\?xml[^>]*>\s*<svg)", re.I)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenStore:
    """Stores sha256(token) per session on disk next to the sessions."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        if not SESSION_ID_RE.match(session_id or ""):
            raise ValueError("bad session id")
        return self.root / f"{session_id}.tok"

    def _read_ttl(self) -> int:
        import os

        try:
            return max(60, int(os.environ.get("EPIDEBUG_SESSION_TTL_S", str(7 * 24 * 3600))))
        except ValueError:
            return 7 * 24 * 3600

    @property
    def ttl_s(self) -> int:
        return self._read_ttl()

    def issue(self, session_id: str) -> str:
        token = new_token()
        self._path(session_id).write_text(f"{_digest(token)} {int(time.time())}", encoding="utf-8")
        return token

    def has(self, session_id: str) -> bool:
        try:
            return self._path(session_id).exists()
        except ValueError:
            return False

    def check(self, session_id: str, token: str | None) -> bool:
        if not token:
            return False
        try:
            path = self._path(session_id)
            raw = path.read_text(encoding="utf-8").strip()
            stored, _, issued = raw.partition(" ")
            issued_at = int(issued) if issued else int(path.stat().st_mtime)
        except (OSError, ValueError):
            return False
        if time.time() - issued_at > self.ttl_s:
            return False  # server-side expiry, independent of cookie max-age
        return hmac.compare_digest(stored, _digest(token))



def safe_display_name(name: str | None) -> str:
    """Original filename kept only as metadata: basename, no control chars."""
    raw = (name or "untitled.bin").replace("\\", "/").split("/")[-1]
    raw = re.sub(r"[\x00-\x1f\x7f]", "", raw).strip().lstrip(".") or "untitled.bin"
    return raw[:180]


def validate_upload(name: str, data: bytes) -> str | None:
    """Return an error message, or None when the file is acceptable."""
    ext = Path(name).suffix.lower()
    if ext in BLOCKED_EXT:
        return f"File type {ext} is not allowed"
    if ext and ext not in ALLOWED_EXT:
        return f"File type {ext} is not allowed"
    head = data[:512]
    if _HTML_SNIFF.match(head):
        return "HTML/SVG/script content is not allowed"
    magics = _IMAGE_MAGIC.get(ext)
    if magics and data and not any(head.startswith(m) for m in magics):
        return f"File content does not match its {ext} extension"
    return None



def csp_header() -> str:
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'"
    )


SECURITY_HEADERS = {
    "Content-Security-Policy": csp_header(),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _extend_from_ingest() -> None:
    try:
        from epidebug.engine import ingest as _ing
    except Exception:  # pragma: no cover
        return
    for name in ("IMAGE_EXT", "CAD_EXT", "SENSOR_EXT", "LOG_EXT", "PROCESS_EXT",
                 "DOC_EXT", "NOTEBOOK_EXT", "MATERIAL_EXT", "VIDEO_EXT"):
        ALLOWED_EXT.update(getattr(_ing, name, set()))
    ALLOWED_EXT.difference_update(BLOCKED_EXT)


_extend_from_ingest()

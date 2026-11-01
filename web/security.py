"""Security helpers for the EpiDebug web API (session tokens, rate limits,
upload validation, response headers)."""

from __future__ import annotations

import hashlib
import logging
import os
import hmac
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

SESSION_ID_RE = re.compile(r"^[0-9a-f]{12}$")
ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
TOKEN_HEADER = "x-session-token"
COOKIE_PREFIX = "epi_s_"
CLIENT_HEADER = "x-epidebug-client"
CLIENT_HEADER_VALUE = "web"

from epidebug.limits import (  # noqa: E402,F401  (re-exported)
    MAX_BUNDLE_FIELD_CHARS,
    MAX_CAPTION_CHARS,
    MAX_DOMAIN_CHARS,
    MAX_FILE_BYTES,
    MAX_FILES_PER_REQUEST,
    MAX_INFO_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_PDF_PAGES,
    MAX_REASON_CHARS,
    MAX_SESSION_MESSAGES,
    MAX_TITLE_CHARS,
    MAX_TOTAL_BYTES,
)

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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class MemoryBackend:
    """Sliding-window counters in a bounded LRU (oldest key evicted first)."""

    def __init__(self, max_keys: int | None = None) -> None:
        self.max_keys = max_keys or _env_int("EPIDEBUG_RATE_MAX_KEYS", 50000)
        self._hits: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str, limits: list[tuple[int, int]]) -> bool:
        """limits = [(window_s, max_hits)]. Records the hit only if allowed."""
        now = time.monotonic()
        horizon = max(w for w, _ in limits)
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                q = self._hits[key] = deque()
                while len(self._hits) > self.max_keys:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)
            while q and now - q[0] > horizon:
                q.popleft()
            for window, cap in limits:
                if sum(1 for t in q if now - t <= window) >= cap:
                    return False
            q.append(now)
            return True

    def __len__(self) -> int:
        return len(self._hits)


class RestKVBackend:
    """Fixed-window counters on Upstash Redis / Vercel KV via the REST API.

    Enabled when UPSTASH_REDIS_REST_URL+UPSTASH_REDIS_REST_TOKEN (or
    KV_REST_API_URL+KV_REST_API_TOKEN) are set. Any KV error falls back to
    the in-memory backend so the app never fails open *and* never hard-fails.
    Credentials are read from env and never logged.

    NOTE: this backend has NOT been exercised against a live Upstash/KV
    instance; tests cover only the in-memory backend and the fallback path.
    Verify counters/expiry on a staging KV before relying on it.
    """

    def __init__(self, url: str, token: str, fallback: MemoryBackend, prefix: str = "epi:rl:") -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._fallback = fallback
        self._prefix = prefix

    @classmethod
    def from_env(cls, fallback: MemoryBackend):
        url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
        token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
        if url and token and os.environ.get("EPIDEBUG_RATE_BACKEND", "auto") != "memory":
            return cls(url, token, fallback)
        return None

    def _pipeline(self, cmds: list[list]) -> list:
        import json as _json
        import urllib.request

        req = urllib.request.Request(
            self._url + "/pipeline",
            data=_json.dumps(cmds).encode(),
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1.5) as res:  # noqa: S310 (configured https URL)
            return [item.get("result") for item in _json.loads(res.read())]

    def hit(self, key: str, limits: list[tuple[int, int]]) -> bool:
        now = int(time.time())
        cmds, keys = [], []
        for window, _ in limits:
            k = f"{self._prefix}{key}:{window}:{now // window}"
            keys.append(k)
            cmds += [["INCR", k], ["EXPIRE", k, str(window + 5)]]
        try:
            results = self._pipeline(cmds)
            counts = [int(results[i * 2]) for i in range(len(limits))]
        except Exception:
            logging.getLogger(__name__).warning("rate-limit KV unavailable; using in-memory fallback")
            return self._fallback.hit(key, limits)
        return all(c <= cap for c, (_, cap) in zip(counts, limits))


def make_backend() -> MemoryBackend | RestKVBackend:
    mem = MemoryBackend()
    return RestKVBackend.from_env(mem) or mem


class RateLimiter:
    """Per-key limiter over a pluggable backend (memory LRU or REST KV)."""

    def __init__(self, per_minute: int, per_hour: int, backend=None, name: str = "llm") -> None:
        self.per_minute = per_minute
        self.per_hour = per_hour
        self.name = name
        self.backend = backend or MemoryBackend()

    def allow(self, key: str) -> bool:
        return self.backend.hit(f"{self.name}:{key}", [(60, self.per_minute), (3600, self.per_hour)])


class DailyBudget:
    """Global circuit breaker: at most ``cap`` LLM-backed calls per UTC day
    across all clients (EPIDEBUG_DAILY_LLM_CAP; 0 disables). Shares the rate
    backend, so with KV configured the cap is global across instances."""

    def __init__(self, cap: int, backend=None) -> None:
        self.cap = cap
        self.backend = backend or MemoryBackend()

    def allow(self) -> bool:
        if self.cap <= 0:
            return True
        day = int(time.time()) // 86400
        return self.backend.hit(f"budget:{day}", [(86400, self.cap)])


def verify_turnstile(token: str, remote_ip: str) -> bool:
    """Cloudflare Turnstile check; only called when EPIDEBUG_TURNSTILE_SECRET is set."""
    import json as _json
    import urllib.parse
    import urllib.request

    secret = os.environ.get("EPIDEBUG_TURNSTILE_SECRET", "")
    if not secret:
        return True
    if not token:
        return False
    body = urllib.parse.urlencode({"secret": secret, "response": token, "remoteip": remote_ip}).encode()
    try:
        with urllib.request.urlopen(  # noqa: S310
            "https://challenges.cloudflare.com/turnstile/v0/siteverify", data=body, timeout=3
        ) as res:
            return bool(_json.loads(res.read()).get("success"))
    except Exception:
        return False


def safe_display_name(name: str | None) -> str:
    """Original filename kept only as metadata: basename, no control chars,
    no trailing dots/spaces (Windows-style ``evil.html.`` -> ``evil.html``)."""
    raw = (name or "untitled.bin").replace("\\", "/").split("/")[-1]
    raw = re.sub(r"[\x00-\x1f\x7f]", "", raw).strip().lstrip(".")
    raw = raw.rstrip(". ") or "untitled.bin"
    return raw[:180]


def normalized_ext(name: str) -> str:
    return Path((name or "").rstrip(". ")).suffix.lower()


SNIFF_BYTES = 8192
_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
_MARKUP_ANYWHERE = re.compile(
    r"<\s*(svg|html|script|iframe|object|embed|foreignobject)\b|<!doctype\s+html|xmlns(?::[\w.-]+)?\s*=\s*[\"']http://www\.w3\.org/(2000/svg|1999/xhtml)",
    re.I,
)
_LEADING_JUNK = re.compile(r"^(\s+|<!--.*?-->|<\?xml[^>]*\?>|<!doctype(?!\s+html)[^>]*>)+", re.I | re.S)
_MARKUP_START = re.compile(r"^<\s*(svg|html|script|iframe|body|head)\b|^<!doctype\s+html", re.I)
# Text formats a browser could render as markup if sniffed; scanned in full.
_FULL_SCAN_EXT = {"", ".xml", ".txt", ".md", ".rst", ".json", ".jsonl", ".yaml", ".yml", ".ini",
                  ".cfg", ".toml", ".bom", ".coa", ".ipynb"}


def _sniff_text(data: bytes) -> str:
    head = data[:SNIFF_BYTES]
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        try:
            return head.decode("utf-16", "ignore")
        except Exception:  # pragma: no cover
            return ""
    for bom in _BOMS:
        if head.startswith(bom):
            head = head[len(bom):]
            break
    return head.replace(b"\x00", b"").decode("latin-1", "ignore")


def looks_like_markup(name: str, data: bytes) -> bool:
    text = _sniff_text(data)
    stripped = _LEADING_JUNK.sub("", text)
    if _MARKUP_START.match(stripped):
        return True
    if normalized_ext(name) in _FULL_SCAN_EXT and _MARKUP_ANYWHERE.search(text):
        return True
    return False


def validate_upload(name: str, data: bytes) -> str | None:
    """Return an error message, or None when the file is acceptable."""
    ext = normalized_ext(name)
    if ext in BLOCKED_EXT:
        return f"File type {ext} is not allowed"
    if ext and ext not in ALLOWED_EXT:
        return f"File type {ext} is not allowed"
    if looks_like_markup(name, data):
        return "HTML/SVG/script content is not allowed"
    head = data[:512]
    magics = _IMAGE_MAGIC.get(ext)
    if magics and data and not any(head.startswith(m) for m in magics):
        return f"File content does not match its {ext} extension"
    return None


def strip_role_prefixes(text: str) -> str:
    """Request-time pass (NFKC, Cf strip, homoglyph fold, role markers).
    Angle brackets are escaped later, when prompts are built."""
    from epidebug.untrusted import clean_stored

    return clean_stored(text or "")


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


def _parse_child(conn, fn, args, kwargs):  # pragma: no cover - runs in child
    try:
        conn.send(("ok", fn(*args, **kwargs)))
    except BaseException as exc:  # noqa: BLE001
        conn.send(("err", repr(exc)[:200]))
    finally:
        conn.close()


class ParseTimeout(Exception):
    pass


def _isolation_possible(fn) -> bool:
    import sys

    if getattr(fn, "__module__", None) == "__main__":
        return False
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    # No __file__ is fine for forkserver (main is skipped); a bogus path is not.
    return main_file is None or os.path.exists(main_file)


def run_isolated(fn, *args, timeout: float = PARSE_TIMEOUT_S, **kwargs):
    """Run ``fn`` in a child process (forkserver) and kill it on timeout.

    Unlike a thread pool, a pathological parser (decompression bomb, PDF
    loop) is actually terminated. Set EPIDEBUG_PARSE_ISOLATION=thread to
    run inline (e.g. platforms without fork); the timeout is then advisory.
    """
    import multiprocessing as mp

    methods = mp.get_all_start_methods()
    if os.environ.get("EPIDEBUG_PARSE_ISOLATION", "process") == "thread" or not methods:
        return fn(*args, **kwargs)
    # Known limitation: forkserver/spawn children re-import ``__main__`` from its
    # file and unpickle ``fn`` by module path. If the main module has no real
    # file (``python -c``, stdin, a REPL, some embedded launchers) or ``fn``
    # lives in ``__main__``, the child dies before running and we would only
    # see "parser exited". In that case we fall back to running inline (timeout
    # advisory only, same as EPIDEBUG_PARSE_ISOLATION=thread) and log a warning.
    if not _isolation_possible(fn):
        logging.getLogger(__name__).warning(
            "parse isolation unavailable (no importable __main__ file); running inline")
        return fn(*args, **kwargs)
    # forkserver avoids fork()-in-a-threaded-server deadlocks; fn must be importable.
    ctx = mp.get_context("forkserver" if "forkserver" in methods else methods[0])
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_parse_child, args=(child, fn, args, kwargs), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(timeout):
            raise ParseTimeout()
        status, value = parent.recv()
    except EOFError as exc:
        raise RuntimeError("parser exited") from exc
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(1)
        parent.close()
    if status != "ok":
        raise RuntimeError(value)
    return value

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

MAX_MESSAGE_CHARS = 4000
MAX_INFO_CHARS = 4000
MAX_SESSION_MESSAGES = 60

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


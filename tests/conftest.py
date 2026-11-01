"""Test defaults: every TestClient acts like the bundled frontend (sends the
X-EpiDebug-Client header) and session-creation limits are relaxed. Security
tests opt out explicitly."""

import pytest
from starlette.testclient import TestClient

_orig_init = TestClient.__init__


def _init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    self.headers.setdefault("x-epidebug-client", "web")


TestClient.__init__ = _init


@pytest.fixture(autouse=True)
def _relaxed_session_limits(monkeypatch):
    monkeypatch.setenv("EPIDEBUG_SESSION_CREATE_PER_MIN", "1000")
    monkeypatch.setenv("EPIDEBUG_SESSION_CREATE_PER_HOUR", "10000")
    for name in ("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL", "EPIDEBUG_TURNSTILE_SECRET", "VERCEL"):
        monkeypatch.delenv(name, raising=False)

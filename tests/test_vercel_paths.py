"""Vercel writable-FS redirects must not change local uvicorn defaults."""

from __future__ import annotations

from pathlib import Path

from web.app import ROOT, create_app, default_upload_dir


def test_default_upload_dir_local(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("EPIDEBUG_UPLOAD_DIR", raising=False)
    assert default_upload_dir() == ROOT / "uploads"


def test_default_upload_dir_vercel_tmp(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("EPIDEBUG_UPLOAD_DIR", raising=False)
    assert default_upload_dir() == Path("/tmp/epidebug-uploads")


def test_default_upload_dir_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("EPIDEBUG_UPLOAD_DIR", str(tmp_path / "custom"))
    assert default_upload_dir() == tmp_path / "custom"


def test_create_app_honors_vercel_tmp(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("EPIDEBUG_UPLOAD_DIR", raising=False)
    app = create_app(prefer_llm=False, cases=[])
    assert app.state.upload_dir == Path("/tmp/epidebug-uploads")
    assert (Path("/tmp/epidebug-uploads") / "sessions").is_dir()

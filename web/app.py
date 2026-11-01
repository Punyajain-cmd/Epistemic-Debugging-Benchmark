#!/usr/bin/env python3
"""Researcher-facing EpiDebug prototype.

Accepts the full experimental record — notes, photos, CAD, logs, sensor
tables — then runs epistemic debugging.

Session JSON stays backward compatible (diagnosis, experiment, session_id)
and adds an additive ``view`` object for a redesigned frontend.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from epidebug.engine import EpistemicDebuggingEngine
from epidebug.engine.chat import replace_dump
from epidebug.engine.ingest import ingest_bytes
from epidebug.engine.present import EVIDENCE_SLOTS, VIEW_CONTRACT, session_payload
from epidebug.engine.session import SessionStore
from epidebug.schema import ExperimentArtifact, ExperimentInput, TestCase
from epidebug.tools import TOOL_REGISTRY, invoke_tool, list_tools
from web import security as sec

log = logging.getLogger("epidebug.web")

STATIC = Path(__file__).resolve().parent / "static"
CASES_DIR = ROOT / "test_cases"
MOCK_DATA = ROOT / "mock_data"
MAX_FILE_BYTES = 25 * 1024 * 1024

DOMAIN_ALIASES = {
    "machining": "manufacturing",
    "cnc": "manufacturing",
    "batteries / energy": "energy",
    "batteries": "energy",
    "energy storage": "energy",
    "electronics": "engineering",
    "mechanical": "engineering",
    "other": None,
    "not sure / mixed": None,
    "mixed": None,
}

ACCEPTS = [
    "photos of the setup or failed part",
    "video (metadata only — frames are not sent to the model)",
    "CAD (STEP, STL, DXF, IGES)",
    "sensor CSV/JSON",
    "machine and console logs",
    "PDFs, notebooks, datasheets",
    "materials, process, and protocol notes",
]


class DiagnoseRequest(BaseModel):
    case_id: Optional[str] = None
    experiment: Optional[ExperimentInput] = None


class RejectRequest(BaseModel):
    hypothesis_id: str
    reason: str = "Rejected by researcher"


class InfoRequest(BaseModel):
    information: str


class FollowUpRequest(BaseModel):
    intervention: str
    outcome: str


class ChatRequest(BaseModel):
    message: str = ""
    session_id: Optional[str] = None
    force_diagnose: bool = False
    title: Optional[str] = None
    domain: Optional[str] = None


class ToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def _case_summary(case: TestCase) -> dict[str, Any]:
    objective = (case.objective or "").strip().replace("\n", " ")
    return {
        "id": case.id,
        "title": case.title,
        "domain": case.domain.value,
        "subdomain": case.subdomain,
        "failure_category": case.failure_category.value,
        "failure_category_label": case.failure_category.short_label,
        "difficulty": case.difficulty.value,
        "information_regime": case.information_regime.value,
        "split": case.split.value,
        "tags": case.tags,
        "objective_preview": objective[:220],
    }


def _normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip()
    aliased = DOMAIN_ALIASES.get(key, DOMAIN_ALIASES.get(key.lower(), key))
    return aliased or None


def _split_lines(value: str | None) -> list[str]:
    if not value:
        return []
    return [ln.strip() for ln in value.replace("\r", "").split("\n") if ln.strip()]


def _fixture_index() -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    if not MOCK_DATA.is_dir():
        return index
    for folder in sorted(p for p in MOCK_DATA.iterdir() if p.is_dir()):
        index[folder.name] = sorted(
            p.name
            for p in folder.iterdir()
            if p.is_file()
            and not p.name.startswith(".")
            and p.name.lower() not in {"readme.md", "license.md"}
        )
    return index


def _restore_file_store(upload_dir: Path) -> dict[str, Path]:
    store: dict[str, Path] = {}
    if not upload_dir.exists():
        return store
    for dest in upload_dir.iterdir():
        if not dest.is_dir() or dest.name in {"sessions", "tokens"}:
            continue
        files = [p for p in dest.iterdir() if p.is_file()]
        if files:
            store[dest.name] = files[0]
    return store


def _session_summaries(engine: EpistemicDebuggingEngine) -> list[dict[str, Any]]:
    items = []
    for session in engine.sessions.list():
        diagnosis = session.diagnosis
        n_files = len(session.experiment.artifacts) if session.experiment else 0
        items.append(
            {
                "session_id": session.session_id,
                "title": session.title,
                "case_id": session.case_id,
                "created_at": session.created_at,
                "file_count": n_files,
                "leading_cause": diagnosis.leading_cause if diagnosis else "",
                "engine_mode": diagnosis.engine_mode if diagnosis else None,
                "confidence": diagnosis.uncertainty.confidence if diagnosis else None,
            }
        )
    items.sort(key=lambda row: row["created_at"], reverse=True)
    return items


def default_upload_dir() -> Path:
    """Writable session/upload root.

    Local default is ``<repo>/uploads``. Vercel Functions only allow writes
    under ``/tmp``, so ``VERCEL=1`` redirects there. ``EPIDEBUG_UPLOAD_DIR``
    always wins (tests and operators).
    """
    override = os.environ.get("EPIDEBUG_UPLOAD_DIR")
    if override:
        return Path(override)
    if os.environ.get("VERCEL"):
        return Path("/tmp/epidebug-uploads")
    return ROOT / "uploads"


def create_app(
    *,
    upload_dir: Path | None = None,
    prefer_llm: bool = True,
    cases: list[TestCase] | None = None,
) -> FastAPI:
    """Build the prototype API. ``upload_dir`` is overrideable for tests."""
    upload_dir = Path(upload_dir) if upload_dir else default_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir = upload_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    file_store = _restore_file_store(upload_dir)

    loaded_cases = cases if cases is not None else TestCase.load_all(CASES_DIR)
    case_index = {case.id: case for case in loaded_cases}
    engine = EpistemicDebuggingEngine(
        prefer_llm=prefer_llm,
        sessions=SessionStore(sessions_dir),
        cases=case_index,
    )

    app = FastAPI(
        title="EpiDebug",
        description="Dump the experiment. Diagnose competing causes.",
        version="0.6.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    tokens = sec.TokenStore(upload_dir / "tokens")
    secure_cookie = bool(os.environ.get("VERCEL"))

    def valid_session_id(session_id: str) -> str:
        sid = (session_id or "").strip()
        if not sec.SESSION_ID_RE.match(sid):
            raise HTTPException(404, "Unknown session")
        return sid

    def require_session(request: Request, session_id: str):
        sid = valid_session_id(session_id)
        token = request.headers.get(sec.TOKEN_HEADER) or request.cookies.get(sec.COOKIE_PREFIX + sid)
        if not tokens.check(sid, token):
            # Same answer for missing and foreign sessions: no existence oracle.
            raise HTTPException(404, "Unknown session")
        try:
            return engine.sessions.get(sid)
        except KeyError as exc:
            raise HTTPException(404, "Unknown session") from exc

    def owns_artifact(request: Request, artifact_id: str) -> bool:
        """True when a caller-held session token covers a session with this file."""
        candidates: list[tuple[str, str | None]] = []
        hdr_sid = request.headers.get("x-session-id", "")
        if hdr_sid:
            candidates.append((hdr_sid, request.headers.get(sec.TOKEN_HEADER)))
        for name, value in request.cookies.items():
            if name.startswith(sec.COOKIE_PREFIX):
                candidates.append((name[len(sec.COOKIE_PREFIX):], value))
        for sid, token in candidates[:20]:
            if not sec.SESSION_ID_RE.match(sid or "") or not tokens.check(sid, token):
                continue
            try:
                session = engine.sessions.get(sid)
            except KeyError:
                continue
            exp = session.experiment
            if exp is not None and any(a.id == artifact_id for a in exp.artifacts):
                return True
        return False

    def issue(session, response: Response) -> dict:
        token = tokens.issue(session.session_id)
        response.set_cookie(
            sec.COOKIE_PREFIX + session.session_id,
            token,
            max_age=tokens.ttl_s,
            httponly=True,
            secure=secure_cookie,
            samesite="strict",
            path="/api",
        )
        payload = dump_session(session)
        payload["session_token"] = token
        return payload

    def clean_text(value: str, limit: int) -> str:
        # Length caps are enforced in the rate-limit PR (H1).
        return (value or "").strip()

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
    if MOCK_DATA.is_dir():
        app.mount("/mock_data", StaticFiles(directory=MOCK_DATA), name="mock_data")
    app.state.engine = engine
    app.state.cases = loaded_cases
    app.state.case_index = case_index
    app.state.upload_dir = upload_dir
    app.state.file_store = file_store

    def save_and_ingest(
        upload: UploadFile,
        role: str | None,
        caption: str,
    ) -> ExperimentArtifact:
        data = upload.file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{upload.filename} exceeds 25 MB")
        filename = upload.filename or "untitled.bin"
        artifact = ingest_bytes(
            filename,
            data,
            role=role,
            caption=caption,
            content_type=getattr(upload, "content_type", None),
        )
        dest = upload_dir / artifact.id
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / filename
        path.write_bytes(data)
        file_store[artifact.id] = path
        if artifact.kind.value == "image":
            artifact.preview_url = f"/api/files/{artifact.id}"
        return artifact

    def dump_session(session):
        return session_payload(session)

    def experiment_from_bundle(
        *,
        title: str,
        domain: str,
        objective: str,
        unexpected_outcome: str,
        setup_description: str,
        setup: str,
        materials: str,
        processing: str,
        protocol: str,
        telemetry: str,
        logs: str,
        context: str,
        roles: str,
        captions: str,
        files: list[UploadFile] | None,
    ) -> ExperimentInput:
        try:
            role_list = json.loads(roles) if roles else []
            caption_list = json.loads(captions) if captions else []
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "roles and captions must be JSON arrays") from exc

        artifacts: list[ExperimentArtifact] = []
        for i, upload in enumerate(files or []):
            if not upload.filename:
                continue
            role = role_list[i] if i < len(role_list) else None
            caption = caption_list[i] if i < len(caption_list) else ""
            artifacts.append(save_and_ingest(upload, role, caption))

        return ExperimentInput(
            title=title or "Untitled experiment",
            domain=_normalize_domain(domain),
            objective=objective,
            unexpected_outcome=unexpected_outcome,
            setup_description=setup_description or setup,
            materials=_split_lines(materials),
            processing=_split_lines(processing),
            protocol=_split_lines(protocol) or _split_lines(processing),
            telemetry_notes=_split_lines(telemetry) + _split_lines(logs),
            contextual_clues=_split_lines(context),
            artifacts=artifacts,
        )

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "cases": len(loaded_cases),
            "engine_mode": "llm" if engine.llm.available else "heuristic",
            "accepts": ACCEPTS,
            "contract_version": VIEW_CONTRACT,
            "evidence_slots": EVIDENCE_SLOTS,
            "max_file_bytes": MAX_FILE_BYTES,
        }

    @app.get("/api/fixtures")
    def list_fixtures():
        return {"contract_version": VIEW_CONTRACT, "fixtures": _fixture_index()}

    @app.get("/api/cases")
    def list_cases():
        return [_case_summary(case) for case in loaded_cases]

    allow_ground_truth = os.environ.get("EPIDEBUG_ALLOW_GROUND_TRUTH") == "1"

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str, include_ground_truth: bool = False):
        case = case_index.get(case_id)
        if case is None:
            raise HTTPException(404, "Unknown case")
        payload = case.model_dump(mode="json")
        if not (include_ground_truth and allow_ground_truth):
            payload.pop("ground_truth", None)
            payload.pop("scoring", None)
        payload["prompt"] = case.to_prompt()
        payload["summary"] = _case_summary(case)
        return payload

    @app.get("/api/files/{artifact_id}")
    def get_file(artifact_id: str, request: Request):
        if not owns_artifact(request, artifact_id):
            raise HTTPException(404, "Unknown file")
        path = file_store.get(artifact_id)
        if path is None or not path.exists():
            raise HTTPException(404, "Unknown file")
        return FileResponse(path, filename=path.name)

    @app.post("/api/diagnose")
    def diagnose(req: DiagnoseRequest, request: Request, response: Response):
        if req.case_id:
            case = case_index.get(req.case_id)
            if case is None:
                raise HTTPException(404, "Unknown case")
            session = engine.open_session(case=case)
        elif req.experiment:
            if not req.experiment.has_content():
                raise HTTPException(400, "Provide notes, files, or an unexpected outcome.")
            session = engine.open_session(experiment=req.experiment)
        else:
            raise HTTPException(400, "Provide case_id or experiment")
        return issue(session, response)

    @app.post("/api/diagnose-bundle")
    async def diagnose_bundle(
        request: Request,
        response: Response,
        title: str = Form("Untitled experiment"),
        domain: str = Form(""),
        objective: str = Form(""),
        unexpected_outcome: str = Form(""),
        setup_description: str = Form(""),
        setup: str = Form(""),
        materials: str = Form(""),
        processing: str = Form(""),
        protocol: str = Form(""),
        telemetry: str = Form(""),
        logs: str = Form(""),
        context: str = Form(""),
        roles: str = Form("[]"),
        captions: str = Form("[]"),
        session_id: str = Form(""),
        files: list[UploadFile] | None = File(default=None),
    ):
        existing = require_session(request, session_id) if session_id.strip() else None
        fields = {
            "title": title, "domain": domain, "objective": objective,
            "unexpected_outcome": unexpected_outcome, "setup_description": setup_description,
            "setup": setup, "materials": materials, "processing": processing,
            "protocol": protocol, "telemetry": telemetry, "logs": logs, "context": context,
        }
        for key, value in fields.items():
            fields[key] = value or ""
        experiment = experiment_from_bundle(**fields, roles=roles, captions=captions, files=files)
        if not experiment.has_content():
            raise HTTPException(400, "Add a description, an unexpected outcome, or at least one file.")
        if existing is not None:
            session = replace_dump(engine, existing, experiment)
            # Keep the caller's existing token: re-issuing would log out the owner's other tabs.
            return dump_session(session)
        session = engine.open_session(experiment=experiment)
        return issue(session, response)

    @app.get("/api/sessions")
    def list_sessions():
        # Listing all sessions was a cross-tenant leak; sessions are capability-scoped.
        raise HTTPException(404, "Not found")

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str, request: Request):
        return dump_session(require_session(request, session_id))

    @app.post("/api/sessions/{session_id}/reject")
    def reject(session_id: str, req: RejectRequest, request: Request):
        require_session(request, session_id)
        try:
            session = engine.reject_hypothesis(
                session_id, req.hypothesis_id[:64], clean_text(req.reason, 500)
            )
        except KeyError as exc:
            raise HTTPException(404, "Unknown session or hypothesis") from exc
        return dump_session(session)

    @app.post("/api/sessions/{session_id}/add-info")
    def add_info(session_id: str, req: InfoRequest, request: Request):
        require_session(request, session_id)
        session = engine.add_information(session_id, clean_text(req.information, sec.MAX_INFO_CHARS))
        return dump_session(session)

    @app.post("/api/sessions/{session_id}/artifacts")
    async def add_session_artifacts(
        session_id: str,
        request: Request,
        roles: str = Form("[]"),
        captions: str = Form("[]"),
        files: list[UploadFile] | None = File(default=None),
    ):
        require_session(request, session_id)
        try:
            role_list = json.loads(roles) if roles else []
            caption_list = json.loads(captions) if captions else []
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "roles and captions must be JSON arrays") from exc
        artifacts = []
        for i, upload in enumerate(files or []):
            if not upload.filename:
                continue
            role = role_list[i] if i < len(role_list) else None
            caption = caption_list[i] if i < len(caption_list) else ""
            artifacts.append(save_and_ingest(upload, role, caption))
        session = engine.add_artifacts(session_id, artifacts)
        return dump_session(session)

    @app.post("/api/sessions/{session_id}/followup")
    def followup(session_id: str, req: FollowUpRequest, request: Request):
        require_session(request, session_id)
        session = engine.record_followup(
            session_id,
            clean_text(req.intervention, sec.MAX_INFO_CHARS),
            clean_text(req.outcome, sec.MAX_INFO_CHARS),
        )
        return dump_session(session)

    def _chat_turn(session_id: str, req: ChatRequest, request: Request):
        message = clean_text(req.message, sec.MAX_MESSAGE_CHARS)
        if not message and not req.force_diagnose:
            raise HTTPException(400, "Provide a message, or set force_diagnose.")
        session = require_session(request, session_id)
        session = engine.chat(session.session_id, message, force_diagnose=req.force_diagnose)
        return dump_session(session)

    @app.post("/api/chat")
    def chat_create_or_continue(req: ChatRequest, request: Request, response: Response):
        if req.session_id:
            return _chat_turn(req.session_id, req, request)
        message = clean_text(req.message, sec.MAX_MESSAGE_CHARS)
        if not message and not req.force_diagnose:
            raise HTTPException(400, "Provide a message, or set force_diagnose.")
        if not message:
            raise HTTPException(400, "A first message is required to start a chat session.")
        session = engine.open_chat_session(
            message,
            title=clean_text(req.title or "", 200),
            domain=_normalize_domain((req.domain or "")[:80]),
            force_diagnose=req.force_diagnose,
        )
        return issue(session, response)

    @app.post("/api/sessions/{session_id}/chat")
    def chat_turn(session_id: str, req: ChatRequest, request: Request):
        return _chat_turn(session_id, req, request)

    @app.get("/api/tools")
    def tools():
        return list_tools()

    @app.post("/api/tools/{name}")
    def run_tool(name: str, req: ToolRequest, request: Request):
        if name not in TOOL_REGISTRY:
            raise HTTPException(404, "Unknown tool")
        try:
            return invoke_tool(name, **req.arguments)
        except Exception as exc:
            log.warning("tool %s failed: %s", name, type(exc).__name__)
            raise HTTPException(400, "Invalid tool arguments") from exc

    return app


app = create_app()


def main():
    import uvicorn

    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()

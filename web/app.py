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
MAX_FILE_BYTES = sec.MAX_FILE_BYTES

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
    allowed_origins = [
        o.strip() for o in os.environ.get("EPIDEBUG_ALLOWED_ORIGINS", "").split(",") if o.strip()
    ]
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["content-type", sec.TOKEN_HEADER, sec.CLIENT_HEADER, "x-session-id", "cf-turnstile-response"],
            allow_credentials=True,
        )

    def _origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        if not origin:
            return True
        if origin in allowed_origins:
            return True
        host = request.headers.get("host", "")
        return origin.split("://", 1)[-1].rstrip("/") == host

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        # State-changing API calls must come from our own frontend: a custom
        # header (forces a CORS preflight cross-site) and, when a browser
        # sends Origin, a same-host or allow-listed origin.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
            if request.headers.get(sec.CLIENT_HEADER) != sec.CLIENT_HEADER_VALUE or not _origin_ok(request):
                return JSONResponse({"detail": "Cross-site request rejected"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in sec.SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    tokens = sec.TokenStore(upload_dir / "tokens")
    rate_backend = sec.make_backend()
    llm_limiter = sec.RateLimiter(
        per_minute=sec._env_int("EPIDEBUG_RATE_PER_MIN", 12),
        per_hour=sec._env_int("EPIDEBUG_RATE_PER_HOUR", 120),
        backend=rate_backend, name="llm",
    )
    # Tools are deterministic (no LLM call): own, looser bucket, not counted
    # against the daily LLM budget.
    tool_limiter = sec.RateLimiter(
        per_minute=sec._env_int("EPIDEBUG_TOOL_RATE_PER_MIN", 60),
        per_hour=sec._env_int("EPIDEBUG_TOOL_RATE_PER_HOUR", 600),
        backend=rate_backend, name="tool",
    )
    session_limiter = sec.RateLimiter(
        per_minute=sec._env_int("EPIDEBUG_SESSION_CREATE_PER_MIN", 3),
        per_hour=sec._env_int("EPIDEBUG_SESSION_CREATE_PER_HOUR", 20),
        backend=rate_backend, name="new",
    )
    daily_budget = sec.DailyBudget(sec._env_int("EPIDEBUG_DAILY_LLM_CAP", 2000), backend=rate_backend)
    app.state.rate_backend = rate_backend
    on_vercel = bool(os.environ.get("VERCEL"))
    secure_cookie = on_vercel

    def client_ip(request: Request) -> str:
        # X-Forwarded-For is client-controlled (Vercel appends, the first hop
        # is whatever the client sent). Only trust headers Vercel's edge
        # overwrites; elsewhere use the socket peer.
        if on_vercel:
            # Trusting these headers is only safe because Vercel's edge OVERWRITES
            # x-vercel-forwarded-for / x-real-ip with the real client IP; a client
            # cannot inject them. Behind any other proxy (or if requests can reach the
            # function without passing Vercel's edge) they are spoofable, so we only
            # read them when running on Vercel and otherwise use the socket peer.
            for name in ("x-vercel-forwarded-for", "x-real-ip"):
                value = request.headers.get(name, "").split(",")[0].strip()
                if value:
                    return value[:64]
        return request.client.host if request.client else "unknown"

    def rate_limit(request: Request) -> None:
        if not llm_limiter.allow(client_ip(request)):
            raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
        if not daily_budget.allow():
            raise HTTPException(503, "Daily usage limit reached. Please try again tomorrow.")

    def limit_session_creation(request: Request) -> None:
        ip = client_ip(request)
        if not session_limiter.allow(ip):
            raise HTTPException(429, "Too many new sessions. Please wait and try again.")
        if os.environ.get("EPIDEBUG_TURNSTILE_SECRET") and not sec.verify_turnstile(
            request.headers.get("cf-turnstile-response", ""), ip
        ):
            raise HTTPException(403, "Verification failed.")

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
        text = (value or "").strip()
        if len(text) > limit:
            raise HTTPException(413, f"Message too long (max {limit} characters).")
        return text

    def check_turns(session) -> None:
        if len(session.messages) >= sec.MAX_SESSION_MESSAGES:
            raise HTTPException(429, "This session reached its turn limit. Start a new session.")
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
        filename = sec.safe_display_name(upload.filename)
        data = upload.file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{filename} exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB")
        problem = sec.validate_upload(filename, data)
        if problem:
            raise HTTPException(415, f"{filename}: {problem}")
        caption = (caption or "")[: sec.MAX_CAPTION_CHARS]
        try:
            artifact = sec.run_isolated(
                ingest_bytes,
                filename,
                data,
                role=role,
                caption=caption,
                content_type=getattr(upload, "content_type", None),
                timeout=sec.PARSE_TIMEOUT_S,
            )
        except sec.ParseTimeout as exc:
            raise HTTPException(422, f"{filename}: parsing timed out") from exc
        except Exception as exc:
            log.exception("ingest failed")
            raise HTTPException(422, f"{filename}: could not be parsed") from exc
        if not sec.ARTIFACT_ID_RE.match(artifact.id or ""):
            raise HTTPException(500, "Internal error")
        dest = upload_dir / artifact.id
        dest.mkdir(parents=True, exist_ok=True)
        # Server-generated storage name; the original name is metadata only.
        path = dest / "blob"
        path.write_bytes(data)
        file_store[artifact.id] = path
        if artifact.kind.value == "image":
            artifact.preview_url = f"/api/files/{artifact.id}"
        return artifact

    def dump_session(session):
        return session_payload(session)

    def check_upload_batch(files) -> None:
        real = [f for f in (files or []) if getattr(f, "filename", None)]
        if len(real) > sec.MAX_FILES_PER_REQUEST:
            raise HTTPException(413, f"Too many files (max {sec.MAX_FILES_PER_REQUEST}).")
        total = 0
        for f in real:
            size = getattr(f, "size", None)
            if size is None:
                try:
                    f.file.seek(0, 2)
                    size = f.file.tell()
                    f.file.seek(0)
                except Exception:
                    size = 0
            total += size or 0
        if total > sec.MAX_TOTAL_BYTES:
            raise HTTPException(413, "Upload is too large in total.")

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

        check_upload_batch(files)
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
            "max_files": sec.MAX_FILES_PER_REQUEST,
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
        if not sec.ARTIFACT_ID_RE.match(artifact_id or "") or not owns_artifact(request, artifact_id):
            raise HTTPException(404, "Unknown file")
        path = file_store.get(artifact_id)
        if path is None or not path.exists():
            raise HTTPException(404, "Unknown file")
        return FileResponse(
            path,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{artifact_id}.bin"',
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @app.post("/api/diagnose")
    def diagnose(req: DiagnoseRequest, request: Request, response: Response):
        limit_session_creation(request)
        rate_limit(request)
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
        if existing is None:
            limit_session_creation(request)
        rate_limit(request)
        fields = {
            "title": title, "domain": domain, "objective": objective,
            "unexpected_outcome": unexpected_outcome, "setup_description": setup_description,
            "setup": setup, "materials": materials, "processing": processing,
            "protocol": protocol, "telemetry": telemetry, "logs": logs, "context": context,
        }
        for key, value in fields.items():
            if len(value or "") > sec.MAX_BUNDLE_FIELD_CHARS:
                raise HTTPException(413, f"{key} is too long (max {sec.MAX_BUNDLE_FIELD_CHARS} characters).")
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
        rate_limit(request)
        try:
            session = engine.reject_hypothesis(
                session_id, req.hypothesis_id[:64], clean_text(req.reason, sec.MAX_REASON_CHARS)
            )
        except KeyError as exc:
            raise HTTPException(404, "Unknown session or hypothesis") from exc
        return dump_session(session)

    @app.post("/api/sessions/{session_id}/add-info")
    def add_info(session_id: str, req: InfoRequest, request: Request):
        require_session(request, session_id)
        rate_limit(request)
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
        rate_limit(request)
        try:
            role_list = json.loads(roles) if roles else []
            caption_list = json.loads(captions) if captions else []
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "roles and captions must be JSON arrays") from exc
        check_upload_batch(files)
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
        rate_limit(request)
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
        check_turns(session)
        rate_limit(request)
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
        limit_session_creation(request)
        rate_limit(request)
        session = engine.open_chat_session(
            message,
            title=clean_text(req.title or "", sec.MAX_TITLE_CHARS),
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
        if not tool_limiter.allow(client_ip(request)):
            raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
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

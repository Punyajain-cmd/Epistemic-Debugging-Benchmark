#!/usr/bin/env python3
"""Researcher-facing EpiDebug prototype.

Accepts the full experimental record — notes, photos, CAD, logs, sensor
tables — then runs epistemic debugging.

Session JSON stays backward compatible (diagnosis, experiment, session_id)
and adds an additive ``view`` object for a redesigned frontend.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from epidebug.engine import EpistemicDebuggingEngine
from epidebug.engine.ingest import ingest_bytes
from epidebug.engine.present import EVIDENCE_SLOTS, VIEW_CONTRACT, session_payload
from epidebug.engine.session import SessionStore
from epidebug.schema import ExperimentArtifact, ExperimentInput, TestCase
from epidebug.tools import TOOL_REGISTRY, invoke_tool, list_tools

STATIC = Path(__file__).resolve().parent / "static"
CASES_DIR = ROOT / "test_cases"
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


def _restore_file_store(upload_dir: Path) -> dict[str, Path]:
    store: dict[str, Path] = {}
    if not upload_dir.exists():
        return store
    for dest in upload_dir.iterdir():
        if not dest.is_dir() or dest.name == "sessions":
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


def create_app(
    *,
    upload_dir: Path | None = None,
    prefer_llm: bool = True,
    cases: list[TestCase] | None = None,
) -> FastAPI:
    """Build the prototype API. ``upload_dir`` is overrideable for tests."""
    upload_dir = Path(upload_dir) if upload_dir else ROOT / "uploads"
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
        version="0.5.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
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

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "cases": len(loaded_cases),
            "llm_available": engine.llm.available,
            "engine_mode": "llm" if engine.llm.available else "heuristic",
            "accepts": ACCEPTS,
            "contract_version": VIEW_CONTRACT,
            "evidence_slots": EVIDENCE_SLOTS,
            "max_file_bytes": MAX_FILE_BYTES,
            "session_payload": [
                "session_id",
                "diagnosis",
                "experiment",
                "history",
                "view",
            ],
            "view_fields": [
                "artifacts",
                "gallery",
                "ingest",
                "hypotheses",
                "lead",
            ],
            "hitl": [
                "POST /api/sessions/{id}/reject",
                "POST /api/sessions/{id}/add-info",
                "POST /api/sessions/{id}/artifacts",
                "POST /api/sessions/{id}/followup",
            ],
        }

    @app.get("/api/cases")
    def list_cases():
        return [_case_summary(case) for case in loaded_cases]

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str, include_ground_truth: bool = False):
        case = case_index.get(case_id)
        if case is None:
            raise HTTPException(404, f"Unknown case {case_id}")
        payload = case.model_dump(mode="json")
        if not include_ground_truth:
            payload.pop("ground_truth", None)
            payload.pop("scoring", None)
        payload["prompt"] = case.to_prompt()
        payload["summary"] = _case_summary(case)
        return payload

    @app.get("/api/files/{artifact_id}")
    def get_file(artifact_id: str):
        path = file_store.get(artifact_id)
        if path is None or not path.exists():
            raise HTTPException(404, "Unknown file")
        return FileResponse(path, filename=path.name)

    @app.post("/api/diagnose")
    def diagnose(req: DiagnoseRequest):
        if req.case_id:
            case = case_index.get(req.case_id)
            if case is None:
                raise HTTPException(404, f"Unknown case {req.case_id}")
            session = engine.open_session(case=case)
        elif req.experiment:
            if not req.experiment.has_content():
                raise HTTPException(400, "Provide notes, files, or an unexpected outcome.")
            session = engine.open_session(experiment=req.experiment)
        else:
            raise HTTPException(400, "Provide case_id or experiment")
        return dump_session(session)

    @app.post("/api/diagnose-bundle")
    async def diagnose_bundle(
        title: str = Form("Untitled experiment"),
        domain: str = Form(""),
        objective: str = Form(""),
        unexpected_outcome: str = Form(""),
        setup_description: str = Form(""),
        materials: str = Form(""),
        processing: str = Form(""),
        protocol: str = Form(""),
        telemetry: str = Form(""),
        logs: str = Form(""),
        context: str = Form(""),
        roles: str = Form("[]"),
        captions: str = Form("[]"),
        files: list[UploadFile] | None = File(default=None),
    ):
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

        experiment = ExperimentInput(
            title=title or "Untitled experiment",
            domain=_normalize_domain(domain),
            objective=objective,
            unexpected_outcome=unexpected_outcome,
            setup_description=setup_description,
            materials=_split_lines(materials),
            processing=_split_lines(processing),
            protocol=_split_lines(protocol) or _split_lines(processing),
            telemetry_notes=_split_lines(telemetry) + _split_lines(logs),
            contextual_clues=_split_lines(context),
            artifacts=artifacts,
        )
        if not experiment.has_content():
            raise HTTPException(400, "Add a description, an unexpected outcome, or at least one file.")
        session = engine.open_session(experiment=experiment)
        return dump_session(session)

    @app.get("/api/sessions")
    def list_sessions():
        return _session_summaries(engine)

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        try:
            return dump_session(engine.sessions.get(session_id))
        except KeyError as exc:
            raise HTTPException(404, "Unknown session") from exc

    @app.post("/api/sessions/{session_id}/reject")
    def reject(session_id: str, req: RejectRequest):
        try:
            session = engine.reject_hypothesis(session_id, req.hypothesis_id, req.reason)
        except KeyError as exc:
            raise HTTPException(404, "Unknown session or hypothesis") from exc
        return dump_session(session)

    @app.post("/api/sessions/{session_id}/add-info")
    def add_info(session_id: str, req: InfoRequest):
        try:
            session = engine.add_information(session_id, req.information)
        except KeyError as exc:
            raise HTTPException(404, "Unknown session") from exc
        return dump_session(session)

    @app.post("/api/sessions/{session_id}/artifacts")
    async def add_session_artifacts(
        session_id: str,
        roles: str = Form("[]"),
        captions: str = Form("[]"),
        files: list[UploadFile] | None = File(default=None),
    ):
        try:
            engine.sessions.get(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Unknown session") from exc
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
    def followup(session_id: str, req: FollowUpRequest):
        try:
            session = engine.record_followup(session_id, req.intervention, req.outcome)
        except KeyError as exc:
            raise HTTPException(404, "Unknown session") from exc
        return dump_session(session)

    @app.get("/api/tools")
    def tools():
        return list_tools()

    @app.post("/api/tools/{name}")
    def run_tool(name: str, req: ToolRequest):
        if name not in TOOL_REGISTRY:
            raise HTTPException(404, f"Unknown tool {name}")
        try:
            return invoke_tool(name, **req.arguments)
        except TypeError as exc:
            raise HTTPException(400, str(exc)) from exc

    return app


app = create_app()


def main():
    import uvicorn

    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()

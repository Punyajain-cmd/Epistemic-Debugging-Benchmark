"""Human-in-the-loop diagnosis sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from epidebug.schema import Diagnosis, ExperimentInput, TestCase


class FollowUpRecord(BaseModel):
    intervention: str
    outcome: str
    timestamp: str = ""


class DiagnosisSession(BaseModel):
    session_id: str
    created_at: str
    case_id: Optional[str] = None
    title: str = "Untitled session"
    experiment: Optional[ExperimentInput] = None
    extra_information: list[str] = Field(default_factory=list)
    rejected: dict[str, str] = Field(default_factory=dict)
    followups: list[FollowUpRecord] = Field(default_factory=list)
    diagnosis: Optional[Diagnosis] = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class SessionStore:
    """Session store. Optionally persists JSON under ``persist_dir``."""

    def __init__(self, persist_dir: Path | str | None = None) -> None:
        self._sessions: dict[str, DiagnosisSession] = {}
        self.persist_dir = Path(persist_dir) if persist_dir else None
        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._load()

    def create(
        self,
        case: TestCase | None = None,
        experiment: ExperimentInput | None = None,
    ) -> DiagnosisSession:
        session = DiagnosisSession(
            session_id=uuid.uuid4().hex[:12],
            created_at=datetime.now(timezone.utc).isoformat(),
            case_id=case.id if case else None,
            title=case.title if case else (experiment.title if experiment else "Untitled session"),
            experiment=experiment,
        )
        return self.save(session)

    def get(self, session_id: str) -> DiagnosisSession:
        if session_id not in self._sessions:
            raise KeyError(session_id)
        return self._sessions[session_id]

    def list(self) -> list[DiagnosisSession]:
        return list(self._sessions.values())

    def save(self, session: DiagnosisSession) -> DiagnosisSession:
        self._sessions[session.session_id] = session
        if self.persist_dir is not None:
            path = self.persist_dir / f"{session.session_id}.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(session.model_dump_json(), encoding="utf-8")
            tmp.replace(path)
        return session

    def _load(self) -> None:
        assert self.persist_dir is not None
        for path in self.persist_dir.glob("*.json"):
            try:
                session = DiagnosisSession.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            self._sessions[session.session_id] = session

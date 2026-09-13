"""Human-in-the-loop diagnosis sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from epidebug.schema import Diagnosis, ExperimentInput, TestCase


class FollowUpRecord(BaseModel):
    intervention: str
    outcome: str
    timestamp: str


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
    """In-memory session store for the prototype."""

    def __init__(self) -> None:
        self._sessions: dict[str, DiagnosisSession] = {}

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
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> DiagnosisSession:
        if session_id not in self._sessions:
            raise KeyError(session_id)
        return self._sessions[session_id]

    def list(self) -> list[DiagnosisSession]:
        return list(self._sessions.values())

    def save(self, session: DiagnosisSession) -> DiagnosisSession:
        self._sessions[session.session_id] = session
        return session

"""Application service implementing the two-transaction Interview harness."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .agent import StoryEditor, event_source_text
from .database import (
    Conflict,
    IngestResult,
    InvalidTransition,
    NotFound,
    Repository,
    RepositoryError,
    SessionBusy,
    StaleRevision,
)
from .domain import (
    DomainError,
    InterviewEvent,
    make_start_event,
    parse_event,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class ServiceFailure(Exception):
    code: str
    message: str
    http_status: int
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


class InterviewService:
    def __init__(self, repository: Repository, editor: StoryEditor):
        self.repository = repository
        self.editor = editor

    def create_interview(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ServiceFailure("invalid_input", "Request body must be an object.", 400)
        unknown = set(raw) - {
            "session_id", "event_id", "title", "seed",
            "storytelling_format", "involvement_mode", "question_target",
        }
        if unknown:
            raise ServiceFailure(
                "invalid_input",
                "Unknown create field(s): " + ", ".join(sorted(unknown)),
                400,
            )
        session_id = raw.get("session_id") or new_id("interview")
        event_id = raw.get("event_id") or new_id("event")
        seed = raw.get("seed")
        title = raw.get("title")
        if (title is None or title == "") and isinstance(seed, str) and seed.strip():
            title = " ".join(seed.strip().split()[:7]).rstrip(".,:;!?") or "Untitled film"
        try:
            event = make_start_event(
                session_id, event_id, seed=seed, title=title or "Untitled film",
                storytelling_format=raw.get("storytelling_format") or "not_sure",
                involvement_mode=raw.get("involvement_mode") or "collaborative",
                question_target=raw.get("question_target", 8),
            )
            ingested = self.repository.create_and_ingest_start(event)
            if ingested.replayed_result is not None:
                return {**ingested.replayed_result, "replayed": True}
            return self._finish(event)
        except DomainError as exc:
            raise ServiceFailure(exc.code, str(exc), 400, exc.as_dict()) from exc
        except RepositoryError as exc:
            raise self._repository_failure(exc) from exc

    def submit_event(self, session_id: str, raw: Mapping[str, Any]) -> dict[str, Any]:
        try:
            event = parse_event(session_id, raw)
            ingested = self.repository.ingest_event(event)
            if ingested.replayed_result is not None:
                return {**ingested.replayed_result, "replayed": True}
            return self._finish(event)
        except DomainError as exc:
            status = 413 if exc.code == "payload_too_large" else 400
            raise ServiceFailure(exc.code, str(exc), status, exc.as_dict()) from exc
        except RepositoryError as exc:
            raise self._repository_failure(exc) from exc

    def _finish(self, event: InterviewEvent) -> dict[str, Any]:
        attempts = 0
        try:
            references = self.repository.validate_event_references(event)
            snapshot = self.repository.load_snapshot(event.session_id, event_id=event.id)
            outcome = self.editor.decide(snapshot, event, references)
            attempts = int(outcome.meta.get("attempts", 0))
            result = self.repository.publish(
                event, outcome.decision, model_meta=outcome.meta,
                reference_context=references,
            )
            return {
                **result,
                "replayed": False,
                "agent": {
                    "backend": outcome.meta.get("backend", ""),
                    "used_fallback": outcome.used_fallback,
                    "dropped_components": outcome.meta.get("dropped_components", []),
                    "validation_errors": list(outcome.validation_errors),
                },
            }
        except InvalidTransition as exc:
            self.repository.mark_failed(event.id, str(exc), attempts=attempts)
            raise ServiceFailure(exc.code, str(exc), 422) from exc
        except StaleRevision as exc:
            self.repository.mark_failed(event.id, str(exc), attempts=attempts)
            raise ServiceFailure(
                exc.code, str(exc), 409,
                {"current_revision": exc.current_revision},
            ) from exc
        except RepositoryError as exc:
            self.repository.mark_failed(event.id, str(exc), attempts=attempts)
            raise self._repository_failure(exc) from exc
        except Exception as exc:
            self.repository.mark_failed(
                event.id,
                "The Story Editor could not complete this turn. Your input is saved and can be retried.",
                attempts=attempts,
            )
            raise ServiceFailure(
                "turn_failed",
                "The Story Editor could not complete this turn. Your input is saved and can be retried.",
                503,
            ) from exc

    def list_interviews(self) -> list[dict[str, Any]]:
        return self.repository.list_sessions()

    def get_interview(self, session_id: str) -> dict[str, Any]:
        try:
            return self.repository.get_session(session_id)
        except RepositoryError as exc:
            raise self._repository_failure(exc) from exc

    @staticmethod
    def _repository_failure(exc: RepositoryError) -> ServiceFailure:
        if isinstance(exc, NotFound):
            return ServiceFailure(exc.code, str(exc), 404)
        if isinstance(exc, StaleRevision):
            return ServiceFailure(
                exc.code, str(exc), 409,
                {"current_revision": exc.current_revision},
            )
        if isinstance(exc, (Conflict, SessionBusy)):
            return ServiceFailure(exc.code, str(exc), 409)
        if isinstance(exc, InvalidTransition):
            return ServiceFailure(exc.code, str(exc), 422)
        return ServiceFailure(exc.code, "The Interview database operation failed.", 500)

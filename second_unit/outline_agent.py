"""Bounded Outline-agent loop for proposing one atomic set of story beats."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping

from .database import SessionBusy
from .model import ModelResult, ModelUnavailable
from .outline import OutlineToolError, OutlineToolkit


PROMPT_VERSION = "outline-beat-builder-1"
MIN_BEATS = 3
MAX_BEATS = 10
LOGGER = logging.getLogger("second_unit.outline_agent")

SYSTEM_PROMPT = """You are Second Unit's Outline Editor. Turn an approved
Interview handoff into a concise, editable short-film beat outline using the
structure the filmmaker selected.

You are now allowed to author connective action, escalation, and possible scene
events. Every beat you write is a proposal, never approved canon. Preserve the
filmmaker's premise, ending, characters, tone, and explicit decisions. Do not
invent a new central character, premise, subplot, location, or ending. When
material is undecided, make the least disruptive concrete proposal and keep it
easy to edit.

Write the smallest useful number of beats, between 3 and 10. Each beat must:
- describe what visibly happens in chronological story order;
- cause, reveal, or change something rather than repeat information;
- have a short title, a one-to-three sentence present-tense summary, and a brief
  dramatic purpose;
- cite only supplied source IDs, or use an empty source_refs list for connective
  invention;
- fit a producible short film and the selected structure without forcing a
  conventional three-act pattern.

Do not write screenplay dialogue, camera directions, shot lists, analysis, or
alternative versions. Return only the requested JSON contract."""

BEAT_SET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["beats"],
    "properties": {
        "beats": {
            "type": "array",
            "minItems": MIN_BEATS,
            "maxItems": MAX_BEATS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "summary", "purpose", "source_refs"],
                "properties": {
                    "title": {"type": "string", "maxLength": 160},
                    "summary": {"type": "string", "maxLength": 2000},
                    "purpose": {"type": "string", "maxLength": 800},
                    "source_refs": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


@dataclass(frozen=True)
class OutlineAgentOutcome:
    outline: dict[str, Any]
    meta: dict[str, Any]


class OutlineAgent:
    def __init__(self, model: Any, tools: OutlineToolkit):
        self.model = model
        self.tools = tools

    def generate(self, session_id: str, expected_revision: int) -> OutlineAgentOutcome:
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise OutlineToolError(
                "invalid_outline_tool_input",
                "expected_revision must be a non-negative integer",
            )
        handoff = self.tools.read_interview_handoff(session_id)
        outline = handoff.get("outline")
        if not outline:
            raise OutlineToolError(
                "outline_not_found", "choose a structure before generating story beats"
            )
        if outline["revision"] != expected_revision:
            from .outline import OutlineRevisionConflict
            raise OutlineRevisionConflict(outline["revision"])
        active_id = outline.get("active_structure_id")
        if not active_id:
            raise OutlineToolError(
                "outline_structure_required", "select a structure before generating story beats"
            )
        if outline.get("beats"):
            raise OutlineToolError(
                "outline_beats_exist", "story beats already exist for this Outline"
            )
        structure = next(
            item for item in outline["structures"] if item["id"] == active_id
        )
        allowed_refs = self._source_ids(handoff)
        diagnostics: list[dict[str, Any]] = [{
            "phase": "request",
            "session_id": session_id,
            "outline_id": outline["id"],
            "base_revision": expected_revision,
            "structure_kind": structure["kind"],
            "answer_count": len(handoff.get("answers", [])),
            "fact_count": len(handoff.get("accepted_facts", [])),
            "gap_count": len(handoff.get("open_gaps", [])),
            "allowed_source_ref_count": len(allowed_refs),
        }]
        try:
            run_id = self.tools.repository.start_outline_agent_run(
                outline["id"], expected_revision, PROMPT_VERSION, diagnostics
            )
        except SessionBusy as exc:
            raise OutlineToolError(
                "outline_agent_busy",
                "Story beats are already being generated. Please wait for that run to finish.",
            ) from exc
        LOGGER.info(
            "outline_generation_started run=%s session=%s outline=%s revision=%s structure=%s",
            run_id, session_id, outline["id"], expected_revision, structure["kind"],
        )
        errors: list[str] = []
        last_result: ModelResult | None = None
        for attempt in range(2):
            attempt_number = attempt + 1
            diagnostics.append({"phase": "model_attempt_started", "attempt": attempt_number})
            self._update_run(
                run_id, diagnostics, model_attempts=attempt_number,
                last_result=last_result,
            )
            try:
                last_result = self.model.decide(
                    system=SYSTEM_PROMPT,
                    prompt=self._prompt(handoff, structure, errors if attempt else []),
                    schema=BEAT_SET_SCHEMA,
                    timeout_seconds=30,
                    max_output_tokens=5_000,
                )
                diagnostics.append({
                    "phase": "model_attempt_completed",
                    "attempt": attempt_number,
                    "backend": last_result.backend,
                    "model": last_result.model,
                    "prompt_tokens": last_result.prompt_tokens,
                    "output_tokens": last_result.output_tokens,
                })
                beats, errors = self._validate(last_result.payload, allowed_refs)
                if errors:
                    diagnostics.append({
                        "phase": "validation_failed",
                        "attempt": attempt_number,
                        "errors": errors[:20],
                    })
                    LOGGER.warning(
                        "outline_generation_validation_failed run=%s attempt=%s errors=%s",
                        run_id, attempt_number, errors,
                    )
                    self._update_run(
                        run_id, diagnostics, model_attempts=attempt_number,
                        last_result=last_result,
                    )
                    continue
                saved = self.tools.execute(
                    "create_beat_batch",
                    {
                        "session_id": session_id,
                        "expected_revision": expected_revision,
                        "beats": beats,
                    },
                    caller="agent",
                )
                diagnostics.append({
                    "phase": "save_completed",
                    "attempt": attempt_number,
                    "beat_count": len(beats),
                    "result_revision": saved["revision"],
                })
                self._update_run(
                    run_id, diagnostics, status="completed",
                    model_attempts=attempt_number, last_result=last_result,
                )
                LOGGER.info(
                    "outline_generation_completed run=%s attempt=%s beats=%s revision=%s",
                    run_id, attempt_number, len(beats), saved["revision"],
                )
                return OutlineAgentOutcome(saved, {
                    "run_id": run_id,
                    "attempts": attempt_number,
                    "backend": last_result.backend,
                    "model": last_result.model,
                    "prompt_tokens": last_result.prompt_tokens,
                    "output_tokens": last_result.output_tokens,
                    "prompt_version": PROMPT_VERSION,
                })
            except ModelUnavailable as exc:
                cause = exc.__cause__ or exc
                provider_type = type(cause).__name__
                provider_message = str(cause)[:4000]
                deadline = "deadline" in provider_message.casefold() or "504" in provider_message
                diagnostics.append({
                    "phase": "provider_retryable" if deadline and attempt == 0 else "provider_failed",
                    "attempt": attempt_number,
                    "provider_error_type": provider_type,
                    "provider_error": provider_message,
                })
                if deadline and attempt == 0:
                    self._update_run(
                        run_id, diagnostics, model_attempts=attempt_number,
                        last_result=last_result,
                    )
                    LOGGER.warning(
                        "outline_generation_deadline_retry run=%s attempt=%s type=%s error=%s",
                        run_id, attempt_number, provider_type, provider_message,
                    )
                    errors = ["The first provider attempt exceeded its deadline."]
                    continue
                self._update_run(
                    run_id, diagnostics, status="failed",
                    model_attempts=attempt_number, last_result=last_result,
                    error_code="outline_agent_unavailable",
                    provider_error_type=provider_type,
                    safe_error=provider_message,
                )
                LOGGER.error(
                    "outline_generation_provider_failed run=%s attempt=%s type=%s error=%s",
                    run_id, attempt_number, provider_type, provider_message,
                    exc_info=(type(cause), cause, cause.__traceback__),
                )
                raise OutlineToolError(
                    "outline_agent_unavailable",
                    "The Outline agent is temporarily unavailable. Nothing was changed.",
                ) from exc
            except OutlineToolError as exc:
                diagnostics.append({
                    "phase": "tool_save_failed", "attempt": attempt_number,
                    "error_code": exc.code, "error": exc.message,
                })
                self._update_run(
                    run_id, diagnostics, status="failed",
                    model_attempts=attempt_number, last_result=last_result,
                    error_code=exc.code, provider_error_type=type(exc).__name__,
                    safe_error=exc.message,
                )
                LOGGER.exception(
                    "outline_generation_save_failed run=%s code=%s", run_id, exc.code
                )
                raise
            except (TypeError, ValueError) as exc:
                errors = [str(exc)]
                response_diagnostics = getattr(exc, "diagnostics", {})
                diagnostics.append({
                    "phase": "response_parse_failed", "attempt": attempt_number,
                    "error_type": type(exc).__name__, "error": str(exc)[:4000],
                    "response": response_diagnostics,
                })
                LOGGER.warning(
                    "outline_generation_parse_failed run=%s attempt=%s type=%s error=%s response=%s",
                    run_id, attempt_number, type(exc).__name__, str(exc),
                    response_diagnostics,
                )
                self._update_run(
                    run_id, diagnostics, model_attempts=attempt_number,
                    last_result=last_result,
                )
        self._update_run(
            run_id, diagnostics, status="failed", model_attempts=2,
            last_result=last_result, error_code="invalid_outline_agent_output",
            provider_error_type="ValidationError", safe_error="; ".join(errors),
        )
        raise OutlineToolError(
            "invalid_outline_agent_output",
            "The Outline agent could not produce a valid beat set. Nothing was changed.",
            {"validation_errors": errors[:8]},
        )

    def _update_run(
        self, run_id: str, diagnostics: list[dict[str, Any]], *,
        status: str = "running", model_attempts: int,
        last_result: ModelResult | None, error_code: str = "",
        provider_error_type: str = "", safe_error: str = "",
    ) -> None:
        self.tools.repository.update_outline_agent_run(
            run_id,
            status=status,
            model_attempts=model_attempts,
            backend=getattr(last_result, "backend", getattr(self.model, "backend", "")),
            model=getattr(last_result, "model", getattr(self.model, "model", "")),
            prompt_tokens=getattr(last_result, "prompt_tokens", 0),
            output_tokens=getattr(last_result, "output_tokens", 0),
            error_code=error_code,
            provider_error_type=provider_error_type,
            safe_error=safe_error,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _source_ids(handoff: Mapping[str, Any]) -> set[str]:
        values: set[str] = set()
        for fact in handoff.get("accepted_facts", []):
            for key in ("id", "source_event_id"):
                if fact.get(key):
                    values.add(fact[key])
        for answer in handoff.get("answers", []):
            if answer.get("source_event_id"):
                values.add(answer["source_event_id"])
        for gap in handoff.get("open_gaps", []):
            if gap.get("id"):
                values.add(gap["id"])
        return values

    @staticmethod
    def _prompt(
        handoff: Mapping[str, Any], structure: Mapping[str, Any],
        repair_errors: list[str],
    ) -> str:
        material = {
            "film": handoff["interview"],
            "selected_structure": {
                "kind": structure["kind"],
                "label": structure["label"],
                "description": structure["rationale"],
            },
            "interview_answers": handoff.get("answers", []),
            "accepted_facts": handoff.get("accepted_facts", []),
            "open_questions": handoff.get("open_questions", []),
            "open_gaps": handoff.get("open_gaps", []),
        }
        prompt = (
            "Create one chronological beat outline from this handoff. Open questions and "
            "gaps are uncertainty, not permission to replace established material.\n\n"
            + json.dumps(material, ensure_ascii=False, indent=2)
        )
        if repair_errors:
            prompt += (
                "\n\nYour previous response was rejected. Correct every issue and return the "
                "complete beat set again:\n- " + "\n- ".join(repair_errors)
            )
        return prompt

    @staticmethod
    def _validate(
        payload: Mapping[str, Any], allowed_refs: set[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if not isinstance(payload, Mapping):
            return [], ["response must be an object"]
        if set(payload) != {"beats"}:
            return [], ["response must contain only beats"]
        raw_beats = payload.get("beats")
        if not isinstance(raw_beats, list) or not MIN_BEATS <= len(raw_beats) <= MAX_BEATS:
            return [], [f"beats must contain between {MIN_BEATS} and {MAX_BEATS} items"]
        errors: list[str] = []
        cleaned: list[dict[str, Any]] = []
        titles: set[str] = set()
        for index, value in enumerate(raw_beats, 1):
            if not isinstance(value, Mapping):
                errors.append(f"beat {index} must be an object")
                continue
            if set(value) != {"title", "summary", "purpose", "source_refs"}:
                errors.append(f"beat {index} has missing or unsupported fields")
                continue
            text_values: dict[str, str] = {}
            for field, maximum in (("title", 160), ("summary", 2000), ("purpose", 800)):
                raw = value.get(field)
                if not isinstance(raw, str) or not raw.strip():
                    errors.append(f"beat {index} {field} is required")
                else:
                    text_values[field] = " ".join(raw.strip().split())
                    if len(text_values[field]) > maximum:
                        errors.append(f"beat {index} {field} is too long")
            refs = value.get("source_refs")
            if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
                errors.append(f"beat {index} source_refs must be a list of IDs")
                refs = []
            elif len(refs) > 20 or len(set(refs)) != len(refs):
                errors.append(f"beat {index} source_refs must be unique and limited to 20")
            else:
                unknown = sorted(set(refs) - allowed_refs)
                if unknown:
                    errors.append(f"beat {index} cites unknown source IDs: {', '.join(unknown)}")
            normalized = text_values.get("title", "").casefold()
            if normalized and normalized in titles:
                errors.append(f"beat {index} repeats another title")
            titles.add(normalized)
            if all(field in text_values for field in ("title", "summary", "purpose")):
                cleaned.append({**text_values, "source_refs": refs})
        return cleaned, errors

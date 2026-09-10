"""Screenplay drafting loop built only from an approved Outline."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from .database import SessionBusy
from .model import ModelResult, ModelUnavailable
from .screenplay import ScreenplayError, ScreenplayToolkit


LOGGER = logging.getLogger("second_unit.screenplay_agent")
PROMPT_VERSION = "screenplay-drafter-2"
SYSTEM_PROMPT = """You are Second Unit's Screenplay Writer. Draft a concise
short-film screenplay from the approved outline and the filmmaker's chosen mode.
You may author scene action, narration, and dialogue, but every scene remains an
editable proposal. Preserve every accepted beat's meaning and order. Do not add
a subplot, central character, premise, ending, or production-heavy spectacle.

Use standard scene headings such as INT. ROOM - NIGHT. Write filmable present-
tense action. Put spoken dialogue into character/line objects; do not embed it in
action. Use narration only when the chosen mode calls for it. Cover every beat
ID at least once. Return JSON only."""

SCENE_SCHEMA = {
    "type": "object", "required": ["scenes"],
    # Keep array cardinality out of Vertex's constrained-decoding schema. Nested
    # maxItems constraints can exceed Gemini's serving state limit; _validate
    # and ScreenplayToolkit enforce the 1–20 scene / 40 dialogue-line bounds.
    "properties": {"scenes": {"type": "array",
        "items": {"type": "object", "required": ["beat_id", "heading", "action", "narration", "dialogue"],
            "properties": {
                "beat_id": {"type": "string"}, "heading": {"type": "string"},
                "action": {"type": "string"}, "narration": {"type": "string"},
                "dialogue": {"type": "array", "items": {
                    "type": "object", "required": ["character", "line"],
                    "properties": {"character": {"type": "string"}, "line": {"type": "string"}},
                }},
            },
        },
    }},
}


class ScreenplayAgent:
    def __init__(self, model: Any, tools: ScreenplayToolkit):
        self.model = model
        self.tools = tools

    def generate(self, session_id: str, expected_revision: int) -> dict[str, Any]:
        handoff = self.tools.read_handoff(session_id)
        screenplay = handoff.get("screenplay")
        if not handoff["available"]:
            raise ScreenplayError("approved_outline_required", "approve the Outline before generating a screenplay")
        if not screenplay or not screenplay["mode"]:
            raise ScreenplayError("screenplay_mode_required", "choose how the screenplay should speak first")
        if screenplay["revision"] != expected_revision:
            from .screenplay import ScreenplayRevisionConflict
            raise ScreenplayRevisionConflict(screenplay["revision"])
        if screenplay["scenes"]:
            raise ScreenplayError("screenplay_scenes_exist", "screenplay scenes already exist")
        beats = handoff["outline"]["beats"]
        accepted_ids = {beat["id"] for beat in beats if beat["approval_status"] == "accepted"}
        diagnostics: list[dict[str, Any]] = [{
            "phase": "request", "session_id": session_id,
            "screenplay_id": screenplay["id"], "base_revision": expected_revision,
            "mode": screenplay["mode"], "accepted_beat_count": len(accepted_ids),
        }]
        try:
            run_id = self.tools.repository.start_screenplay_agent_run(
                screenplay["id"], expected_revision, PROMPT_VERSION, diagnostics
            )
        except SessionBusy as exc:
            raise ScreenplayError(
                "screenplay_agent_busy",
                "The screenplay is already being generated. Please wait for it to finish.",
            ) from exc
        LOGGER.info(
            "screenplay_generation_started run=%s session=%s screenplay=%s revision=%s mode=%s",
            run_id, session_id, screenplay["id"], expected_revision, screenplay["mode"],
        )
        errors: list[str] = []
        last_result: ModelResult | None = None
        for attempt in range(2):
            attempt_number = attempt + 1
            diagnostics.append({"phase": "model_attempt_started", "attempt": attempt_number})
            self._update_run(run_id, diagnostics, model_attempts=attempt_number, last_result=last_result)
            try:
                last_result = self.model.decide(
                    system=SYSTEM_PROMPT,
                    prompt=self._prompt(handoff, errors if attempt else []),
                    schema=SCENE_SCHEMA,
                    timeout_seconds=30 if attempt == 0 else 45,
                    max_output_tokens=8_000,
                )
                diagnostics.append({
                    "phase": "model_attempt_completed", "attempt": attempt_number,
                    "backend": last_result.backend, "model": last_result.model,
                    "prompt_tokens": last_result.prompt_tokens,
                    "output_tokens": last_result.output_tokens,
                })
                scenes, errors = self._validate(last_result.payload, accepted_ids)
                if errors:
                    diagnostics.append({
                        "phase": "validation_failed", "attempt": attempt_number,
                        "errors": errors[:20],
                    })
                    LOGGER.warning(
                        "screenplay_generation_validation_failed run=%s attempt=%s errors=%s",
                        run_id, attempt_number, errors,
                    )
                    self._update_run(
                        run_id, diagnostics, model_attempts=attempt_number,
                        last_result=last_result,
                    )
                    continue
                saved = self.tools.create_scene_batch(
                    session_id, expected_revision, scenes, actor="agent"
                )
                diagnostics.append({
                    "phase": "save_completed", "attempt": attempt_number,
                    "scene_count": len(scenes), "result_revision": saved["revision"],
                })
                self._update_run(
                    run_id, diagnostics, status="completed",
                    model_attempts=attempt_number, last_result=last_result,
                )
                LOGGER.info(
                    "screenplay_generation_completed run=%s attempt=%s scenes=%s revision=%s",
                    run_id, attempt_number, len(scenes), saved["revision"],
                )
                return {"screenplay": saved, "agent": {
                    "run_id": run_id, "attempts": attempt_number,
                    "backend": last_result.backend, "model": last_result.model,
                    "prompt_version": PROMPT_VERSION,
                    "prompt_tokens": last_result.prompt_tokens,
                    "output_tokens": last_result.output_tokens,
                }}
            except ModelUnavailable as exc:
                cause = exc.__cause__ or exc
                provider_type = type(cause).__name__
                provider_message = str(cause)[:4000]
                folded = provider_message.casefold()
                retryable = any(marker in folded for marker in (
                    "deadline", "timed out", "timeout", "429", "503",
                    "temporarily unavailable", "service unavailable",
                ))
                diagnostics.append({
                    "phase": "provider_retryable" if retryable and attempt == 0 else "provider_failed",
                    "attempt": attempt_number, "provider_error_type": provider_type,
                    "provider_error": provider_message,
                })
                if retryable and attempt == 0:
                    self._update_run(
                        run_id, diagnostics, model_attempts=attempt_number,
                        last_result=last_result,
                    )
                    LOGGER.warning(
                        "screenplay_generation_provider_retry run=%s attempt=%s type=%s error=%s",
                        run_id, attempt_number, provider_type, provider_message,
                    )
                    errors = ["The first provider attempt failed transiently; return the complete screenplay."]
                    continue
                self._update_run(
                    run_id, diagnostics, status="failed", model_attempts=attempt_number,
                    last_result=last_result, safe_error=f"{provider_type}: {provider_message}",
                )
                LOGGER.error(
                    "screenplay_generation_provider_failed run=%s attempt=%s type=%s error=%s",
                    run_id, attempt_number, provider_type, provider_message,
                    exc_info=(type(cause), cause, cause.__traceback__),
                )
                raise ScreenplayError(
                    "screenplay_agent_unavailable",
                    "The Screenplay agent is temporarily unavailable. Nothing was changed.",
                ) from exc
            except ScreenplayError as exc:
                diagnostics.append({
                    "phase": "tool_save_failed", "attempt": attempt_number,
                    "error_code": exc.code, "error": exc.message,
                })
                self._update_run(
                    run_id, diagnostics, status="failed", model_attempts=attempt_number,
                    last_result=last_result, safe_error=exc.message,
                )
                LOGGER.exception(
                    "screenplay_generation_save_failed run=%s code=%s", run_id, exc.code
                )
                raise
            except (TypeError, ValueError) as exc:
                errors = [str(exc)]
                diagnostics.append({
                    "phase": "response_parse_failed", "attempt": attempt_number,
                    "error_type": type(exc).__name__, "error": str(exc)[:4000],
                    "response": getattr(exc, "diagnostics", {}),
                })
                LOGGER.warning(
                    "screenplay_generation_parse_failed run=%s attempt=%s type=%s error=%s",
                    run_id, attempt_number, type(exc).__name__, str(exc),
                )
                self._update_run(
                    run_id, diagnostics, model_attempts=attempt_number,
                    last_result=last_result,
                )
        self._update_run(
            run_id, diagnostics, status="failed", model_attempts=2,
            last_result=last_result, safe_error="; ".join(errors),
        )
        raise ScreenplayError(
            "invalid_screenplay_agent_output",
            "The Screenplay agent could not produce a valid scene draft. Nothing was changed.",
            {"validation_errors": errors[:10]},
        )

    def _update_run(
        self, run_id: str, diagnostics: list[dict[str, Any]], *,
        status: str = "running", model_attempts: int,
        last_result: ModelResult | None, safe_error: str = "",
    ) -> None:
        self.tools.repository.update_screenplay_agent_run(
            run_id, status=status, model_attempts=model_attempts,
            backend=getattr(last_result, "backend", getattr(self.model, "backend", "")),
            model=getattr(last_result, "model", getattr(self.model, "model", "")),
            safe_error=safe_error, diagnostics=diagnostics,
        )

    @staticmethod
    def _prompt(handoff: Mapping[str, Any], errors: list[str]) -> str:
        value = {
            "film": handoff["interview"], "screenplay_mode": handoff["screenplay"]["mode"],
            "approved_structure": next(item for item in handoff["outline"]["structures"] if item["approval_status"] == "accepted"),
            "accepted_beats": [item for item in handoff["outline"]["beats"] if item["approval_status"] == "accepted"],
        }
        prompt = "Draft the screenplay scenes from this approved plan:\n" + json.dumps(value, ensure_ascii=False, indent=2)
        if errors:
            prompt += "\nCorrect these rejected-output issues and return the whole screenplay again:\n- " + "\n- ".join(errors)
        return prompt

    @staticmethod
    def _validate(payload: Mapping[str, Any], accepted_ids: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
        scenes = payload.get("scenes") if isinstance(payload, Mapping) else None
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 20:
            return [], ["scenes must contain 1–20 items"]
        errors = []
        cited = set()
        for index, scene in enumerate(scenes, 1):
            if not isinstance(scene, Mapping):
                errors.append(f"scene {index} must be an object")
                continue
            beat_id = scene.get("beat_id")
            if beat_id not in accepted_ids:
                errors.append(f"scene {index} cites an unaccepted beat")
            else:
                cited.add(beat_id)
        missing = accepted_ids - cited
        if missing:
            errors.append("every accepted beat must be covered by at least one scene")
        return [dict(item) for item in scenes if isinstance(item, Mapping)], errors

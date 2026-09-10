"""Production breakdown extraction loop for an approved screenplay."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from .breakdown import (
    BREAKDOWN_CATEGORIES,
    CATEGORY_KEYS,
    BreakdownError,
    BreakdownToolkit,
)
from .database import SessionBusy
from .model import ModelResult, ModelUnavailable


LOGGER = logging.getLogger("second_unit.breakdown_agent")
PROMPT_VERSION = "breakdown-extractor-2"
SYSTEM_PROMPT = """You are Second Unit's Production Breakdown assistant.
Extract concrete shoot requirements from every approved screenplay scene. Return
only requirements that are visible, spoken, explicitly implied by the scene, or
necessary to execute it safely. Do not add generic crew or equipment that every
shoot would already need. Keep names short and details practical. Use only the
provided scene IDs and category keys. Cover every scene and include at least one
Location requirement for every scene, even when multiple scenes share a place.
These are editable
proposals for the filmmaker, not final production facts. Return JSON only."""

ITEM_SET_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["scene_id", "category", "name", "details"],
                "properties": {
                    "scene_id": {"type": "string"},
                    "category": {"type": "string"},
                    "name": {"type": "string"},
                    "details": {"type": "string"},
                },
            },
        },
    },
}


class BreakdownAgent:
    def __init__(self, model: Any, tools: BreakdownToolkit):
        self.model = model
        self.tools = tools

    def generate(
        self, session_id: str, expected_revision: int, *,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        handoff = self.tools.read_handoff(session_id)
        screenplay = handoff.get("screenplay")
        breakdown = handoff.get("breakdown")
        if not handoff["available"] or not screenplay:
            raise BreakdownError(
                "approved_screenplay_required",
                "approve the screenplay before generating its production breakdown",
            )
        current_revision = breakdown["revision"] if breakdown else 0
        if current_revision != expected_revision:
            from .breakdown import BreakdownRevisionConflict
            raise BreakdownRevisionConflict(current_revision)
        if breakdown and breakdown["status"] == "approved" and replace_existing:
            raise BreakdownError("breakdown_approved", "reopen the breakdown before regenerating it")
        if breakdown and any(scene["items"] for scene in breakdown["scenes"]) and not replace_existing:
            raise BreakdownError("breakdown_items_exist", "production requirements already exist")
        scene_ids = {scene["id"] for scene in screenplay["scenes"]}
        diagnostics: list[dict[str, Any]] = [{
            "phase": "request", "session_id": session_id,
            "screenplay_id": screenplay["id"], "base_revision": expected_revision,
            "scene_count": len(scene_ids), "replace_existing": replace_existing,
        }]
        try:
            run_id = self.tools.repository.start_breakdown_agent_run(
                screenplay["id"], expected_revision, PROMPT_VERSION, diagnostics
            )
        except SessionBusy as exc:
            raise BreakdownError(
                "breakdown_agent_busy",
                "The production breakdown is already being generated. Please wait.",
            ) from exc
        LOGGER.info(
            "breakdown_generation_started run=%s session=%s screenplay=%s revision=%s",
            run_id, session_id, screenplay["id"], expected_revision,
        )
        errors: list[str] = []
        last_result: ModelResult | None = None
        for attempt in range(2):
            attempt_number = attempt + 1
            diagnostics.append({"phase": "model_attempt_started", "attempt": attempt_number})
            self._update_run(run_id, diagnostics, attempt_number, last_result)
            try:
                last_result = self.model.decide(
                    system=SYSTEM_PROMPT,
                    prompt=self._prompt(screenplay, errors if attempt else []),
                    schema=ITEM_SET_SCHEMA,
                    timeout_seconds=30 if attempt == 0 else 45,
                    max_output_tokens=8_000,
                )
                diagnostics.append({
                    "phase": "model_attempt_completed", "attempt": attempt_number,
                    "backend": last_result.backend, "model": last_result.model,
                    "prompt_tokens": last_result.prompt_tokens,
                    "output_tokens": last_result.output_tokens,
                })
                items, errors = self._validate(last_result.payload, scene_ids)
                if errors:
                    diagnostics.append({
                        "phase": "validation_failed", "attempt": attempt_number,
                        "errors": errors[:20],
                    })
                    LOGGER.warning(
                        "breakdown_generation_validation_failed run=%s attempt=%s errors=%s",
                        run_id, attempt_number, errors,
                    )
                    self._update_run(run_id, diagnostics, attempt_number, last_result)
                    continue
                save = self.tools.replace_item_batch if replace_existing else self.tools.create_item_batch
                saved = save(session_id, expected_revision, items, actor="agent")
                diagnostics.append({
                    "phase": "save_completed", "attempt": attempt_number,
                    "item_count": len(items), "result_revision": saved["revision"],
                    "replaced_existing": replace_existing,
                })
                self._update_run(
                    run_id, diagnostics, attempt_number, last_result, status="completed"
                )
                LOGGER.info(
                    "breakdown_generation_completed run=%s attempt=%s items=%s revision=%s",
                    run_id, attempt_number, len(items), saved["revision"],
                )
                return {"breakdown": saved, "agent": {
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
                    self._update_run(run_id, diagnostics, attempt_number, last_result)
                    LOGGER.warning(
                        "breakdown_generation_provider_retry run=%s attempt=%s type=%s error=%s",
                        run_id, attempt_number, provider_type, provider_message,
                    )
                    errors = ["The first provider attempt failed transiently; return the complete breakdown."]
                    continue
                self._update_run(
                    run_id, diagnostics, attempt_number, last_result,
                    status="failed", safe_error=f"{provider_type}: {provider_message}",
                )
                LOGGER.error(
                    "breakdown_generation_provider_failed run=%s attempt=%s type=%s error=%s",
                    run_id, attempt_number, provider_type, provider_message,
                    exc_info=(type(cause), cause, cause.__traceback__),
                )
                raise BreakdownError(
                    "breakdown_agent_unavailable",
                    "The Breakdown agent is temporarily unavailable. Nothing was changed.",
                ) from exc
            except BreakdownError as exc:
                diagnostics.append({
                    "phase": "tool_save_failed", "attempt": attempt_number,
                    "error_code": exc.code, "error": exc.message,
                })
                self._update_run(
                    run_id, diagnostics, attempt_number, last_result,
                    status="failed", safe_error=exc.message,
                )
                LOGGER.exception(
                    "breakdown_generation_save_failed run=%s code=%s", run_id, exc.code
                )
                raise
            except (TypeError, ValueError) as exc:
                errors = [str(exc)]
                diagnostics.append({
                    "phase": "response_parse_failed", "attempt": attempt_number,
                    "error_type": type(exc).__name__, "error": str(exc)[:4000],
                    "response": getattr(exc, "diagnostics", {}),
                })
                self._update_run(run_id, diagnostics, attempt_number, last_result)
        self._update_run(
            run_id, diagnostics, 2, last_result,
            status="failed", safe_error="; ".join(errors),
        )
        raise BreakdownError(
            "invalid_breakdown_agent_output",
            "The Breakdown agent could not produce a valid requirement set. Nothing was changed.",
            {"validation_errors": errors[:10]},
        )

    def _update_run(
        self, run_id: str, diagnostics: list[dict[str, Any]],
        attempts: int, result: ModelResult | None, *,
        status: str = "running", safe_error: str = "",
    ) -> None:
        self.tools.repository.update_breakdown_agent_run(
            run_id, status=status, model_attempts=attempts,
            backend=getattr(result, "backend", getattr(self.model, "backend", "")),
            model=getattr(result, "model", getattr(self.model, "model", "")),
            safe_error=safe_error, diagnostics=diagnostics,
        )

    @staticmethod
    def _prompt(screenplay: Mapping[str, Any], errors: list[str]) -> str:
        value = {
            "allowed_categories": [
                {"key": item["category"], "meaning": item["description"]}
                for item in BREAKDOWN_CATEGORIES
            ],
            "screenplay_mode": screenplay["mode"],
            "scenes": [{
                "scene_id": scene["id"], "heading": scene["heading"],
                "action": scene["action"], "narration": scene["narration"],
                "dialogue": scene["dialogue"],
            } for scene in screenplay["scenes"]],
        }
        prompt = "Break down these approved screenplay scenes:\n" + json.dumps(
            value, ensure_ascii=False, indent=2
        )
        if errors:
            prompt += "\nCorrect these rejected-output issues and return the whole breakdown again:\n- "
            prompt += "\n- ".join(errors)
        return prompt

    @staticmethod
    def _validate(
        payload: Mapping[str, Any], scene_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 120:
            return [], ["items must contain 1–120 production requirements"]
        errors: list[str] = []
        cited: set[str] = set()
        location_cited: set[str] = set()
        cleaned: list[dict[str, Any]] = []
        for index, item in enumerate(items, 1):
            if not isinstance(item, Mapping):
                errors.append(f"requirement {index} must be an object")
                continue
            scene_id = item.get("scene_id")
            if scene_id not in scene_ids:
                errors.append(f"requirement {index} cites an unknown screenplay scene")
            else:
                cited.add(scene_id)
            if item.get("category") not in CATEGORY_KEYS:
                errors.append(f"requirement {index} uses an unsupported category")
            elif item.get("category") == "location" and scene_id in scene_ids:
                location_cited.add(scene_id)
            cleaned.append(dict(item))
        if scene_ids - cited:
            errors.append("every screenplay scene must have at least one production requirement")
        if scene_ids - location_cited:
            errors.append("every screenplay scene must have at least one Location requirement")
        return cleaned, errors

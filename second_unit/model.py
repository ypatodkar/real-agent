"""Provider adapter for one structured Story Editor decision."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


PROMPT_MODEL_DEFAULTS = {
    "vertex": "gemini-2.5-flash",
    "aistudio": "gemini-flash-latest",
}
DEFAULT_TEXT_TIMEOUT_SECONDS = 20.0
MAX_TEXT_TIMEOUT_SECONDS = 60.0
MAX_OUTPUT_TOKENS = 2_500


class ModelUnavailable(RuntimeError):
    """A safe provider failure; private provider details stay in the cause."""


class ModelInvalidOutput(ValueError):
    """The provider answered, but not with the promised structured object."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class ModelResult:
    payload: dict[str, Any]
    backend: str
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0


def load_env() -> None:
    """Load names from a local or parent .env without overriding the process."""
    package_root = Path(__file__).resolve().parent.parent
    for path in (package_root / ".env", package_root.parent / ".env"):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip("'\"")


class GeminiModel:
    def __init__(self, *, backend: str, client: Any | None = None,
                 model: str | None = None, timeout_seconds: float | None = None):
        if backend not in {"vertex", "aistudio"}:
            raise ValueError("backend must be vertex or aistudio")
        self.backend = backend
        self.model = model or os.environ.get("SECOND_UNIT_TEXT_MODEL") or PROMPT_MODEL_DEFAULTS[backend]
        if timeout_seconds is None:
            try:
                timeout_seconds = float(os.environ.get(
                    "SECOND_UNIT_TEXT_TIMEOUT_SECONDS",
                    str(DEFAULT_TEXT_TIMEOUT_SECONDS),
                ))
            except (TypeError, ValueError):
                timeout_seconds = DEFAULT_TEXT_TIMEOUT_SECONDS
        # Keep the configured/default deadline bounded at 30 seconds. A larger
        # deadline is available only to an explicit, stage-specific call.
        self.timeout_seconds = min(30.0, max(5.0, float(timeout_seconds)))
        if client is not None:
            self._client = client
            return
        try:
            from google import genai
            if backend == "vertex":
                self._client = genai.Client(
                    vertexai=True,
                    project=os.environ["GOOGLE_CLOUD_PROJECT"],
                    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
                )
            else:
                self._client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        except Exception as exc:
            raise ModelUnavailable(
                "The Story Editor model could not be initialized."
            ) from exc

    def decide(self, *, system: str, prompt: str,
               schema: Mapping[str, Any], timeout_seconds: float | None = None,
               max_output_tokens: int | None = None) -> ModelResult:
        deadline = self.timeout_seconds if timeout_seconds is None else min(
            MAX_TEXT_TIMEOUT_SECONDS, max(5.0, float(timeout_seconds))
        )
        output_cap = MAX_OUTPUT_TOKENS if max_output_tokens is None else min(
            8_000, max(500, int(max_output_tokens))
        )
        try:
            from google.genai import types
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    http_options=types.HttpOptions(
                        timeout=int(deadline * 1_000),
                    ),
                    system_instruction=system,
                    temperature=0.35,
                    max_output_tokens=output_cap,
                    response_mime_type="application/json",
                    response_schema=dict(schema),
                ),
            )
        except Exception as exc:
            raise ModelUnavailable(
                "The Story Editor is temporarily unavailable."
            ) from exc
        raw_text = ""
        try:
            raw = response.model_dump(mode="json", exclude_none=True)
        except Exception:
            raw = {}
        try:
            parsed = getattr(response, "parsed", None)
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(mode="json", exclude_none=True)
            if isinstance(parsed, Mapping):
                payload = dict(parsed)
            else:
                raw_text = (response.text or "").strip()
                payload = json.loads(raw_text)
            if not isinstance(payload, dict):
                raise ValueError("structured response was not an object")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelInvalidOutput(
                "The Story Editor returned invalid structured JSON.",
                {
                    "raw_text": raw_text[:12_000],
                    "finish_reasons": [
                        str((candidate or {}).get("finish_reason", ""))
                        for candidate in (raw.get("candidates") or [])
                    ],
                    "usage_metadata": raw.get("usage_metadata") or {},
                    "parse_error_type": type(exc).__name__,
                    "parse_error": str(exc)[:2000],
                },
            ) from exc
        usage = raw.get("usage_metadata") or {}
        return ModelResult(
            payload=payload,
            backend=self.backend,
            model=self.model,
            prompt_tokens=int(usage.get("prompt_token_count") or 0),
            output_tokens=int(usage.get("candidates_token_count") or 0),
        )


class NoModel:
    backend = "local_fallback"
    model = "deterministic"

    def decide(self, **_: Any) -> ModelResult:
        raise ModelUnavailable("No language model is configured.")


def get_model() -> GeminiModel | NoModel:
    load_env()
    if os.environ.get("SECOND_UNIT_OFFLINE", "").strip().lower() in {"1", "true", "yes"}:
        return NoModel()
    requested = os.environ.get("SECOND_UNIT_BACKEND", "").strip().lower()
    if requested == "vertex" and os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return GeminiModel(backend="vertex")
    if requested == "aistudio" and os.environ.get("GOOGLE_API_KEY"):
        return GeminiModel(backend="aistudio")
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return GeminiModel(backend="vertex")
    if os.environ.get("GOOGLE_API_KEY"):
        return GeminiModel(backend="aistudio")
    return NoModel()

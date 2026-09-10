"""One typed decision per call, through a JSON action schema.

Not native function calling. Once state updates are batched into a single
`TurnDecision`, the normal path returns one structured object — function calling
buys nothing there and ties the design to one provider. The only genuine tool is
history search, which is a second call by definition.

    GOOGLE_CLOUD_PROJECT -> Vertex AI
    GOOGLE_API_KEY       -> Gemini Developer API
    neither              -> no client; tests use stub_model instead
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field

import budget
import prompt
from harness import ProviderError

FALLBACK_MODEL = {"vertex": "gemini-2.5-flash", "aistudio": "gemini-flash-latest"}

_STRING = {"type": "string"}
DECISION_SCHEMA = {
    "type": "object",
    "required": ["state_updates", "response"],
    "properties": {
        "state_updates": {
            "type": "object",
            "properties": {
                "facts": {"type": "array", "items": {
                    "type": "object", "required": ["text", "evidence"],
                    "properties": {"text": _STRING, "evidence": _STRING}}},
                "gap_changes": {"type": "array", "items": {
                    "type": "object", "required": ["action"],
                    "properties": {"action": {"type": "string",
                                              "enum": ["open", "update", "resolve"]},
                                   "text": _STRING, "gap_id": _STRING, "evidence": _STRING}}},
                "readiness": {
                    "type": "object",
                    "properties": {
                        "ready": {"type": "boolean"},
                        "reason": _STRING,
                        "lenses": {"type": "object", "properties": {
                            "carries": _STRING, "pressure": _STRING,
                            "sequence": _STRING, "ending": _STRING}}}},
            }},
        "response": {
            "type": "object",
            "required": ["primary_intent"],
            "properties": {
                "primary_intent": {"type": "string", "enum": [
                    "ask_question", "offer_suggestions", "reflect_and_confirm",
                    "coach_writer", "recommend_outline"]},
                "question": _STRING,
                "focus": _STRING,
                "blocks": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"kind": {"type": "string",
                                            "enum": ["reflection", "coaching", "note"]},
                                   "text": _STRING}}},
                "suggestions": {"type": "array", "items": {
                    "type": "object", "required": ["label", "detail"],
                    "properties": {"label": _STRING, "detail": _STRING}}},
            }},
    },
}


@dataclass
class Reply:
    raw: dict
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    cost_micro_usd: int = 0
    prompt_text: str = ""
    provider_payload: dict = field(default_factory=dict)


def load_dotenv() -> None:
    """The clean build shares the parent's credentials — one .env, one setup."""
    path = pathlib.Path(__file__).parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


class GeminiClient:
    def __init__(self, *, vertex: bool, recorder=None):
        from google import genai

        self.backend = "vertex" if vertex else "aistudio"
        self.name = os.environ.get("SECOND_UNIT_TEXT_MODEL") or FALLBACK_MODEL[self.backend]
        self.recorder = recorder
        if vertex:
            self._client = genai.Client(
                vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
        else:
            self._client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    def decide(self, snapshot, allowed: set[str], repair: str | None = None) -> Reply:
        import json

        from google.genai import types

        text = prompt.render(snapshot, allowed, repair)
        try:
            response = self._client.models.generate_content(
                model=self.name, contents=text,
                config=types.GenerateContentConfig(
                    system_instruction=prompt.SYSTEM,
                    temperature=0.8,
                    response_mime_type="application/json",
                    response_schema=DECISION_SCHEMA))
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

        usage = getattr(response, "usage_metadata", None)
        tokens_in = getattr(usage, "prompt_token_count", 0) or 0
        tokens_out = getattr(usage, "candidates_token_count", 0) or 0
        payload = response.model_dump(mode="json", exclude_none=True)
        if self.recorder:
            self.recorder.write("prompt", {"text": text, "repair": repair})
            self.recorder.write("reply", payload)
        try:
            raw = json.loads(response.text or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError(f"the provider returned unparseable JSON: {exc}") from exc

        return Reply(raw=raw, tokens_in=tokens_in, tokens_out=tokens_out, model=self.name,
                     cost_micro_usd=budget.cost_micro_usd(self.name, tokens_in, tokens_out),
                     prompt_text=text, provider_payload=payload)


def get_client(recorder=None):
    load_dotenv()
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return GeminiClient(vertex=True, recorder=recorder)
    if os.environ.get("GOOGLE_API_KEY"):
        return GeminiClient(vertex=False, recorder=recorder)
    raise ProviderError("no credentials: set GOOGLE_CLOUD_PROJECT or GOOGLE_API_KEY")

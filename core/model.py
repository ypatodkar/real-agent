"""The model client — one interface, three backends.

Gemini text, Imagen and TTS all go through `google-genai`. The pipeline orchestrates;
it never touches a model. That split matters for one specific reason:

    the recorder needs the provider payload VERBATIM, including
    groundingMetadata, or replay stops being free.

Chat abstractions normalise responses into a common message shape, and
provider-specific metadata is exactly what gets flattened out in that
translation. Calling the SDK directly means there is nothing in the path that
could drop it.

Backend selection, in order:

    SECOND_UNIT_BACKEND=vertex   + GOOGLE_CLOUD_PROJECT   -> Vertex AI  (GCP credits)
    SECOND_UNIT_BACKEND=aistudio + GOOGLE_API_KEY         -> Gemini Developer API
    neither                                               -> stub, no network

GOOGLE_CLOUD_PROJECT wins over GOOGLE_API_KEY when both are set, so switching to
Vertex is one added line and falling back is one comment. Vertex authenticates
through ADC: either `gcloud auth application-default login`, or
GOOGLE_APPLICATION_CREDENTIALS pointing at a service-account JSON — the latter
needs no CLI at all, which is the console-only route.

Which one your $100 actually covers is a billing question, not a code question.
Credits are usually attached to a GCP billing account, which means Vertex.
Confirm before the sweep, not after.
"""

from __future__ import annotations

import json
import os
import re
import pathlib
import random
import time
from dataclasses import dataclass, field
from typing import Any

# Model names differ by backend: AI Studio retired gemini-2.5-* for new keys and
# offers gemini-flash-latest; Vertex has no such alias and does serve 2.5. So the
# default depends on where we end up, and SECOND_UNIT_TEXT_MODEL overrides both.
FALLBACK_MODEL = {"vertex": "gemini-2.5-flash", "aistudio": "gemini-flash-latest"}


def default_model(backend: str = "aistudio") -> str:
    """Resolved at call time, never at import.

    Read as a module constant this would evaluate before load_dotenv() runs, so
    SECOND_UNIT_TEXT_MODEL set in .env would be silently ignored — which looks
    exactly like the override not working.
    """
    return os.environ.get("SECOND_UNIT_TEXT_MODEL") or FALLBACK_MODEL.get(
        backend, FALLBACK_MODEL["aistudio"])

# Placeholders, same status as the caps in ARCHITECTURE.md §9. Confirm against
# current published rates before trusting any cost number this produces.
RATES_PER_MTOK = {
    "gemini-2.5-flash": {"in": 0.30, "out": 2.50},
    "gemini-2.5-pro":   {"in": 1.25, "out": 10.00},
}


class ProviderError(RuntimeError):
    """A call reached the provider and was refused.

    Typed so the harness can apply the stage's breach behaviour instead of the
    run dying on an unhandled traceback. A 403 in week four should degrade the
    stage and let QC report the damage, not lose the whole run.
    """

    def __init__(self, message: str, *, status: int | None = None, hint: str = ""):
        self.status, self.hint = status, hint
        super().__init__(message)


@dataclass
class Response:
    text: str
    raw: dict[str, Any]                  # verbatim provider payload — for the recorder
    usage: dict[str, int] = field(default_factory=dict)
    cost: float = 0.0
    grounding: list[dict] = field(default_factory=list)
    stub: bool = False

    def json(self) -> Any:
        """Parse the text as JSON, digging it out of prose if need be.

        Non-grounded calls set response_mime_type and come back clean. Grounded
        calls cannot — the API refuses both at once — so those arrive as prose
        that may fence the JSON in a code block or wrap it in commentary.
        """
        text = (self.text or "").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
        if fence:
            return json.loads(fence.group(1).strip())

        start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
        if start == -1:
            raise ValueError("no JSON found in response")
        closer = "}" if text[start] == "{" else "]"
        return json.loads(text[start:text.rfind(closer) + 1])


def _price(model: str, usage: dict[str, int]) -> float:
    rate = RATES_PER_MTOK.get(model)
    if not rate:
        return 0.0
    return round(
        usage.get("prompt_tokens", 0) / 1e6 * rate["in"]
        + usage.get("output_tokens", 0) / 1e6 * rate["out"],
        6,
    )


class StubClient:
    """No network. Deterministic fixtures so the graph runs without credentials."""

    backend = "stub"

    def generate(self, prompt: str, *, stub: dict, model: str | None = None,
                 grounded: bool = False, json_out: bool = False, **_) -> Response:
        model = model or default_model("stub")
        payload = json.dumps(stub)
        return Response(
            text=payload,
            raw={"_stub": True, "model": model,
                 "candidates": [{"content": {"parts": [{"text": payload}]}}],
                 "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0}},
            usage={"prompt_tokens": 0, "output_tokens": 0},
            cost=0.0,
            stub=True,
        )


class GeminiClient:
    """Real calls. Same class for Vertex and AI Studio — only the Client differs."""

    def __init__(self, *, vertex: bool):
        from google import genai

        self.backend = "vertex" if vertex else "aistudio"
        if vertex:
            # Credentials come from ADC, which is either
            #   gcloud auth application-default login   (CLI route), or
            #   GOOGLE_APPLICATION_CREDENTIALS -> a service-account JSON (web route).
            # Either way google-auth resolves it; nothing key-shaped is read here.
            creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            if creds and not pathlib.Path(creds).exists():
                raise ProviderError(
                    f"GOOGLE_APPLICATION_CREDENTIALS points at {creds}, which does not exist",
                    hint="check the path in .env, and that the JSON was moved there.",
                )
            self._client = genai.Client(
                vertexai=True,
                project=os.environ["GOOGLE_CLOUD_PROJECT"],
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            self._client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    def generate(self, prompt: str, *, model: str | None = None,
                 system: str | None = None, schema: Any = None,
                 json_out: bool = False, grounded: bool = False,
                 temperature: float = 0.2, **_) -> Response:
        from google.genai import types

        model = model or default_model(self.backend)

        cfg: dict[str, Any] = {"temperature": temperature}
        if system:
            cfg["system_instruction"] = system
        if json_out or schema is not None:
            cfg["response_mime_type"] = "application/json"
        if schema is not None:
            cfg["response_schema"] = schema
        if grounded:
            # Grounding cannot be combined with a JSON response type on this API,
            # so a grounded call returns prose and the caller extracts from it.
            cfg.pop("response_schema", None)
            cfg.pop("response_mime_type", None)
            cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]

        # Vertex can return short-lived 429s even when the project still has
        # quota. Retry those here so every caller gets the same behaviour.
        # Keep the wait small: this client is used by an interactive UI.
        result = None
        for attempt in range(3):
            try:
                result = self._client.models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(**cfg),
                )
                break
            except Exception as exc:
                message = str(exc)
                transient = "429" in message or "RESOURCE_EXHAUSTED" in message
                if transient and attempt < 2:
                    time.sleep((1.25 * (2 ** attempt)) + random.uniform(0, 0.35))
                    continue
                raise ProviderError(
                    message[:400],
                    status=429 if transient else None,
                    hint=_diagnose(exc),
                ) from exc

        if result is None:  # defensive; the loop either returns or raises
            raise ProviderError("model returned no result")

        raw = result.model_dump(mode="json", exclude_none=True)

        um = raw.get("usage_metadata") or {}
        usage = {
            "prompt_tokens": um.get("prompt_token_count", 0),
            "output_tokens": um.get("candidates_token_count", 0),
        }

        grounding = [
            c["grounding_metadata"]
            for c in raw.get("candidates", [])
            if c.get("grounding_metadata")
        ]

        return Response(
            text=result.text or "",
            raw=raw,                       # verbatim — this is what replay reads
            usage=usage,
            cost=_price(model, usage),
            grounding=grounding,
        )


# Google separates "the API is off" from "this key may not call it". Getting
# those the wrong way round costs an afternoon in the wrong console page.
_HINTS = {
    "API_KEY_SERVICE_BLOCKED":
        "the API is enabled, but THIS KEY is restricted from calling it. "
        "Console > APIs & Services > Credentials > the key > API restrictions: "
        "add Generative Language API, or set 'Don't restrict key'. "
        "Enabling the API again will not help.",
    "SERVICE_DISABLED":
        "the API itself is not enabled on this project. Enable it at "
        "console.cloud.google.com/apis/library/generativelanguage.googleapis.com "
        "for the project named in the error.",
    "API_KEY_INVALID":     "the key is malformed or has been revoked.",
    "could not automatically determine credentials":
        "Vertex found no credentials. Either run "
        "`gcloud auth application-default login`, or set "
        "GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON path.",
    "aiplatform.googleapis.com":
        "enable the Vertex AI API on this project, and confirm the service "
        "account has the 'Vertex AI User' role.",
    "prepayment credits are depleted":
        "AI Studio prepayment is at zero. This pool is SEPARATE from Google Cloud "
        "credits — GCP credit only applies via Vertex. Either top up at "
        "ai.studio/projects, or switch to Vertex by setting GOOGLE_CLOUD_PROJECT.",
    "RESOURCE_EXHAUSTED":  "quota exhausted — rate limit, free-tier cap, or depleted credit.",
    "no longer available to new users":
        "this model is retired for new keys. Run tools/check_models.py and set "
        "SECOND_UNIT_TEXT_MODEL in .env to one that is callable.",
    "NOT_FOUND":           "the model name is not available on this backend.",
    "PERMISSION_DENIED":   "the key authenticated but is not allowed to call this API.",
}


def _diagnose(exc: Exception) -> str:
    text = str(exc)
    for token, hint in _HINTS.items():
        if token in text:
            return hint
    return ""


def load_dotenv(path: pathlib.Path | None = None) -> list[str]:
    """Read .env into the environment. Existing variables always win.

    Hand-rolled rather than a dependency: it is fifteen lines, and the file is
    gitignored so nothing here reaches the repository.
    """
    path = path or (pathlib.Path(__file__).resolve().parent.parent / ".env")
    loaded: list[str] = []
    if not path.exists():
        return loaded

    seen: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if not key:
            continue

        # A duplicate is almost always an append that was meant to be a replace.
        # Silently keeping the first one sends you debugging a key you are not
        # using, so the LAST wins and it says so.
        if key in seen:
            print(f"  [.env] {key} appears more than once — using the last. "
                  f"Delete the earlier line to silence this.")
        seen.add(key)

        if key not in os.environ or key in loaded:
            os.environ[key] = value
            if key not in loaded:
                loaded.append(key)      # names only — never the values
    return loaded


def get_client(verbose: bool = True):
    load_dotenv()
    backend = os.environ.get("SECOND_UNIT_BACKEND", "").lower()

    if backend == "vertex" and os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return GeminiClient(vertex=True)
    if backend == "aistudio" and os.environ.get("GOOGLE_API_KEY"):
        return GeminiClient(vertex=False)

    # infer, so a key alone is enough to go live
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return GeminiClient(vertex=True)
    if os.environ.get("GOOGLE_API_KEY"):
        return GeminiClient(vertex=False)

    if verbose:
        print("  [model] no credentials — using stubs. Set GOOGLE_CLOUD_PROJECT "
              "(Vertex) or GOOGLE_API_KEY (AI Studio) to go live.")
    return StubClient()

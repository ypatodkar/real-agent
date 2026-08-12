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
import pathlib
from dataclasses import dataclass, field
from typing import Any

# An alias rather than a pinned version: gemini-2.5-flash was listed by the API
# but 404s for new keys ("no longer available to new users"), which is a failure
# that only shows up at call time.
DEFAULT_TEXT_MODEL = os.environ.get("SECOND_UNIT_TEXT_MODEL", "gemini-flash-latest")

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
        """Parse the text as JSON. Stages ask for structured output."""
        return json.loads(self.text)


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

    def generate(self, prompt: str, *, stub: dict, model: str = DEFAULT_TEXT_MODEL,
                 grounded: bool = False, **_) -> Response:
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

    def generate(self, prompt: str, *, model: str = DEFAULT_TEXT_MODEL,
                 system: str | None = None, schema: Any = None,
                 grounded: bool = False, temperature: float = 0.2,
                 **_) -> Response:
        from google.genai import types

        cfg: dict[str, Any] = {"temperature": temperature}
        if system:
            cfg["system_instruction"] = system
        if schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_schema"] = schema
        if grounded:
            # Grounding and a response_schema are mutually exclusive on this API.
            cfg.pop("response_schema", None)
            cfg.pop("response_mime_type", None)
            cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]

        try:
            result = self._client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(**cfg),
            )
        except Exception as exc:
            raise ProviderError(str(exc)[:400], hint=_diagnose(exc)) from exc

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

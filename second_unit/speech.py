"""Speech-to-text boundary for the Interview composer.

The browser sends one bounded, mono PCM16 recording over the application's
normal HTTP connection.  This module validates that in-memory recording and
uses Google Cloud Speech-to-Text V2 for synchronous recognition.  It never
writes audio to a file, database, log, or trajectory record.

The service deliberately contains no HTTP-framework code.  An endpoint can
map :class:`SpeechError.http_status` and :meth:`SpeechError.as_dict` directly
to a safe response without exposing provider exception text.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any, Callable, Mapping, Optional


PCM_CONTENT_TYPE = "application/octet-stream"
BYTES_PER_SAMPLE = 2
MIN_SAMPLE_RATE_HZ = 8_000
MAX_SAMPLE_RATE_HZ = 48_000

# Google synchronous recognition has a sixty-second audio ceiling.  Keeping a
# small margin prevents recordings near the browser timer boundary from being
# rejected after transport and sample rounding.
MAX_DURATION_SECONDS = 55
MAX_AUDIO_BYTES = MAX_SAMPLE_RATE_HZ * BYTES_PER_SAMPLE * MAX_DURATION_SECONDS

DEFAULT_LANGUAGE = "en-US"
DEFAULT_MODEL = "latest_long"
DEFAULT_TIMEOUT_SECONDS = 60.0

_LANGUAGE_RE = re.compile(
    r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"
)

ClientFactory = Callable[[], Any]
CapabilityProbe = Callable[[], "SpeechCapability"]


@dataclass(frozen=True)
class SpeechCapability:
    """Whether this process is configured to attempt transcription."""

    available: bool
    code: str
    message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class ValidatedAudio:
    """Validated request values passed to the provider adapter."""

    content: bytes
    sample_rate_hz: int
    language: str
    duration_ms: int


@dataclass(frozen=True)
class Transcription:
    """Editable transcription returned to the Interview composer."""

    transcript: str
    language: str
    duration_ms: int

    def as_dict(self) -> dict[str, object]:
        return {
            "transcript": self.transcript,
            "language": self.language,
            "duration_ms": self.duration_ms,
        }


class SpeechError(Exception):
    """A typed, user-safe failure suitable for an HTTP response."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable

    def as_dict(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            }
        }


def _validation_error(
    code: str,
    message: str,
    *,
    http_status: int = 400,
) -> SpeechError:
    return SpeechError(code, message, http_status=http_status, retryable=False)


def validate_pcm_upload(
    audio: object,
    *,
    content_type: str,
    sample_rate_hz: int,
    language: str = DEFAULT_LANGUAGE,
) -> ValidatedAudio:
    """Validate an in-memory raw mono PCM16 upload.

    PCM is signed, little-endian, one channel, with no container header.  The
    endpoint is expected to reject an excessive ``Content-Length`` before
    reading the body; the byte check here is a second boundary.
    """

    media_type = (
        content_type.partition(";")[0].strip().lower()
        if isinstance(content_type, str)
        else ""
    )
    if media_type != PCM_CONTENT_TYPE:
        raise _validation_error(
            "unsupported_media_type",
            f"Voice audio must use {PCM_CONTENT_TYPE}.",
            http_status=415,
        )

    if (
        isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, int)
        or not MIN_SAMPLE_RATE_HZ <= sample_rate_hz <= MAX_SAMPLE_RATE_HZ
    ):
        raise _validation_error(
            "invalid_sample_rate",
            "The microphone sample rate must be between 8000 and 48000 Hz.",
        )

    normalized_language = language.strip() if isinstance(language, str) else ""
    if (
        not normalized_language
        or len(normalized_language) > 35
        or _LANGUAGE_RE.fullmatch(normalized_language) is None
    ):
        raise _validation_error(
            "invalid_language",
            "The speech language must be a valid language tag such as en-US.",
        )

    if not isinstance(audio, (bytes, bytearray, memoryview)):
        raise _validation_error(
            "invalid_audio",
            "Voice audio must be sent as raw PCM16 bytes.",
        )

    content = bytes(audio)
    if not content:
        raise _validation_error(
            "empty_audio",
            "No microphone audio was received. You can try again or type instead.",
        )
    if len(content) > MAX_AUDIO_BYTES:
        raise _validation_error(
            "audio_too_large",
            "The voice recording is too large. Keep it under 55 seconds.",
            http_status=413,
        )
    if len(content) % BYTES_PER_SAMPLE:
        raise _validation_error(
            "invalid_pcm",
            "The microphone audio ended with an incomplete PCM16 sample.",
        )

    duration_seconds = len(content) / (sample_rate_hz * BYTES_PER_SAMPLE)
    if duration_seconds > MAX_DURATION_SECONDS:
        raise _validation_error(
            "audio_too_long",
            "The voice recording is too long. Keep it under 55 seconds.",
            http_status=413,
        )

    return ValidatedAudio(
        content=content,
        sample_rate_hz=sample_rate_hz,
        language=normalized_language,
        duration_ms=round(duration_seconds * 1000),
    )


def _default_client_factory() -> Any:
    from google.cloud import speech_v2

    return speech_v2.SpeechClient()


def detect_capability(
    *,
    project_id: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    client_factory: Optional[ClientFactory] = None,
) -> SpeechCapability:
    """Perform a local capability check without making a billable API call.

    Credentials, API enablement, quota, and network access are still verified
    by the actual recognition request and become a safe ``provider_unavailable``
    error.  A supplied client factory makes this probe deterministic in tests.
    """

    environment = os.environ if environ is None else environ
    configured_project = (
        project_id if project_id is not None else environment.get("GOOGLE_CLOUD_PROJECT")
    )
    if not configured_project or not configured_project.strip():
        return SpeechCapability(
            False,
            "speech_not_configured",
            "Set GOOGLE_CLOUD_PROJECT to enable voice typing.",
        )

    if client_factory is not None:
        return SpeechCapability(
            True,
            "speech_configured",
            "Voice typing is configured. Google is checked when you transcribe.",
        )

    try:
        from google.cloud import speech_v2  # noqa: F401
    except ImportError:
        return SpeechCapability(
            False,
            "speech_dependency_missing",
            "Install google-cloud-speech to enable voice typing.",
        )

    try:
        import google.auth
        google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except Exception:
        return SpeechCapability(
            False,
            "speech_credentials_missing",
            "Google Cloud credentials are not available. You can continue typing.",
        )

    return SpeechCapability(
        True,
        "speech_configured",
        "Voice typing is configured. Google is checked when you transcribe.",
    )


class SpeechService:
    """Validate PCM and synchronously transcribe it with Google STT V2."""

    def __init__(
        self,
        *,
        project_id: Optional[str] = None,
        model: Optional[str] = None,
        client_factory: Optional[ClientFactory] = None,
        capability_probe: Optional[CapabilityProbe] = None,
        environ: Optional[Mapping[str, str]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._project_id = (
            project_id
            if project_id is not None
            else self._environ.get("GOOGLE_CLOUD_PROJECT", "")
        ).strip()
        self._model = (
            model
            if model is not None
            else self._environ.get("SECOND_UNIT_SPEECH_MODEL", DEFAULT_MODEL)
        ).strip() or DEFAULT_MODEL
        self._client_factory = client_factory or _default_client_factory
        self._capability_probe = capability_probe
        if timeout_seconds is None:
            try:
                timeout_seconds = float(
                    self._environ.get(
                        "SECOND_UNIT_SPEECH_TIMEOUT_SECONDS",
                        str(DEFAULT_TIMEOUT_SECONDS),
                    )
                )
            except (TypeError, ValueError):
                timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self._timeout_seconds = min(60.0, max(5.0, float(timeout_seconds)))

    def capability(self) -> SpeechCapability:
        if self._capability_probe is not None:
            try:
                capability = self._capability_probe()
            except Exception:
                return SpeechCapability(
                    False,
                    "speech_capability_failed",
                    "Voice typing could not be initialized. You can continue typing.",
                )
            if not isinstance(capability, SpeechCapability):
                return SpeechCapability(
                    False,
                    "speech_capability_failed",
                    "Voice typing could not be initialized. You can continue typing.",
                )
            return capability

        factory_for_probe = (
            self._client_factory if self._client_factory is not _default_client_factory else None
        )
        return detect_capability(
            project_id=self._project_id,
            environ=self._environ,
            client_factory=factory_for_probe,
        )

    def transcribe(
        self,
        audio: object,
        *,
        content_type: str,
        sample_rate_hz: int,
        language: str = DEFAULT_LANGUAGE,
    ) -> Transcription:
        validated = validate_pcm_upload(
            audio,
            content_type=content_type,
            sample_rate_hz=sample_rate_hz,
            language=language,
        )

        capability = self.capability()
        if not capability.available:
            raise SpeechError(
                capability.code,
                capability.message,
                http_status=503,
                retryable=False,
            )

        request = {
            "recognizer": (
                f"projects/{self._project_id}/locations/global/recognizers/_"
            ),
            "config": {
                "explicit_decoding_config": {
                    "encoding": "LINEAR16",
                    "sample_rate_hertz": validated.sample_rate_hz,
                    "audio_channel_count": 1,
                },
                "language_codes": [validated.language],
                "model": self._model,
            },
            "content": validated.content,
        }

        try:
            client = self._client_factory()
            response = client.recognize(
                request=request,
                timeout=self._timeout_seconds,
            )
            transcript = _extract_transcript(response)
        except Exception as exc:
            if exc.__class__.__name__ == "DeadlineExceeded":
                raise SpeechError(
                    "provider_timeout",
                    "Voice transcription took too long. Your typed text is safe; please try again.",
                    http_status=504,
                    retryable=True,
                ) from exc
            raise SpeechError(
                "provider_unavailable",
                "Voice transcription is temporarily unavailable. Your typed text is safe.",
                http_status=503,
                retryable=True,
            ) from exc

        if not transcript:
            raise SpeechError(
                "no_speech_detected",
                "No clear speech was detected. You can try again or type instead.",
                http_status=422,
                retryable=False,
            )

        return Transcription(
            transcript=transcript,
            language=validated.language,
            duration_ms=validated.duration_ms,
        )


def _extract_transcript(response: object) -> str:
    segments: list[str] = []
    for result in getattr(response, "results", ()) or ():
        alternatives = getattr(result, "alternatives", ()) or ()
        if not alternatives:
            continue
        text = getattr(alternatives[0], "transcript", "")
        normalized = " ".join(text.split()) if isinstance(text, str) else ""
        if normalized:
            segments.append(normalized)
    return " ".join(segments)


def transcribe_pcm(
    audio: object,
    *,
    content_type: str,
    sample_rate_hz: int,
    language: str = DEFAULT_LANGUAGE,
    service: Optional[SpeechService] = None,
) -> Transcription:
    """Convenience entry point for a same-origin HTTP endpoint."""

    active_service = service or SpeechService()
    return active_service.transcribe(
        audio,
        content_type=content_type,
        sample_rate_hz=sample_rate_hz,
        language=language,
    )

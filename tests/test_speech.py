from __future__ import annotations

from types import SimpleNamespace
import unittest

from second_unit.speech import (
    BYTES_PER_SAMPLE,
    MAX_AUDIO_BYTES,
    MAX_DURATION_SECONDS,
    PCM_CONTENT_TYPE,
    SpeechCapability,
    SpeechError,
    SpeechService,
    detect_capability,
    validate_pcm_upload,
)


def pcm_for(*, sample_rate: int = 16_000, seconds: float = 0.1) -> bytes:
    sample_count = int(sample_rate * seconds)
    return b"\x01\x00" * sample_count


class FakeClient:
    def __init__(self, transcripts: list[str] | None = None) -> None:
        self.requests: list[dict[str, object]] = []
        self.transcripts = transcripts if transcripts is not None else ["hello world"]

    def recognize(self, *, request: dict[str, object], timeout: float) -> object:
        self.requests.append(request)
        self.requests[-1]["_timeout"] = timeout
        results = [
            SimpleNamespace(
                alternatives=[SimpleNamespace(transcript=transcript)]
            )
            for transcript in self.transcripts
        ]
        return SimpleNamespace(results=results)


class FailingClient:
    def recognize(self, *, request: dict[str, object], timeout: float) -> object:
        raise RuntimeError("private credential or provider detail")


class DeadlineExceeded(Exception):
    pass


class TimeoutClient:
    def recognize(self, *, request: dict[str, object], timeout: float) -> object:
        raise DeadlineExceeded("private provider deadline detail")


AVAILABLE = SpeechCapability(
    True,
    "speech_available",
    "Voice typing is available.",
)


class ValidationTests(unittest.TestCase):
    def test_accepts_pcm_and_calculates_duration(self) -> None:
        validated = validate_pcm_upload(
            pcm_for(seconds=0.25),
            content_type="Application/Octet-Stream; charset=binary",
            sample_rate_hz=16_000,
            language="en-US",
        )
        self.assertEqual(validated.duration_ms, 250)
        self.assertEqual(validated.language, "en-US")

    def assert_error(
        self,
        expected_code: str,
        *,
        audio: object = b"\x00\x00",
        content_type: str = PCM_CONTENT_TYPE,
        sample_rate_hz: int = 16_000,
        language: str = "en-US",
        expected_status: int = 400,
    ) -> SpeechError:
        with self.assertRaises(SpeechError) as caught:
            validate_pcm_upload(
                audio,
                content_type=content_type,
                sample_rate_hz=sample_rate_hz,
                language=language,
            )
        self.assertEqual(caught.exception.code, expected_code)
        self.assertEqual(caught.exception.http_status, expected_status)
        self.assertFalse(caught.exception.retryable)
        return caught.exception

    def test_rejects_wrong_content_type(self) -> None:
        self.assert_error(
            "unsupported_media_type",
            content_type="audio/webm",
            expected_status=415,
        )

    def test_rejects_sample_rates_outside_supported_range(self) -> None:
        self.assert_error("invalid_sample_rate", sample_rate_hz=7_999)
        self.assert_error("invalid_sample_rate", sample_rate_hz=48_001)
        self.assert_error("invalid_sample_rate", sample_rate_hz=True)

    def test_rejects_invalid_language_tag(self) -> None:
        self.assert_error("invalid_language", language="../../secret")
        self.assert_error("invalid_language", language="")

    def test_rejects_empty_non_bytes_and_incomplete_pcm(self) -> None:
        self.assert_error("empty_audio", audio=b"")
        self.assert_error("invalid_audio", audio="not bytes")
        self.assert_error("invalid_pcm", audio=b"\x00")

    def test_rejects_absolute_byte_limit_before_provider(self) -> None:
        self.assert_error(
            "audio_too_large",
            audio=b"\x00" * (MAX_AUDIO_BYTES + BYTES_PER_SAMPLE),
            sample_rate_hz=48_000,
            expected_status=413,
        )

    def test_rejects_duration_limit_at_lower_sample_rate(self) -> None:
        audio = pcm_for(
            sample_rate=8_000,
            seconds=MAX_DURATION_SECONDS + 0.01,
        )
        self.assert_error(
            "audio_too_long",
            audio=audio,
            sample_rate_hz=8_000,
            expected_status=413,
        )


class CapabilityTests(unittest.TestCase):
    def test_missing_project_is_unavailable(self) -> None:
        capability = detect_capability(environ={})
        self.assertFalse(capability.available)
        self.assertEqual(capability.code, "speech_not_configured")

    def test_injected_client_marks_configured_project_available(self) -> None:
        capability = detect_capability(
            project_id="test-project",
            environ={},
            client_factory=lambda: FakeClient(),
        )
        self.assertTrue(capability.available)
        self.assertEqual(capability.code, "speech_configured")

    def test_failing_capability_probe_is_safe(self) -> None:
        def broken_probe() -> SpeechCapability:
            raise RuntimeError("do not expose this")

        service = SpeechService(
            project_id="test-project",
            client_factory=lambda: FakeClient(),
            capability_probe=broken_probe,
        )
        capability = service.capability()
        self.assertFalse(capability.available)
        self.assertNotIn("do not expose", capability.message)


class TranscriptionTests(unittest.TestCase):
    def make_service(self, client: object) -> SpeechService:
        return SpeechService(
            project_id="test-project",
            model="latest_long",
            client_factory=lambda: client,
            capability_probe=lambda: AVAILABLE,
        )

    def test_builds_google_v2_request_and_joins_segments(self) -> None:
        client = FakeClient(["  A first thought.  ", "Then   another."])
        service = self.make_service(client)

        result = service.transcribe(
            pcm_for(seconds=0.5),
            content_type=PCM_CONTENT_TYPE,
            sample_rate_hz=16_000,
            language="en-US",
        )

        self.assertEqual(result.transcript, "A first thought. Then another.")
        self.assertEqual(result.duration_ms, 500)
        self.assertEqual(result.as_dict()["language"], "en-US")
        self.assertEqual(len(client.requests), 1)

        request = client.requests[0]
        self.assertEqual(
            request["recognizer"],
            "projects/test-project/locations/global/recognizers/_",
        )
        config = request["config"]
        self.assertEqual(config["model"], "latest_long")
        self.assertEqual(config["language_codes"], ["en-US"])
        self.assertEqual(
            config["explicit_decoding_config"],
            {
                "encoding": "LINEAR16",
                "sample_rate_hertz": 16_000,
                "audio_channel_count": 1,
            },
        )
        self.assertEqual(request["content"], pcm_for(seconds=0.5))
        self.assertEqual(request["_timeout"], 60.0)

    def test_unavailable_capability_prevents_provider_call(self) -> None:
        calls = 0

        def client_factory() -> FakeClient:
            nonlocal calls
            calls += 1
            return FakeClient()

        unavailable = SpeechCapability(
            False,
            "speech_not_configured",
            "Voice typing is unavailable.",
        )
        service = SpeechService(
            project_id="test-project",
            client_factory=client_factory,
            capability_probe=lambda: unavailable,
        )

        with self.assertRaises(SpeechError) as caught:
            service.transcribe(
                pcm_for(),
                content_type=PCM_CONTENT_TYPE,
                sample_rate_hz=16_000,
            )
        self.assertEqual(caught.exception.code, "speech_not_configured")
        self.assertEqual(caught.exception.http_status, 503)
        self.assertEqual(calls, 0)

    def test_provider_failure_is_retryable_and_does_not_leak_details(self) -> None:
        service = self.make_service(FailingClient())
        with self.assertRaises(SpeechError) as caught:
            service.transcribe(
                pcm_for(),
                content_type=PCM_CONTENT_TYPE,
                sample_rate_hz=16_000,
            )

        error = caught.exception
        self.assertEqual(error.code, "provider_unavailable")
        self.assertEqual(error.http_status, 503)
        self.assertTrue(error.retryable)
        self.assertNotIn("private credential", str(error))
        self.assertNotIn("private credential", str(error.as_dict()))

    def test_provider_deadline_has_specific_safe_retryable_error(self) -> None:
        service = self.make_service(TimeoutClient())
        with self.assertRaises(SpeechError) as caught:
            service.transcribe(
                pcm_for(),
                content_type=PCM_CONTENT_TYPE,
                sample_rate_hz=16_000,
            )

        error = caught.exception
        self.assertEqual(error.code, "provider_timeout")
        self.assertEqual(error.http_status, 504)
        self.assertTrue(error.retryable)
        self.assertNotIn("private provider", str(error.as_dict()))

    def test_empty_provider_result_is_typed_no_speech_error(self) -> None:
        service = self.make_service(FakeClient([]))
        with self.assertRaises(SpeechError) as caught:
            service.transcribe(
                pcm_for(),
                content_type=PCM_CONTENT_TYPE,
                sample_rate_hz=16_000,
            )

        self.assertEqual(caught.exception.code, "no_speech_detected")
        self.assertEqual(caught.exception.http_status, 422)
        self.assertFalse(caught.exception.retryable)

    def test_validation_happens_before_capability_or_provider(self) -> None:
        probed = False

        def probe() -> SpeechCapability:
            nonlocal probed
            probed = True
            return AVAILABLE

        service = SpeechService(
            project_id="test-project",
            client_factory=lambda: FakeClient(),
            capability_probe=probe,
        )
        with self.assertRaises(SpeechError) as caught:
            service.transcribe(
                b"",
                content_type=PCM_CONTENT_TYPE,
                sample_rate_hz=16_000,
            )
        self.assertEqual(caught.exception.code, "empty_audio")
        self.assertFalse(probed)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
from typing import Any
import unittest

from second_unit.agent import StoryEditor
from second_unit.database import Repository
from second_unit.model import ModelResult, NoModel
from second_unit.outline_agent import OutlineAgent
from second_unit.server import MAX_JSON_BYTES, Application, create_server
from second_unit.service import InterviewService
from second_unit.speech import SpeechCapability, SpeechError, Transcription


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


class FakeSpeechService:
    """In-memory speech boundary used to exercise the same-origin HTTP route."""

    def __init__(self) -> None:
        self.capability_value = SpeechCapability(
            True,
            "speech_available",
            "Voice typing is available in this test.",
        )
        self.error: SpeechError | None = None
        self.requests: list[dict[str, Any]] = []

    def capability(self) -> SpeechCapability:
        return self.capability_value

    def transcribe(
        self,
        audio: object,
        *,
        content_type: str,
        sample_rate_hz: int,
        language: str,
    ) -> Transcription:
        self.requests.append({
            "audio": audio,
            "content_type": content_type,
            "sample_rate_hz": sample_rate_hz,
            "language": language,
        })
        if self.error is not None:
            raise self.error
        return Transcription(
            transcript="Mara waits for the first train home.",
            language=language,
            duration_ms=100,
        )


class BeatModel:
    def decide(self, **_: object) -> ModelResult:
        return ModelResult(
            payload={"beats": [
                {
                    "title": "Discovery",
                    "summary": "Mara sees the empty platform and realizes the train has gone.",
                    "purpose": "Establish the problem.",
                    "source_refs": [],
                },
                {
                    "title": "Choice",
                    "summary": "Mara leaves the station and takes the dark road home.",
                    "purpose": "Turn pressure into action.",
                    "source_refs": [],
                },
                {
                    "title": "Arrival",
                    "summary": "Mara reaches home with a changed understanding of the night.",
                    "purpose": "Complete the visible journey.",
                    "source_refs": [],
                },
            ]},
            backend="fake", model="outline-test",
        )


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

        self.repository = Repository(
            Path(self.temporary.name) / "interview.sqlite3"
        )
        self.service = InterviewService(
            self.repository,
            StoryEditor(NoModel()),
        )
        self.speech = FakeSpeechService()
        self.application = Application(self.service, self.speech, STATIC_DIR)
        self.server = create_server(self.application, "127.0.0.1", 0)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            name="second-unit-http-test",
            daemon=True,
        )
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.host, self.port = self.server.server_address[:2]

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive(), "HTTP test server did not stop")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = HTTPConnection(self.host, self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            response_body = response.read()
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            return response.status, response_headers, response_body
        finally:
            connection.close()

    def request_json(
        self,
        method: str,
        path: str,
        value: object,
        *,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        status, headers, body = self.request(
            method,
            path,
            body=json.dumps(value).encode("utf-8"),
            headers={"Content-Type": content_type},
        )
        return status, headers, json.loads(body)

    def response_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        status, response_headers, response_body = self.request(
            method,
            path,
            body=body,
            headers=headers,
        )
        return status, response_headers, json.loads(response_body)

    def create_interview(self) -> dict[str, Any]:
        status, _, result = self.request_json(
            "POST",
            "/api/interviews",
            {
                "session_id": "interview_http_0001",
                "event_id": "event_start_http_0001",
                "title": "Last Train",
                "seed": "Mara misses the last train home.",
                "storytelling_format": "hybrid",
                "involvement_mode": "collaborative",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["session"]["question_target"], 8)
        return result

    def test_static_index_is_served_with_safe_headers(self) -> None:
        status, headers, body = self.request("GET", "/")

        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertIn(b"Second Unit \xe2\x80\x94 Interview", body)
        self.assertIn(b'id="startForm"', body)
        self.assertNotIn(b'id="questionTarget"', body)
        self.assertIn(b'id="finishInterviewButton"', body)
        self.assertIn(b'id="outlineStageButton"', body)
        self.assertIn(b'id="outlineWorkspace"', body)
        self.assertIn(b'id="outlineFacts"', body)
        self.assertIn(b'id="backToInterview"', body)
        self.assertIn(b'class="outline-source"', body)
        self.assertIn(b'id="outlineStructureArea"', body)
        self.assertIn(b'id="structureProposalForm"', body)
        self.assertIn(b'id="generateBeatsButton"', body)
        self.assertIn(b'id="outlineBeatList"', body)
        self.assertIn(b'id="beatForm"', body)
        self.assertIn(b'id="screenplayStageButton"', body)
        self.assertIn(b'id="screenplayWorkspace"', body)
        self.assertIn(b'id="screenplayModeGrid"', body)
        self.assertIn(b'id="screenplaySceneList"', body)
        self.assertIn(b'id="screenplayApprovalPanel"', body)
        self.assertIn(b'id="breakdownStageButton"', body)
        self.assertIn(b'id="breakdownWorkspace"', body)
        self.assertIn(b'id="generateBreakdownButton"', body)
        self.assertIn(b'id="regenerateBreakdownButton"', body)
        self.assertIn(b'id="locationScoutPanel"', body)
        self.assertIn(b'id="locationAutocomplete"', body)
        self.assertIn(b'id="findLocationsButton"', body)
        self.assertIn(b'id="locationMap"', body)
        self.assertIn(b'id="breakdownSceneList"', body)
        self.assertIn(b'id="breakdownItemForm"', body)

    def test_non_loopback_bind_is_rejected_without_authentication(self) -> None:
        with self.assertRaisesRegex(ValueError, "local application"):
            create_server(object(), "0.0.0.0", 0)  # type: ignore[arg-type]

    def test_health_reports_injected_model_and_speech_capability(self) -> None:
        status, headers, result = self.response_json("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], {
            "backend": "local_fallback",
            "model": "deterministic",
        })
        self.assertEqual(result["speech"]["code"], "speech_available")
        self.assertTrue(result["speech"]["available"])

    def test_list_create_and_read_interview(self) -> None:
        status, _, empty = self.response_json("GET", "/api/interviews")
        self.assertEqual(status, 200)
        self.assertEqual(empty, {"interviews": []})

        created = self.create_interview()
        session_id = created["session"]["id"]
        self.assertEqual(created["session"]["revision"], 1)
        self.assertEqual(created["session"]["title"], "Last Train")
        self.assertEqual(created["timeline"][0]["event"]["kind"], "interview_started")
        self.assertIsNotNone(created["current_response"])
        self.assertTrue(created["agent"]["used_fallback"])
        self.assertEqual(created["current_response"]["intent"], "ask_questions")
        self.assertEqual(len(created["current_response"]["questions"]), 8)

        status, _, listing = self.response_json("GET", "/api/interviews")
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["interviews"]), 1)
        self.assertEqual(listing["interviews"][0]["id"], session_id)

        status, _, loaded = self.response_json(
            "GET", f"/api/interviews/{session_id}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(loaded["session"], created["session"])
        self.assertEqual(loaded["timeline"], created["timeline"])
        self.assertEqual(
            loaded["current_response"]["id"],
            created["current_response"]["id"],
        )

    def test_typed_event_is_processed_without_synthetic_writer_text(self) -> None:
        created = self.create_interview()
        session_id = created["session"]["id"]
        response_id = created["current_response"]["id"]

        status, _, result = self.request_json(
            "POST",
            f"/api/interviews/{session_id}/events",
            {
                "event_id": "event_options_http_0001",
                "expected_revision": 1,
                "kind": "suggestions_requested",
                "payload": {"response_id": response_id},
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(result["session"]["revision"], 2)
        self.assertEqual(result["current_response"]["intent"], "offer_suggestions")
        self.assertEqual(len(result["current_response"]["suggestions"]), 3)
        submitted = result["timeline"][-1]["event"]
        self.assertEqual(submitted["kind"], "suggestions_requested")
        self.assertEqual(submitted["payload"], {"response_id": response_id})
        self.assertNotIn("text", submitted["payload"])

    def test_complete_question_set_is_submitted_as_one_typed_event(self) -> None:
        created = self.create_interview()
        questions = created["current_response"]["questions"]

        status, _, result = self.request_json(
            "POST",
            f"/api/interviews/{created['session']['id']}/events",
            {
                "event_id": "event_questionnaire_http_0001",
                "expected_revision": 1,
                "kind": "questionnaire_submitted",
                "payload": {
                    "response_id": created["current_response"]["id"],
                    "answers": [
                        {
                            "question_id": question["id"],
                            "text": f"Answer {index} grounded in the film.",
                        }
                        for index, question in enumerate(questions, 1)
                    ],
                },
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(result["session"]["revision"], 2)
        self.assertEqual(result["current_response"]["intent"], "coach_writer")
        submitted = result["timeline"][-1]["event"]
        self.assertEqual(submitted["kind"], "questionnaire_submitted")
        self.assertEqual(len(submitted["payload"]["answers"]), 8)

    def test_malformed_json_returns_typed_400(self) -> None:
        status, _, result = self.response_json(
            "POST",
            "/api/interviews",
            body=b'{"seed":',
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(status, 400)
        self.assertEqual(result["error"]["code"], "invalid_json")
        self.assertNotIn("Traceback", result["error"]["message"])

    def test_json_content_type_and_size_are_enforced_at_http_boundary(self) -> None:
        status, _, unsupported = self.response_json(
            "POST",
            "/api/interviews",
            body=b"{}",
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(status, 415)
        self.assertEqual(unsupported["error"]["code"], "unsupported_media_type")

        # Announce the oversized request without streaming a megabyte after the
        # server has already (correctly) rejected Content-Length. This avoids a
        # client-side BrokenPipe race while still testing the HTTP boundary.
        connection = HTTPConnection(self.host, self.port, timeout=3)
        try:
            connection.putrequest("POST", "/api/interviews")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(MAX_JSON_BYTES + 1))
            connection.endheaders()
            response = connection.getresponse()
            oversized = json.loads(response.read())
            self.assertEqual(response.status, 413)
        finally:
            connection.close()
        self.assertEqual(oversized["error"]["code"], "payload_too_large")

    def test_unknown_routes_return_typed_404(self) -> None:
        status, _, result = self.response_json("GET", "/api/not-a-route")

        self.assertEqual(status, 404)
        self.assertEqual(result["error"]["code"], "not_found")

    def test_outline_handoff_read_does_not_create_an_outline(self) -> None:
        created = self.create_interview()
        session_id = created["session"]["id"]

        status, _, result = self.response_json(
            "GET", f"/api/interviews/{session_id}/outline"
        )

        self.assertEqual(status, 200)
        self.assertEqual(result["interview"]["id"], session_id)
        self.assertEqual(len(result["structure_options"]), 6)
        self.assertTrue(all(option["description"] for option in result["structure_options"]))
        self.assertIsNone(result["outline"])
        with self.repository.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM outlines").fetchone()[0]
        self.assertEqual(count, 0)

    def test_filmmaker_can_propose_and_select_an_outline_structure(self) -> None:
        created = self.create_interview()
        session_id = created["session"]["id"]

        status, _, proposed = self.request_json(
            "POST",
            f"/api/interviews/{session_id}/outline/tools/propose_story_structure",
            {
                "expected_revision": 0,
                "kind": "visual_progression",
            },
        )
        self.assertEqual(status, 200)
        outline = proposed["outline"]
        self.assertEqual(outline["revision"], 1)
        self.assertEqual(outline["structures"][0]["origin"], "filmmaker")
        self.assertEqual(outline["structures"][0]["label"], "Visual progression")
        self.assertIn("visible actions", outline["structures"][0]["rationale"])

        status, _, selected = self.request_json(
            "POST",
            f"/api/interviews/{session_id}/outline/tools/select_story_structure",
            {
                "expected_revision": 1,
                "structure_id": outline["structures"][0]["id"],
            },
        )
        self.assertEqual(status, 200)
        selected_outline = selected["outline"]
        self.assertEqual(selected_outline["revision"], 2)
        self.assertEqual(
            selected_outline["active_structure_id"],
            outline["structures"][0]["id"],
        )
        self.assertEqual(
            selected_outline["structures"][0]["approval_status"], "accepted"
        )

    def test_outline_routes_reject_stale_revisions_and_spoofed_identity(self) -> None:
        created = self.create_interview()
        session_id = created["session"]["id"]
        path = f"/api/interviews/{session_id}/outline/tools/propose_story_structure"
        first = {
            "expected_revision": 0,
            "kind": "acts",
            "label": "Two movements",
            "rationale": "A compact turn at the midpoint.",
        }
        status, _, _ = self.request_json("POST", path, first)
        self.assertEqual(status, 200)

        status, _, stale = self.request_json("POST", path, first)
        self.assertEqual(status, 409)
        self.assertEqual(stale["error"]["code"], "stale_outline_revision")
        self.assertEqual(stale["error"]["details"]["current_revision"], 1)

        status, _, spoofed = self.request_json(
            "POST", path, {**first, "expected_revision": 1, "actor": "agent"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(spoofed["error"]["code"], "invalid_outline_tool_input")

        status, _, unknown = self.request_json(
            "POST",
            f"/api/interviews/{session_id}/outline/tools/not_a_tool",
            {"expected_revision": 1},
        )
        self.assertEqual(status, 404)
        self.assertEqual(unknown["error"]["code"], "not_found")

    def test_outline_generation_route_saves_agent_beats(self) -> None:
        created = self.create_interview()
        session_id = created["session"]["id"]
        base = f"/api/interviews/{session_id}/outline"
        status, _, proposed = self.request_json(
            "POST", f"{base}/tools/propose_story_structure",
            {"expected_revision": 0, "kind": "visual_progression"},
        )
        self.assertEqual(status, 200)
        status, _, selected = self.request_json(
            "POST", f"{base}/tools/select_story_structure",
            {
                "expected_revision": 1,
                "structure_id": proposed["outline"]["structures"][0]["id"],
            },
        )
        self.assertEqual(status, 200)
        self.application.outline_agent = OutlineAgent(
            BeatModel(), self.application.outline_tools
        )

        status, _, generated = self.request_json(
            "POST", f"{base}/generate", {"expected_revision": 2}
        )

        self.assertEqual(status, 200)
        self.assertEqual(len(generated["outline"]["beats"]), 3)
        self.assertTrue(all(
            beat["approval_status"] == "proposed"
            for beat in generated["outline"]["beats"]
        ))
        self.assertEqual(generated["agent"]["model"], "outline-test")

    def test_rejects_dns_rebinding_and_cross_origin_requests(self) -> None:
        status, _, rebound = self.response_json(
            "GET",
            "/api/interviews",
            headers={"Host": "attacker.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(rebound["error"]["code"], "local_origin_required")

        status, _, cross_origin = self.response_json(
            "GET",
            "/api/interviews",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": "http://attacker.example",
            },
        )
        self.assertEqual(status, 403)
        self.assertEqual(cross_origin["error"]["code"], "local_origin_required")

        status, _, allowed = self.response_json(
            "GET",
            "/api/interviews",
            headers={
                "Host": f"localhost:{self.port}",
                "Origin": f"http://localhost:{self.port}",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(allowed, {"interviews": []})

    def test_same_origin_speech_transcription_success(self) -> None:
        audio = b"\x01\x00" * 1_600
        status, _, result = self.response_json(
            "POST",
            "/api/speech/transcribe",
            body=audio,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Sample-Rate": "16000",
                "X-Speech-Language": "en-US",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(result, {
            "transcript": "Mara waits for the first train home.",
            "language": "en-US",
            "duration_ms": 100,
        })
        self.assertEqual(len(self.speech.requests), 1)
        self.assertEqual(self.speech.requests[0]["audio"], audio)
        self.assertEqual(self.speech.requests[0]["sample_rate_hz"], 16_000)

    def test_same_origin_speech_error_is_typed_and_safe(self) -> None:
        self.speech.error = SpeechError(
            "provider_unavailable",
            "Voice transcription is temporarily unavailable. Your typed text is safe.",
            http_status=503,
            retryable=True,
        )
        status, _, result = self.response_json(
            "POST",
            "/api/speech/transcribe",
            body=b"\x01\x00" * 800,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Sample-Rate": "16000",
            },
        )

        self.assertEqual(status, 503)
        self.assertEqual(result["error"]["code"], "provider_unavailable")
        self.assertTrue(result["error"]["retryable"])
        self.assertNotIn("credential", json.dumps(result).lower())

    def test_get_requests_do_not_mutate_counts_or_revision(self) -> None:
        created = self.create_interview()
        session_id = created["session"]["id"]
        before_counts = self.repository.counts(session_id)
        before_revision = created["session"]["revision"]

        for path in (
            "/",
            "/api/health",
            "/api/interviews",
            f"/api/interviews/{session_id}",
            f"/api/interviews/{session_id}",
            f"/api/interviews/{session_id}/outline",
        ):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 200, path)

        after_counts = self.repository.counts(session_id)
        loaded = self.service.get_interview(session_id)
        self.assertEqual(after_counts, before_counts)
        self.assertEqual(loaded["session"]["revision"], before_revision)


if __name__ == "__main__":
    unittest.main()

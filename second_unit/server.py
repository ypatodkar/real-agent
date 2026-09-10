"""Threaded local HTTP server for the Interview application."""

from __future__ import annotations

import json
import logging
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Type
from urllib.parse import unquote, urlsplit

from . import APP_VERSION
from .breakdown import BreakdownError, BreakdownRevisionConflict, BreakdownToolkit
from .breakdown_agent import BreakdownAgent
from .database import NotFound
from .locations import LocationError, LocationScoutService
from .outline import (
    STRUCTURE_PRESETS,
    OutlineRevisionConflict,
    OutlineToolError,
    OutlineToolkit,
)
from .outline_agent import OutlineAgent
from .screenplay import ScreenplayError, ScreenplayRevisionConflict, ScreenplayToolkit
from .screenplay_agent import ScreenplayAgent
from .service import InterviewService, ServiceFailure
from .speech import MAX_AUDIO_BYTES, SpeechError, SpeechService


MAX_JSON_BYTES = 256_000
LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}
LOGGER = logging.getLogger("second_unit.server")


@dataclass
class Application:
    service: InterviewService
    speech: SpeechService
    static_dir: Path
    outline_tools: OutlineToolkit | None = None
    outline_agent: OutlineAgent | None = None
    screenplay_tools: ScreenplayToolkit | None = None
    screenplay_agent: ScreenplayAgent | None = None
    breakdown_tools: BreakdownToolkit | None = None
    breakdown_agent: BreakdownAgent | None = None
    location_scout: LocationScoutService | None = None

    def __post_init__(self) -> None:
        if self.outline_tools is None:
            self.outline_tools = OutlineToolkit(self.service.repository)
        if self.outline_agent is None:
            self.outline_agent = OutlineAgent(self.service.editor.model, self.outline_tools)
        if self.screenplay_tools is None:
            self.screenplay_tools = ScreenplayToolkit(self.service.repository)
        if self.screenplay_agent is None:
            self.screenplay_agent = ScreenplayAgent(self.service.editor.model, self.screenplay_tools)
        if self.breakdown_tools is None:
            self.breakdown_tools = BreakdownToolkit(self.service.repository)
        if self.breakdown_agent is None:
            self.breakdown_agent = BreakdownAgent(self.service.editor.model, self.breakdown_tools)
        if self.location_scout is None:
            self.location_scout = LocationScoutService(self.service.repository)

    def health(self) -> dict[str, Any]:
        model = self.service.editor.model
        return {
            "ok": True,
            "version": APP_VERSION,
            "model": {
                "backend": getattr(model, "backend", "unknown"),
                "model": getattr(model, "model", "unknown"),
            },
            "speech": self.speech.capability().as_dict(),
            "maps": {"available": self.location_scout.config()["available"]},
        }


def make_handler(application: Application) -> Type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        app = application

        def do_GET(self) -> None:  # noqa: N802
            if not self._allow_local_request():
                return
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/api/health":
                    self._send_json(self.app.health())
                    return
                if path == "/api/maps/config":
                    self._send_json(self.app.location_scout.config())
                    return
                if path == "/api/interviews":
                    self._send_json({"interviews": self.app.service.list_interviews()})
                    return
                if path.startswith("/api/interviews/"):
                    parts = [part for part in path.split("/") if part]
                    if len(parts) == 4 and parts[3] == "outline":
                        self._send_json(
                            self.app.outline_tools.read_interview_handoff(parts[2])
                        )
                        return
                    if len(parts) == 4 and parts[3] == "screenplay":
                        self._send_json(self.app.screenplay_tools.read_handoff(parts[2]))
                        return
                    if len(parts) == 4 and parts[3] == "breakdown":
                        self._send_json(self.app.breakdown_tools.read_handoff(parts[2]))
                        return
                    if len(parts) == 3:
                        self._send_json(self.app.service.get_interview(parts[2]))
                        return
                    self._send_error(404, "not_found", "API route not found.")
                    return
                self._serve_static(path)
            except ServiceFailure as exc:
                self._send_json(exc.as_dict(), status=exc.http_status)
            except OutlineToolError as exc:
                self._send_outline_error(exc)
            except ScreenplayError as exc:
                self._send_screenplay_error(exc)
            except BreakdownError as exc:
                self._send_breakdown_error(exc)
            except LocationError as exc:
                self._send_json({"error": exc.as_dict()}, status=exc.http_status)
            except NotFound:
                self._send_error(404, "not_found", "Interview not found.")
            except Exception:
                LOGGER.exception("GET request failed path=%s", path)
                self._send_error(500, "server_error", "The local server could not complete this request.")

        def do_POST(self) -> None:  # noqa: N802
            if not self._allow_local_request():
                return
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/api/interviews":
                    result = self.app.service.create_interview(self._read_json())
                    self._send_json(result, status=201)
                    return
                if path.startswith("/api/interviews/") and path.endswith("/events"):
                    parts = [part for part in path.split("/") if part]
                    if len(parts) != 4:
                        self._send_error(404, "not_found", "API route not found.")
                        return
                    result = self.app.service.submit_event(parts[2], self._read_json())
                    self._send_json(result)
                    return
                if path.startswith("/api/interviews/") and path.endswith("/outline/generate"):
                    parts = [part for part in path.split("/") if part]
                    if len(parts) != 5 or parts[3:] != ["outline", "generate"]:
                        self._send_error(404, "not_found", "API route not found.")
                        return
                    arguments = self._read_json()
                    if set(arguments) != {"expected_revision"}:
                        self._send_error(
                            400, "invalid_outline_tool_input",
                            "Beat generation requires only expected_revision.",
                        )
                        return
                    outcome = self.app.outline_agent.generate(
                        parts[2], arguments["expected_revision"]
                    )
                    self._send_json({"outline": outcome.outline, "agent": outcome.meta})
                    return
                if path.startswith("/api/interviews/") and path.endswith("/screenplay/mode"):
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    if len(parts) != 5 or set(arguments) != {"expected_revision", "mode"}:
                        self._send_error(400, "invalid_screenplay_input", "Mode requires expected_revision and mode.")
                        return
                    result = self.app.screenplay_tools.choose_mode(
                        parts[2], arguments["expected_revision"], arguments["mode"]
                    )
                    self._send_json({"screenplay": result})
                    return
                if path.startswith("/api/interviews/") and path.endswith("/screenplay/generate"):
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    if len(parts) != 5 or set(arguments) != {"expected_revision"}:
                        self._send_error(400, "invalid_screenplay_input", "Generation requires expected_revision.")
                        return
                    self._send_json(self.app.screenplay_agent.generate(parts[2], arguments["expected_revision"]))
                    return
                if path.startswith("/api/interviews/") and path.endswith("/screenplay/decision"):
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    if len(parts) != 5 or set(arguments) != {"expected_revision", "decision"}:
                        self._send_error(400, "invalid_screenplay_input", "Decision requires expected_revision and decision.")
                        return
                    result = self.app.screenplay_tools.record_screenplay_decision(
                        parts[2], arguments["expected_revision"], arguments["decision"]
                    )
                    self._send_json({"screenplay": result})
                    return
                if path.startswith("/api/interviews/") and "/screenplay/scenes/" in path:
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    if len(parts) != 6 or parts[3:5] != ["screenplay", "scenes"]:
                        self._send_error(404, "not_found", "API route not found.")
                        return
                    required = {"expected_revision", "heading", "action", "narration", "dialogue"}
                    if set(arguments) != required:
                        self._send_error(400, "invalid_screenplay_input", "Scene edit fields are incomplete.")
                        return
                    result = self.app.screenplay_tools.edit_scene(
                        parts[2], scene_id=parts[5], **arguments
                    )
                    self._send_json({"screenplay": result})
                    return
                if path.startswith("/api/interviews/") and path.endswith("/breakdown/generate"):
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    if len(parts) != 5 or set(arguments) != {"expected_revision"}:
                        self._send_error(400, "invalid_breakdown_input", "Generation requires expected_revision.")
                        return
                    self._send_json(self.app.breakdown_agent.generate(
                        parts[2], arguments["expected_revision"]
                    ))
                    return
                if path.startswith("/api/interviews/") and path.endswith("/breakdown/regenerate"):
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    if len(parts) != 5 or set(arguments) != {"expected_revision"}:
                        self._send_error(400, "invalid_breakdown_input", "Regeneration requires expected_revision.")
                        return
                    self._send_json(self.app.breakdown_agent.generate(
                        parts[2], arguments["expected_revision"], replace_existing=True
                    ))
                    return
                if path.startswith("/api/interviews/") and path.endswith("/breakdown/items"):
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    required = {"expected_revision", "scene_id", "category", "name", "details"}
                    if len(parts) != 5 or set(arguments) != required:
                        self._send_error(400, "invalid_breakdown_input", "Requirement fields are incomplete.")
                        return
                    result = self.app.breakdown_tools.add_item(parts[2], **arguments)
                    self._send_json({"breakdown": result})
                    return
                if path.startswith("/api/interviews/") and "/breakdown/items/" in path:
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    if len(parts) == 7 and parts[3:5] == ["breakdown", "items"] and parts[6] == "archive":
                        if set(arguments) != {"expected_revision"}:
                            self._send_error(400, "invalid_breakdown_input", "Remove requires expected_revision.")
                            return
                        result = self.app.breakdown_tools.archive_item(
                            parts[2], item_id=parts[5], **arguments
                        )
                        self._send_json({"breakdown": result})
                        return
                    required = {"expected_revision", "category", "name", "details"}
                    if len(parts) != 6 or parts[3:5] != ["breakdown", "items"] or set(arguments) != required:
                        self._send_error(400, "invalid_breakdown_input", "Requirement edit fields are incomplete.")
                        return
                    result = self.app.breakdown_tools.edit_item(
                        parts[2], item_id=parts[5], **arguments
                    )
                    self._send_json({"breakdown": result})
                    return
                if path.startswith("/api/interviews/") and path.endswith("/breakdown/decision"):
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    if len(parts) != 5 or set(arguments) != {"expected_revision", "decision"}:
                        self._send_error(400, "invalid_breakdown_input", "Decision requires expected_revision and decision.")
                        return
                    result = self.app.breakdown_tools.record_breakdown_decision(
                        parts[2], arguments["expected_revision"], arguments["decision"]
                    )
                    self._send_json({"breakdown": result})
                    return
                if path.startswith("/api/interviews/") and path.endswith("/locations/search"):
                    parts = [part for part in path.split("/") if part]
                    if len(parts) != 5:
                        self._send_error(404, "not_found", "Location route not found.")
                        return
                    result = self.app.location_scout.search(parts[2], self._read_json())
                    self._send_json({"locations": result})
                    return
                if path.startswith("/api/interviews/") and path.endswith("/locations/shortlist"):
                    parts = [part for part in path.split("/") if part]
                    arguments = self._read_json()
                    required = {"search_id", "place_id", "breakdown_item_id", "shortlisted"}
                    if len(parts) != 5 or set(arguments) != required:
                        self._send_error(400, "invalid_location_input", "Shortlist fields are incomplete.")
                        return
                    result = self.app.location_scout.set_shortlisted(parts[2], **arguments)
                    self._send_json({"location": result})
                    return
                if path.startswith("/api/interviews/") and "/outline/tools/" in path:
                    parts = [part for part in path.split("/") if part]
                    if len(parts) != 6 or parts[3:5] != ["outline", "tools"]:
                        self._send_error(404, "not_found", "API route not found.")
                        return
                    tool_name = parts[5]
                    allowed = {
                        "propose_story_structure", "select_story_structure",
                        "create_beat", "edit_beat", "move_beat",
                        "archive_beat", "record_beat_decision", "record_outline_decision",
                    }
                    if tool_name not in allowed:
                        self._send_error(404, "not_found", "Outline tool route not found.")
                        return
                    arguments = self._read_json()
                    if "session_id" in arguments or "actor" in arguments or "origin" in arguments:
                        self._send_error(
                            400, "invalid_outline_tool_input",
                            "Session and caller identity come from the trusted local route.",
                        )
                        return
                    if tool_name == "propose_story_structure":
                        preset = STRUCTURE_PRESETS.get(arguments.get("kind"))
                        if preset:
                            arguments.setdefault("label", preset["label"])
                            arguments.setdefault("rationale", preset["description"])
                    result = self.app.outline_tools.execute(
                        tool_name, {**arguments, "session_id": parts[2]},
                        caller="filmmaker",
                    )
                    self._send_json({"outline": result})
                    return
                if path == "/api/speech/transcribe":
                    self._transcribe()
                    return
                self._send_error(404, "not_found", "API route not found.")
            except ServiceFailure as exc:
                self._send_json(exc.as_dict(), status=exc.http_status)
            except OutlineToolError as exc:
                self._send_outline_error(exc)
            except ScreenplayError as exc:
                self._send_screenplay_error(exc)
            except BreakdownError as exc:
                self._send_breakdown_error(exc)
            except LocationError as exc:
                self._send_json({"error": exc.as_dict()}, status=exc.http_status)
            except NotFound:
                self._send_error(404, "not_found", "Interview not found.")
            except SpeechError as exc:
                self._send_json(exc.as_dict(), status=exc.http_status)
            except RequestError as exc:
                self._send_error(exc.status, exc.code, str(exc))
            except Exception:
                LOGGER.exception("POST request failed path=%s", path)
                self._send_error(500, "server_error", "The local server could not complete this request.")

        def _allow_local_request(self) -> bool:
            """Reject DNS rebinding and cross-origin access to the local API."""
            host_header = self.headers.get("Host", "")
            try:
                host_url = urlsplit(f"//{host_header}")
                hostname = (host_url.hostname or "").lower()
                host_port = host_url.port
            except ValueError:
                hostname, host_port = "", None
            server_port = int(self.server.server_address[1])
            if hostname not in LOOPBACK_HOSTS or host_port not in {None, server_port}:
                self._send_error(403, "local_origin_required", "This local server accepts only loopback requests.")
                return False

            origin = self.headers.get("Origin")
            if origin:
                try:
                    origin_url = urlsplit(origin)
                    origin_hostname = (origin_url.hostname or "").lower()
                    origin_port = origin_url.port
                except ValueError:
                    origin_url, origin_hostname, origin_port = None, "", None
                if not (
                    origin_url
                    and origin_url.scheme == "http"
                    and origin_hostname in LOOPBACK_HOSTS
                    and origin_port == server_port
                ):
                    self._send_error(403, "local_origin_required", "Cross-origin access to this local server is blocked.")
                    return False
            return True

        def _read_json(self) -> dict[str, Any]:
            media_type = self.headers.get("Content-Type", "").partition(";")[0].lower()
            if media_type != "application/json":
                raise RequestError(415, "unsupported_media_type", "Expected application/json.")
            length = self._content_length(MAX_JSON_BYTES)
            if length == 0:
                raise RequestError(400, "empty_body", "Request body is required.")
            try:
                value = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RequestError(400, "invalid_json", "Request body is not valid JSON.") from exc
            if not isinstance(value, dict):
                raise RequestError(400, "invalid_json", "Request body must be a JSON object.")
            return value

        def _transcribe(self) -> None:
            length = self._content_length(MAX_AUDIO_BYTES)
            sample_rate_raw = self.headers.get("X-Sample-Rate", "")
            try:
                sample_rate = int(sample_rate_raw)
            except ValueError as exc:
                raise RequestError(400, "invalid_sample_rate", "X-Sample-Rate must be an integer.") from exc
            result = self.app.speech.transcribe(
                self.rfile.read(length),
                content_type=self.headers.get("Content-Type", ""),
                sample_rate_hz=sample_rate,
                language=self.headers.get("X-Speech-Language", "en-US"),
            )
            self._send_json(result.as_dict())

        def _content_length(self, limit: int) -> int:
            raw = self.headers.get("Content-Length")
            if raw is None:
                raise RequestError(411, "length_required", "Content-Length is required.")
            try:
                length = int(raw)
            except ValueError as exc:
                raise RequestError(400, "invalid_length", "Content-Length is invalid.") from exc
            if length < 0:
                raise RequestError(400, "invalid_length", "Content-Length is invalid.")
            if length > limit:
                raise RequestError(413, "payload_too_large", "Request body is too large.")
            return length

        def _serve_static(self, path: str) -> None:
            name = "index.html" if path in {"", "/"} else path.lstrip("/")
            if name not in {"index.html", "app.js", "style.css", "pcm-worklet.js"}:
                self._send_error(404, "not_found", "Page not found.")
                return
            target = self.app.static_dir / name
            if not target.is_file():
                self._send_error(404, "not_found", "Application asset not found.")
                return
            body = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            elif target.suffix in {".html", ".css"}:
                content_type += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, value: Any, *, status: int = 200) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, status: int, code: str, message: str) -> None:
            self.close_connection = True
            self._send_json({"error": {"code": code, "message": message}}, status=status)

        def _send_outline_error(self, exc: OutlineToolError) -> None:
            LOGGER.warning(
                "Outline request rejected code=%s message=%s details=%s",
                exc.code, exc.message, exc.details or {},
            )
            if isinstance(exc, OutlineRevisionConflict):
                status = 409
            elif exc.code == "outline_agent_busy":
                status = 409
            elif exc.code in {"outline_not_found", "outline_structure_not_found", "outline_beat_not_found"}:
                status = 404
            elif exc.code in {
                "outline_limit", "outline_structure_required", "outline_structure_in_use",
                "outline_beats_exist", "outline_not_ready", "outline_approved",
                "decided_material_protected",
            }:
                status = 422
            elif exc.code == "outline_agent_unavailable":
                status = 503
            elif exc.code == "invalid_outline_agent_output":
                status = 502
            elif exc.code == "filmmaker_action_required":
                status = 403
            else:
                status = 400
            self._send_json({"error": exc.as_dict()}, status=status)

        def _send_screenplay_error(self, exc: ScreenplayError) -> None:
            if isinstance(exc, ScreenplayRevisionConflict):
                status = 409
            elif exc.code == "screenplay_agent_busy":
                status = 409
            elif exc.code in {"screenplay_not_found", "screenplay_scene_not_found"}:
                status = 404
            elif exc.code in {"screenplay_agent_unavailable"}:
                status = 503
            elif exc.code in {"invalid_screenplay_agent_output"}:
                status = 502
            elif exc.code in {
                "approved_outline_required", "screenplay_mode_required",
                "screenplay_scenes_exist", "screenplay_approved",
                "screenplay_not_ready", "screenplay_in_use",
            }:
                status = 422
            else:
                status = 400
            LOGGER.warning("Screenplay request rejected code=%s message=%s", exc.code, exc.message)
            self._send_json({"error": exc.as_dict()}, status=status)

        def _send_breakdown_error(self, exc: BreakdownError) -> None:
            if isinstance(exc, BreakdownRevisionConflict):
                status = 409
            elif exc.code == "breakdown_agent_busy":
                status = 409
            elif exc.code in {"breakdown_not_found", "breakdown_item_not_found"}:
                status = 404
            elif exc.code in {"breakdown_agent_unavailable"}:
                status = 503
            elif exc.code == "invalid_breakdown_agent_output":
                status = 502
            elif exc.code in {
                "approved_screenplay_required", "breakdown_items_exist",
                "breakdown_not_ready", "breakdown_approved",
            }:
                status = 422
            else:
                status = 400
            LOGGER.warning(
                "Breakdown request rejected code=%s message=%s", exc.code, exc.message
            )
            self._send_json({"error": exc.as_dict()}, status=status)

        def log_message(self, format: str, *args: Any) -> None:
            # Creative text and speech contents never enter access logs. Keep the
            # local terminal quiet except for startup and explicit exceptions.
            return

    return Handler


@dataclass(frozen=True)
class RequestError(Exception):
    status: int
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def create_server(application: Application, host: str, port: int) -> ThreadingHTTPServer:
    if host.lower() not in LOOPBACK_HOSTS:
        raise ValueError(
            "Second Unit is an unauthenticated local application and may bind only to 127.0.0.1 or localhost."
        )
    server = ThreadingHTTPServer((host, port), make_handler(application))
    server.daemon_threads = True
    return server

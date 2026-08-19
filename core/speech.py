"""Low-latency browser microphone transcription through Google Cloud STT V2.

The browser sends mono LINEAR16 frames over a local WebSocket. This module
bridges that stream to Google's bidirectional gRPC API and sends interim and
final transcript events back over the same socket. Audio is never written to
disk.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Iterator

MAX_SECONDS = 120
MAX_FRAME_BYTES = 15_000


def available() -> tuple[bool, str]:
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return False, "GOOGLE_CLOUD_PROJECT is not configured"
    try:
        from google.cloud import speech_v2  # noqa: F401
        from websockets.sync.server import serve  # noqa: F401
    except ImportError:
        return False, "install google-cloud-speech and websockets"
    return True, "configured"


def _send(connection, payload: dict) -> None:
    connection.send(json.dumps(payload))


def transcribe_connection(connection) -> None:
    """Handle one browser audio stream. Never persists received audio."""
    ok, reason = available()
    if not ok:
        _send(connection, {"type": "error", "message": reason})
        return

    from google.cloud import speech_v2
    from google.cloud.speech_v2.types import cloud_speech
    from websockets.exceptions import ConnectionClosed

    try:
        hello = connection.recv(timeout=10)
        if not isinstance(hello, str):
            raise ValueError("audio configuration must be sent before audio")
        config = json.loads(hello)
        if config.get("type") != "start":
            raise ValueError("missing start message")

        sample_rate = int(config.get("sample_rate") or 16_000)
        if not 8_000 <= sample_rate <= 48_000:
            raise ValueError("unsupported microphone sample rate")
        language = str(config.get("language") or "en-US")[:16]

        client = speech_v2.SpeechClient()
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        recognizer = f"projects/{project}/locations/global/recognizers/_"
        decoding = cloud_speech.ExplicitDecodingConfig(
            encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
            audio_channel_count=1,
        )
        recognition = cloud_speech.RecognitionConfig(
            explicit_decoding_config=decoding,
            language_codes=[language],
            model=os.environ.get("SECOND_UNIT_SPEECH_MODEL", "latest_short"),
        )
        streaming = cloud_speech.StreamingRecognitionConfig(
            config=recognition,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                interim_results=True,
            ),
        )

        def requests() -> Iterator[cloud_speech.StreamingRecognizeRequest]:
            yield cloud_speech.StreamingRecognizeRequest(
                recognizer=recognizer,
                streaming_config=streaming,
            )
            audio_bytes = 0
            started = time.monotonic()
            while time.monotonic() - started < MAX_SECONDS:
                try:
                    message = connection.recv(timeout=3)
                except TimeoutError:
                    continue
                if isinstance(message, str):
                    event = json.loads(message)
                    if event.get("type") == "stop":
                        break
                    continue
                if not message:
                    continue
                if len(message) > MAX_FRAME_BYTES:
                    raise ValueError("audio frame exceeds Google streaming limit")
                audio_bytes += len(message)
                if audio_bytes > sample_rate * 2 * MAX_SECONDS:
                    break
                yield cloud_speech.StreamingRecognizeRequest(audio=message)

        _send(connection, {"type": "ready", "max_seconds": MAX_SECONDS})
        responses = client.streaming_recognize(requests=requests())
        for response in responses:
            for result in response.results:
                if not result.alternatives:
                    continue
                _send(connection, {
                    "type": "final" if result.is_final else "interim",
                    "text": result.alternatives[0].transcript,
                    "stability": float(result.stability or 0),
                })
        _send(connection, {"type": "done"})
    except ConnectionClosed:
        return
    except Exception as exc:
        try:
            _send(connection, {"type": "error", "message": str(exc)[:180]})
        except Exception:
            pass


def start_server(port: int) -> bool:
    """Start the WebSocket bridge beside the standard-library HTTP server."""
    ok, _ = available()
    if not ok:
        return False

    from websockets.sync.server import serve

    def run() -> None:
        with serve(
            transcribe_connection,
            "127.0.0.1",
            port,
            compression=None,
            max_size=MAX_FRAME_BYTES,
            max_queue=64,
        ) as server:
            server.serve_forever()

    threading.Thread(target=run, name="speech-websocket", daemon=True).start()
    return True

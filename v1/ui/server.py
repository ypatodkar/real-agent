"""A small HTTP server for the UI. Standard library only — no new dependencies.

Runs the pipeline on a background thread and exposes four endpoints:

    POST /api/run              {topic, involvement}  -> {run_id}
    GET  /api/run/<id>                               -> progress, pause, result
    POST /api/run/<id>/resume  {edits}               -> continue past a gate
    GET  /out/<id>.mp4                               -> the finished video

Progress comes from tailing the trajectory the recorder already writes, so the
harness needs no callback hooks to be observable — the run's own audit log is
the progress feed.

    python3 ui/server.py     then open http://localhost:8000
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import traceback
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.budget import Ledger  # noqa: E402
from harness.node import Aborted  # noqa: E402
from harness.recorder import Recorder, new_run_id  # noqa: E402
from harness.state import new_production, total_spend  # noqa: E402
from pipeline.graph import build  # noqa: E402

UI = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "out"
RUNS = ROOT / "runs"

STAGE_LABELS = [
    ("brief", "Brief"), ("gate1", "Gate 1"), ("scoring_select", "Score"),
    ("outline", "Outline"), ("research", "Research"), ("script", "Script"),
    ("qc_plan", "QC · plan"), ("gate2", "Gate 2"), ("casting", "Casting"),
    ("voice", "Voice"), ("envelope", "Envelope"), ("compositor", "Composite"),
    ("qc_render", "QC · render"),
]


class Session:
    """One run. Owns the generator so a gate can suspend mid-pipeline."""

    def __init__(self, topic: str, involvement: int):
        self.run_id = new_run_id()
        self.topic = topic
        self.involvement = involvement
        self.ledger = Ledger()
        self.recorder = Recorder(self.run_id)
        self.status = "running"
        self.pause = None
        self.final: dict | None = None
        self.error: str | None = None
        self._resume: dict | None = None
        self._ready = threading.Event()
        threading.Thread(target=self._drive, daemon=True).start()

    def _drive(self) -> None:
        try:
            app = build(self.run_id, self.ledger, self.recorder)
            gen = app.run(new_production(self.topic, self.involvement, self.run_id))
            reply = None
            while True:
                try:
                    pause = gen.send(reply)
                except StopIteration as stop:
                    self.final = dict(stop.value or {})
                    self.status = "done"
                    return

                self.pause = {"gate": pause.gate, "proposal": pause.proposal,
                              "editable": pause.editable}
                self.status = "paused"
                self._ready.clear()
                self._ready.wait()
                reply, self.pause, self.status = self._resume, None, "running"
        except Aborted as exc:
            self.error, self.status = f"aborted — {exc}", "error"
        except Exception:
            self.error, self.status = traceback.format_exc(limit=3), "error"

    def resume(self, edits: dict) -> None:
        self._resume = edits or {}
        self._ready.set()

    # --- progress, read from the trajectory the recorder is already writing

    def progress(self) -> list[dict]:
        path = RUNS / self.run_id / "trajectory.jsonl"
        started, ended = set(), {}
        if path.exists():
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row["kind"] == "stage_start":
                    started.add(row["stage"])
                elif row["kind"] == "stage_end":
                    ended[row["stage"]] = row

        # Gates are resolved by the runner, not by stage(), so they never appear
        # in the trajectory. Infer them from position: a gate is done once
        # anything after it has started.
        order = [n for n, _ in STAGE_LABELS]
        furthest = max((order.index(n) for n in started if n in order), default=-1)

        out = []
        for idx, (name, label) in enumerate(STAGE_LABELS):
            is_gate = name in ("gate1", "gate2")
            if self.pause and self.pause["gate"] == name:
                state = "waiting"
            elif name in ended or (is_gate and idx < furthest):
                state = "done"
            elif name in started:
                state = "active"
            else:
                state = "pending"
            out.append({"id": name, "label": label, "state": state,
                        "cost": ended.get(name, {}).get("cost", 0.0),
                        "ms": ended.get(name, {}).get("ms")})
        return out

    def snapshot(self) -> dict:
        final = self.final or {}
        video = OUT / f"{self.run_id}.mp4"
        return {
            "run_id": self.run_id,
            "status": self.status,
            "error": self.error,
            "stages": self.progress(),
            "pause": self.pause,
            "log": final.get("log", []),
            "outcome": final.get("outcome"),
            "rounds": final.get("repair_round", 0),
            "violations": final.get("violations", []),
            "applicable": final.get("applicable_rules", []),
            "brief": final.get("brief"),
            "script": final.get("script", {}).get("lines"),
            "spend": total_spend(final) if final else round(self.ledger.spent, 6),
            "cap": self.ledger.run_total,
            "video": f"/out/{self.run_id}.mp4" if video.exists() else None,
        }


SESSIONS: dict[str, Session] = {}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI), **kw)

    def log_message(self, *a):  # quiet
        pass

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path.startswith("/api/run/"):
            session = SESSIONS.get(path.split("/")[3])
            return self._json(session.snapshot() if session else {"error": "unknown run"},
                              200 if session else 404)

        if path.startswith("/out/"):
            target = OUT / pathlib.Path(path).name
            if not target.exists():
                return self._json({"error": "not found"}, 404)
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges", "none")
            self.end_headers()
            return self.wfile.write(data)

        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        size = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(size) or b"{}")

        if path == "/api/run":
            topic = (payload.get("topic") or "").strip()
            if not topic:
                return self._json({"error": "topic is required"}, 400)
            session = Session(topic, int(payload.get("involvement", 0)))
            SESSIONS[session.run_id] = session
            return self._json({"run_id": session.run_id})

        if path.startswith("/api/run/") and path.endswith("/resume"):
            session = SESSIONS.get(path.split("/")[3])
            if not session:
                return self._json({"error": "unknown run"}, 404)
            session.resume(payload.get("edits") or {})
            return self._json({"ok": True})

        return self._json({"error": "not found"}, 404)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Second Unit  ->  http://localhost:{port}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()

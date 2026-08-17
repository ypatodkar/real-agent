"""HTTP server for Second Unit. Standard library only.

    POST /api/project                  {seed}      -> project, first question
    GET  /api/project/<id>                         -> current state
    POST /api/project/<id>/answer      {text}      -> records it, asks the next
    POST /api/project/<id>/skip                    -> moves on, records the skip
    GET  /api/health                               -> model + Grafana status

Projects are JSON files under store/. No database — a short film's development
is a few kilobytes, and a file you can open in a text editor is easier to trust
than a schema migration.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import traceback
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import interview, scriptwright, telemetry  # noqa: E402
from core.model import get_client, load_dotenv  # noqa: E402

load_dotenv()

STATIC = ROOT / "app" / "static"
STORE = ROOT / "store"
STORE.mkdir(exist_ok=True)

MODEL = get_client(verbose=False)


# ----------------------------------------------------------------- storage


def path_for(project_id: str) -> pathlib.Path:
    safe = "".join(c for c in project_id if c.isalnum() or c in "-_")
    return STORE / f"{safe}.json"


def load(project_id: str) -> interview.Session | None:
    path = path_for(project_id)
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    session = interview.Session(
        project_id=raw["project_id"], seed=raw.get("seed", ""),
        stage=raw.get("stage", "premise"),
    )
    session.turns = [interview.Turn(**t) for t in raw.get("turns", [])]
    return session


def save(session: interview.Session) -> None:
    path_for(session.project_id).write_text(json.dumps({
        "project_id": session.project_id,
        "seed": session.seed,
        "stage": session.stage,
        "turns": [t.__dict__ for t in session.turns],
        "updated": time.time(),
    }, indent=2))


# ----------------------------------------------------------------- the ask


def next_question(session: interview.Session) -> dict:
    """Choose a shape, have the model word it, reject anything that leaks.

    The ranking comes from Grafana at runtime. When it is empty — early on, or
    if the read fails — the shapes fall back to declaration order, which is
    roughly how a script editor would work through them anyway.
    """
    ranking = telemetry.rank_shapes_sync(session.stage)

    last_error = None
    for _ in range(3):                      # a leak is worth one more try
        shape = interview.choose_shape(session, ranking)
        response = MODEL.generate(
            interview.build_prompt(session, shape),
            system=interview.SYSTEM,
            json_out=True,
            schema=interview.QUESTION_SCHEMA,
            temperature=0.9,                # questions should not be samey
            stub={"question": "What is this film about, underneath the plot?",
                  "listening_for": shape.probes},
        )
        try:
            question, listening = interview.parse(response.json(), shape)
        except (ValueError, TypeError) as exc:
            last_error = str(exc)
            session.turns.append(interview.Turn(shape.id, session.stage, "", skipped=True))
            continue

        session.turns.pop() if session.turns and not session.turns[-1].question else None
        turn = interview.Turn(shape.id, session.stage, question)
        session.turns.append(turn)
        return {"question": question, "listening_for": listening,
                "shape": shape.id, "ranked": bool(ranking)}

    return {"question": "What is the film about when it is not about its plot?",
            "listening_for": "the real subject", "shape": "under_the_pitch",
            "ranked": False, "note": f"model kept proposing answers: {last_error}"}


def write_up(session: interview.Session) -> dict:
    """Assemble the interview into a scene outline."""
    response = MODEL.generate(
        scriptwright.build_prompt(session),
        system=scriptwright.SYSTEM,
        json_out=True,
        schema=scriptwright.SCENES_SCHEMA,
        temperature=0.4,                 # assembling, not inventing
        stub={"title": "Untitled", "logline": "", "scenes": [], "gaps": []},
    )
    outline = scriptwright.parse(response.json(), session)
    path_for(session.project_id + "-outline").write_text(
        json.dumps(outline.to_dict(), indent=2))
    return outline.to_dict()


def state_of(session: interview.Session, extra: dict | None = None) -> dict:
    answered = [t for t in session.turns if t.answer and not t.skipped]
    current = session.turns[-1] if session.turns else None
    ready, note = scriptwright.readiness(session)
    return {
        "can_write_up": ready,
        "write_up_note": note,
        "project_id": session.project_id,
        "seed": session.seed,
        "stage": session.stage,
        "stages": interview.STAGES,
        "goal": interview.STAGE_GOAL[session.stage],
        "question": current.question if current and not current.answer else None,
        "answered": len(answered),
        "words": sum(t.words for t in answered),
        "established": [{"q": t.question, "a": t.answer} for t in answered],
        **(extra or {}),
    }


# ----------------------------------------------------------------- handler


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def log_message(self, *a):
        pass

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/health":
            loki = telemetry._loki_config()
            return self._send({
                "model": MODEL.backend,
                "grafana_read": bool(telemetry.rank_shapes_sync("premise")) or "connected",
                "grafana_write": "configured" if loki else "not configured",
            })

        if path.startswith("/api/project/"):
            session = load(path.split("/")[3])
            return self._send(state_of(session) if session else {"error": "not found"},
                              200 if session else 404)

        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        size = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(size) or b"{}")

        try:
            if path == "/api/project":
                session = interview.Session(
                    project_id=f"p{int(time.time())}",
                    seed=(body.get("seed") or "").strip(),
                )
                asked = next_question(session)
                save(session)
                return self._send(state_of(session, asked))

            parts = path.split("/")
            session = load(parts[3]) if len(parts) > 3 else None
            if session is None:
                return self._send({"error": "not found"}, 404)

            if path.endswith("/answer"):
                text = (body.get("text") or "").strip()
                seconds = float(body.get("seconds") or 0)
                if session.turns and not session.turns[-1].answer:
                    turn = session.turns[-1]
                    turn.answer = text
                    turn.words = len(text.split())
                    telemetry.record(telemetry.Outcome(
                        turn.shape_id, turn.stage, session.project_id,
                        words=turn.words, seconds=seconds))
                moved = session.advance_if_ready()
                asked = next_question(session)
                save(session)
                return self._send(state_of(session, {**asked, "stage_changed": moved}))

            if path.endswith("/write-up"):
                ready, note = scriptwright.readiness(session)
                if not ready:
                    return self._send({"error": note}, 400)
                return self._send({**state_of(session), "outline": write_up(session)})

            if path.endswith("/skip"):
                if session.turns and not session.turns[-1].answer:
                    turn = session.turns[-1]
                    turn.skipped = True
                    telemetry.record(telemetry.Outcome(
                        turn.shape_id, turn.stage, session.project_id,
                        words=0, skipped=True))
                asked = next_question(session)
                save(session)
                return self._send(state_of(session, asked))

        except Exception:
            traceback.print_exc()
            return self._send({"error": "something broke — check the server log"}, 500)

        return self._send({"error": "not found"}, 404)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Second Unit  ->  http://localhost:{port}")
    print(f"  model    {MODEL.backend}")
    print(f"  grafana  write {'on' if telemetry._loki_config() else 'off'}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()

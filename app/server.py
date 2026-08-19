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
import sqlite3
import sys
import time
import traceback
import uuid
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import interview, scriptwright, speech, telemetry  # noqa: E402
from core.model import ProviderError, get_client, load_dotenv  # noqa: E402

load_dotenv()

STATIC = ROOT / "app" / "static"
STORE = ROOT / "store"
STORE.mkdir(exist_ok=True)
DATABASE = STORE / "second_unit.sqlite3"

MODEL = get_client(verbose=False)
SPEECH_PORT: int | None = None
SPEECH_ON = False


def init_database() -> None:
    with sqlite3.connect(DATABASE) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                seed TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                readiness REAL NOT NULL,
                readiness_reason TEXT NOT NULL,
                established_json TEXT NOT NULL,
                critical_gaps_json TEXT NOT NULL,
                transcript_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)


init_database()


DRAFT_SYSTEM = """You are closing a short-film development session and saving
an accurate draft for its writer. Reassess the complete conversation and
summarize only what the writer established. Do not invent, complete, improve,
or suggest story content. Unresolved decisions belong in critical_gaps.

Return JSON with: title, summary, established, critical_gaps, readiness, reason.
Readiness is from 0 to 1 and measures whether the material can support an
honest 4–9 scene outline without invention."""

DRAFT_SCHEMA = {
    "type": "object",
    "required": ["title", "summary", "established", "critical_gaps", "readiness", "reason"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "established": {"type": "array", "items": {"type": "string"}},
        "critical_gaps": {"type": "array", "items": {"type": "string"}},
        "readiness": {"type": "number"},
        "reason": {"type": "string"},
    },
}


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
        involvement=int(raw.get("involvement", 75)),
        readiness=raw.get("readiness", 0.0),
        readiness_reason=raw.get("readiness_reason", "The story has not been assessed yet."),
        critical_gaps=raw.get("critical_gaps", []),
        established=raw.get("established", []),
        ready_streak=raw.get("ready_streak", 0),
        complete=raw.get("complete", False),
    )
    session.turns = [interview.Turn(**t) for t in raw.get("turns", [])]
    # Older sessions were marked complete after 15 answers regardless of the
    # story assessment. Reopen those false completions during migration.
    if "involvement" not in raw and session.complete and session.readiness < interview.READY_THRESHOLD:
        session.complete = False
    return session


def save(session: interview.Session) -> None:
    path_for(session.project_id).write_text(json.dumps({
        "project_id": session.project_id,
        "seed": session.seed,
        "involvement": session.involvement,
        "turns": [t.__dict__ for t in session.turns],
        "readiness": session.readiness,
        "readiness_reason": session.readiness_reason,
        "critical_gaps": session.critical_gaps,
        "established": session.established,
        "ready_streak": session.ready_streak,
        "complete": session.complete,
        "updated": time.time(),
    }, indent=2))


def draft_fallback(session: interview.Session) -> dict:
    answered = session.answered()
    material = session.established or [t.answer for t in answered]
    summary = " ".join(material).strip()
    return {
        "title": (session.seed[:72].strip() or "Untitled draft"),
        "summary": summary or "No story material was established before the session ended.",
        "established": material,
        "critical_gaps": session.critical_gaps,
        "readiness": session.readiness,
        "reason": session.readiness_reason,
    }


def summarize_draft(session: interview.Session) -> dict:
    """Reassess and summarize a closing session without adding story content."""
    fallback = draft_fallback(session)
    prompt = scriptwright.build_prompt(session) + "\n\nSave an accurate draft of this unfinished work."
    try:
        response = MODEL.generate(
            prompt,
            system=DRAFT_SYSTEM,
            json_out=True,
            schema=DRAFT_SCHEMA,
            temperature=0.2,
            stub=fallback,
        )
        payload = response.json()
        readiness = max(0.0, min(1.0, float(payload.get("readiness", session.readiness))))
        return {
            "title": str(payload.get("title") or fallback["title"]).strip(),
            "summary": str(payload.get("summary") or fallback["summary"]).strip(),
            "established": [str(x).strip() for x in payload.get("established", []) if str(x).strip()],
            "critical_gaps": [str(x).strip() for x in payload.get("critical_gaps", []) if str(x).strip()],
            "readiness": readiness,
            "reason": str(payload.get("reason") or fallback["reason"]).strip(),
        }
    except (ProviderError, ValueError, TypeError):
        return fallback


def archive_draft(session: interview.Session) -> dict:
    summary = summarize_draft(session)
    draft_id = f"d_{uuid.uuid4().hex}"
    transcript = [t.__dict__ for t in session.turns]
    with sqlite3.connect(DATABASE) as db:
        db.execute("""
            INSERT INTO drafts (
                id, project_id, seed, title, summary, readiness,
                readiness_reason, established_json, critical_gaps_json,
                transcript_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            draft_id, session.project_id, session.seed, summary["title"],
            summary["summary"], summary["readiness"], summary["reason"],
            json.dumps(summary["established"]),
            json.dumps(summary["critical_gaps"]), json.dumps(transcript), time.time(),
        ))
    return {"draft_id": draft_id, **summary}


def list_drafts() -> list[dict]:
    with sqlite3.connect(DATABASE) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT id, project_id, title, summary, readiness, readiness_reason,
                   created_at FROM drafts ORDER BY created_at DESC
        """).fetchall()
    return [dict(row) for row in rows]


def list_history() -> list[dict]:
    """Completed outlines and archived drafts, newest first."""
    items = [
        {
            "id": row["id"],
            "kind": "draft",
            "title": row["title"],
            "summary": row["summary"],
            "readiness": row["readiness"],
            "created_at": row["created_at"],
        }
        for row in list_drafts()
    ]
    for outline_path in STORE.glob("*-outline.json"):
        project_id = outline_path.stem.removesuffix("-outline")
        try:
            outline = json.loads(outline_path.read_text())
            project_path = path_for(project_id)
            project = json.loads(project_path.read_text()) if project_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            continue
        items.append({
            "id": project_id,
            "kind": "outline",
            "title": outline.get("title") or "Untitled",
            "summary": outline.get("logline") or project.get("seed", ""),
            "readiness": project.get("readiness", 0),
            "created_at": outline_path.stat().st_mtime,
        })
    return sorted(items, key=lambda item: item["created_at"], reverse=True)


def history_detail(kind: str, item_id: str) -> dict | None:
    if kind == "draft":
        with sqlite3.connect(DATABASE) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM drafts WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        return {
            "id": data["id"], "kind": "draft", "title": data["title"],
            "seed": data["seed"], "summary": data["summary"],
            "readiness": data["readiness"], "reason": data["readiness_reason"],
            "established": json.loads(data["established_json"]),
            "critical_gaps": json.loads(data["critical_gaps_json"]),
            "transcript": json.loads(data["transcript_json"]),
            "created_at": data["created_at"],
        }
    if kind == "outline":
        outline_path = path_for(item_id + "-outline")
        project_path = path_for(item_id)
        if not outline_path.exists():
            return None
        try:
            outline = json.loads(outline_path.read_text())
            project = json.loads(project_path.read_text()) if project_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return None
        return {
            "id": item_id, "kind": "outline",
            "title": outline.get("title") or "Untitled",
            "seed": project.get("seed", ""), "summary": outline.get("logline", ""),
            "readiness": project.get("readiness", 0),
            "reason": project.get("readiness_reason", ""),
            "established": project.get("established", []),
            "critical_gaps": outline.get("gaps", []),
            "transcript": project.get("turns", []), "outline": outline,
            "created_at": outline_path.stat().st_mtime,
        }
    return None


# ----------------------------------------------------------------- the ask


def next_question(session: interview.Session, force: bool = False) -> dict:
    """Reassess the conversation and ask its highest-impact next question."""
    if session.involvement_ceiling_reached() and not force:
        session.complete = True
        session.readiness_reason = (
            "The shorter interview has reached its safety limit. The agent can "
            "develop or expose the remaining gaps according to your chosen involvement."
        )
        return {"question": None, "ranked": False, "interview_complete": True}

    ranking = telemetry.rank_shapes_sync("dynamic")

    last_error = None
    for _ in range(3):                      # a leak is worth one more try
        shape = interview.choose_shape(session, ranking)
        try:
            response = MODEL.generate(
                interview.build_prompt(session, ranking),
                system=interview.SYSTEM,
                json_out=True,
                schema=interview.QUESTION_SCHEMA,
                temperature=0.9,                # questions should not be samey
                stub={
                    "question": interview.fallback_question(shape),
                    "listening_for": shape.probes,
                    "focus": shape.id,
                    "established": session.established,
                    "critical_gaps": ["The story still needs more specific material."],
                    "readiness": session.readiness,
                    "reason": "More specific material is needed before outlining.",
                    "should_continue": True,
                },
            )
        except ProviderError as exc:
            # Asking can continue safely without the model: every shape has a
            # hand-written, rule-compliant calibration question. Preserve the
            # chosen shape so telemetry still learns from the answer.
            question = interview.fallback_question(shape)
            turn = interview.Turn(shape.id, "dynamic", question)
            session.turns.append(turn)
            return {
                "question": question,
                "listening_for": shape.probes,
                "shape": shape.id,
                "ranked": bool(ranking),
                "degraded": True,
                "note": "Vertex is temporarily busy; using a built-in question.",
            }
        try:
            assessment = interview.parse(response.json())
        except (ValueError, TypeError) as exc:
            last_error = str(exc)
            continue

        session.apply_assessment(assessment)
        if session.complete and not force:
            return {"question": None, "focus": assessment["focus"],
                    "listening_for": assessment["listening_for"],
                    "ranked": bool(ranking), "interview_complete": True}

        question = assessment["question"]
        if not question:  # writer explicitly requested another question
            question = interview.fallback_question(shape)
        turn = interview.Turn(assessment["focus"], "dynamic", question)
        session.turns.append(turn)
        return {"question": question, "listening_for": assessment["listening_for"],
                "focus": assessment["focus"], "ranked": bool(ranking),
                "interview_complete": False}

    shape = interview.choose_shape(session, ranking)
    question = interview.fallback_question(shape)
    session.turns.append(interview.Turn(shape.id, "dynamic", question))
    return {"question": question, "listening_for": shape.probes,
            "focus": shape.id, "ranked": False, "degraded": True,
            "note": f"using a built-in question after invalid model output: {last_error}"}


def write_up(session: interview.Session) -> dict:
    """Assemble the interview into a scene outline."""
    response = MODEL.generate(
        scriptwright.build_prompt(session),
        system=scriptwright.system_for(session),
        json_out=True,
        schema=scriptwright.SCENES_SCHEMA,
        temperature=0.4,                 # assembling, not inventing
        stub={"title": "Untitled", "logline": "", "scenes": [], "gaps": [],
              "ai_added": []},
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
        "involvement": session.involvement,
        "collaboration_mode": session.collaboration_mode,
        "goal": session.readiness_reason or "Finding what this story needs next",
        "question": current.question if current and not current.answer else None,
        "answered": len(answered),
        "words": sum(t.words for t in answered),
        "readiness": session.readiness,
        "readiness_percent": round(session.readiness * 100),
        "readiness_reason": session.readiness_reason,
        "critical_gaps": session.critical_gaps,
        "interview_complete": session.complete,
        "established": session.established or [{"q": t.question, "a": t.answer} for t in answered],
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
                "grafana_read": bool(telemetry.rank_shapes_sync("dynamic")) or "connected",
                "grafana_write": "configured" if loki else "not configured",
                "speech": "configured" if SPEECH_ON else speech.available()[1],
                "speech_ws_port": SPEECH_PORT if SPEECH_ON else None,
            })

        if path == "/api/drafts":
            return self._send({"drafts": list_drafts()})

        if path == "/api/history":
            return self._send({"history": list_history()})

        if path.startswith("/api/history/"):
            parts = path.split("/")
            detail = history_detail(parts[3], parts[4]) if len(parts) == 5 else None
            return self._send(detail if detail else {"error": "not found"},
                              200 if detail else 404)

        if path.startswith("/api/project/"):
            session = load(path.split("/")[3])
            if session and not session.complete and (
                not session.turns or session.turns[-1].answer or session.turns[-1].skipped
            ):
                asked = next_question(session)
                save(session)
                return self._send(state_of(session, asked))
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
                    project_id=f"p_{uuid.uuid4().hex}",
                    seed=(body.get("seed") or "").strip(),
                    involvement=max(0, min(100, int(body.get("involvement", 75)))),
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
                asked = next_question(session)
                save(session)
                return self._send(state_of(session, asked))

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

            if path.endswith("/continue"):
                session.complete = False
                session.ready_streak = 0
                asked = next_question(session, force=True)
                save(session)
                return self._send(state_of(session, asked))

            if path.endswith("/abort"):
                draft = archive_draft(session)
                return self._send({"saved": True, "draft": draft})

        except Exception:
            traceback.print_exc()
            return self._send({"error": "something broke — check the server log"}, 500)

        return self._send({"error": "not found"}, 404)


def main() -> None:
    global SPEECH_ON, SPEECH_PORT
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    SPEECH_PORT = port + 1
    SPEECH_ON = speech.start_server(SPEECH_PORT)
    print(f"Second Unit  ->  http://localhost:{port}")
    print(f"  model    {MODEL.backend}")
    print(f"  grafana  write {'on' if telemetry._loki_config() else 'off'}")
    print(f"  speech   {'ws://localhost:' + str(SPEECH_PORT) if SPEECH_ON else 'off'}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()

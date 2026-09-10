"""The only context the model sees. Assembled from SQLite, never from memory.

The model spends no call discovering the current story: facts, gaps, recent
turns, suggestion states, readiness history and any contradictions this answer
raises are all here before the first token is generated.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import continuity
import store
from models import Event

RECENT_TURNS = 5
STUCK_PATTERNS = re.compile(
    r"^\s*(idk|dunno|i don'?t know|not sure|no idea|you decide|you choose|"
    r"i'?m not sure|no clue)\b", re.I)
STUCK_RUN = 3


def looks_stuck(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    return bool(STUCK_PATTERNS.match(stripped)) and len(stripped.split()) <= 12


@dataclass
class Snapshot:
    session: dict
    event: Event
    revision: int
    facts: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    turns: list = field(default_factory=list)
    recent_questions: list = field(default_factory=list)
    recent_intents: list = field(default_factory=list)
    suggestions_open: list = field(default_factory=list)
    readiness: dict | None = None
    conflicts: list = field(default_factory=list)
    answered_count: int = 0
    stuck: bool = False
    latest_answer_text: str = ""

    @property
    def has_writer_input(self) -> bool:
        """Something of the writer's own to reflect back. Choosing an idea counts;
        it is a contribution, not a passive click."""
        return bool(self.latest_answer_text) or bool(
            self.event.payload.get("suggestion_ids"))

    @property
    def involvement_mode(self) -> str:
        involvement = self.session["involvement"]
        if involvement <= 33:
            return "ai_led"
        return "collaborative" if involvement <= 66 else "author_led"


def build(db, session_id: str, event: Event) -> Snapshot:
    row = store.session(db, session_id)
    facts = store.active_facts(db, session_id)
    latest = store.latest_response(db, session_id)

    answers = _recent_answer_texts(db, session_id)
    latest_text = event.text
    stuck = looks_stuck(latest_text) and sum(
        1 for a in answers[-(STUCK_RUN - 1):] if looks_stuck(a)) >= STUCK_RUN - 1

    readiness_rows = store.readiness_history(db, session_id, limit=1)
    readiness = None
    if readiness_rows:
        r = readiness_rows[0]
        readiness = {"ready": bool(r["ready"]), "reason": r["reason"],
                     "lenses": json.loads(r["lenses_json"] or "{}")}

    return Snapshot(
        session=dict(row),
        event=event,
        revision=row["revision"],
        facts=[dict(f) for f in facts],
        gaps=[dict(g) for g in store.open_gaps(db, session_id)],
        turns=[dict(t) for t in store.turns(db, session_id)][-RECENT_TURNS:],
        recent_questions=store.recent_questions(db, session_id),
        recent_intents=store.recent_intents(db, session_id),
        suggestions_open=[dict(s) for s in store.suggestions_for(db, latest["id"])]
                          if latest else [],
        readiness=readiness,
        conflicts=continuity.conflicts(latest_text, facts),
        answered_count=store.answered_count(db, session_id),
        stuck=stuck,
        latest_answer_text=latest_text,
    )


def _recent_answer_texts(db, session_id: str, limit: int = 4) -> list[str]:
    rows = db.execute(
        "SELECT payload_json FROM interview_events WHERE session_id = ?"
        " AND kind = 'message_submitted' ORDER BY created_at DESC LIMIT ?",
        (session_id, limit)).fetchall()
    out = []
    for r in rows:
        try:
            out.append(str(json.loads(r["payload_json"]).get("text") or ""))
        except json.JSONDecodeError:
            out.append("")
    return list(reversed(out))

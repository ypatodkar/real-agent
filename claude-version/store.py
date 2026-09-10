"""The only module that writes SQL.

Two short transactions per turn, with the model call between them:

    TX1  ingest   the event and a pending run, guarded by an idempotency key
    ...            snapshot, model, validation — no transaction held open
    TX2  publish  revision check, then response + facts + gaps + readiness
                  + run result, together, and the revision increments

Nothing half-applied ever becomes visible. If the process dies between the two,
the event is still pending and replaying it returns the same committed answer.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from models import Event, Response, TurnDecision, new_id, now

DB_PATH = Path(__file__).parent / "interview.sqlite3"
SCHEMA = Path(__file__).parent / "schema.sql"


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)   # explicit transactions
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(path: Path | str = DB_PATH) -> None:
    db = connect(path)
    try:
        db.executescript(SCHEMA.read_text())
    finally:
        db.close()


# ------------------------------------------------------------------ sessions


def create_session(db, seed: str, storytelling_format: str, involvement: int) -> str:
    sid, stamp = new_id("s"), now()
    db.execute(
        "INSERT INTO interview_sessions (id, seed, storytelling_format, involvement,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, seed.strip(), storytelling_format, max(0, min(100, involvement)), stamp, stamp))
    return sid


def session(db, session_id: str):
    return db.execute("SELECT * FROM interview_sessions WHERE id = ?", (session_id,)).fetchone()


def set_status(db, session_id: str, status: str) -> None:
    db.execute("UPDATE interview_sessions SET status = ?, updated_at = ? WHERE id = ?",
               (status, now(), session_id))


# -------------------------------------------------------------------- TX1


def ingest(db, event: Event) -> tuple[str, str | None]:
    """Persist the event and a pending run. Returns (run_id, already_committed_response_id).

    A repeated idempotency key never runs twice: if the first attempt committed,
    its response id comes back and the caller replays it.
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        existing = db.execute(
            "SELECT id, status, response_id FROM interview_events"
            " WHERE session_id = ? AND idempotency_key = ?",
            (event.session_id, event.idempotency_key)).fetchone()
        if existing and existing["status"] == "completed":
            db.execute("COMMIT")
            return "", existing["response_id"]
        if existing:
            event.id = existing["id"]            # retry of a pending event
        else:
            db.execute(
                "INSERT INTO interview_events (id, session_id, idempotency_key, kind,"
                " payload_json, expected_revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event.id, event.session_id, event.idempotency_key, event.kind,
                 json.dumps(event.payload), event.expected_revision, event.created_at))
        run_id = new_id("run")
        db.execute(
            "INSERT INTO agent_runs (id, session_id, event_id, status, started_at)"
            " VALUES (?, ?, ?, 'running', ?)",
            (run_id, event.session_id, event.id, now()))
        db.execute("COMMIT")
        return run_id, None
    except Exception:
        db.execute("ROLLBACK")
        raise


# -------------------------------------------------------------------- TX2


class RevisionConflict(RuntimeError):
    """Someone else committed while this turn was thinking."""


def commit_turn(db, session_id: str, event: Event, run_id: str,
                decision: TurnDecision, expected_revision: int, *,
                dropped: list[str] | None = None, model_calls: int = 0,
                repairs: int = 0, cost_micro_usd: int = 0,
                latency_ms: int = 0) -> str:
    """Publish the whole turn, or nothing. Returns the response id."""
    db.execute("BEGIN IMMEDIATE")
    try:
        current = db.execute("SELECT revision FROM interview_sessions WHERE id = ?",
                             (session_id,)).fetchone()["revision"]
        if current != expected_revision:
            db.execute("ROLLBACK")
            raise RevisionConflict(f"expected {expected_revision}, found {current}")

        response = decision.response
        rid = new_id("resp")
        db.execute(
            "INSERT INTO assistant_responses (id, session_id, addressed_event_id,"
            " primary_intent, blocks_json, question, focus, degraded, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, session_id, event.id, response.primary_intent,
             json.dumps(response.blocks), response.question, response.focus,
             int(response.degraded), now()))

        for card in response.suggestions:
            db.execute("INSERT INTO suggestions (id, response_id, label, detail)"
                       " VALUES (?, ?, ?, ?)", (card.id, rid, card.label, card.detail))

        for fact in decision.facts:
            db.execute(
                "INSERT INTO story_facts (id, session_id, text, source_event_id,"
                " evidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (fact.id, session_id, fact.text, event.id, fact.evidence, now()))

        for change in decision.gap_changes:
            if change.action == "resolve" and change.gap_id:
                db.execute("UPDATE story_gaps SET status = 'resolved', resolved_at = ?,"
                           " evidence = ? WHERE id = ? AND session_id = ?",
                           (now(), change.evidence, change.gap_id, session_id))
            elif change.text:
                db.execute("INSERT INTO story_gaps (id, session_id, text, evidence,"
                           " created_at) VALUES (?, ?, ?, ?, ?)",
                           (new_id("gap"), session_id, change.text, change.evidence, now()))

        if decision.readiness is not None:
            db.execute(
                "INSERT INTO readiness_assessments (id, session_id, event_id, ready,"
                " lenses_json, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id("rd"), session_id, event.id, int(decision.readiness.ready),
                 json.dumps(decision.readiness.lenses), decision.readiness.reason, now()))
            if decision.readiness.ready:
                db.execute("UPDATE interview_sessions SET status = 'ready_for_outline'"
                           " WHERE id = ? AND status = 'active'", (session_id,))

        db.execute("UPDATE interview_events SET status = 'completed', response_id = ?"
                   " WHERE id = ?", (rid, event.id))
        if run_id:
            db.execute(
                "UPDATE agent_runs SET status = 'completed', outcome = ?, model_calls = ?,"
                " repairs = ?, cost_micro_usd = ?, latency_ms = ?, finished_at = ?"
                " WHERE id = ?",
                (response.primary_intent, model_calls, repairs, cost_micro_usd,
                 latency_ms, now(), run_id))
        for reason in dropped or []:
            add_step(db, run_id, kind="dropped_update", detail="", rejected=reason)

        db.execute("UPDATE interview_sessions SET revision = revision + 1, updated_at = ?"
                   " WHERE id = ?", (now(), session_id))
        db.execute("COMMIT")
        return rid
    except Exception:
        try:
            db.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def fail_run(db, run_id: str, reason: str) -> None:
    if not run_id:
        return
    db.execute("UPDATE agent_runs SET status = 'failed', outcome = ?, finished_at = ?"
               " WHERE id = ?", (reason[:200], now(), run_id))


def add_step(db, run_id: str, kind: str, detail: str = "", rejected: str | None = None,
             tokens_in: int = 0, tokens_out: int = 0, cost_micro_usd: int = 0) -> None:
    if not run_id:
        return
    seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM agent_steps WHERE run_id = ?",
                     (run_id,)).fetchone()["n"]
    db.execute(
        "INSERT INTO agent_steps (id, run_id, seq, kind, detail, rejected, tokens_in,"
        " tokens_out, cost_micro_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id("st"), run_id, seq, kind, detail[:1000], rejected, tokens_in,
         tokens_out, cost_micro_usd, now()))


# ------------------------------------------------------------------- reads


def response(db, response_id: str):
    return db.execute("SELECT * FROM assistant_responses WHERE id = ?", (response_id,)).fetchone()


def latest_response(db, session_id: str):
    return db.execute("SELECT * FROM assistant_responses WHERE session_id = ?"
                      " ORDER BY created_at DESC, rowid DESC LIMIT 1", (session_id,)).fetchone()


def suggestions_for(db, response_id: str):
    return db.execute("SELECT * FROM suggestions WHERE response_id = ?", (response_id,)).fetchall()


def active_facts(db, session_id: str):
    return db.execute("SELECT * FROM story_facts WHERE session_id = ? AND status = 'active'"
                      " ORDER BY created_at", (session_id,)).fetchall()


def supersede_fact(db, fact_id: str, by_fact_id: str) -> None:
    db.execute("UPDATE story_facts SET status = 'superseded', superseded_by = ? WHERE id = ?",
               (by_fact_id, fact_id))


def open_gaps(db, session_id: str):
    return db.execute("SELECT * FROM story_gaps WHERE session_id = ? AND status = 'open'"
                      " ORDER BY created_at", (session_id,)).fetchall()


def readiness_history(db, session_id: str, limit: int = 5):
    return db.execute("SELECT * FROM readiness_assessments WHERE session_id = ?"
                      " ORDER BY created_at DESC LIMIT ?", (session_id, limit)).fetchall()


def turns(db, session_id: str, limit: int = 50):
    """Completed exchanges: a response and the event that answered it."""
    return db.execute(
        "SELECT r.id, r.primary_intent, r.question, r.focus, r.blocks_json, r.created_at,"
        "       e.kind AS answer_kind, e.payload_json AS answer_payload"
        " FROM assistant_responses r"
        " LEFT JOIN interview_events e ON e.id = ("
        "     SELECT id FROM interview_events WHERE session_id = r.session_id"
        "     AND created_at >= r.created_at AND kind <> 'interview_started'"
        "     ORDER BY created_at LIMIT 1)"
        " WHERE r.session_id = ? ORDER BY r.created_at LIMIT ?", (session_id, limit)).fetchall()


def recent_questions(db, session_id: str, limit: int = 8) -> list[str]:
    rows = db.execute("SELECT question FROM assistant_responses WHERE session_id = ?"
                      " AND question <> '' ORDER BY created_at DESC LIMIT ?",
                      (session_id, limit)).fetchall()
    return [r["question"] for r in rows]


def recent_intents(db, session_id: str, limit: int = 4) -> list[str]:
    rows = db.execute("SELECT primary_intent FROM assistant_responses WHERE session_id = ?"
                      " ORDER BY created_at DESC LIMIT ?", (session_id, limit)).fetchall()
    return [r["primary_intent"] for r in rows]


def answered_count(db, session_id: str) -> int:
    return db.execute(
        "SELECT COUNT(*) AS n FROM interview_events WHERE session_id = ?"
        " AND kind IN ('message_submitted', 'suggestions_selected')", (session_id,)
    ).fetchone()["n"]

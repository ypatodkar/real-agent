"""Phase 1: the data model holds a conversation with no model involved."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store
from models import Event, Fact, Readiness, Response, SuggestionCard, TurnDecision


def decision(intent="ask_question", question="What does he open?", facts=(), readiness=None,
             suggestions=()):
    return TurnDecision(
        response=Response(primary_intent=intent, question=question,
                          suggestions=[SuggestionCard(label=l, detail=d) for l, d in suggestions]),
        facts=[Fact(text=t, evidence=e) for t, e in facts],
        readiness=readiness)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".sqlite3")
        store.initialize(self.path)
        self.db = store.connect(self.path)
        self.sid = store.create_session(self.db, "a locksmith", "hybrid", 60)

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def commit(self, event, dec, revision=None):
        run_id, existing = store.ingest(self.db, event)
        if existing:
            return existing
        rev = store.session(self.db, self.sid)["revision"] if revision is None else revision
        return store.commit_turn(self.db, self.sid, event, run_id, dec, rev)

    def test_commit_increments_revision_and_saves_everything(self):
        event = Event(kind="interview_started", session_id=self.sid)
        rid = self.commit(event, decision(
            facts=[("He is a locksmith.", "a locksmith")],
            suggestions=[("Public", "Costlier silence"), ("Private", "More threatening")]))
        self.assertEqual(store.session(self.db, self.sid)["revision"], 1)
        self.assertEqual(len(store.active_facts(self.db, self.sid)), 1)
        self.assertEqual(len(store.suggestions_for(self.db, rid)), 2)
        self.assertEqual(store.response(self.db, rid)["question"], "What does he open?")

    def test_replaying_an_event_returns_the_same_response(self):
        event = Event(kind="message_submitted", session_id=self.sid,
                      payload={"text": "hello"}, idempotency_key="same-key")
        first = self.commit(event, decision())
        again = Event(kind="message_submitted", session_id=self.sid,
                      payload={"text": "hello"}, idempotency_key="same-key")
        run_id, existing = store.ingest(self.db, again)
        self.assertEqual(existing, first)
        self.assertEqual(run_id, "")
        self.assertEqual(store.session(self.db, self.sid)["revision"], 1)

    def test_stale_revision_is_refused(self):
        self.commit(Event(kind="interview_started", session_id=self.sid), decision())
        event = Event(kind="message_submitted", session_id=self.sid)
        run_id, _ = store.ingest(self.db, event)
        with self.assertRaises(store.RevisionConflict):
            store.commit_turn(self.db, self.sid, event, run_id, decision(), 0)
        self.assertEqual(store.session(self.db, self.sid)["revision"], 1)

    def test_nothing_commits_when_the_revision_moved(self):
        self.commit(Event(kind="interview_started", session_id=self.sid), decision())
        before = len(store.active_facts(self.db, self.sid))
        event = Event(kind="message_submitted", session_id=self.sid)
        run_id, _ = store.ingest(self.db, event)
        with self.assertRaises(store.RevisionConflict):
            store.commit_turn(self.db, self.sid, event, run_id,
                              decision(facts=[("Invented.", "x")]), 0)
        self.assertEqual(len(store.active_facts(self.db, self.sid)), before)

    def test_facts_supersede_rather_than_overwrite(self):
        e1 = Event(kind="message_submitted", session_id=self.sid)
        self.commit(e1, decision(facts=[("Nobody knows about the key.", "never told anyone")]))
        old = store.active_facts(self.db, self.sid)[0]
        e2 = Event(kind="message_submitted", session_id=self.sid)
        self.commit(e2, decision(facts=[("His brother has known for years.", "known for years")]))
        new = [f for f in store.active_facts(self.db, self.sid) if f["id"] != old["id"]][0]
        store.supersede_fact(self.db, old["id"], new["id"])
        active = store.active_facts(self.db, self.sid)
        self.assertEqual([f["text"] for f in active], ["His brother has known for years."])
        row = self.db.execute("SELECT * FROM story_facts WHERE id = ?", (old["id"],)).fetchone()
        self.assertEqual(row["status"], "superseded")
        self.assertEqual(row["superseded_by"], new["id"])

    def test_readiness_is_append_only_and_moves_status(self):
        e = Event(kind="message_submitted", session_id=self.sid)
        self.commit(e, decision(readiness=Readiness(ready=False, reason="no sequence yet")))
        e2 = Event(kind="message_submitted", session_id=self.sid)
        self.commit(e2, decision(readiness=Readiness(
            ready=True, reason="all four lenses", lenses={"carries": "the locksmith"})))
        history = store.readiness_history(self.db, self.sid)
        self.assertEqual(len(history), 2)
        self.assertEqual(store.session(self.db, self.sid)["status"], "ready_for_outline")


if __name__ == "__main__":
    unittest.main()

"""Phase 2: the whole reliability story, offline and deterministic."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harness
import store
import stub_model as sm
from fixtures.conversations import CASES, BY_ID
from models import Event, Fact, Response, TurnDecision


def ask_decision(question, facts=()):
    return TurnDecision(response=Response(primary_intent="ask_question", question=question),
                        facts=[Fact(text=t, evidence=e) for t, e in facts])


class Base(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".sqlite3")
        store.initialize(self.path)
        self.db = store.connect(self.path)

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def seed(self, case):
        """Replay the case's history into the database, then return its event."""
        sid = store.create_session(self.db, case.seed, case.storytelling_format,
                                   case.involvement)
        event = Event(kind="interview_started", session_id=sid)
        declared = list(case.facts)
        for question, answer in case.history:
            run_id, _ = store.ingest(self.db, event)
            facts = [(declared.pop(0), answer[:60])] if (declared and answer) else (
                [(answer[:120], answer[:60])] if answer else [])
            store.commit_turn(self.db, sid, event, run_id, ask_decision(question, facts),
                              store.session(self.db, sid)["revision"])
            if not answer:
                break
            event = Event(kind="message_submitted", session_id=sid, payload={"text": answer})
        return sid, Event(kind=case.event_kind, session_id=sid, payload=case.event_payload)


class FixtureTests(Base):
    """Every fixture routes correctly and commits exactly one response."""

    def test_allowed_intents_are_permitted(self):
        for case in CASES:
            with self.subTest(case=case.id):
                sid, event = self.seed(case)
                intent = sorted(case.allowed)[0]
                model = sm.StubModel(script=[_valid_for(intent)])
                outcome = harness.run_turn(self.db, model, sid, event)
                self.assertTrue(outcome.response_id, f"{case.id} committed nothing")
                self.assertIn(outcome.response.primary_intent, case.allowed)
                self.assertFalse(outcome.degraded, f"{case.id} fell back unexpectedly")

    def test_forbidden_intents_never_reach_the_writer(self):
        for case in CASES:
            if not case.forbidden:
                continue
            with self.subTest(case=case.id):
                sid, event = self.seed(case)
                bad = sorted(case.forbidden)[0]
                model = sm.StubModel(script=[_valid_for(bad), _valid_for(bad)])
                outcome = harness.run_turn(self.db, model, sid, event)
                self.assertNotIn(outcome.response.primary_intent, case.forbidden,
                                 f"{case.id} let {bad} through")
                self.assertGreaterEqual(outcome.repairs, 1)

    def test_options_request_can_only_be_answered_with_options(self):
        """The original bug, in both of its states."""
        for case_id in ("options_requested", "options_requested_past_ceiling"):
            with self.subTest(case=case_id):
                sid, event = self.seed(BY_ID[case_id])
                model = sm.StubModel(script=[sm.ask("What happens next?"), sm.suggest()])
                outcome = harness.run_turn(self.db, model, sid, event)
                self.assertEqual(outcome.response.primary_intent, "offer_suggestions")
                self.assertEqual(len(outcome.response.suggestions), 2)
                self.assertEqual(outcome.repairs, 1)
                self.assertIn("not allowed", model.seen[1][1])

    def test_an_options_request_is_not_recorded_as_an_answer(self):
        sid, event = self.seed(BY_ID["options_requested"])
        before = store.answered_count(self.db, sid)
        harness.run_turn(self.db, sm.StubModel(script=[sm.suggest()]), sid, event)
        self.assertEqual(store.answered_count(self.db, sid), before)


class ReliabilityTests(Base):
    def test_replaying_an_event_returns_the_committed_response(self):
        sid, event = self.seed(BY_ID["concrete_answer"])
        event.idempotency_key = "abc"
        first = harness.run_turn(self.db, sm.StubModel(script=[sm.ask("First?")]), sid, event)
        again = Event(kind="message_submitted", session_id=sid,
                      payload=event.payload, idempotency_key="abc")
        model = sm.StubModel(script=[sm.ask("Second?")])
        second = harness.run_turn(self.db, model, sid, again)
        self.assertTrue(second.replayed)
        self.assertEqual(second.response_id, first.response_id)
        self.assertEqual(second.response.question, "First?")
        self.assertEqual(model.seen, [], "the model was called on a replay")

    def test_a_repair_carries_the_exact_reason(self):
        sid, event = self.seed(BY_ID["concrete_answer"])
        history = [q for q, _ in BY_ID["concrete_answer"].history]
        model = sm.StubModel(script=[sm.ask(history[0]), sm.ask("What does he confess first?")])
        outcome = harness.run_turn(self.db, model, sid, event)
        self.assertEqual(outcome.repairs, 1)
        self.assertIn("repeats an earlier question", model.seen[1][1])
        self.assertEqual(outcome.response.question, "What does he confess first?")

    def test_partial_failure_commits_the_response_and_drops_the_fact(self):
        sid, event = self.seed(BY_ID["concrete_answer"])
        model = sm.StubModel(script=[sm.ask(
            "What does he confess first?",
            facts=[("He works nights.", "cannot sleep"),          # quoted: kept
                   ("He has a brother in Lisbon.", "brother in Lisbon")])])  # invented: dropped
        outcome = harness.run_turn(self.db, model, sid, event)
        self.assertTrue(outcome.response_id)
        kept = [f["text"] for f in store.active_facts(self.db, sid)]
        self.assertIn("He works nights.", kept)
        self.assertNotIn("He has a brother in Lisbon.", kept)
        self.assertTrue(any("Lisbon" in d for d in outcome.dropped))

    def test_readiness_without_evidence_is_all_or_nothing(self):
        sid, event = self.seed(BY_ID["outline_ready"])
        thin = sm.recommend(lenses={"carries": "the man"})
        model = sm.StubModel(script=[thin, sm.ask("What does he do in the kitchen?")])
        outcome = harness.run_turn(self.db, model, sid, event)
        self.assertEqual(outcome.response.primary_intent, "ask_question")
        self.assertEqual(store.readiness_history(self.db, sid), [])

    def test_provider_failure_falls_back_by_event_kind(self):
        for case_id, expected in (("options_requested", "offer_suggestions"),
                                  ("direct_question", "coach_writer"),
                                  ("concrete_answer", "ask_question"),
                                  ("suggestion_selected", "reflect_and_confirm")):
            with self.subTest(case=case_id):
                sid, event = self.seed(BY_ID[case_id])
                model = sm.StubModel(script=[harness.ProviderError("vertex is down")])
                outcome = harness.run_turn(self.db, model, sid, event)
                self.assertTrue(outcome.degraded)
                self.assertEqual(outcome.response.primary_intent, expected)
                self.assertTrue(outcome.response_id, "the fallback was not committed")

    def test_a_failed_turn_saves_no_story_facts(self):
        sid, event = self.seed(BY_ID["concrete_answer"])
        before = len(store.active_facts(self.db, sid))
        model = sm.StubModel(script=[harness.ProviderError("down")])
        harness.run_turn(self.db, model, sid, event)
        self.assertEqual(len(store.active_facts(self.db, sid)), before)

    def test_a_concurrent_commit_does_not_lose_the_turn(self):
        sid, event = self.seed(BY_ID["concrete_answer"])
        run_id, _ = store.ingest(self.db, event)
        stale = store.session(self.db, sid)["revision"]

        other = Event(kind="message_submitted", session_id=sid, payload={"text": "elsewhere"})
        other_run, _ = store.ingest(self.db, other)
        store.commit_turn(self.db, sid, other, other_run, ask_decision("Meanwhile?"), stale)

        with self.assertRaises(store.RevisionConflict):
            store.commit_turn(self.db, sid, event, run_id, ask_decision("Mine?"), stale)
        self.assertEqual(store.latest_response(self.db, sid)["question"], "Meanwhile?")

    def test_contradictions_reach_the_snapshot(self):
        sid, event = self.seed(BY_ID["contradiction"])
        model = sm.StubModel(script=[sm.reflect("You said nobody knew. Which is true now?")])
        harness.run_turn(self.db, model, sid, event)
        import snapshot as snapshot_module
        snap = snapshot_module.build(self.db, sid, event)
        self.assertTrue(snap.conflicts, "the contradiction was not precomputed")


def _valid_for(intent):
    return {"ask_question": sm.ask("What is the very first thing we see?"),
            "offer_suggestions": sm.suggest(),
            "reflect_and_confirm": sm.reflect(),
            "coach_writer": sm.coach(),
            "recommend_outline": sm.recommend()}[intent]


if __name__ == "__main__":
    unittest.main()

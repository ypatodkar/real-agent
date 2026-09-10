from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from second_unit.agent import StoryEditor, event_source_text
from second_unit.database import Repository
from second_unit.domain import InterviewEvent
from second_unit.model import ModelResult, ModelUnavailable, NoModel
from second_unit.service import InterviewService, ServiceFailure


class UnavailableModel:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, **_: object) -> ModelResult:
        self.calls += 1
        raise ModelUnavailable("provider unavailable")


class UnexpectedFailureModel:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, **_: object) -> ModelResult:
        self.calls += 1
        raise RuntimeError("private provider failure")


class QueueModel:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.prompts: list[str] = []

    def decide(self, *, prompt: str, **_: object) -> ModelResult:
        self.prompts.append(prompt)
        return ModelResult(
            payload=self.payloads.pop(0),
            backend="fake",
            model="fake-model",
            prompt_tokens=11,
            output_tokens=7,
        )


def ask_decision(question: str) -> dict:
    return {
        "response": {
            "intent": "ask_question",
            "guidance": "Let us make the consequence visible.",
            "question": question,
            "focus": "consequence",
            "listening_for": "a visible action",
            "suggestions": [],
        },
        "fact_candidates": [],
        "gap_changes": [],
        "readiness": None,
    }


def response_decision(intent: str, *, question: str = "") -> dict:
    return {
        "response": {
            "intent": intent,
            "guidance": "Here is the part of the story I am holding onto.",
            "question": question,
            "focus": "story",
            "listening_for": "the filmmaker's direction",
            "suggestions": [],
        },
        "fact_candidates": [],
        "gap_changes": [],
        "readiness": None,
    }


class InterviewServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "interview.sqlite3"
        self.repository = Repository(self.database_path)
        self.service = InterviewService(self.repository, StoryEditor(NoModel()))
        self.created = self.service.create_interview({
            "event_id": "event_start_0001",
            "title": "Last Train",
            "seed": "Mara misses the last train home.",
            "storytelling_format": "hybrid",
            "involvement_mode": "collaborative",
        })
        self.session_id = self.created["session"]["id"]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def submit(
        self,
        *,
        event_id: str,
        revision: int,
        kind: str,
        payload: dict,
        service: InterviewService | None = None,
    ) -> dict:
        return (service or self.service).submit_event(self.session_id, {
            "event_id": event_id,
            "expected_revision": revision,
            "kind": kind,
            "payload": payload,
        })


class SuggestionCanonTests(InterviewServiceTestCase):
    def test_selecting_one_stable_id_only_canonizes_that_suggestion(self) -> None:
        offered = self.submit(
            event_id="event_options_0001",
            revision=1,
            kind="suggestions_requested",
            payload={"response_id": self.created["current_response"]["id"]},
        )
        suggestions = offered["current_response"]["suggestions"]
        self.assertEqual(len(suggestions), 3)
        selected = suggestions[1]
        unselected = [suggestions[0], suggestions[2]]

        result = self.submit(
            event_id="event_select_0001",
            revision=2,
            kind="suggestions_selected",
            payload={"suggestion_ids": [selected["id"]], "note": ""},
        )

        facts = result["facts"]
        selected_facts = [
            item for item in facts if item["source_suggestion_id"] == selected["id"]
        ]
        self.assertEqual(len(selected_facts), 1)
        self.assertIn(selected["label"], selected_facts[0]["text"])
        for item in unselected:
            self.assertFalse(any(
                fact["source_suggestion_id"] == item["id"] for fact in facts
            ))

        statuses = {
            item["id"]: item["status"]
            for timeline_item in result["timeline"]
            for item in ((timeline_item["response"] or {}).get("suggestions") or [])
        }
        self.assertEqual(statuses[selected["id"]], "selected")
        self.assertTrue(all(statuses[item["id"]] == "superseded" for item in unselected))

    def test_adaptation_note_replaces_the_original_suggestion_as_fact_source(self) -> None:
        offered = self.submit(
            event_id="event_options_adapt_0001",
            revision=1,
            kind="suggestions_requested",
            payload={"response_id": self.created["current_response"]["id"]},
        )
        selected = offered["current_response"]["suggestions"][0]
        note = "Mara leaves the station but keeps the ticket."
        event = InterviewEvent(
            "event_select_adapt_0001",
            self.session_id,
            2,
            "suggestions_selected",
            {"suggestion_ids": [selected["id"]], "note": note},
        )

        self.assertEqual(
            event_source_text(event, {"suggestions": [selected]}),
            note,
        )
        result = self.submit(
            event_id=event.id,
            revision=event.expected_revision,
            kind=event.kind,
            payload=event.payload,
        )
        self.assertTrue(any(fact["text"] == note for fact in result["facts"]))
        self.assertFalse(any(
            fact["source_suggestion_id"] == selected["id"]
            for fact in result["facts"]
        ))

    def test_advancing_without_selecting_supersedes_old_suggestions(self) -> None:
        offered = self.submit(
            event_id="event_options_orphan_0001",
            revision=1,
            kind="suggestions_requested",
            payload={"response_id": self.created["current_response"]["id"]},
        )
        old_ids = {item["id"] for item in offered["current_response"]["suggestions"]}

        advanced = self.submit(
            event_id="event_advance_orphan_0001",
            revision=2,
            kind="message_submitted",
            payload={
                "text": "She walks home through the sleeping city.",
                "purpose": "answer",
                "response_id": offered["current_response"]["id"],
            },
        )

        statuses = {
            item["id"]: item["status"]
            for timeline_item in advanced["timeline"]
            for item in ((timeline_item["response"] or {}).get("suggestions") or [])
        }
        self.assertTrue(old_ids)
        self.assertTrue(all(statuses[item] == "superseded" for item in old_ids))

    def test_old_response_suggestions_are_rejected_even_if_still_marked_shown(self) -> None:
        offered = self.submit(
            event_id="event_options_old_0001",
            revision=1,
            kind="suggestions_requested",
            payload={"response_id": self.created["current_response"]["id"]},
        )
        old_suggestion_id = offered["current_response"]["suggestions"][0]["id"]
        advanced = self.submit(
            event_id="event_advance_old_0001",
            revision=2,
            kind="message_submitted",
            payload={
                "text": "Mara starts walking along the tracks.",
                "purpose": "answer",
                "response_id": offered["current_response"]["id"],
            },
        )
        with self.repository.transaction(immediate=True) as db:
            db.execute(
                """UPDATE suggestions SET status = 'shown', resolved_event_id = NULL,
                   resolved_at = NULL WHERE id = ?""",
                (old_suggestion_id,),
            )

        for index, kind in enumerate(
            ("suggestions_selected", "suggestions_rejected"), 1
        ):
            with self.subTest(kind=kind):
                with self.assertRaises(ServiceFailure) as caught:
                    self.submit(
                        event_id=f"event_old_suggestion_{index:04d}",
                        revision=advanced["session"]["revision"],
                        kind=kind,
                        payload={
                            "suggestion_ids": [old_suggestion_id],
                            "note": "",
                        },
                    )
                self.assertEqual(caught.exception.code, "invalid_transition")
                self.assertEqual(caught.exception.http_status, 422)
                self.assertIn("current assistant response", str(caught.exception))


class ResponseActionReferenceTests(InterviewServiceTestCase):
    def test_question_help_branches_without_replacing_the_questionnaire(self) -> None:
        questionnaire = self.created["current_response"]
        question = questionnaire["questions"][0]

        branched = self.submit(
            event_id="event_question_help_0001",
            revision=1,
            kind="question_ideas_requested",
            payload={
                "response_id": questionnaire["id"],
                "question_id": question["id"],
                "draft_answer": "Maybe Mara chooses to wait.",
            },
        )

        self.assertEqual(branched["session"]["revision"], 2)
        self.assertEqual(branched["current_response"]["id"], questionnaire["id"])
        self.assertEqual(
            branched["session"]["readiness_score"],
            self.created["session"]["readiness_score"],
        )
        self.assertEqual(branched["facts"], self.created["facts"])
        self.assertEqual(branched["gaps"], self.created["gaps"])
        branch = branched["timeline"][-1]
        self.assertEqual(branch["event"]["kind"], "question_ideas_requested")
        self.assertEqual(branch["event"]["payload"]["question_id"], question["id"])
        self.assertEqual(branch["response"]["intent"], "offer_suggestions")
        self.assertEqual(len(branch["response"]["suggestions"]), 3)

        submitted = self.submit(
            event_id="event_after_question_help_0001",
            revision=2,
            kind="questionnaire_submitted",
            payload={
                "response_id": questionnaire["id"],
                "answers": [{"question_id": question["id"], "text": "Mara waits."}],
            },
        )
        self.assertEqual(submitted["session"]["revision"], 3)

    def test_latest_questionnaire_answers_can_be_revised_without_conflicting_canon(self) -> None:
        questionnaire = self.created["current_response"]
        question = questionnaire["questions"][0]
        original_event_id = "event_original_answers_0001"
        original_text = "Mara waits beneath the station clock."
        revised_event_id = "event_revised_answers_0001"
        revised_text = "Mara leaves the station and walks home."

        def model_decision(event_id: str, text: str) -> dict:
            decision = response_decision("coach_writer")
            decision["fact_candidates"] = [{
                "text": text,
                "source_event_id": event_id,
                "evidence": text,
            }]
            return decision

        original_service = InterviewService(
            self.repository,
            StoryEditor(QueueModel([model_decision(original_event_id, original_text)])),
        )
        submitted = self.submit(
            event_id=original_event_id,
            revision=1,
            kind="questionnaire_submitted",
            payload={
                "response_id": questionnaire["id"],
                "answers": [{"question_id": question["id"], "text": original_text}],
            },
            service=original_service,
        )
        self.assertTrue(any(fact["text"] == original_text for fact in submitted["facts"]))

        # Help remains available inside the original question even though the
        # summary response is now current.
        helped = self.submit(
            event_id="event_edit_help_0001",
            revision=2,
            kind="question_ideas_requested",
            payload={
                "response_id": questionnaire["id"],
                "question_id": question["id"],
                "draft_answer": original_text,
            },
        )
        self.assertEqual(helped["current_response"]["id"], submitted["current_response"]["id"])

        revised_service = InterviewService(
            self.repository,
            StoryEditor(QueueModel([model_decision(revised_event_id, revised_text)])),
        )
        revised = self.submit(
            event_id=revised_event_id,
            revision=3,
            kind="questionnaire_revised",
            payload={
                "target_event_id": original_event_id,
                "questionnaire_response_id": questionnaire["id"],
                "answers": [{"question_id": question["id"], "text": revised_text}],
            },
            service=revised_service,
        )

        active_texts = {fact["text"] for fact in revised["facts"]}
        self.assertNotIn(original_text, active_texts)
        self.assertIn(revised_text, active_texts)
        self.assertEqual(revised["timeline"][-1]["event"]["kind"], "questionnaire_revised")

        with self.assertRaises(ServiceFailure) as caught:
            self.submit(
                event_id="event_stale_revision_0001",
                revision=4,
                kind="questionnaire_revised",
                payload={
                    "target_event_id": original_event_id,
                    "questionnaire_response_id": questionnaire["id"],
                    "answers": [{"question_id": question["id"], "text": "Another version."}],
                },
            )
        self.assertEqual(caught.exception.code, "invalid_transition")
        self.assertIn("latest questionnaire", str(caught.exception))

    def test_filmmaker_can_finish_without_waiting_for_perfect_readiness(self) -> None:
        model = UnexpectedFailureModel()
        service = InterviewService(self.repository, StoryEditor(model))

        finished = self.submit(
            event_id="event_finish_interview_0001",
            revision=1,
            kind="interview_finished",
            payload={"response_id": self.created["current_response"]["id"]},
            service=service,
        )

        self.assertEqual(model.calls, 0)
        self.assertEqual(finished["session"]["status"], "ready_for_outline")
        self.assertEqual(finished["current_response"]["intent"], "coach_writer")
        self.assertEqual(finished["current_response"]["question"], "")
        self.assertEqual(finished["session"]["readiness_score"], 0)

    def test_every_supplied_response_id_must_be_current(self) -> None:
        old_response_id = self.created["current_response"]["id"]
        advanced = self.submit(
            event_id="event_make_response_stale_0001",
            revision=1,
            kind="message_submitted",
            payload={
                "text": "Mara waits until the station closes.",
                "purpose": "answer",
                "response_id": old_response_id,
            },
        )
        self.assertNotEqual(advanced["current_response"]["id"], old_response_id)
        cases = (
            (
                "message_submitted",
                {"text": "A late answer.", "purpose": "answer", "response_id": old_response_id},
            ),
            ("suggestions_requested", {"response_id": old_response_id}),
            ("continue_interview", {"response_id": old_response_id}),
            ("question_skipped", {"response_id": old_response_id, "note": ""}),
            ("reflection_confirmed", {"response_id": old_response_id, "note": ""}),
            ("response_continued", {"response_id": old_response_id, "note": ""}),
        )

        for index, (kind, payload) in enumerate(cases, 1):
            with self.subTest(kind=kind):
                if kind == "continue_interview":
                    with self.repository.transaction(immediate=True) as db:
                        db.execute(
                            """UPDATE interview_sessions SET status = 'ready_for_outline'
                               WHERE id = ?""",
                            (self.session_id,),
                        )
                with self.assertRaises(ServiceFailure) as caught:
                    self.submit(
                        event_id=f"event_stale_response_{index:04d}",
                        revision=advanced["session"]["revision"],
                        kind=kind,
                        payload=payload,
                    )
                self.assertEqual(caught.exception.code, "invalid_transition")
                self.assertEqual(caught.exception.http_status, 422)
                self.assertIn("no longer current", str(caught.exception))
                if kind == "continue_interview":
                    with self.repository.transaction(immediate=True) as db:
                        db.execute(
                            "UPDATE interview_sessions SET status = 'active' WHERE id = ?",
                            (self.session_id,),
                        )

    def test_message_and_suggestion_actions_may_address_the_current_response(self) -> None:
        options = self.submit(
            event_id="event_current_options_0001",
            revision=1,
            kind="suggestions_requested",
            payload={"response_id": self.created["current_response"]["id"]},
        )
        answered = self.submit(
            event_id="event_current_message_0001",
            revision=2,
            kind="message_submitted",
            payload={
                "text": "Mara chooses the walk home.",
                "purpose": "answer",
                "response_id": options["current_response"]["id"],
            },
        )
        self.assertEqual(answered["session"]["revision"], 3)

    def test_continue_interview_requires_outline_ready_status(self) -> None:
        response_id = self.created["current_response"]["id"]
        with self.assertRaises(ServiceFailure) as caught:
            self.submit(
                event_id="event_continue_active_0001",
                revision=1,
                kind="continue_interview",
                payload={"response_id": response_id},
            )
        self.assertEqual(caught.exception.code, "invalid_transition")
        self.assertIn("outline-ready", str(caught.exception))

        with self.repository.transaction(immediate=True) as db:
            db.execute(
                "UPDATE interview_sessions SET status = 'ready_for_outline' WHERE id = ?",
                (self.session_id,),
            )
        continued = self.submit(
            event_id="event_continue_ready_0001",
            revision=1,
            kind="continue_interview",
            payload={"response_id": response_id},
        )

        self.assertEqual(continued["session"]["revision"], 2)
        self.assertEqual(continued["session"]["status"], "active")

    def test_reflection_confirmation_requires_a_current_reflection(self) -> None:
        with self.assertRaises(ServiceFailure) as wrong_intent:
            self.submit(
                event_id="event_confirm_question_0001",
                revision=1,
                kind="reflection_confirmed",
                payload={
                    "response_id": self.created["current_response"]["id"],
                    "note": "",
                },
            )
        self.assertIn("reflection response", str(wrong_intent.exception))

        service = InterviewService(
            self.repository,
            StoryEditor(QueueModel([response_decision("reflect_and_confirm")])),
        )
        reflected = self.submit(
            event_id="event_reflection_0001",
            revision=1,
            kind="message_submitted",
            payload={
                "text": "Mara is afraid that going home means admitting defeat.",
                "purpose": "nuance",
                "response_id": self.created["current_response"]["id"],
            },
            service=service,
        )
        confirmed = self.submit(
            event_id="event_confirm_reflection_0001",
            revision=2,
            kind="reflection_confirmed",
            payload={"response_id": reflected["current_response"]["id"], "note": ""},
        )

        self.assertEqual(confirmed["session"]["revision"], 3)

    def test_question_skip_requires_a_nonempty_current_question(self) -> None:
        with self.repository.transaction(immediate=True) as db:
            db.execute(
                "UPDATE assistant_responses SET intent = 'ask_question', question = ? WHERE id = ?",
                ("What happens next?", self.created["current_response"]["id"]),
            )
        skipped = self.submit(
            event_id="event_skip_question_0001",
            revision=1,
            kind="question_skipped",
            payload={
                "response_id": self.created["current_response"]["id"],
                "note": "",
            },
        )
        self.assertEqual(skipped["session"]["revision"], 2)

    def test_question_skip_rejects_question_free_coaching(self) -> None:
        coached = self.submit(
            event_id="event_question_for_skip_0001",
            revision=1,
            kind="message_submitted",
            payload={
                "text": "Would a silent ending work better?",
                "purpose": "question",
                "response_id": self.created["current_response"]["id"],
            },
        )
        self.assertEqual(coached["current_response"]["intent"], "coach_writer")
        self.assertEqual(coached["current_response"]["question"], "")

        with self.assertRaises(ServiceFailure) as caught:
            self.submit(
                event_id="event_skip_coaching_0001",
                revision=2,
                kind="question_skipped",
                payload={"response_id": coached["current_response"]["id"], "note": ""},
            )
        self.assertIn("containing a question", str(caught.exception))

    def test_response_continue_requires_question_free_coaching(self) -> None:
        with self.assertRaises(ServiceFailure) as wrong_intent:
            self.submit(
                event_id="event_continue_question_0001",
                revision=1,
                kind="response_continued",
                payload={
                    "response_id": self.created["current_response"]["id"],
                    "note": "",
                },
            )
        self.assertIn("question-free coaching", str(wrong_intent.exception))

        coaching_with_question = self.submit(
            event_id="event_coaching_question_0001",
            revision=1,
            kind="message_submitted",
            payload={
                "text": "Should Mara leave now?",
                "purpose": "question",
                "response_id": self.created["current_response"]["id"],
            },
        )
        with self.repository.transaction(immediate=True) as db:
            db.execute(
                "UPDATE assistant_responses SET question = ? WHERE id = ?",
                ("Should Mara leave now?", coaching_with_question["current_response"]["id"]),
            )
        with self.assertRaises(ServiceFailure) as has_question:
            self.submit(
                event_id="event_continue_coaching_question_0001",
                revision=2,
                kind="response_continued",
                payload={
                    "response_id": coaching_with_question["current_response"]["id"],
                    "note": "",
                },
            )
        self.assertIn("question-free coaching", str(has_question.exception))

    def test_response_continue_accepts_question_free_coaching(self) -> None:
        coached = self.submit(
            event_id="event_question_for_continue_0001",
            revision=1,
            kind="message_submitted",
            payload={
                "text": "Would a silent ending work better?",
                "purpose": "question",
                "response_id": self.created["current_response"]["id"],
            },
        )
        continued = self.submit(
            event_id="event_continue_coaching_0001",
            revision=2,
            kind="response_continued",
            payload={"response_id": coached["current_response"]["id"], "note": ""},
        )

        self.assertEqual(continued["session"]["revision"], 3)


class IdempotencyAndRevisionTests(InterviewServiceTestCase):
    def test_create_rejects_unknown_or_non_text_story_fields(self) -> None:
        with self.assertRaises(ServiceFailure) as unknown:
            self.service.create_interview({
                "event_id": "event_bad_create_0001",
                "seed": "A valid seed.",
                "unexpected": "must not disappear",
            })
        self.assertEqual(unknown.exception.http_status, 400)

        with self.assertRaises(ServiceFailure) as wrong_type:
            self.service.create_interview({
                "event_id": "event_bad_create_0002",
                "seed": ["not", "text"],
            })
        self.assertEqual(wrong_type.exception.http_status, 400)

    def test_duplicate_create_replays_the_same_session(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = Repository(Path(temporary.name) / "create.sqlite3")
        service = InterviewService(repository, StoryEditor(NoModel()))
        raw = {
            "session_id": "interview_client_create_0001",
            "event_id": "event_client_create_0001",
            "title": "Platform",
            "seed": "A woman waits on an empty platform.",
            "storytelling_format": "not_sure",
            "involvement_mode": "collaborative",
        }

        first = service.create_interview(raw)
        counts = repository.counts(raw["session_id"])
        replay = service.create_interview(raw)

        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["session"]["id"], raw["session_id"])
        self.assertEqual(replay["session"]["revision"], 1)
        self.assertEqual(repository.counts(raw["session_id"]), counts)

    def test_duplicate_event_replays_without_new_rows_or_revision(self) -> None:
        raw = {
            "event_id": "event_message_0001",
            "expected_revision": 1,
            "kind": "message_submitted",
            "payload": {
                "text": "She waits until sunrise instead of calling anyone.",
                "purpose": "answer",
                "response_id": self.created["current_response"]["id"],
            },
        }
        first = self.service.submit_event(self.session_id, raw)
        counts = self.repository.counts(self.session_id)

        replay = self.service.submit_event(self.session_id, raw)

        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["session"]["revision"], first["session"]["revision"])
        self.assertEqual(
            replay["current_response"]["id"],
            first["current_response"]["id"],
        )
        self.assertEqual(self.repository.counts(self.session_id), counts)

    def test_replaying_an_older_event_returns_the_latest_session_projection(self) -> None:
        old_raw = {
            "event_id": "event_old_replay_0001",
            "expected_revision": 1,
            "kind": "message_submitted",
            "payload": {
                "text": "Mara waits until the station closes.",
                "purpose": "answer",
                "response_id": self.created["current_response"]["id"],
            },
        }
        old_result = self.service.submit_event(self.session_id, old_raw)
        latest = self.submit(
            event_id="event_after_old_replay_0001",
            revision=2,
            kind="message_submitted",
            payload={
                "text": "Then she starts walking along the tracks.",
                "purpose": "answer",
                "response_id": old_result["current_response"]["id"],
            },
        )
        counts = self.repository.counts(self.session_id)

        replay = self.service.submit_event(self.session_id, old_raw)

        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["session"]["revision"], latest["session"]["revision"])
        self.assertEqual(
            replay["current_response"]["id"], latest["current_response"]["id"]
        )
        self.assertEqual(replay["timeline"], latest["timeline"])
        self.assertEqual(self.repository.counts(self.session_id), counts)

    def test_replaying_create_after_a_newer_turn_returns_latest_projection(self) -> None:
        raw = {
            "session_id": "interview_create_replay_latest_0001",
            "event_id": "event_create_replay_latest_0001",
            "title": "Platform",
            "seed": "A woman waits on an empty platform.",
            "storytelling_format": "not_sure",
            "involvement_mode": "collaborative",
        }
        created = self.service.create_interview(raw)
        latest = self.service.submit_event(raw["session_id"], {
            "event_id": "event_after_create_replay_0001",
            "expected_revision": 1,
            "kind": "message_submitted",
            "payload": {
                "text": "She sees the last train pass without stopping.",
                "purpose": "answer",
                "response_id": created["current_response"]["id"],
            },
        })
        counts = self.repository.counts(raw["session_id"])

        replay = self.service.create_interview(raw)

        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["session"]["revision"], 2)
        self.assertEqual(
            replay["current_response"]["id"], latest["current_response"]["id"]
        )
        self.assertEqual(self.repository.counts(raw["session_id"]), counts)

    def test_duplicate_id_with_different_payload_is_a_conflict(self) -> None:
        self.submit(
            event_id="event_message_0002",
            revision=1,
            kind="message_submitted",
            payload={"text": "She waits until sunrise.", "purpose": "answer"},
        )
        counts = self.repository.counts(self.session_id)

        with self.assertRaises(ServiceFailure) as caught:
            self.submit(
                event_id="event_message_0002",
                revision=1,
                kind="message_submitted",
                payload={"text": "She calls her brother instead.", "purpose": "answer"},
            )

        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(caught.exception.http_status, 409)
        self.assertEqual(self.repository.counts(self.session_id), counts)

    def test_stale_revision_creates_no_event_or_partial_state(self) -> None:
        before = self.repository.counts(self.session_id)
        with self.assertRaises(ServiceFailure) as caught:
            self.submit(
                event_id="event_stale_0001",
                revision=0,
                kind="message_submitted",
                payload={"text": "This arrived too late.", "purpose": "answer"},
            )

        self.assertEqual(caught.exception.code, "stale_revision")
        self.assertEqual(caught.exception.http_status, 409)
        self.assertEqual(caught.exception.details, {"current_revision": 1})
        self.assertEqual(self.repository.counts(self.session_id), before)
        self.assertEqual(
            self.service.get_interview(self.session_id)["session"]["revision"],
            1,
        )


class AgentBehaviorTests(InterviewServiceTestCase):
    def test_invalid_readiness_does_not_discard_options_or_direct_coaching(self) -> None:
        invalid_readiness = {
            "lenses": {
                "carrier": {"status": "supported", "evidence": "The lonely protagonist"},
                "pressure": {"status": "supported", "evidence": "Time is running out"},
                "visible_sequence": {"status": "missing", "evidence": ""},
                "ending": {"status": "missing", "evidence": ""},
            },
            "reason": "The setup is emerging.",
            "recommend_outline": False,
        }
        options = {
            "response": {
                "intent": "offer_suggestions",
                "guidance": "These directions build from Mara and the empty platform.",
                "question": "",
                "focus": "Mara's next action",
                "listening_for": "a selected direction",
                "suggestions": [
                    {"label": "Follow the tracks", "detail": "Mara turns delay into a physical journey."},
                    {"label": "Enter the signal room", "detail": "Mara acts on the mystery inside the station."},
                ],
            },
            "fact_candidates": [],
            "gap_changes": [],
            "readiness": invalid_readiness,
        }
        coaching = {
            "response": {
                "intent": "coach_writer",
                "guidance": "This short does not need a subplot; deepen Mara's main choice first.",
                "question": "",
                "focus": "subplot decision",
                "listening_for": "the filmmaker's direction",
                "suggestions": [],
            },
            "fact_candidates": [],
            "gap_changes": [],
            "readiness": invalid_readiness,
        }
        model = QueueModel([options, coaching])
        service = InterviewService(self.repository, StoryEditor(model))

        offered = self.submit(
            event_id="event_partial_options_0001",
            revision=1,
            kind="suggestions_requested",
            payload={},
            service=service,
        )
        self.assertFalse(offered["agent"]["used_fallback"])
        self.assertEqual(offered["agent"]["dropped_components"], ["readiness"])
        self.assertIn("Mara", offered["current_response"]["guidance"])
        self.assertEqual(len(offered["current_response"]["suggestions"]), 2)

        coached = self.submit(
            event_id="event_partial_coaching_0001",
            revision=2,
            kind="message_submitted",
            payload={"text": "Does this need a subplot?", "purpose": "question"},
            service=service,
        )
        self.assertFalse(coached["agent"]["used_fallback"])
        self.assertEqual(coached["agent"]["dropped_components"], ["readiness"])
        self.assertIn("does not need a subplot", coached["current_response"]["guidance"])
        self.assertEqual(coached["current_response"]["question"], "")
        self.assertEqual(len(model.prompts), 2)
        self.assertIsNotNone(coached["timeline"][-1]["event"]["agent_run"]["diagnostic"])

    def test_unsupported_model_fact_and_readiness_never_reach_canon(self) -> None:
        event_id = "event_unsupported_state_0001"
        invalid = {
            "response": {
                "intent": "recommend_outline",
                "guidance": "The story is ready.",
                "question": "",
                "focus": "readiness",
                "listening_for": "",
                "suggestions": [],
            },
            "fact_candidates": [{
                "text": "Mara attacks the conductor.",
                "source_event_id": event_id,
                "evidence": "Mara",
            }],
            "gap_changes": [],
            "readiness": {
                "lenses": {
                    name: {"status": "supported", "evidence": "Invented evidence"}
                    for name in ("carrier", "pressure", "visible_sequence", "ending")
                },
                "reason": "All four lenses are supposedly supported.",
                "recommend_outline": True,
            },
        }
        model = QueueModel([invalid, invalid])
        service = InterviewService(self.repository, StoryEditor(model))

        result = self.submit(
            event_id=event_id,
            revision=1,
            kind="message_submitted",
            payload={"text": "Mara waits until dawn.", "purpose": "answer"},
            service=service,
        )

        self.assertEqual(len(model.prompts), 2)
        self.assertTrue(result["agent"]["used_fallback"])
        self.assertEqual(result["session"]["status"], "active")
        self.assertEqual(result["session"]["readiness_score"], 0)
        self.assertFalse(any(
            fact["text"] == "Mara attacks the conductor."
            for fact in result["facts"]
        ))

    def test_offline_question_set_contains_the_full_target_with_simple_help(self) -> None:
        response = self.created["current_response"]
        self.assertEqual(response["intent"], "ask_questions")
        self.assertEqual(len(response["questions"]), 8)
        self.assertEqual(len({item["text"] for item in response["questions"]}), 8)
        self.assertTrue(all(item["explanation"] for item in response["questions"]))
        self.assertTrue(all(len(item["explanation"].split()) <= 18 for item in response["questions"]))

    def test_generated_set_size_becomes_the_pacing_boundary(self) -> None:
        limited = self.service.create_interview({
            "event_id": "event_limited_start_0001",
            "title": "Three Questions",
            "seed": "A woman finds a key in an empty theatre.",
            "storytelling_format": "not_sure",
            "involvement_mode": "collaborative",
            "question_target": 3,
        })
        session_id = limited["session"]["id"]
        planned = limited["current_response"]["questions"]
        generated_count = len(planned)
        self.assertEqual(limited["session"]["question_target"], generated_count)
        self.assertGreaterEqual(generated_count, 3)
        result = self.service.submit_event(session_id, {
            "event_id": "event_limited_answers_0001",
            "expected_revision": 1,
            "kind": "questionnaire_submitted",
            "payload": {
                "response_id": limited["current_response"]["id"],
                "answers": [
                    {"question_id": item["id"], "text": f"Visible answer {index}."}
                    for index, item in enumerate(planned, 1)
                ],
            },
        })

        self.assertEqual(result["current_response"]["intent"], "coach_writer")
        self.assertEqual(result["current_response"]["question"], "")
        asked = sum(
            (1 if item["response"] and item["response"]["question"] else 0)
            + len((item["response"] or {}).get("questions", []))
            for item in result["timeline"]
        )
        self.assertEqual(asked, generated_count)

        continued = self.service.submit_event(session_id, {
            "event_id": "event_limited_continue_0001",
            "expected_revision": 2,
            "kind": "response_continued",
            "payload": {"response_id": result["current_response"]["id"], "note": ""},
        })
        self.assertTrue(continued["current_response"]["question"])
        asked = sum(
            (1 if item["response"] and item["response"]["question"] else 0)
            + len((item["response"] or {}).get("questions", []))
            for item in continued["timeline"]
        )
        self.assertEqual(asked, generated_count + 1)

    def test_provider_fallback_preserves_existing_readiness(self) -> None:
        with self.repository.transaction(immediate=True) as db:
            db.execute(
                """UPDATE interview_sessions
                   SET readiness_score = 0.75, readiness_reason = ? WHERE id = ?""",
                ("Three accepted lenses are supported.", self.session_id),
            )

        result = self.submit(
            event_id="event_fallback_preserves_ready_0001",
            revision=1,
            kind="suggestions_requested",
            payload={},
        )

        self.assertTrue(result["agent"]["used_fallback"])
        self.assertEqual(result["session"]["readiness_score"], 0.75)
        self.assertEqual(
            result["session"]["readiness_reason"],
            "Three accepted lenses are supported.",
        )

    def test_structurally_invalid_decision_also_gets_one_repair(self) -> None:
        valid = response_decision("coach_writer")
        model = QueueModel([{"not_response": True}, valid])
        service = InterviewService(self.repository, StoryEditor(model))

        result = self.submit(
            event_id="event_repair_shape_0001",
            revision=1,
            kind="message_submitted",
            payload={"text": "The platform lights go out.", "purpose": "answer"},
            service=service,
        )

        self.assertEqual(len(model.prompts), 2)
        self.assertIn("decision.response must be an object", model.prompts[1])
        self.assertFalse(result["agent"]["used_fallback"])

    def test_provider_unavailability_commits_typed_local_fallback(self) -> None:
        model = UnavailableModel()
        service = InterviewService(self.repository, StoryEditor(model))

        result = self.submit(
            event_id="event_options_0002",
            revision=1,
            kind="suggestions_requested",
            payload={},
            service=service,
        )

        self.assertEqual(model.calls, 1)
        self.assertTrue(result["agent"]["used_fallback"])
        self.assertEqual(result["current_response"]["intent"], "offer_suggestions")
        self.assertEqual(len(result["current_response"]["suggestions"]), 3)
        self.assertEqual(result["session"]["revision"], 2)
        self.assertEqual(result["timeline"][-1]["event"]["status"], "completed")
        self.assertIn("Mara", result["current_response"]["guidance"])
        self.assertIsNotNone(result["timeline"][-1]["event"]["agent_run"]["diagnostic"])

    def test_direct_question_gets_coaching_instead_of_another_interrogation(self) -> None:
        result = self.submit(
            event_id="event_question_0001",
            revision=1,
            kind="message_submitted",
            payload={
                "text": "Would narration make this feel too distant?",
                "purpose": "question",
            },
        )

        response = result["current_response"]
        self.assertEqual(response["intent"], "coach_writer")
        self.assertTrue(response["guidance"])
        self.assertEqual(response["question"], "")

    def test_invalid_model_decision_gets_one_repair_with_exact_reason(self) -> None:
        invalid = ask_decision("What is the final image?")
        valid = {
            "response": {
                "intent": "offer_suggestions",
                "guidance": "Here are two possible directions.",
                "question": "",
                "focus": "ending direction",
                "listening_for": "a selected ending direction",
                "suggestions": [
                    {
                        "label": "Leave before dawn",
                        "detail": "An early exit makes the ending an active refusal.",
                    },
                    {
                        "label": "Wait for sunrise",
                        "detail": "Waiting turns the ending into endurance rather than escape.",
                    },
                ],
            },
            "fact_candidates": [],
            "gap_changes": [],
            "readiness": None,
        }
        model = QueueModel([invalid, valid])
        service = InterviewService(self.repository, StoryEditor(model))

        result = self.submit(
            event_id="event_options_0003",
            revision=1,
            kind="suggestions_requested",
            payload={},
            service=service,
        )

        self.assertEqual(len(model.prompts), 2)
        self.assertIn("not allowed", model.prompts[1])
        self.assertFalse(result["agent"]["used_fallback"])
        self.assertEqual(result["current_response"]["intent"], "offer_suggestions")


class FailureRecoveryTests(InterviewServiceTestCase):
    def test_unexpected_failure_preserves_event_and_revision_then_allows_retry(self) -> None:
        broken_model = UnexpectedFailureModel()
        broken_service = InterviewService(self.repository, StoryEditor(broken_model))
        raw = {
            "event_id": "event_retry_0001",
            "expected_revision": 1,
            "kind": "message_submitted",
            "payload": {
                "text": "She chooses to wait on the empty platform.",
                "purpose": "answer",
            },
        }
        before = self.repository.counts(self.session_id)

        with self.assertRaises(ServiceFailure) as caught:
            broken_service.submit_event(self.session_id, raw)

        self.assertEqual(caught.exception.code, "turn_failed")
        self.assertEqual(caught.exception.http_status, 503)
        failed = self.service.get_interview(self.session_id)
        self.assertEqual(failed["session"]["revision"], 1)
        self.assertEqual(failed["timeline"][-1]["event"]["status"], "failed_retryable")
        self.assertEqual(failed["timeline"][-1]["event"]["expected_revision"], 1)
        self.assertEqual(failed["timeline"][-1]["event"]["payload"], raw["payload"])
        self.assertIsNone(failed["timeline"][-1]["response"])
        after_failure = self.repository.counts(self.session_id)
        self.assertEqual(after_failure["interview_events"], before["interview_events"] + 1)
        self.assertEqual(after_failure["assistant_responses"], before["assistant_responses"])
        self.assertEqual(after_failure["story_facts"], before["story_facts"])
        self.assertNotIn("private provider failure", str(failed))

        recovered = self.service.submit_event(self.session_id, raw)

        self.assertEqual(recovered["session"]["revision"], 2)
        self.assertEqual(recovered["timeline"][-1]["event"]["status"], "completed")
        self.assertIsNotNone(recovered["timeline"][-1]["response"])
        self.assertEqual(
            self.repository.counts(self.session_id)["interview_events"],
            after_failure["interview_events"],
        )


if __name__ == "__main__":
    unittest.main()

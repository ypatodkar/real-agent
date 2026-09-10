from __future__ import annotations

import unittest

from second_unit.domain import (
    DomainError,
    FactCandidate,
    InterviewEvent,
    PlannedQuestion,
    SuggestionCandidate,
    TurnDecision,
    make_start_event,
    parse_event,
    parse_turn_decision,
    validate_decision,
)


SESSION_ID = "interview_test_0001"
EVENT_ID = "event_test_0001"


def event_body(kind: str, payload: object, *, revision: object = 0) -> dict:
    return {
        "event_id": EVENT_ID,
        "expected_revision": revision,
        "kind": kind,
        "payload": payload,
    }


def event(kind: str, payload: dict | None = None) -> InterviewEvent:
    return InterviewEvent(EVENT_ID, SESSION_ID, 0, kind, payload or {})


class EventPayloadValidationTests(unittest.TestCase):
    def test_start_event_validates_question_target(self) -> None:
        parsed = make_start_event(
            SESSION_ID,
            EVENT_ID,
            seed="A train waits in the dark.",
            title="Night Train",
            storytelling_format="hybrid",
            involvement_mode="collaborative",
            question_target=12,
        )
        self.assertEqual(parsed.payload["question_target"], 12)

        for invalid in (2, 21, True, "8"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                DomainError, "question_target"
            ):
                make_start_event(
                    SESSION_ID,
                    EVENT_ID,
                    seed="A train waits in the dark.",
                    title="Night Train",
                    storytelling_format="hybrid",
                    involvement_mode="collaborative",
                    question_target=invalid,  # type: ignore[arg-type]
                )

    def test_parses_and_normalizes_a_message_event(self) -> None:
        parsed = parse_event(
            SESSION_ID,
            event_body(
                "message_submitted",
                {"text": "  The train leaves at dawn.  ", "purpose": "answer"},
            ),
        )

        self.assertEqual(parsed.id, EVENT_ID)
        self.assertEqual(parsed.expected_revision, 0)
        self.assertEqual(
            parsed.payload,
            {"text": "The train leaves at dawn.", "purpose": "answer"},
        )

    def test_question_help_is_a_typed_question_branch_event(self) -> None:
        parsed = parse_event(
            SESSION_ID,
            event_body("question_ideas_requested", {
                "response_id": "response_test_0001",
                "question_id": "question_test_0001",
                "draft_answer": "  Perhaps she stays.  ",
            }),
        )

        self.assertEqual(parsed.payload, {
            "response_id": "response_test_0001",
            "question_id": "question_test_0001",
            "draft_answer": "Perhaps she stays.",
        })

    def test_questionnaire_revision_has_an_explicit_target_and_question_set(self) -> None:
        parsed = parse_event(
            SESSION_ID,
            event_body("questionnaire_revised", {
                "target_event_id": "event_answers_0001",
                "questionnaire_response_id": "response_questions_0001",
                "answers": [{
                    "question_id": "question_story_0001",
                    "text": "  She chooses to walk home.  ",
                }],
            }),
        )

        self.assertEqual(parsed.payload, {
            "target_event_id": "event_answers_0001",
            "questionnaire_response_id": "response_questions_0001",
            "answers": [{
                "question_id": "question_story_0001",
                "text": "She chooses to walk home.",
            }],
        })

    def test_rejects_boolean_revision_unknown_kind_and_empty_message(self) -> None:
        with self.assertRaisesRegex(DomainError, "non-negative integer"):
            parse_event(
                SESSION_ID,
                event_body("suggestions_requested", {}, revision=True),
            )
        with self.assertRaisesRegex(DomainError, "unsupported event kind"):
            parse_event(SESSION_ID, event_body("invent_story", {}))
        with self.assertRaisesRegex(DomainError, "text is required"):
            parse_event(
                SESSION_ID,
                event_body("message_submitted", {"text": "   "}),
            )

    def test_rejects_duplicate_or_missing_suggestion_ids(self) -> None:
        duplicate = "suggestion_same_0001"
        with self.assertRaisesRegex(DomainError, "must be unique"):
            parse_event(
                SESSION_ID,
                event_body(
                    "suggestions_selected",
                    {"suggestion_ids": [duplicate, duplicate]},
                ),
            )
        with self.assertRaisesRegex(DomainError, "one to three"):
            parse_event(
                SESSION_ID,
                event_body("suggestions_selected", {"suggestion_ids": []}),
            )

    def test_rejects_falsey_non_object_payload(self) -> None:
        # An empty list must not be silently converted to an empty object. Doing
        # so weakens event identity and hides malformed clients.
        with self.assertRaisesRegex(DomainError, "payload must be an object"):
            parse_event(
                SESSION_ID,
                event_body("suggestions_requested", []),
            )

    def test_rejects_unknown_payload_fields(self) -> None:
        # Event payloads are a versioned API contract. Unknown fields should be
        # rejected rather than silently discarded before the idempotency hash.
        with self.assertRaises(DomainError):
            parse_event(
                SESSION_ID,
                event_body(
                    "message_submitted",
                    {
                        "text": "The train leaves at dawn.",
                        "purpose": "answer",
                        "unexpected": "silently losing this would be unsafe",
                    },
                ),
            )


class TurnDecisionParsingTests(unittest.TestCase):
    @staticmethod
    def payload(*, intent: str = "ask_question", suggestions: object = None) -> dict:
        return {
            "response": {
                "intent": intent,
                "guidance": "Let us make the moment visible.",
                "question": "What does she do when the train arrives?",
                "focus": "visible action",
                "listening_for": "an action",
                "suggestions": [] if suggestions is None else suggestions,
            },
            "fact_candidates": [],
            "gap_changes": [],
            "readiness": None,
        }

    def test_parses_a_typed_decision(self) -> None:
        parsed = parse_turn_decision(self.payload())
        self.assertEqual(parsed.intent, "ask_question")
        self.assertEqual(parsed.focus, "visible action")
        self.assertEqual(parsed.suggestions, ())

    def test_rejects_wrong_collection_types_even_when_falsey(self) -> None:
        payload = self.payload(suggestions="")
        with self.assertRaisesRegex(DomainError, "suggestions must be a list"):
            parse_turn_decision(payload)

        payload = self.payload()
        payload["fact_candidates"] = ""
        with self.assertRaisesRegex(DomainError, "fact_candidates must be a list"):
            parse_turn_decision(payload)

        payload = self.payload()
        payload["gap_changes"] = ""
        with self.assertRaisesRegex(DomainError, "gap_changes must be a list"):
            parse_turn_decision(payload)

    def test_rejects_non_boolean_outline_recommendation(self) -> None:
        payload = self.payload(intent="recommend_outline")
        payload["response"]["question"] = ""
        payload["readiness"] = {
            "lenses": {
                name: {"status": "supported", "evidence": f"Evidence for {name}"}
                for name in ("carrier", "pressure", "visible_sequence", "ending")
            },
            "reason": "All four lenses are supported.",
            "recommend_outline": "false",
        }
        with self.assertRaisesRegex(DomainError, "recommend_outline must be a boolean"):
            parse_turn_decision(payload)

    def test_rejects_an_unsupported_gap_impact(self) -> None:
        payload = self.payload()
        payload["gap_changes"] = [{
            "action": "open",
            "description": "Mara has no reason to stay.",
            "gap_id": "",
            "impact": "advisory",
            "evidence": "",
        }]

        with self.assertRaisesRegex(DomainError, "unsupported gap impact") as caught:
            parse_turn_decision(payload)

        self.assertEqual(caught.exception.code, "invalid_model_output")
        self.assertEqual(caught.exception.field, "gap.impact")


class TurnDecisionValidationTests(unittest.TestCase):
    def test_opening_question_count_is_chosen_by_the_agent(self) -> None:
        decision = TurnDecision(
            intent="ask_questions",
            questions=tuple(
                PlannedQuestion(
                    text=f"What story decision belongs in area {index}?",
                    explanation="This helps us shape the first outline.",
                    focus=f"area {index}",
                )
                for index in range(1, 6)
            ),
        )
        errors = validate_decision(
            decision,
            event=event("interview_started"),
            recent_questions=[],
            source_text="A train waits in the dark.",
            active_gap_ids=[],
            question_target=8,
        )

        self.assertEqual(errors, [])

    def test_rejects_a_compound_question_with_one_question_mark(self) -> None:
        decision = TurnDecision(
            intent="ask_question",
            question="What kind of confessions are these, and how does the octopus react?",
        )
        errors = validate_decision(
            decision,
            event=event("message_submitted", {"text": "Confessions.", "purpose": "answer"}),
            recent_questions=[],
            source_text="Confessions.",
            active_gap_ids=[],
        )
        self.assertTrue(any("compound" in item for item in errors), errors)

    def test_rejects_a_question_hidden_in_guidance(self) -> None:
        decision = TurnDecision(
            intent="ask_question",
            guidance="The confessions reveal his inner life. What kind are they?",
            question="How does the octopus react?",
        )
        errors = validate_decision(
            decision,
            event=event("message_submitted", {"text": "Confessions.", "purpose": "answer"}),
            recent_questions=[],
            source_text="Confessions.",
            active_gap_ids=[],
        )
        self.assertTrue(any("guidance" in item for item in errors), errors)

    def test_question_target_blocks_automatic_questions_but_allows_explicit_continue(self) -> None:
        decision = TurnDecision(
            intent="ask_question",
            question="What happens next?",
        )
        errors = validate_decision(
            decision,
            event=event("message_submitted", {"text": "She leaves.", "purpose": "answer"}),
            recent_questions=[],
            source_text="She leaves.",
            active_gap_ids=[],
            question_target=3,
            questions_asked=3,
        )
        self.assertTrue(any("question target" in item for item in errors), errors)

        self.assertEqual(
            validate_decision(
                decision,
                event=event("response_continued"),
                recent_questions=[],
                source_text="",
                active_gap_ids=[],
                question_target=3,
                questions_asked=3,
            ),
            [],
        )

    def test_question_purpose_can_never_establish_a_fact(self) -> None:
        filmmaker_question = event(
            "message_submitted",
            {"text": "Should Mara use narration?", "purpose": "question"},
        )
        parsed = parse_turn_decision({
            "response": {
                "intent": "coach_writer",
                "guidance": "Narration could create useful distance.",
                "question": "",
                "focus": "format",
                "listening_for": "a filmmaker decision",
                "suggestions": [],
            },
            "fact_candidates": [{
                "text": "Mara uses narration",
                "source_event_id": EVENT_ID,
                "evidence": "Mara use narration",
            }],
            "gap_changes": [],
            "readiness": None,
        })

        errors = validate_decision(
            parsed,
            event=filmmaker_question,
            recent_questions=[],
            source_text=filmmaker_question.payload["text"],
            active_gap_ids=[],
        )

        self.assertTrue(any("cannot establish story facts" in item for item in errors))

    def test_instruction_message_cannot_establish_a_story_fact(self) -> None:
        instruction = event(
            "message_submitted",
            {"text": "Give Mara a red suitcase.", "purpose": "instruction"},
        )
        decision = TurnDecision(
            intent="coach_writer",
            facts=(
                FactCandidate(
                    "Give Mara a red suitcase.",
                    EVENT_ID,
                    "Give Mara a red suitcase.",
                ),
            ),
        )

        errors = validate_decision(
            decision,
            event=instruction,
            recent_questions=[],
            source_text=instruction.payload["text"],
            active_gap_ids=[],
        )

        self.assertTrue(any("cannot establish story facts" in item for item in errors))

    def test_fact_text_must_itself_be_supported_by_the_source(self) -> None:
        message = event(
            "message_submitted",
            {"text": "Mara waits on the platform.", "purpose": "answer"},
        )
        parsed = parse_turn_decision({
            "response": {
                "intent": "ask_question",
                "guidance": "",
                "question": "What happens at dawn?",
                "focus": "next beat",
                "listening_for": "an action",
                "suggestions": [],
            },
            "fact_candidates": [{
                "text": "Mara attacks the conductor.",
                "source_event_id": EVENT_ID,
                "evidence": "Mara",
            }],
            "gap_changes": [],
            "readiness": None,
        })

        errors = validate_decision(
            parsed,
            event=message,
            recent_questions=[],
            source_text=message.payload["text"],
            active_gap_ids=[],
        )

        self.assertTrue(any("not an exact excerpt" in item for item in errors))

    def test_supported_readiness_must_cite_accepted_story_material(self) -> None:
        message = event(
            "message_submitted",
            {"text": "Mara waits until dawn.", "purpose": "answer"},
        )
        payload = TurnDecisionParsingTests.payload(intent="recommend_outline")
        payload["response"]["question"] = ""
        payload["readiness"] = {
            "lenses": {
                name: {"status": "supported", "evidence": "Invented evidence"}
                for name in ("carrier", "pressure", "visible_sequence", "ending")
            },
            "reason": "Ready.",
            "recommend_outline": True,
        }
        parsed = parse_turn_decision(payload)

        errors = validate_decision(
            parsed,
            event=message,
            recent_questions=[],
            source_text=message.payload["text"],
            active_gap_ids=[],
            accepted_source_texts=["Mara misses the last train."],
        )

        self.assertEqual(
            sum("accepted story material" in item for item in errors),
            4,
        )

    def test_non_acceptance_actions_cannot_establish_facts(self) -> None:
        options_request = event("suggestions_requested")
        decision = TurnDecision(
            intent="offer_suggestions",
            suggestions=(
                SuggestionCandidate("Leave", "Leaving creates an irreversible choice."),
                SuggestionCandidate("Stay", "Staying forces a confrontation."),
            ),
            facts=(
                FactCandidate("She leaves.", EVENT_ID, "She leaves"),
            ),
        )
        errors = validate_decision(
            decision,
            event=options_request,
            recent_questions=[],
            source_text="She leaves",
            active_gap_ids=[],
        )
        self.assertTrue(any("cannot establish" in item for item in errors), errors)

    def test_options_request_only_accepts_suggestion_intent(self) -> None:
        request = event("suggestions_requested")
        invalid = TurnDecision(
            intent="ask_question",
            question="What happens next?",
        )
        errors = validate_decision(
            invalid,
            event=request,
            recent_questions=[],
            source_text="",
            active_gap_ids=[],
        )
        self.assertTrue(any("not allowed" in item for item in errors))

        valid = TurnDecision(
            intent="offer_suggestions",
            suggestions=(
                SuggestionCandidate("Leave now", "Departure makes the loss immediate."),
                SuggestionCandidate("Miss the train", "Delay forces a confrontation."),
            ),
        )
        self.assertEqual(
            validate_decision(
                valid,
                event=request,
                recent_questions=[],
                source_text="",
                active_gap_ids=[],
            ),
            [],
        )

    def test_rejects_repeated_questions_and_unproven_facts(self) -> None:
        message = event(
            "message_submitted",
            {"text": "Mara misses the last train.", "purpose": "answer"},
        )
        raw = TurnDecision(
            intent="ask_question",
            question="What happens when Mara misses the last train?",
        )
        repeated = validate_decision(
            raw,
            event=message,
            recent_questions=["What happens when Mara misses the last train?"],
            source_text=message.payload["text"],
            active_gap_ids=[],
        )
        self.assertTrue(any("repeats" in item for item in repeated))

        parsed = parse_turn_decision({
            "response": {
                "intent": "ask_question",
                "guidance": "",
                "question": "What does Mara do next?",
                "focus": "consequence",
                "listening_for": "visible action",
                "suggestions": [],
            },
            "fact_candidates": [{
                "text": "Mara boards the train.",
                "source_event_id": EVENT_ID,
                "evidence": "boards the train",
            }],
            "gap_changes": [],
            "readiness": None,
        })
        provenance = validate_decision(
            parsed,
            event=message,
            recent_questions=[],
            source_text=message.payload["text"],
            active_gap_ids=[],
        )
        self.assertTrue(any("exact excerpt" in item for item in provenance))

    def test_direct_filmmaker_question_cannot_be_ignored_with_another_question(self) -> None:
        filmmaker_question = event(
            "message_submitted",
            {
                "text": "Would narration make this feel too distant?",
                "purpose": "question",
            },
        )
        interrogation = TurnDecision(
            intent="ask_question",
            question="What is the final image?",
        )
        errors = validate_decision(
            interrogation,
            event=filmmaker_question,
            recent_questions=[],
            source_text=filmmaker_question.payload["text"],
            active_gap_ids=[],
        )
        self.assertTrue(
            any("direct question" in item or "coach" in item for item in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()

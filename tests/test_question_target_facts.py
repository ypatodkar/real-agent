"""The question target may pause the questioning. It must not eat the answers.

Regression test for the Mystery box interview: five answers were submitted, the
target-reached rule rejected the decision because it carried a question, and the
whole turn — including every fact extracted from those answers — was discarded
in favour of a hardcoded pause message.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from second_unit.agent import StoryEditor
from second_unit.database import Repository
from second_unit.model import ModelResult
from second_unit.service import InterviewService

ANSWER = "She runs back along the platform and hammers on the driver's window."


class QueueModel:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def decide(self, **_: object) -> ModelResult:
        self.calls += 1
        payload = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return ModelResult(payload=payload, backend="fake",
                           model="fake-model", prompt_tokens=11, output_tokens=7)


def decision_with_question_and_fact(event_id: str) -> dict:
    """What the model naturally returns on an answer turn: a fact it extracted,
    and a follow-up question it does not yet know is unwelcome."""
    return {
        "response": {
            "intent": "ask_question",
            "guidance": "That gives us something visible.",
            "question": "What does the driver do when she reaches the window?",
            "focus": "consequence",
            "listening_for": "a visible action",
            "suggestions": [],
        },
        "fact_candidates": [{
            "text": "hammers on the driver's window",
            "source_event_id": event_id,
            "evidence": "hammers on the driver's window",
        }],
        "gap_changes": [],
        "readiness": None,
    }


class QuestionTargetPreservesFacts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Repository(Path(self.temporary.name) / "interview.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_a_paused_turn_still_records_the_facts_it_found(self) -> None:
        answer_event = "event_answer_00000003"

        def question(text: str) -> dict:
            return {
                "response": {"intent": "ask_question", "guidance": "Go on.",
                             "question": text, "focus": "opening",
                             "listening_for": "an action", "suggestions": []},
                "fact_candidates": [], "gap_changes": [], "readiness": None,
            }

        model = QueueModel([
            question("What does Mara do when the train pulls away?"),
            question("Who else is still on the platform?"),
            question("What is she carrying?"),
            decision_with_question_and_fact(answer_event),
        ])
        service = InterviewService(self.repository, StoryEditor(model))

        created = service.create_interview({
            "event_id": "event_start_0001",
            "title": "Last Train",
            "seed": "Mara misses the last train home.",
            "storytelling_format": "hybrid",
            "involvement_mode": "collaborative",
            "question_target": 3,
        })
        session_id = created["session"]["id"]
        revision = created["session"]["revision"]

        # Two answers take the interview to its three-question target.
        for index in (1, 2):
            result = service.submit_event(session_id, {
                "event_id": f"event_answer_0000000{index}",
                "expected_revision": revision,
                "kind": "message_submitted",
                "payload": {"text": f"Answer {index} with a visible action.",
                            "purpose": "answer"},
            })
            revision = result["session"]["revision"]

        # The target is now reached. This answer arrives with a fact worth keeping.
        result = service.submit_event(session_id, {
            "event_id": answer_event,
            "expected_revision": revision,
            "kind": "message_submitted",
            "payload": {"text": ANSWER, "purpose": "answer"},
        })

        self.assertEqual(result["current_response"]["question"], "",
                         "the target was reached, so no further question is asked")
        self.assertFalse(result["agent"]["used_fallback"],
                         "the model's decision should be kept, not replaced by a canned pause")
        self.assertIn(
            "hammers on the driver's window",
            [fact["text"] for fact in result["facts"]],
            "the fact extracted from the answer must survive the pause",
        )


if __name__ == "__main__":
    unittest.main()

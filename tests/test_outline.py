from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from second_unit.agent import StoryEditor
from second_unit.database import Repository
from second_unit.database import SessionBusy
from second_unit.model import NoModel
from second_unit.outline import (
    OUTLINE_TOOL_SPECS,
    OutlineRevisionConflict,
    OutlineToolError,
    OutlineToolkit,
)
from second_unit.model import ModelResult, ModelUnavailable
from second_unit.outline_agent import BEAT_SET_SCHEMA, OutlineAgent
from second_unit.service import InterviewService


class QueueModel:
    def __init__(self, payloads: list[dict] | None = None, *, unavailable: bool = False):
        self.payloads = list(payloads or [])
        self.unavailable = unavailable
        self.prompts: list[str] = []

    def decide(self, *, prompt: str, **_: object) -> ModelResult:
        self.prompts.append(prompt)
        if self.unavailable:
            raise ModelUnavailable("provider unavailable")
        return ModelResult(
            payload=self.payloads.pop(0), backend="fake", model="outline-test",
            prompt_tokens=20, output_tokens=30,
        )


def beat_payload(prefix: str = "") -> dict:
    return {"beats": [
        {
            "title": f"{prefix}Discovery",
            "summary": "Mara finds the empty platform and realizes the last train has gone.",
            "purpose": "Establish the immediate problem.",
            "source_refs": [],
        },
        {
            "title": f"{prefix}Decision",
            "summary": "Mara leaves the station and chooses the unlit road home.",
            "purpose": "Turn the problem into an active choice.",
            "source_refs": [],
        },
        {
            "title": f"{prefix}Arrival",
            "summary": "Mara reaches home changed by what happened on the walk.",
            "purpose": "Land the consequence in a final image.",
            "source_refs": [],
        },
    ]}


class OutlineToolkitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Repository(Path(self.temporary.name) / "outline.sqlite3")
        self.service = InterviewService(self.repository, StoryEditor(NoModel()))
        self.created = self.service.create_interview({
            "session_id": "interview_outline_0001",
            "event_id": "event_outline_start_0001",
            "title": "Last Train",
            "seed": "Mara misses the last train and must choose whether to walk home.",
            "storytelling_format": "hybrid",
            "involvement_mode": "collaborative",
        })
        self.session_id = self.created["session"]["id"]
        self.tools = OutlineToolkit(self.repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_selected_structure(self) -> dict:
        outline = self.tools.read_outline(self.session_id)
        proposed = self.tools.propose_story_structure(
            self.session_id, outline["revision"], "visual_progression",
            "The walk home", "A chain of visible encounters keeps the short cinematic.",
        )
        return self.tools.select_story_structure(
            self.session_id, proposed["revision"], proposed["structures"][0]["id"]
        )

    def test_tool_contract_names_are_unique_and_mark_filmmaker_actions(self) -> None:
        names = [item["name"] for item in OUTLINE_TOOL_SPECS]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all("input_schema" in item for item in OUTLINE_TOOL_SPECS))
        by_name = {item["name"]: item for item in OUTLINE_TOOL_SPECS}
        self.assertTrue(by_name["select_story_structure"]["requires_filmmaker_action"])
        self.assertTrue(by_name["record_beat_decision"]["requires_filmmaker_action"])
        self.assertFalse(by_name["read_interview_handoff"]["mutates"])
        self.assertNotIn("uniqueItems", str(BEAT_SET_SCHEMA))

    def test_agent_tool_calls_cannot_spoof_filmmaker_identity(self) -> None:
        outline = self.tools.read_outline(self.session_id)
        proposed = self.tools.execute("propose_story_structure", {
            "session_id": self.session_id,
            "expected_revision": outline["revision"],
            "kind": "visual_progression",
            "label": "Visible journey",
            "rationale": "The images carry the progression.",
        })
        structure_id = proposed["structures"][0]["id"]

        with self.assertRaisesRegex(OutlineToolError, "only the filmmaker"):
            self.tools.execute("select_story_structure", {
                "session_id": self.session_id,
                "expected_revision": proposed["revision"],
                "structure_id": structure_id,
            })
        with self.assertRaisesRegex(OutlineToolError, "identity"):
            self.tools.execute("select_story_structure", {
                "session_id": self.session_id,
                "expected_revision": proposed["revision"],
                "structure_id": structure_id,
                "actor": "filmmaker",
            })

        selected = self.tools.execute("select_story_structure", {
            "session_id": self.session_id,
            "expected_revision": proposed["revision"],
            "structure_id": structure_id,
        }, caller="filmmaker")
        self.assertEqual(selected["active_structure_id"], structure_id)

    def test_reading_handoff_does_not_create_or_mutate_an_outline(self) -> None:
        questionnaire = self.created["current_response"]
        first_question = questionnaire["questions"][0]
        answered = self.service.submit_event(self.session_id, {
            "event_id": "event_outline_answers_0001",
            "expected_revision": 1,
            "kind": "questionnaire_submitted",
            "payload": {
                "response_id": questionnaire["id"],
                "answers": [{
                    "question_id": first_question["id"],
                    "text": "Mara carries the film through the walk.",
                }],
            },
        })

        handoff = self.tools.read_interview_handoff(self.session_id)

        self.assertEqual(handoff["outline"], None)
        self.assertEqual(handoff["interview"]["revision"], answered["session"]["revision"])
        self.assertEqual(handoff["answers"][0]["question"], first_question["text"])
        self.assertEqual(len(handoff["open_questions"]), len(questionnaire["questions"]) - 1)
        with self.repository.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM outlines").fetchone()[0]
        self.assertEqual(count, 0)

    def test_structure_and_beat_tools_are_versioned_and_ordered(self) -> None:
        selected = self._create_selected_structure()
        self.assertEqual(selected["revision"], 2)
        self.assertEqual(selected["structures"][0]["approval_status"], "accepted")

        first = self.tools.create_beat(
            self.session_id, 2, "Empty platform",
            "Mara realizes the last train has gone.", origin="agent",
            source_refs=["event_outline_start_0001"],
        )
        first_id = first["beats"][0]["id"]
        self.assertEqual(first["beats"][0]["approval_status"], "proposed")
        self.assertEqual(first["beats"][0]["source_refs"], ["event_outline_start_0001"])

        second = self.tools.create_beat(
            self.session_id, 3, "The choice",
            "Mara leaves the station on foot.", position=1, origin="filmmaker",
        )
        second_id = second["beats"][0]["id"]
        self.assertEqual([beat["position"] for beat in second["beats"]], [1, 2])
        self.assertEqual(second["beats"][0]["approval_status"], "accepted")

        moved = self.tools.move_beat(
            self.session_id, 4, first_id, 1, actor="filmmaker"
        )
        self.assertEqual([beat["id"] for beat in moved["beats"]], [first_id, second_id])

        edited = self.tools.edit_beat(
            self.session_id, 5, first_id,
            summary="Mara watches the empty rails before turning toward the street.",
            actor="agent",
        )
        self.assertIn("empty rails", edited["beats"][0]["summary"])

        accepted = self.tools.record_beat_decision(
            self.session_id, 6, first_id, "accepted", note="Keep this opening."
        )
        self.assertEqual(accepted["beats"][0]["approval_status"], "accepted")

        archived = self.tools.archive_beat(
            self.session_id, 7, second_id, actor="filmmaker"
        )
        self.assertEqual(len(archived["beats"]), 1)
        self.assertEqual(archived["beats"][0]["position"], 1)
        with self.repository.connect() as db:
            versions = db.execute(
                "SELECT revision, operation FROM outline_versions ORDER BY revision"
            ).fetchall()
            approvals = db.execute("SELECT decision FROM outline_approvals").fetchall()
        self.assertEqual([row["revision"] for row in versions], list(range(9)))
        self.assertEqual(len(approvals), 3)

    def test_stale_revision_and_agent_changes_to_accepted_material_are_rejected(self) -> None:
        selected = self._create_selected_structure()
        beat_state = self.tools.create_beat(
            self.session_id, selected["revision"], "Walk begins",
            "Mara steps onto the empty road.", origin="filmmaker",
        )
        beat_id = beat_state["beats"][0]["id"]

        with self.assertRaises(OutlineRevisionConflict) as stale:
            self.tools.propose_story_structure(
                self.session_id, 0, "acts", "Three acts", "A conventional option."
            )
        self.assertEqual(stale.exception.details, {"current_revision": 3})

        for action in (
            lambda: self.tools.edit_beat(
                self.session_id, 3, beat_id, title="Changed silently", actor="agent"
            ),
            lambda: self.tools.move_beat(
                self.session_id, 3, beat_id, 1, actor="agent"
            ),
            lambda: self.tools.archive_beat(
                self.session_id, 3, beat_id, actor="agent"
            ),
        ):
            with self.subTest(action=action), self.assertRaisesRegex(
                OutlineToolError, "cannot"
            ):
                action()

        unchanged = self.tools.read_outline(self.session_id, create=False)
        self.assertEqual(unchanged["revision"], 3)
        self.assertEqual(unchanged["beats"][0]["title"], "Walk begins")

    def test_switching_structure_with_existing_beats_is_blocked(self) -> None:
        outline = self.tools.read_outline(self.session_id)
        first = self.tools.propose_story_structure(
            self.session_id, 0, "visual_progression", "Visual journey", "Built around images."
        )
        second = self.tools.propose_story_structure(
            self.session_id, 1, "narration_led", "Voice-led", "Built around memory."
        )
        first_id, second_id = [item["id"] for item in second["structures"]]
        selected = self.tools.select_story_structure(self.session_id, 2, first_id)
        with_beat = self.tools.create_beat(
            self.session_id, selected["revision"], "Platform", "The station empties."
        )

        with self.assertRaisesRegex(OutlineToolError, "archive beats"):
            self.tools.select_story_structure(
                self.session_id, with_beat["revision"], second_id
            )
        current = self.tools.read_outline(self.session_id, create=False)
        self.assertEqual(current["active_structure_id"], first_id)
        self.assertEqual(current["revision"], with_beat["revision"])

    def test_outline_agent_saves_a_complete_beat_set_atomically(self) -> None:
        selected = self._create_selected_structure()
        model = QueueModel([beat_payload()])
        agent = OutlineAgent(model, self.tools)

        outcome = agent.generate(self.session_id, selected["revision"])

        self.assertEqual(outcome.outline["revision"], selected["revision"] + 1)
        self.assertEqual(len(outcome.outline["beats"]), 3)
        self.assertTrue(all(
            beat["approval_status"] == "proposed"
            for beat in outcome.outline["beats"]
        ))
        self.assertEqual(outcome.meta["attempts"], 1)
        self.assertIn("selected_structure", model.prompts[0])
        with self.repository.connect() as db:
            versions = db.execute(
                "SELECT operation FROM outline_versions ORDER BY revision"
            ).fetchall()
            run = db.execute(
                "SELECT * FROM outline_agent_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(versions[-1]["operation"], "create_beat_batch")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["model_attempts"], 1)
        self.assertEqual(run["model"], "outline-test")

    def test_outline_agent_repairs_invalid_refs_without_partial_writes(self) -> None:
        selected = self._create_selected_structure()
        invalid = beat_payload()
        invalid["beats"][0]["source_refs"] = ["event_does_not_exist"]
        model = QueueModel([invalid, beat_payload("Repaired ")])
        agent = OutlineAgent(model, self.tools)

        outcome = agent.generate(self.session_id, selected["revision"])

        self.assertEqual(outcome.meta["attempts"], 2)
        self.assertEqual(len(outcome.outline["beats"]), 3)
        self.assertIn("unknown source IDs", model.prompts[1])

    def test_outline_approval_requires_filmmaker_accepted_beats(self) -> None:
        selected = self._create_selected_structure()
        proposed = self.tools.execute("create_beat_batch", {
            "session_id": self.session_id,
            "expected_revision": selected["revision"],
            "beats": beat_payload()["beats"],
        })
        with self.assertRaisesRegex(OutlineToolError, "accept or archive"):
            self.tools.record_outline_decision(
                self.session_id, proposed["revision"], "accepted"
            )
        with self.assertRaisesRegex(OutlineToolError, "only the filmmaker"):
            self.tools.execute("record_outline_decision", {
                "session_id": self.session_id,
                "expected_revision": proposed["revision"],
                "decision": "accepted",
            })

        state = proposed
        for beat in proposed["beats"]:
            state = self.tools.record_beat_decision(
                self.session_id, state["revision"], beat["id"], "accepted"
            )
        approved = self.tools.record_outline_decision(
            self.session_id, state["revision"], "accepted"
        )
        self.assertEqual(approved["status"], "approved")
        with self.assertRaisesRegex(OutlineToolError, "reopen"):
            self.tools.edit_beat(
                self.session_id, approved["revision"], approved["beats"][0]["id"],
                title="Silent approved change",
            )

        reopened = self.tools.record_outline_decision(
            self.session_id, approved["revision"], "reopened"
        )
        self.assertEqual(reopened["status"], "draft")

    def test_unavailable_outline_agent_does_not_change_the_outline(self) -> None:
        selected = self._create_selected_structure()
        agent = OutlineAgent(QueueModel(unavailable=True), self.tools)

        with self.assertRaisesRegex(OutlineToolError, "temporarily unavailable"):
            agent.generate(self.session_id, selected["revision"])

        current = self.tools.read_outline(self.session_id, create=False)
        self.assertEqual(current["revision"], selected["revision"])
        self.assertEqual(current["beats"], [])
        with self.repository.connect() as db:
            run = db.execute(
                "SELECT * FROM outline_agent_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "outline_agent_unavailable")
        self.assertEqual(run["provider_error_type"], "ModelUnavailable")

    def test_only_one_outline_generation_run_can_be_active(self) -> None:
        selected = self._create_selected_structure()
        first = self.repository.start_outline_agent_run(
            selected["id"], selected["revision"], "test", []
        )

        with self.assertRaisesRegex(SessionBusy, "already running"):
            self.repository.start_outline_agent_run(
                selected["id"], selected["revision"], "test", []
            )

        self.repository.update_outline_agent_run(first, status="failed")


if __name__ == "__main__":
    unittest.main()

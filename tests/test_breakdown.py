from pathlib import Path
import tempfile
import unittest

from second_unit.agent import StoryEditor
from second_unit.breakdown import BreakdownError, BreakdownToolkit
from second_unit.breakdown_agent import BreakdownAgent, ITEM_SET_SCHEMA
from second_unit.database import Repository
from second_unit.model import ModelResult, NoModel
from second_unit.outline import OutlineToolkit
from second_unit.screenplay import ScreenplayError, ScreenplayToolkit
from second_unit.screenplay_agent import ScreenplayAgent
from second_unit.service import InterviewService


class SceneModel:
    def __init__(self, beat_ids):
        self.beat_ids = beat_ids

    def decide(self, **_):
        return ModelResult(payload={"scenes": [
            {
                "beat_id": beat_id, "heading": f"INT. ROOM {index} - NIGHT",
                "action": f"Mara handles a lamp in room {index}.",
                "narration": "", "dialogue": [],
            }
            for index, beat_id in enumerate(self.beat_ids, 1)
        ]}, backend="fake", model="scene-test")


class RequirementModel:
    def __init__(self, scene_ids, prefix="Room"):
        self.scene_ids = scene_ids
        self.prefix = prefix

    def decide(self, **_):
        return ModelResult(payload={"items": [
            {
                "scene_id": scene_id, "category": "location",
                "name": f"{self.prefix} {index}", "details": "Interior, available at night.",
            }
            for index, scene_id in enumerate(self.scene_ids, 1)
        ]}, backend="fake", model="breakdown-test")


class BreakdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.temp.name) / "breakdown.db")
        service = InterviewService(self.repo, StoryEditor(NoModel()))
        created = service.create_interview({
            "session_id": "interview_breakdown_0001",
            "event_id": "event_breakdown_0001",
            "title": "Rooms", "seed": "Mara searches two rooms for a missing lamp.",
            "storytelling_format": "hybrid", "involvement_mode": "collaborative",
        })
        self.session_id = created["session"]["id"]
        outlines = OutlineToolkit(self.repo)
        proposed = outlines.propose_story_structure(
            self.session_id, 0, "sequences", "Two rooms", "A visible search."
        )
        selected = outlines.select_story_structure(
            self.session_id, 1, proposed["structures"][0]["id"]
        )
        outline = outlines.create_beat_batch(self.session_id, selected["revision"], [
            {"title": "First room", "summary": "Mara searches the first room.", "purpose": "Begin.", "source_refs": []},
            {"title": "Second room", "summary": "Mara finds the lamp.", "purpose": "Resolve.", "source_refs": []},
        ])
        for beat in outline["beats"]:
            outline = outlines.record_beat_decision(
                self.session_id, outline["revision"], beat["id"], "accepted"
            )
        outline = outlines.record_outline_decision(
            self.session_id, outline["revision"], "accepted"
        )
        screenplays = ScreenplayToolkit(self.repo)
        screenplay = screenplays.choose_mode(self.session_id, 0, "visual")
        screenplay = ScreenplayAgent(
            SceneModel([beat["id"] for beat in outline["beats"]]), screenplays
        ).generate(self.session_id, screenplay["revision"])["screenplay"]
        self.screenplay = screenplays.record_screenplay_decision(
            self.session_id, screenplay["revision"], "accepted"
        )
        self.screenplays = screenplays
        self.tools = BreakdownToolkit(self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def test_generate_edit_add_remove_and_approve_breakdown(self):
        self.assertNotIn("maxItems", str(ITEM_SET_SCHEMA))
        handoff = self.tools.read_handoff(self.session_id)
        self.assertTrue(handoff["available"])
        self.assertIsNone(handoff["breakdown"])
        scene_ids = [scene["id"] for scene in self.screenplay["scenes"]]

        outcome = BreakdownAgent(RequirementModel(scene_ids), self.tools).generate(
            self.session_id, 0
        )
        breakdown = outcome["breakdown"]
        self.assertEqual(len(breakdown["scenes"]), 2)
        self.assertTrue(all(scene["items"] for scene in breakdown["scenes"]))

        first = breakdown["scenes"][0]["items"][0]
        breakdown = self.tools.edit_item(
            self.session_id, breakdown["revision"], first["id"],
            "location", "Apartment bedroom", "Needs blackout control.",
        )
        breakdown = self.tools.add_item(
            self.session_id, breakdown["revision"], scene_ids[0],
            "prop", "Lamp", "Hero prop with matching backup.",
        )
        added = next(
            item for scene in breakdown["scenes"] for item in scene["items"]
            if item["name"] == "Lamp"
        )
        breakdown = self.tools.archive_item(
            self.session_id, breakdown["revision"], added["id"]
        )
        approved = self.tools.record_breakdown_decision(
            self.session_id, breakdown["revision"], "accepted"
        )
        self.assertEqual(approved["status"], "approved")
        self.assertTrue(all(
            item["approval_status"] == "accepted"
            for scene in approved["scenes"] for item in scene["items"]
        ))
        with self.assertRaisesRegex(BreakdownError, "reopen"):
            self.tools.edit_item(
                self.session_id, approved["revision"], first["id"],
                "location", "Changed", "",
            )

    def test_screenplay_cannot_reopen_after_breakdown_starts(self):
        scene_ids = [scene["id"] for scene in self.screenplay["scenes"]]
        BreakdownAgent(RequirementModel(scene_ids), self.tools).generate(self.session_id, 0)
        with self.assertRaisesRegex(ScreenplayError, "cannot be reopened"):
            self.screenplays.record_screenplay_decision(
                self.session_id, self.screenplay["revision"], "reopened"
            )

    def test_regeneration_atomically_replaces_active_items_and_keeps_history(self):
        scene_ids = [scene["id"] for scene in self.screenplay["scenes"]]
        first = BreakdownAgent(RequirementModel(scene_ids), self.tools).generate(
            self.session_id, 0
        )["breakdown"]
        old_ids = {
            item["id"] for scene in first["scenes"] for item in scene["items"]
        }

        second = BreakdownAgent(
            RequirementModel(scene_ids, prefix="New room"), self.tools
        ).generate(
            self.session_id, first["revision"], replace_existing=True
        )["breakdown"]

        new_items = [item for scene in second["scenes"] for item in scene["items"]]
        self.assertTrue(all(item["name"].startswith("New room") for item in new_items))
        self.assertTrue(old_ids.isdisjoint({item["id"] for item in new_items}))
        with self.repo.transaction() as db:
            archived = db.execute(
                "SELECT COUNT(*) FROM breakdown_items WHERE breakdown_id=? AND status='archived'",
                (second["id"],),
            ).fetchone()[0]
            operation = db.execute(
                "SELECT operation FROM breakdown_versions WHERE breakdown_id=? ORDER BY revision DESC LIMIT 1",
                (second["id"],),
            ).fetchone()[0]
        self.assertEqual(archived, len(old_ids))
        self.assertEqual(operation, "replace_item_batch")

    def test_agent_rejects_unknown_scene_without_partial_save(self):
        with self.assertRaisesRegex(BreakdownError, "valid requirement set"):
            BreakdownAgent(RequirementModel(["scene_unknown"]), self.tools).generate(
                self.session_id, 0
            )
        self.assertIsNone(self.tools.read_handoff(self.session_id)["breakdown"])
        with self.repo.transaction() as db:
            run = db.execute(
                "SELECT * FROM breakdown_agent_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(run["status"], "failed")


if __name__ == "__main__":
    unittest.main()

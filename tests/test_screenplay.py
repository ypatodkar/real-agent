from pathlib import Path
import tempfile
import unittest

from second_unit.agent import StoryEditor
from second_unit.database import Repository
from second_unit.model import ModelResult, ModelUnavailable, NoModel
from second_unit.outline import OutlineToolkit
from second_unit.screenplay import ScreenplayError, ScreenplayToolkit
from second_unit.screenplay_agent import SCENE_SCHEMA, ScreenplayAgent
from second_unit.service import InterviewService


class SceneModel:
    def __init__(self, beat_ids):
        self.beat_ids = beat_ids

    def decide(self, **_):
        return ModelResult(payload={"scenes": [
            {"beat_id": beat_id, "heading": f"EXT. ROAD - NIGHT {index}",
             "action": f"Mara crosses the road during movement {index}.",
             "narration": "", "dialogue": []}
            for index, beat_id in enumerate(self.beat_ids, 1)
        ]}, backend="fake", model="scene-test")


class RetrySceneModel(SceneModel):
    backend = "fake"
    model = "scene-retry-test"

    def __init__(self, beat_ids):
        super().__init__(beat_ids)
        self.calls = 0

    def decide(self, **arguments):
        self.calls += 1
        if self.calls == 1:
            try:
                raise TimeoutError("provider deadline exceeded")
            except TimeoutError as exc:
                raise ModelUnavailable("provider unavailable") from exc
        return super().decide(**arguments)


class ScreenplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.temp.name) / "screenplay.db")
        service = InterviewService(self.repo, StoryEditor(NoModel()))
        created = service.create_interview({
            "session_id": "interview_screenplay_0001", "event_id": "event_screenplay_0001",
            "title": "Last Train", "seed": "Mara misses the last train and walks home.",
            "storytelling_format": "hybrid", "involvement_mode": "collaborative",
        })
        self.session_id = created["session"]["id"]
        outlines = OutlineToolkit(self.repo)
        outline = outlines.read_outline(self.session_id)
        proposed = outlines.propose_story_structure(
            self.session_id, 0, "sequences", "Three movements", "A visible journey."
        )
        selected = outlines.select_story_structure(
            self.session_id, 1, proposed["structures"][0]["id"]
        )
        state = outlines.create_beat_batch(self.session_id, 2, [
            {"title": "Missed train", "summary": "Mara sees the empty rails.", "purpose": "Start.", "source_refs": []},
            {"title": "Walk", "summary": "Mara takes the road home.", "purpose": "Change.", "source_refs": []},
            {"title": "Arrival", "summary": "Mara reaches her door.", "purpose": "End.", "source_refs": []},
        ])
        for beat in state["beats"]:
            state = outlines.record_beat_decision(
                self.session_id, state["revision"], beat["id"], "accepted"
            )
        self.outline = outlines.record_outline_decision(
            self.session_id, state["revision"], "accepted"
        )
        self.tools = ScreenplayToolkit(self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def test_mode_generation_and_scene_edit_are_versioned(self):
        self.assertNotIn("maxItems", str(SCENE_SCHEMA))
        handoff = self.tools.read_handoff(self.session_id)
        self.assertTrue(handoff["available"])
        self.assertEqual(len(handoff["modes"]), 4)

        chosen = self.tools.choose_mode(self.session_id, 0, "hybrid")
        self.assertEqual(chosen["revision"], 1)
        beat_ids = [beat["id"] for beat in self.outline["beats"]]
        generated = ScreenplayAgent(SceneModel(beat_ids), self.tools).generate(
            self.session_id, 1
        )["screenplay"]
        self.assertEqual(len(generated["scenes"]), 3)
        self.assertTrue(all(scene["approval_status"] == "proposed" for scene in generated["scenes"]))

        first = generated["scenes"][0]
        edited = self.tools.edit_scene(
            self.session_id, generated["revision"], first["id"],
            "EXT. EMPTY STATION - NIGHT", "Mara watches the empty rails.", "", [],
        )
        self.assertEqual(edited["scenes"][0]["approval_status"], "accepted")
        self.assertEqual(edited["scenes"][0]["heading"], "EXT. EMPTY STATION - NIGHT")
        with self.assertRaisesRegex(ScreenplayError, "remove scenes"):
            self.tools.choose_mode(self.session_id, edited["revision"], "visual")

    def test_agent_must_cover_every_accepted_beat(self):
        chosen = self.tools.choose_mode(self.session_id, 0, "visual")
        beat_ids = [beat["id"] for beat in self.outline["beats"]]
        with self.assertRaisesRegex(ScreenplayError, "valid scene draft"):
            ScreenplayAgent(SceneModel(beat_ids[:1]), self.tools).generate(
                self.session_id, chosen["revision"]
            )
        current = self.tools.read(self.outline["id"])
        self.assertEqual(current["scenes"], [])

    def test_transient_provider_failure_is_retried_and_logged(self):
        chosen = self.tools.choose_mode(self.session_id, 0, "hybrid")
        beat_ids = [beat["id"] for beat in self.outline["beats"]]
        model = RetrySceneModel(beat_ids)

        outcome = ScreenplayAgent(model, self.tools).generate(
            self.session_id, chosen["revision"]
        )

        self.assertEqual(model.calls, 2)
        self.assertEqual(outcome["agent"]["attempts"], 2)
        with self.repo.transaction() as db:
            run = db.execute(
                "SELECT * FROM screenplay_agent_runs WHERE id = ?",
                (outcome["agent"]["run_id"],),
            ).fetchone()
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["model_attempts"], 2)
        self.assertIn("provider_retryable", run["diagnostics_json"])


if __name__ == "__main__":
    unittest.main()

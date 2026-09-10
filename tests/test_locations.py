from pathlib import Path
import tempfile
import unittest

from second_unit.agent import StoryEditor
from second_unit.breakdown import BreakdownToolkit
from second_unit.breakdown_agent import BreakdownAgent
from second_unit.database import Repository
from second_unit.locations import LocationError, LocationScoutService
from second_unit.model import ModelResult, NoModel
from second_unit.outline import OutlineToolkit
from second_unit.screenplay import ScreenplayToolkit
from second_unit.screenplay_agent import ScreenplayAgent
from second_unit.service import InterviewService


class SceneModel:
    def __init__(self, beat_id: str) -> None:
        self.beat_id = beat_id

    def decide(self, **_):
        return ModelResult(payload={"scenes": [{
            "beat_id": self.beat_id,
            "heading": "INT. DINER - NIGHT",
            "action": "Mara waits alone at the counter.",
            "narration": "",
            "dialogue": [],
        }]}, backend="fake", model="scene-test")


class RequirementModel:
    def __init__(self, scene_id: str) -> None:
        self.scene_id = scene_id

    def decide(self, **_):
        return ModelResult(payload={"items": [{
            "scene_id": self.scene_id,
            "category": "location",
            "name": "Late-night diner",
            "details": "Quiet interior with a long counter.",
        }]}, backend="fake", model="breakdown-test")


class FakeMapsClient:
    def __init__(self) -> None:
        self.queries = []

    def search_places(self, query, latitude, longitude, radius_meters):
        self.queries.append((query, latitude, longitude, radius_meters))
        return [
            {
                "place_id": "place_near", "name": "Near Diner",
                "address": "1 Main Street", "latitude": 34.1,
                "longitude": -118.2, "rating": 4.6,
                "maps_uri": "https://maps.google.com/?cid=near",
            },
            {
                "place_id": "place_far", "name": "Far Diner",
                "address": "99 Long Road", "latitude": 35.0,
                "longitude": -119.0, "rating": 4.0,
                "maps_uri": "https://maps.google.com/?cid=far",
            },
        ]

    def route_matrix(self, origin_place_id, destination_place_ids):
        self.origin = origin_place_id
        self.destinations = destination_place_ids
        return {
            "place_near": {"duration_seconds": 720, "distance_meters": 6400},
            "place_far": {"duration_seconds": 3600, "distance_meters": 64000},
        }


class LocationScoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Repository(Path(self.temp.name) / "locations.db")
        interviews = InterviewService(self.repo, StoryEditor(NoModel()))
        created = interviews.create_interview({
            "session_id": "interview_location_0001",
            "event_id": "event_location_0001",
            "title": "Diner", "seed": "Mara waits in a diner after midnight.",
            "storytelling_format": "hybrid", "involvement_mode": "collaborative",
        })
        self.session_id = created["session"]["id"]
        outlines = OutlineToolkit(self.repo)
        outline = outlines.propose_story_structure(
            self.session_id, 0, "sequences", "One quiet night", "A compact progression."
        )
        outline = outlines.select_story_structure(
            self.session_id, 1, outline["structures"][0]["id"]
        )
        outline = outlines.create_beat_batch(self.session_id, outline["revision"], [{
            "title": "Waiting", "summary": "Mara waits at the diner counter.",
            "purpose": "Establish the night.", "source_refs": [],
        }])
        outline = outlines.record_beat_decision(
            self.session_id, outline["revision"], outline["beats"][0]["id"], "accepted"
        )
        outline = outlines.record_outline_decision(
            self.session_id, outline["revision"], "accepted"
        )
        screenplays = ScreenplayToolkit(self.repo)
        screenplay = screenplays.choose_mode(self.session_id, 0, "visual")
        screenplay = ScreenplayAgent(
            SceneModel(outline["beats"][0]["id"]), screenplays
        ).generate(self.session_id, screenplay["revision"])["screenplay"]
        screenplay = screenplays.record_screenplay_decision(
            self.session_id, screenplay["revision"], "accepted"
        )
        breakdowns = BreakdownToolkit(self.repo)
        BreakdownAgent(
            RequirementModel(screenplay["scenes"][0]["id"]), breakdowns
        ).generate(self.session_id, 0)
        self.client = FakeMapsClient()
        self.scout = LocationScoutService(self.repo, self.client)

    def test_search_filters_by_travel_time_and_persists_only_place_ids(self):
        result = self.scout.search(self.session_id, {
            "base_place_id": "place_base",
            "base_latitude": 34.05,
            "base_longitude": -118.25,
            "max_travel_minutes": 30,
        })

        candidates = result["groups"][0]["candidates"]
        self.assertEqual([item["place_id"] for item in candidates], ["place_near"])
        self.assertEqual(self.client.origin, "place_base")
        self.assertIn("Late-night diner", self.client.queries[0][0])

        with self.repo.transaction() as db:
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(location_candidates)")
            }
            saved = db.execute(
                "SELECT place_id, status FROM location_candidates WHERE search_id=?",
                (result["search_id"],),
            ).fetchone()
        self.assertEqual(saved["place_id"], "place_near")
        self.assertEqual(saved["status"], "suggested")
        self.assertTrue({"name", "address", "latitude", "longitude"}.isdisjoint(columns))

        handoff = BreakdownToolkit(self.repo).read_handoff(self.session_id)
        attached = handoff["breakdown"]["scenes"][0]["items"][0]["location_links"]
        self.assertEqual(attached[0]["place_id"], "place_near")
        self.assertIn("query_place_id=place_near", attached[0]["maps_url"])

        item_id = result["groups"][0]["breakdown_item_id"]
        shortlisted = self.scout.set_shortlisted(
            self.session_id, result["search_id"], "place_near", item_id, True
        )
        self.assertEqual(shortlisted["status"], "shortlisted")
        reloaded = BreakdownToolkit(self.repo).read_handoff(self.session_id)
        link = reloaded["breakdown"]["scenes"][0]["items"][0]["location_links"][0]
        self.assertEqual(link["status"], "shortlisted")

    def test_search_rejects_invalid_coordinates_without_calling_google(self):
        with self.assertRaisesRegex(LocationError, "outside the supported range"):
            self.scout.search(self.session_id, {
                "base_place_id": "place_base",
                "base_latitude": 34.05,
                "base_longitude": -999,
                "max_travel_minutes": 30,
            })
        self.assertEqual(self.client.queries, [])


if __name__ == "__main__":
    unittest.main()

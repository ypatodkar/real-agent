import unittest

from core.interview import Session, Turn, build_prompt, parse


class InterviewQualityTests(unittest.TestCase):
    def payload(self, question):
        return {
            "response_kind": "question",
            "guidance": "",
            "suggestions": [],
            "question": question,
            "listening_for": "a visible decision",
            "focus": "choice",
            "established": [],
            "critical_gaps": ["The ending is undecided."],
            "readiness": 0.4,
            "reason": "The visible sequence is incomplete.",
            "should_continue": True,
        }

    def test_prompt_includes_storytelling_format(self):
        session = Session("p1", storytelling_format="narrated")
        self.assertIn("Storytelling format: narrated", build_prompt(session))
        self.assertIn("narrator's relationship", build_prompt(session))

    def test_rejects_repeated_question(self):
        session = Session("p1")
        session.turns.append(Turn("ending", "dynamic", "What is the last image we see?"))
        with self.assertRaisesRegex(ValueError, "repeats"):
            parse(self.payload("What is the last image that we see?"), session)

    def test_unexplained_readiness_drop_is_held(self):
        session = Session("p1", readiness=0.7, critical_gaps=["The ending is undecided."])
        session.apply_assessment(self.payload("What changes in the final scene?"))
        self.assertEqual(session.readiness, 0.7)

    def test_new_gap_allows_readiness_drop(self):
        session = Session("p1", readiness=0.7, critical_gaps=["The ending is undecided."])
        payload = self.payload("What changes in the final scene?")
        payload["critical_gaps"] = ["The narrator's account now contradicts the visible ending."]
        session.apply_assessment(payload)
        self.assertEqual(session.readiness, 0.4)

    def test_suggestions_are_allowed_in_guidance(self):
        payload = self.payload("Which direction feels closest to your film?")
        payload["response_kind"] = "suggestions"
        payload["guidance"] = (
            "Two possibilities: keep the confrontation public for social pressure, "
            "or move it somewhere private so silence becomes threatening."
        )
        payload["suggestions"] = [
            {"id": "public", "label": "Keep it public", "detail": "Social pressure raises the cost."},
            {"id": "private", "label": "Make it private", "detail": "Silence makes the threat intimate."},
        ]
        result = parse(payload, Session("p1"))
        self.assertEqual(result["response_kind"], "suggestions")
        self.assertIn("Two possibilities", result["guidance"])
        self.assertEqual(len(result["suggestions"]), 2)

    def test_explicit_help_request_requires_suggestions(self):
        session = Session("p1")
        self.assertIn("MUST use response_kind `suggestions`", build_prompt(
            session, require_suggestions=True
        ))
        with self.assertRaisesRegex(ValueError, "explicitly requested"):
            parse(self.payload("What happens next?"), session, require_suggestions=True)


if __name__ == "__main__":
    unittest.main()

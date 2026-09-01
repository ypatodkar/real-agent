"""Turning the interview into scenes.

This stage writes the film. It takes everything the interview established and
develops it into a complete scene outline, filling in whatever the material
needs — locations, beats, connective tissue, an ending — rather than stopping
at the edge of what was said out loud.

Output is a scene outline, not a screenplay. For a short film that is the more
useful artifact — it is what a director blocks from and what the breakdown
reads to find cast, locations and props.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MIN_ANSWERS = 4          # below this there is not enough to assemble anything


SYSTEM = """You are writing a scene outline for a short film, 5 to 20 minutes,
from an interview with the writer.

Use everything the writer gave you — their words, their images, their
specifics — and develop the rest. Where the material runs out, make a choice
and write it. A complete outline they can react to is worth more than a set of
holes they have to fill.

Scene count: 4 to 9. Shorts do not have more, and a short with three has not
been thought through.

For each scene give:
  n         scene number, from 1
  slug      a location in screenplay style, upper case: INT. FLAT - NIGHT
  who       the characters in the scene
  action    2 to 4 sentences. What happens and what we see. Present tense.
  why       one line: what this scene is for. Why the film is poorer without it.
  from      short quote or paraphrase of the answer this grew from

Return JSON: {"title": str, "logline": str, "scenes": [...]}
"""


SCENES_SCHEMA = {
    "type": "object",
    "required": ["title", "logline", "scenes"],
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "slug", "who", "action", "why", "from"],
                "properties": {
                    "n": {"type": "integer"},
                    "slug": {"type": "string"},
                    "who": {"type": "array", "items": {"type": "string"}},
                    "action": {"type": "string"},
                    "why": {"type": "string"},
                    "from": {"type": "string"},
                },
            },
        },
    },
}


@dataclass
class Outline:
    title: str
    logline: str
    scenes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"title": self.title, "logline": self.logline, "scenes": self.scenes}


def readiness(session) -> tuple[bool, str]:
    """Is there enough here to write anything from?"""
    answered = [t for t in session.turns if t.answer and not t.skipped]
    minimums = {"ai_led": (2, 20), "collaborative": (3, 40), "author_led": (4, 60)}
    min_answers, min_words = minimums[session.collaboration_mode]
    if len(answered) < min_answers:
        return False, (f"{len(answered)} of {min_answers} questions answered — "
                       f"a few more and there will be enough to work with")
    words = sum(t.words for t in answered)
    if words < min_words:
        return False, "the answers are very short — try one more with some detail"
    return True, f"{len(answered)} answers, {words} words"


def build_prompt(session) -> str:
    answered = [t for t in session.turns if t.answer and not t.skipped]
    body = "\n\n".join(f"Q: {t.question}\nA: {t.answer}" for t in answered)
    seed = f"They started with: {session.seed}\n\n" if session.seed else ""
    format_instruction = {
        "narrated": "The film is narrated. Make clear what narration carries and what each scene shows rather than says.",
        "dialogue_led": "The film is dialogue-led. Build scenes around playable conversational turns and visible behavior.",
        "hybrid": "The film is hybrid. Keep narration and dialogue roles distinct in each scene.",
        "not_sure": "The storytelling format is undecided. Choose the one the material wants and write it that way.",
    }.get(session.storytelling_format, "")
    return (
        f"{seed}Storytelling format: {session.storytelling_format}. {format_instruction}\n\n"
        f"The interview:\n\n{body}\n\n"
        "Write the outline."
    )


def parse(payload: dict, session) -> Outline:
    scenes = payload.get("scenes") or []
    if not scenes:
        raise ValueError("no scenes in response")

    for i, scene in enumerate(scenes, 1):
        scene.setdefault("n", i)
        scene.setdefault("who", [])

    return Outline(
        title=(payload.get("title") or "Untitled").strip(),
        logline=(payload.get("logline") or "").strip(),
        scenes=scenes,
    )

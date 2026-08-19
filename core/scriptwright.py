"""Turning the interview into scenes.

The interviewer is careful never to write your film. This stage has to be
equally careful, in a harder position — it is producing prose, and producing
prose is exactly where a model starts inventing.

So the rule is **assemble, do not author.** Every scene must trace to something
the writer actually said. If they never mentioned a brother, there is no
brother. Where the material runs out, the scene says what is missing rather
than filling it in, because a gap the writer can see is worth more than a
plausible invention they have to notice and delete.

Output is a scene outline, not a screenplay. For a short film that is the more
useful artifact — it is what a director blocks from and what the breakdown
reads to find cast, locations and props.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MIN_ANSWERS = 4          # below this there is not enough to assemble anything


SYSTEM = """You are assembling a scene outline for a short film, 5 to 20
minutes, from an interview with the writer.

THE RULE: you assemble, you do not author.

Everything in the outline must come from what the writer said. Use their
words, their images, their specifics. You may arrange, order and compress.
You may not invent.

If something a scene needs is missing — a location was never named, we never
learn how it ends — write it in the scene's `missing` field. Do not fill the
gap yourself. A hole the writer can see is worth more than a plausible
invention they have to find and delete.

Scene count: 4 to 9. Shorts do not have more, and a short with three has not
been thought through.

For each scene give:
  n         scene number, from 1
  slug      a location in screenplay style, upper case: INT. FLAT - NIGHT
            If they never said where, use INT. UNSPECIFIED - DAY and say so
            in `missing`.
  who       character names or descriptions the writer used
  action    2 to 4 sentences. What happens and what we see. Present tense.
  why       one line: what this scene is for. Why the film is poorer without it.
  missing   what the writer has not decided yet. Empty string if nothing.
  from      short quote or paraphrase of the answer this came from

Return JSON: {"title": str, "logline": str, "scenes": [...], "gaps": [str],
"ai_added": [str]}
title and logline must also be assembled, not invented. gaps lists the big
unanswered questions across the whole film.
"""

AI_LED = """
MODE-SPECIFIC EXCEPTION TO THE NO-INVENTION RULE ABOVE: the writer selected
AI-led development. You may develop coherent missing story
material after honoring every fact and preference they supplied. Do not
overwrite their decisions. List every substantial detail you introduced in
`ai_added` so authorship remains transparent. Produce a complete, usable
outline rather than leaving gaps you can responsibly bridge.
"""

COLLABORATIVE = """
MODE-SPECIFIC EXCEPTION TO THE NO-INVENTION RULE ABOVE: the writer selected
collaborative development. Preserve all of their decisions.
You may add only minor connective material needed to make the supplied moments
cohere; do not invent a new central character, goal, conflict, turn, or ending.
List every addition in `ai_added`. Leave consequential undecided choices in
`gaps` for the writer.
"""

SCENES_SCHEMA = {
    "type": "object",
    "required": ["title", "logline", "scenes", "gaps", "ai_added"],
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "ai_added": {"type": "array", "items": {"type": "string"}},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "slug", "who", "action", "why", "missing", "from"],
                "properties": {
                    "n": {"type": "integer"},
                    "slug": {"type": "string"},
                    "who": {"type": "array", "items": {"type": "string"}},
                    "action": {"type": "string"},
                    "why": {"type": "string"},
                    "missing": {"type": "string"},
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
    gaps: list[str] = field(default_factory=list)
    ai_added: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)   # audit findings

    def to_dict(self) -> dict:
        return {"title": self.title, "logline": self.logline, "scenes": self.scenes,
                "gaps": self.gaps, "ai_added": self.ai_added,
                "unsupported": self.unsupported}


def system_for(session) -> str:
    if session.collaboration_mode == "ai_led":
        return SYSTEM + AI_LED
    if session.collaboration_mode == "collaborative":
        return SYSTEM + COLLABORATIVE
    return SYSTEM


def readiness(session) -> tuple[bool, str]:
    """Is there enough here to assemble anything honest?"""
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
    mode_instruction = {
        "ai_led": "Develop missing material as permitted, and disclose every addition.",
        "collaborative": "Bridge only minor connective gaps, and disclose every addition.",
        "author_led": "Assemble the outline using only what is above.",
    }[session.collaboration_mode]
    return f"{seed}The interview:\n\n{body}\n\n{mode_instruction}"


# ----------------------------------------------------------------- the audit


def _terms(text: str) -> set[str]:
    """Content words, lowercased. Crude on purpose — it only has to catch nouns."""
    stop = {
        "the", "and", "that", "this", "with", "from", "have", "has", "his", "her",
        "she", "they", "them", "their", "what", "when", "where", "which", "who",
        "него", "into", "onto", "about", "just", "then", "than", "will", "would",
        "been", "was", "were", "are", "for", "not", "but", "him", "you", "your",
        "int", "ext", "day", "night", "unspecified", "continuous", "later",
    }
    return {w for w in re.findall(r"[a-z][a-z'-]{3,}", text.lower()) if w not in stop}


def audit(outline: Outline, session) -> list[str]:
    """Flag proper nouns in the outline that the writer never used.

    Not a proof — a smoke alarm. A model that invents tends to invent *names*,
    and a name nobody typed is the cheapest possible signal that it happened.
    """
    said = _terms(" ".join(
        [session.seed] + [t.answer for t in session.turns if t.answer]
    ))

    # Only mid-sentence capitals count. A capital after a full stop or at the
    # start of a field is grammar, not a name — matching those flagged "The"
    # and "This" as inventions, which is noise that hides the real signal.
    ignore = {
        "int", "ext", "day", "night", "later", "continuous", "morning",
        "evening", "afternoon", "flashback", "montage",
    }

    invented: set[str] = set()
    for scene in outline.scenes:
        for text in [scene.get("action", ""), *(scene.get("who") or [])]:
            for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", text):
                before = text[:match.start()].rstrip()
                if not before or before[-1] in ".!?:":
                    continue                      # sentence-initial — grammar
                word = match.group(0)
                if word.lower() not in said and word.lower() not in ignore:
                    invented.add(word)

    return sorted(invented)


def parse(payload: dict, session) -> Outline:
    scenes = payload.get("scenes") or []
    if not scenes:
        raise ValueError("no scenes in response")

    for i, scene in enumerate(scenes, 1):
        scene.setdefault("n", i)
        scene.setdefault("missing", "")
        scene.setdefault("who", [])

    outline = Outline(
        title=(payload.get("title") or "Untitled").strip(),
        logline=(payload.get("logline") or "").strip(),
        scenes=scenes,
        gaps=[g for g in (payload.get("gaps") or []) if g],
        ai_added=[x for x in (payload.get("ai_added") or []) if x],
    )
    if session.collaboration_mode == "author_led":
        outline.unsupported = audit(outline, session)
    return outline

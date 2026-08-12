"""The interviewer.

Every other AI writing tool generates. You give it a premise, it gives you a
story, and the story is the model's. This one asks the question a good script
editor would ask and then gets out of the way.

The hard rule, enforced in the prompt and checked after: **never propose the
answer.** No "perhaps he could…", no three options to choose from, no example
that is really a suggestion. A question that contains its own answer has done
the writer's thinking for them, which is the one thing this must not do.

Which question to ask is chosen from a bank of *shapes*, and which shape gets
used is informed by what has actually made writers write — see core.telemetry.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------- the stages

STAGES = ["premise", "character", "conflict", "shape", "scenes"]

STAGE_GOAL = {
    "premise":   "find what the film is actually about, underneath the pitch",
    "character": "find who it happens to, and what they are wrong about",
    "conflict":  "find what is in the way, and what it costs",
    "shape":     "find the turn — the moment there is no going back",
    "scenes":    "find the handful of scenes that carry it",
}


@dataclass(frozen=True)
class Shape:
    """A kind of question, not a question. The model writes the actual words."""

    id: str
    stage: str
    probes: str          # what this shape is trying to surface
    guidance: str        # how to aim it, given the conversation so far
    example: str         # for the model's calibration — never shown to the user


SHAPES: list[Shape] = [
    Shape("specificity", "premise",
          "vagueness hiding as theme",
          "Take the vaguest noun they used and demand the exact one.",
          "You said 'a job'. Which job, exactly? Name it."),
    Shape("under_the_pitch", "premise",
          "the real subject beneath the logline",
          "Ask what the film is about when it is not about its plot.",
          "Take the plot away. What is left that you still care about?"),
    Shape("one_image", "premise",
          "the image that survives",
          "Ask for the single frame a viewer remembers a week later.",
          "What is the one shot someone describes to a friend?"),

    Shape("wrong_belief", "character",
          "the character's self-deception",
          "Ask what they believe about themselves that is not true.",
          "What does she believe about herself that the film disproves?"),
    Shape("owed", "character",
          "entitlement as motive",
          "Ask what they think the world owes them.",
          "What does he think he is owed?"),
    Shape("absence", "character",
          "who is missing",
          "Ask about someone not in the room who is shaping it anyway.",
          "Who is not in this film but is in every scene of it?"),

    Shape("cost", "conflict",
          "the price of winning",
          "Ask what getting what they want will cost them.",
          "She gets exactly what she wanted. What does it cost her?"),
    Shape("want_vs_need", "conflict",
          "the gap between want and need",
          "Ask for both, separately, and let the gap show.",
          "What does he want? Now — what does he need? Are they the same?"),
    Shape("if_nothing", "conflict",
          "stakes by subtraction",
          "Ask what happens if nothing changes. If nothing, there is no film.",
          "If he does nothing at all, what happens?"),

    Shape("no_return", "shape",
          "the irreversible moment",
          "Ask for the point past which the old life is unavailable.",
          "At what moment can she not go back?"),
    Shape("last_image", "shape",
          "the ending as a picture",
          "Ask for the final frame, not the final event.",
          "What is the last thing we see? Not what happens — what we see."),
    Shape("who_changes", "shape",
          "whether anything moved",
          "Ask who is different at the end, and how you would see it.",
          "Who is different at the end, and in what shot would we notice?"),

    Shape("smallest_scene", "scenes",
          "the scene that carries the most with the least",
          "Ask which scene would survive if the budget halved.",
          "If you could only shoot three scenes, which three?"),
    Shape("where", "scenes",
          "location as meaning",
          "Ask why this place and not a cheaper one.",
          "Why does this happen here? What would be lost somewhere else?"),
    Shape("without_words", "scenes",
          "visual storytelling",
          "Ask them to carry a beat with no dialogue.",
          "Play that moment with nobody speaking. What do we see?"),
]

BY_STAGE: dict[str, list[Shape]] = {
    s: [sh for sh in SHAPES if sh.stage == s] for s in STAGES
}


# --------------------------------------------------------------- the prompt

SYSTEM = """You are a script editor working with someone on a short film,
5 to 20 minutes. You have one job: ask the question that makes them realise
what they actually meant.

ABSOLUTE RULE — you never propose the answer.

Forbidden, every time:
  - offering options ("it could be A, or B")
  - suggesting content ("perhaps he is a widower")
  - writing any part of their film for them
  - examples that are really suggestions
  - praise ("great idea!") — it is noise and it flatters them into stopping

Allowed:
  - one question
  - at most one short sentence before it, only if it names something they
    actually said, to show you were listening

Write like a person, not a form. Warm, direct, curious. Short.
Use their own words back at them. Concrete beats abstract every time.

Return JSON: {"question": str, "listening_for": str}
where listening_for is a note to yourself about what a good answer contains.
"""

FORBIDDEN = [
    re.compile(r"\b(?:you could|you might|perhaps|maybe|what if.{0,40}\bwas\b)", re.I),
    re.compile(r"\b(?:for example|for instance|such as|e\.g\.)", re.I),
    re.compile(r"\b(?:option|alternatively|or you can)\b", re.I),
    re.compile(r"\b(?:great|love it|nice|excellent|brilliant|good idea)\b", re.I),
]


def leaks_an_answer(question: str) -> str | None:
    """Catch the model doing the writer's thinking. Returns the offending bit."""
    for pattern in FORBIDDEN:
        hit = pattern.search(question)
        if hit:
            return hit.group(0)
    if question.count("?") > 1:
        return "more than one question"
    return None


# --------------------------------------------------------------- the session


@dataclass
class Turn:
    shape_id: str
    stage: str
    question: str
    answer: str = ""
    words: int = 0          # how much they wrote — the signal that matters
    skipped: bool = False


@dataclass
class Session:
    project_id: str
    seed: str = ""                       # whatever fragment they started with
    stage: str = "premise"
    turns: list[Turn] = field(default_factory=list)

    def used(self) -> set[str]:
        return {t.shape_id for t in self.turns}

    def answered_this_stage(self) -> int:
        return sum(1 for t in self.turns
                   if t.stage == self.stage and t.answer and not t.skipped)

    def transcript(self, limit: int = 6) -> str:
        return "\n\n".join(
            f"Q: {t.question}\nA: {t.answer or '(skipped)'}"
            for t in self.turns[-limit:]
        )

    def advance_if_ready(self, per_stage: int = 3) -> bool:
        """Move on once a stage has produced enough. Returns True if moved."""
        if self.answered_this_stage() >= per_stage:
            i = STAGES.index(self.stage)
            if i + 1 < len(STAGES):
                self.stage = STAGES[i + 1]
                return True
        return False


def choose_shape(session: Session, ranking: list[str] | None = None) -> Shape:
    """Pick the next question shape.

    `ranking` comes from telemetry — shape ids ordered by how much writing they
    have actually produced. Without it, fall back to declaration order, which is
    roughly the order a script editor would work through anyway.
    """
    pool = [s for s in BY_STAGE[session.stage] if s.id not in session.used()]
    if not pool:
        pool = BY_STAGE[session.stage]

    if ranking:
        order = {sid: i for i, sid in enumerate(ranking)}
        pool = sorted(pool, key=lambda s: order.get(s.id, len(order)))
    return pool[0]


def build_prompt(session: Session, shape: Shape) -> str:
    parts = [
        f"Stage: {session.stage} — {STAGE_GOAL[session.stage]}",
        f"Question shape to use: {shape.probes}",
        f"How to aim it: {shape.guidance}",
        f"Calibration (do NOT reuse these words): {shape.example}",
    ]
    if session.seed:
        parts.append(f"\nWhat they started with: {session.seed}")
    if session.turns:
        parts.append(f"\nSo far:\n{session.transcript()}")
    parts.append("\nAsk one question. JSON only.")
    return "\n".join(parts)


def parse(payload: dict[str, Any], shape: Shape) -> tuple[str, str]:
    """Pull the question out, refusing one that answers itself."""
    question = (payload.get("question") or "").strip()
    if not question:
        raise ValueError("no question in response")

    leak = leaks_an_answer(question)
    if leak:
        raise ValueError(f"question proposes an answer ({leak!r}): {question!r}")

    return question, (payload.get("listening_for") or shape.probes)


QUESTION_SCHEMA = {
    "type": "object",
    "required": ["question", "listening_for"],
    "properties": {
        "question": {"type": "string"},
        "listening_for": {"type": "string"},
    },
}

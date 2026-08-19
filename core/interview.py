"""The interviewer.

Every other AI writing tool generates. You give it a premise, it gives you a
story, and the story is the model's. This one asks the question a good script
editor would ask and then gets out of the way.

The hard rule, enforced in the prompt and checked after: **never propose the
answer.** No "perhaps he could…", no three options to choose from, no example
that is really a suggestion. A question that contains its own answer has done
the writer's thinking for them, which is the one thing this must not do.

After every answer the model reassesses the whole conversation, identifies the
highest-impact unresolved issue, and asks about that. The bank of question
shapes is only an offline fallback; it does not control the live interview.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------- safety boundaries

READY_THRESHOLD = 0.80
READY_STREAK = 2
MIN_SUBSTANTIVE_ANSWERS = 4
SUBSTANTIVE_WORDS = 12
MAX_CONTEXT_TURNS = 15


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

# Provider-independent questions for degraded operation. Unlike `example`,
# these must work without assuming a character, place, object, or pronoun from
# the writer's material.
FALLBACK_QUESTIONS = {
    "specificity": "What is the most specific detail you already know?",
    "under_the_pitch": "What is this film about when it is not about its plot?",
    "one_image": "What is the one shot someone describes to a friend?",
    "wrong_belief": "What does the central character believe about themselves that the film disproves?",
    "owed": "What does the central character think they are owed?",
    "absence": "Who is absent but still shapes what happens?",
    "cost": "If the central character gets what they want, what does it cost them?",
    "want_vs_need": "What does the central character want, and what do they actually need?",
    "if_nothing": "If nobody acts, what happens?",
    "no_return": "At what moment is the old life no longer available?",
    "last_image": "What is the last thing we see?",
    "who_changes": "Who is different at the end, and how can we see it?",
    "smallest_scene": "Only three scenes can be shot. Which three survive?",
    "where": "Why must this story happen in this place?",
    "without_words": "Which important moment can play without anybody speaking?",
}


def fallback_question(shape: Shape) -> str:
    return FALLBACK_QUESTIONS[shape.id]


# --------------------------------------------------------------- the prompt

SYSTEM = """You are a script editor developing a 5 to 20 minute short film
with its writer. After every answer, reassess the ENTIRE conversation. Identify
the single highest-impact unresolved issue in this particular story and ask the
question that will move it forward most.

There is no prescribed sequence and no checklist to march through. Do not ask
about character, conflict, structure, or scenes merely because those are common
screenwriting categories. Follow the material. If an answer already establishes
several things, accept that and investigate what now matters most.

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

Assess whether the existing material can support an honest 4–9 scene outline
without invention. A high readiness score requires enough specific, visible
events and consequential change to arrange a beginning, development and ending.
Do not penalize an unconventional story for lacking conventional plot machinery.

Return JSON with:
  question        one next question; empty only when no critical gap remains
  listening_for   what useful new information the answer might establish
  focus            a short label you choose for the uncertainty being explored
  established      concise facts now supported by the writer's words
  critical_gaps    only gaps that prevent an honest outline
  readiness        number from 0 to 1 for the whole conversation, not one answer
  reason           one sentence explaining the readiness assessment
  should_continue  whether another question is more useful than outlining now
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
    involvement: int = 75                 # 0 = AI leads, 100 = writer authors every choice
    turns: list[Turn] = field(default_factory=list)
    readiness: float = 0.0
    readiness_reason: str = "The story has not been assessed yet."
    critical_gaps: list[str] = field(default_factory=list)
    established: list[str] = field(default_factory=list)
    ready_streak: int = 0
    complete: bool = False

    @property
    def collaboration_mode(self) -> str:
        if self.involvement <= 33:
            return "ai_led"
        if self.involvement <= 66:
            return "collaborative"
        return "author_led"

    def used(self) -> set[str]:
        return {t.shape_id for t in self.turns}

    def answered(self) -> list[Turn]:
        return [t for t in self.turns if t.answer and not t.skipped]

    def substantive_answers(self) -> int:
        return sum(t.words >= SUBSTANTIVE_WORDS for t in self.answered())

    def involvement_ceiling_reached(self) -> bool:
        """Safety ceiling only; normal stopping is decided from context."""
        answered = len(self.answered())
        if self.collaboration_mode == "ai_led":
            return answered >= 5
        return self.collaboration_mode == "collaborative" and answered >= 9

    def transcript(self, limit: int = 6) -> str:
        return "\n\n".join(
            f"Q: {t.question}\nA: {t.answer or '(skipped)'}"
            for t in self.turns[-limit:]
        )

    def apply_assessment(self, payload: dict[str, Any]) -> None:
        self.readiness = max(0.0, min(1.0, float(payload.get("readiness", 0))))
        self.readiness_reason = str(payload.get("reason") or "").strip()
        self.critical_gaps = [str(g).strip() for g in payload.get("critical_gaps", []) if str(g).strip()]
        self.established = [str(f).strip() for f in payload.get("established", []) if str(f).strip()]

        model_ready = (
            self.readiness >= READY_THRESHOLD
            and not self.critical_gaps
            and not bool(payload.get("should_continue", True))
        )
        self.ready_streak = self.ready_streak + 1 if model_ready else 0
        enough_source = (
            self.substantive_answers() >= MIN_SUBSTANTIVE_ANSWERS
            and sum(t.words for t in self.answered()) >= 60
        )
        self.complete = enough_source and self.ready_streak >= READY_STREAK

        # Lower-involvement modes may stop before outline readiness because the
        # writer has explicitly permitted the outline stage to develop gaps.
        # `should_continue` is the model's contextual judgment, while minimums
        # prevent it from ending after one shallow exchange.
        answered = len(self.answered())
        should_continue = bool(payload.get("should_continue", True))
        if self.collaboration_mode == "ai_led":
            self.complete = (answered >= 2 and not should_continue) or self.involvement_ceiling_reached()
        elif self.collaboration_mode == "collaborative":
            self.complete = (answered >= 3 and not should_continue) or self.involvement_ceiling_reached()



def choose_shape(session: Session, ranking: list[str] | None = None) -> Shape:
    """Pick an emergency fallback shape when the model is unavailable."""
    pool = [s for s in SHAPES if s.id not in session.used()] or SHAPES

    if ranking:
        order = {sid: i for i, sid in enumerate(ranking)}
        pool = sorted(pool, key=lambda s: order.get(s.id, len(order)))
    return pool[0]


def build_prompt(session: Session, historical_focuses: list[str] | None = None) -> str:
    mode_guidance = {
        "ai_led": "The writer wants a very short interview before the AI develops missing material. Decide dynamically when enough direction exists; usually 2–4 questions, never more than 5.",
        "collaborative": "The writer wants a moderate collaborative interview. Decide dynamically when the important decisions are established; usually 4–7 questions, never more than 9.",
        "author_led": "The writer wants to make every story decision. Continue dynamically until the material is honestly ready.",
    }
    parts = [
        f"Creative involvement: {session.involvement}/100 ({session.collaboration_mode}).",
        mode_guidance[session.collaboration_mode],
        "During the interview you still ask without suggesting answers; permission to invent applies only to the later outline stage.",
    ]
    if session.seed:
        parts.append(f"What they started with: {session.seed}")
    if session.turns:
        parts.append(f"So far:\n{session.transcript(limit=MAX_CONTEXT_TURNS)}")
    if historical_focuses:
        parts.append("Historically productive focus labels from Grafana (weak evidence only; do not force one): "
                     + ", ".join(historical_focuses[:5]))
    parts.append("Reassess the story and decide what it needs next. JSON only.")
    return "\n".join(parts)


def parse(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a dynamic assessment and refuse a question that answers itself."""
    question = (payload.get("question") or "").strip()
    if not question and payload.get("should_continue", True):
        raise ValueError("no question in response")

    leak = leaks_an_answer(question)
    if leak:
        raise ValueError(f"question proposes an answer ({leak!r}): {question!r}")

    try:
        readiness = float(payload.get("readiness", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("readiness is not a number") from exc
    if not 0 <= readiness <= 1:
        raise ValueError("readiness must be between 0 and 1")

    return {
        **payload,
        "question": question,
        "focus": str(payload.get("focus") or "story").strip()[:80],
        "listening_for": str(payload.get("listening_for") or "useful story detail").strip(),
        "readiness": readiness,
    }


QUESTION_SCHEMA = {
    "type": "object",
    "required": ["question", "listening_for", "focus", "established",
                 "critical_gaps", "readiness", "reason", "should_continue"],
    "properties": {
        "question": {"type": "string"},
        "listening_for": {"type": "string"},
        "focus": {"type": "string"},
        "established": {"type": "array", "items": {"type": "string"}},
        "critical_gaps": {"type": "array", "items": {"type": "string"}},
        "readiness": {"type": "number"},
        "reason": {"type": "string"},
        "should_continue": {"type": "boolean"},
    },
}

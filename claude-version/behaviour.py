"""Did the agent actually do the work? Measured, not rejected.

The prototype passed every structural check while staging nothing, offering
suggestions on every turn, and never moving readiness. Structure is not
behaviour, so these observations are recorded on the run and scored in
evaluation — blocking a turn on them would punish the filmmaker for the model's
laziness.
"""

from __future__ import annotations

import re

CONCRETE = re.compile(r"\b(\d+|because|when|after|before|while|then|so that)\b", re.I)
MIN_CONCRETE_WORDS = 12
SUGGESTION_STREAK = 3
READINESS_STALE_TURNS = 5


def looks_concrete(text: str) -> bool:
    """A checkable answer: long enough, and doing more than naming a topic."""
    words = text.split()
    return len(words) >= MIN_CONCRETE_WORDS and bool(CONCRETE.search(text))


def observe(snapshot, decision) -> list[str]:
    notes = []

    if looks_concrete(snapshot.latest_answer_text) and not decision.facts:
        notes.append("concrete answer settled nothing: no facts staged")

    intents = [decision.response.primary_intent] + list(snapshot.recent_intents)
    if snapshot.event.kind != "suggestions_requested":
        streak = 0
        for intent in intents:
            if intent != "offer_suggestions":
                break
            streak += 1
        if streak >= SUGGESTION_STREAK:
            notes.append(f"{streak} suggestion turns in a row without being asked")

    if decision.readiness is None and len(snapshot.turns) >= READINESS_STALE_TURNS \
            and snapshot.readiness is None:
        notes.append(f"no readiness assessment in {len(snapshot.turns)} turns")

    return notes

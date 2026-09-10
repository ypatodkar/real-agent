"""Event kind and story state decide which responses are even legal.

Intent enforcement lives in code, not in the prompt. "Give me options" returning
a question was the original bug; a prompt instruction is not a guarantee, and a
validator that only checks structure would have passed it.
"""

from __future__ import annotations

import re

ASK = "ask_question"
SUGGEST = "offer_suggestions"
REFLECT = "reflect_and_confirm"
COACH = "coach_writer"
RECOMMEND = "recommend_outline"
ALL = {ASK, SUGGEST, REFLECT, COACH, RECOMMEND}

BASE = {
    "interview_started": {ASK, SUGGEST},
    "message_submitted": {ASK, SUGGEST, REFLECT, COACH, RECOMMEND},
    "suggestions_requested": {SUGGEST},
    "suggestions_selected": {ASK, REFLECT, COACH, RECOMMEND},
    "suggestions_rejected": {SUGGEST, ASK, COACH},
    "question_skipped": {ASK, SUGGEST},
    "continue_interview": {ASK, SUGGEST},
    "decision_revised": {REFLECT, ASK},
}

MIN_ANSWERS_BEFORE_RECOMMEND = 4

# "does a short film need a subplot?" is a question to the editor, not a story
# answer. Answering it comes before asking anything else.
META = re.compile(r"\b(should i|do i|does a|do you|what do you|how do i|is it (ok|fine|normal)|"
                  r"can i|would you|whats the|what is the) \b", re.I)


def is_direct_question(text: str) -> bool:
    stripped = text.strip()
    if not stripped.endswith("?"):
        return False
    return bool(META.search(stripped)) or len(stripped.split()) <= 12


def allowed_intents(snapshot) -> tuple[set[str], str]:
    """Returns (legal intents, why it was narrowed)."""
    kind = snapshot.event.kind
    allowed = set(BASE.get(kind, ALL))
    why = ""

    if kind == "suggestions_requested":
        return allowed, "an options request can only be answered with options"

    if kind == "message_submitted" and is_direct_question(snapshot.event.text):
        return {COACH, REFLECT}, "the writer asked the editor a direct question"

    if snapshot.stuck:
        return ({SUGGEST, COACH} & allowed) or {SUGGEST}, \
               "the writer has said they do not know several times running"

    # Reflection needs something of the writer's to reflect.
    if not snapshot.has_writer_input:
        allowed.discard(REFLECT)
        why = why or "no answer text to reflect"

    if snapshot.answered_count < MIN_ANSWERS_BEFORE_RECOMMEND:
        allowed.discard(RECOMMEND)
        why = why or f"fewer than {MIN_ANSWERS_BEFORE_RECOMMEND} answers so far"

    if kind == "suggestions_selected":
        allowed.discard(SUGGEST)
        why = why or "the writer just chose an idea; build on it rather than offering more"

    return allowed, why

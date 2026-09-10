"""What the writer gets when the model or the budget fails.

A single hand-written question is the wrong answer to every event. If they
pressed Give me options, handing back a question reproduces the original bug one
layer down — so fallbacks are chosen by event kind, and they never invent story.
"""

from __future__ import annotations

from models import Response, SuggestionCard

QUESTIONS = [
    ("image", "What is the first image you can see, even if you do not know what it means yet?"),
    ("visible", "What would we actually see happening in that moment?"),
    ("choice", "What choice does someone make that they cannot take back?"),
    ("pressure", "What makes waiting impossible — why does this happen now?"),
    ("ending", "What is the last thing we see before the film ends?"),
    ("cost", "What does it cost them to do it?"),
]

# Deliberately about form, not about anyone's story: a local fallback must not
# invent film material the writer never mentioned.
LOCAL_OPTIONS = [
    ("Show it happening", "We watch the moment play out, so the audience judges it for themselves."),
    ("Show the aftermath", "We arrive once it is done, and the film is about what it left behind."),
    ("Show someone hearing about it", "The event stays offscreen and the scene is about the reaction."),
]


def _next_question(snapshot) -> tuple[str, str]:
    used = {t.get("focus") for t in snapshot.turns}
    for focus, question in QUESTIONS:
        if focus not in used:
            return focus, question
    return QUESTIONS[0]


def for_event(snapshot, reason: str, allowed: set[str] | None = None) -> Response:
    """A fallback is still a response, so it obeys the same routing rules.

    Falling back to a question when the writer is stuck, or when they asked for
    options, is the original bug wearing a different hat.
    """
    kind = snapshot.event.kind
    allowed = allowed or {"ask_question"}

    if kind == "suggestions_requested" or (
            "offer_suggestions" in allowed and "ask_question" not in allowed):
        return Response(
            primary_intent="offer_suggestions",
            question="Which of these directions is closest, or would you rather say it your way?",
            focus="fallback",
            blocks=[{"kind": "note", "text":
                     "The editor is unavailable, so these are general directions rather than "
                     "ideas about your film. You can also answer in your own words."}],
            suggestions=[SuggestionCard(label=l, detail=d) for l, d in LOCAL_OPTIONS],
            degraded=True)

    if kind in {"suggestions_selected", "decision_revised"} and "reflect_and_confirm" in allowed:
        # Never partially apply a choice. Preserve it and let them retry.
        return Response(
            primary_intent="reflect_and_confirm", focus="fallback",
            blocks=[{"kind": "reflection", "text":
                     "Your choice is saved but the editor could not respond just now. "
                     "Try again and nothing will be lost."}],
            degraded=True)

    if "coach_writer" in allowed and (
            "ask_question" not in allowed or snapshot.event.text.strip().endswith("?")):
        return Response(
            primary_intent="coach_writer", focus="fallback",
            blocks=[{"kind": "coaching", "text":
                     "The editor could not reach an answer just now. Your message is saved — "
                     "try again in a moment."}],
            degraded=True)

    if "ask_question" not in allowed:
        intent = sorted(allowed)[0]
        return Response(primary_intent=intent, focus="fallback",
                        blocks=[{"kind": "note", "text":
                                 "The editor is briefly unavailable. Nothing has been lost — "
                                 "try again in a moment."}],
                        degraded=True)

    focus, question = _next_question(snapshot)
    return Response(primary_intent="ask_question", question=question, focus=focus,
                    blocks=[{"kind": "note", "text":
                             "The editor is briefly unavailable, so this is a general question "
                             "rather than one about your film."}],
                    degraded=True)

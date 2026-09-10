"""What a decision must look like to be committed. Structural only.

Each component is judged independently, because the partial-failure rule
depends on it: a valid response commits even when a fact beside it is dropped.
Behavioural checks — did the agent actually do any work — live in behaviour.py,
because they measure rather than reject.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from models import INTENTS

MAX_QUESTION = 320
DUPLICATE_OVERLAP = 0.8

STOP = {"what", "when", "where", "which", "that", "this", "with", "from",
        "does", "would", "could", "your", "their", "about", "have", "into"}


@dataclass
class Result:
    response_errors: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)      # component: reason

    @property
    def response_ok(self) -> bool:
        return not self.response_errors


def key(question: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']{4,}", question.lower()) if w not in STOP}


def repeats(question: str, previous: list[str]) -> str | None:
    candidate = key(question)
    for earlier in previous:
        prior = key(earlier)
        if not candidate or not prior:
            continue
        if len(candidate & prior) / max(1, min(len(candidate), len(prior))) >= DUPLICATE_OVERLAP:
            return earlier
    return None


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def _evidence_pool(snapshot) -> str:
    """Everything a fact is allowed to be built from: what the writer wrote,
    what they selected, and the idea they started with. The agent may develop
    material freely — but developed material lives in suggestions until the
    writer chooses it. It does not become canon by assertion."""
    parts = [snapshot.event.text, snapshot.session.get("seed", "")]
    for card in snapshot.suggestions_open:
        if card.get("status") == "selected":
            parts.extend([card.get("label", ""), card.get("detail", "")])
    for sid in snapshot.event.payload.get("suggestion_ids") or []:
        for card in snapshot.suggestions_open:
            if card.get("id") == sid:
                parts.extend([card.get("label", ""), card.get("detail", "")])
    return _normalise(" ".join(p for p in parts if p))


def validate(decision, snapshot, allowed: set[str]) -> Result:
    result = Result()
    response = decision.response
    intent = response.primary_intent

    if intent not in INTENTS:
        result.response_errors.append(f"{intent!r} is not a response intent")
        return result
    if intent not in allowed:
        result.response_errors.append(
            f"{intent} is not allowed for this event; choose one of "
            f"{', '.join(sorted(allowed))}")

    if response.question.count("?") > 1:
        result.response_errors.append("only one question per response")
    if len(response.question) > MAX_QUESTION:
        result.response_errors.append(
            f"the question is {len(response.question)} characters; keep it under {MAX_QUESTION}")

    if intent == "ask_question":
        if not response.question:
            result.response_errors.append("ask_question needs a question")
        else:
            earlier = repeats(response.question, snapshot.recent_questions)
            if earlier:
                result.response_errors.append(
                    f"that repeats an earlier question: {earlier!r}; ask something else")

    if intent == "offer_suggestions":
        cards = response.suggestions
        if not 2 <= len(cards) <= 3:
            result.response_errors.append(
                f"offer_suggestions needs two or three ideas, got {len(cards)}")
        if any(not c.detail.strip() for c in cards):
            result.response_errors.append("every idea needs a sentence on its dramatic effect")
        if len({c.label.strip().lower() for c in cards}) != len(cards):
            result.response_errors.append("the ideas must be distinct")
        if not response.question.strip():
            result.response_errors.append(
                "offer_suggestions needs a question inviting the writer to choose")

    if intent == "reflect_and_confirm" and not any(
            b["kind"] == "reflection" for b in response.blocks):
        result.response_errors.append("reflect_and_confirm needs a reflection block")

    if intent == "coach_writer" and not any(
            b["kind"] == "coaching" for b in response.blocks):
        result.response_errors.append("coach_writer needs a coaching block")

    if intent == "recommend_outline":
        readiness = decision.readiness
        if readiness is None or not readiness.ready:
            result.response_errors.append(
                "recommend_outline must come with a readiness assessment marked ready")
        elif len(readiness.lenses) < 4 or not readiness.reason:
            result.response_errors.append(
                "readiness needs evidence for carries, pressure, sequence and ending,"
                " plus a reason")

    # --- state updates, judged one at a time -----------------------------
    pool = _evidence_pool(snapshot)
    for fact in list(decision.facts):
        if not fact.evidence.strip():
            decision.facts.remove(fact)
            result.dropped.append(f"fact {fact.text[:48]!r}: no evidence quoted")
        elif _normalise(fact.evidence)[:60].strip() not in pool:
            decision.facts.remove(fact)
            result.dropped.append(
                f"fact {fact.text[:48]!r}: evidence is not in anything the writer wrote or chose")

    for change in list(decision.gap_changes):
        if change.action == "resolve" and not change.gap_id:
            decision.gap_changes.remove(change)
            result.dropped.append("gap resolve: no gap_id")
        elif change.action in {"open", "update"} and not change.text:
            decision.gap_changes.remove(change)
            result.dropped.append("gap change: no text")

    if decision.readiness is not None and decision.readiness.ready:
        if len(decision.readiness.lenses) < 4 or not decision.readiness.reason:
            # All or nothing: a half-evidenced readiness leaves the last one standing.
            decision.readiness = None
            result.dropped.append("readiness: ready without evidence for all four lenses")

    return result

"""A model that returns whatever the test tells it to.

Phase 2 exists to prove the harness without a network: every reliability
property — idempotency, revision conflicts, repair, partial failure, fallback —
is testable with this and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness import ProviderError


@dataclass
class Reply:
    raw: dict
    tokens_in: int = 100
    tokens_out: int = 40
    model: str = "stub"
    cost_micro_usd: int = 0


@dataclass
class StubModel:
    script: list = field(default_factory=list)     # dicts, or ProviderError instances
    seen: list = field(default_factory=list)       # (allowed, repair_reason)

    def decide(self, snapshot, allowed, repair):
        self.seen.append((set(allowed), repair))
        if not self.script:
            raise ProviderError("stub script exhausted")
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return Reply(raw=nxt)


def ask(question="What does he do next?", focus="action", facts=(), readiness=None):
    return {"state_updates": _updates(facts, readiness),
            "response": {"primary_intent": "ask_question", "question": question, "focus": focus}}


def suggest(cards=(("Public", "Silence costs more."), ("Private", "The threat turns intimate.")),
            question="Which is closest?", facts=(), readiness=None):
    return {"state_updates": _updates(facts, readiness),
            "response": {"primary_intent": "offer_suggestions", "question": question,
                         "focus": "fork",
                         "suggestions": [{"label": l, "detail": d} for l, d in cards]}}


def reflect(text="You are saying the notes became confessions.", facts=(), readiness=None):
    return {"state_updates": _updates(facts, readiness),
            "response": {"primary_intent": "reflect_and_confirm", "focus": "check",
                         "blocks": [{"kind": "reflection", "text": text}]}}


def coach(text="A short film rarely needs a subplot.", facts=(), readiness=None):
    return {"state_updates": _updates(facts, readiness),
            "response": {"primary_intent": "coach_writer", "focus": "craft",
                         "blocks": [{"kind": "coaching", "text": text}]}}


def recommend(lenses=None, reason="Four lenses are covered.", facts=()):
    lenses = lenses or {"carries": "the man", "pressure": "the failed pitch",
                        "sequence": "pitch, pool, kitchen", "ending": "dancing badly"}
    return {"state_updates": _updates(facts, {"ready": True, "reason": reason, "lenses": lenses}),
            "response": {"primary_intent": "recommend_outline", "focus": "readiness",
                         "blocks": [{"kind": "note", "text": reason}]}}


def _updates(facts, readiness):
    updates = {"facts": [{"text": t, "evidence": e} for t, e in facts]}
    if readiness:
        updates["readiness"] = readiness
    return updates

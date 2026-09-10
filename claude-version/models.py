"""Typed shapes, no I/O. What the model returns and what the harness moves around.

`TurnDecision` is the whole model contract: every state change the turn wants,
plus the one response the filmmaker sees, in a single object. Batching them is
what keeps a normal turn to one model call — the prototype's latency varied
between two and twelve seconds because each fact could become its own round trip.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

INTENTS = {"ask_question", "offer_suggestions", "reflect_and_confirm",
           "coach_writer", "recommend_outline"}
QUESTION_INTENTS = {"ask_question"}          # intents that advance by asking

EVENT_KINDS = {"interview_started", "message_submitted", "suggestions_requested",
               "suggestions_selected", "suggestions_rejected", "question_skipped",
               "continue_interview", "decision_revised"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class Event:
    kind: str
    session_id: str = ""
    payload: dict = field(default_factory=dict)
    idempotency_key: str = ""
    expected_revision: int | None = None
    id: str = field(default_factory=lambda: new_id("evt"))
    created_at: str = field(default_factory=now)

    def __post_init__(self):
        if not self.idempotency_key:
            self.idempotency_key = self.id

    @property
    def text(self) -> str:
        return str(self.payload.get("text") or "").strip()


@dataclass
class Fact:
    text: str
    evidence: str = ""
    id: str = field(default_factory=lambda: new_id("fact"))

    @classmethod
    def parse(cls, raw: Any) -> "Fact | None":
        if not isinstance(raw, dict):
            return None
        text = str(raw.get("text") or "").strip()
        if not text:
            return None
        return cls(text=text[:400], evidence=str(raw.get("evidence") or "").strip()[:400])


@dataclass
class GapChange:
    action: str                      # open | update | resolve
    text: str = ""
    gap_id: str = ""
    evidence: str = ""

    @classmethod
    def parse(cls, raw: Any) -> "GapChange | None":
        if not isinstance(raw, dict):
            return None
        action = str(raw.get("action") or "").strip()
        if action not in {"open", "update", "resolve"}:
            return None
        return cls(action=action, text=str(raw.get("text") or "").strip()[:300],
                   gap_id=str(raw.get("gap_id") or "").strip(),
                   evidence=str(raw.get("evidence") or "").strip()[:400])


@dataclass
class Readiness:
    """Evidence first. The percentage, if it ever returns, is derived from this."""

    ready: bool
    reason: str = ""
    lenses: dict = field(default_factory=dict)   # carries | pressure | sequence | ending

    LENSES = ("carries", "pressure", "sequence", "ending")

    @classmethod
    def parse(cls, raw: Any) -> "Readiness | None":
        if not isinstance(raw, dict):
            return None
        lenses = raw.get("lenses")
        if not isinstance(lenses, dict):
            lenses = {}
        return cls(ready=bool(raw.get("ready")),
                   reason=str(raw.get("reason") or "").strip()[:400],
                   lenses={k: str(v)[:300] for k, v in lenses.items()
                           if k in cls.LENSES and str(v).strip()})


@dataclass
class SuggestionCard:
    label: str
    detail: str = ""
    id: str = field(default_factory=lambda: new_id("sug"))

    @classmethod
    def parse(cls, raw: Any) -> "SuggestionCard | None":
        if not isinstance(raw, dict):
            return None
        label = str(raw.get("label") or "").strip()
        if not label:
            return None
        return cls(label=label[:120], detail=str(raw.get("detail") or "").strip()[:300])


@dataclass
class Response:
    primary_intent: str
    question: str = ""
    focus: str = "story"
    blocks: list[dict] = field(default_factory=list)      # {kind: reflection|coaching, text}
    suggestions: list[SuggestionCard] = field(default_factory=list)
    degraded: bool = False

    @classmethod
    def parse(cls, raw: Any) -> "Response | None":
        if not isinstance(raw, dict):
            return None
        blocks = [{"kind": str(b.get("kind") or "note")[:20],
                   "text": str(b.get("text") or "").strip()[:1200]}
                  for b in (raw.get("blocks") or []) if isinstance(b, dict)
                  and str(b.get("text") or "").strip()]
        cards = [c for c in (SuggestionCard.parse(s) for s in raw.get("suggestions") or [])
                 if c]
        return cls(primary_intent=str(raw.get("primary_intent") or "").strip(),
                   question=str(raw.get("question") or "").strip()[:400],
                   focus=str(raw.get("focus") or "story").strip()[:80],
                   blocks=blocks[:3], suggestions=cards[:4])


@dataclass
class TurnDecision:
    response: Response
    facts: list[Fact] = field(default_factory=list)
    gap_changes: list[GapChange] = field(default_factory=list)
    readiness: Readiness | None = None

    @classmethod
    def parse(cls, raw: Any) -> "TurnDecision | None":
        """Coercion only. Judgement belongs to validate.py."""
        if not isinstance(raw, dict):
            return None
        response = Response.parse(raw.get("response"))
        if response is None:
            return None
        updates = raw.get("state_updates") or {}
        if not isinstance(updates, dict):
            updates = {}
        return cls(
            response=response,
            facts=[f for f in (Fact.parse(x) for x in updates.get("facts") or []) if f][:8],
            gap_changes=[g for g in (GapChange.parse(x) for x in updates.get("gap_changes") or [])
                         if g][:5],
            readiness=Readiness.parse(updates.get("readiness")),
        )

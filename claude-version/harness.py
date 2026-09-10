"""One filmmaker event in, one committed response out, then stop.

    TX1 ingest -> snapshot -> route -> model -> validate -> (repair) -> TX2 commit

Everything expensive happens between the transactions, so no database lock is
ever held while a provider is running. Nothing here decides what to say; it
decides what is allowed, what is valid, and what is safely saved.

The partial-failure rule, which the design documents left open: a valid response
commits even when a state update beside it is dropped. A dropped fact can be
re-derived from the same answer next turn. A lost response wastes the
filmmaker's turn, which is the more expensive mistake.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import behaviour
import budget
import fallback
import router
import snapshot as snapshot_module
import store
import validate
from models import Event, Response, TurnDecision
from recorder import Recorder

MAX_MODEL_CALLS = 4          # hard ceiling: normal path is one, repair is two
MAX_REPAIRS = 1              # one ordinary correction; more needs recovery budget


class ProviderError(RuntimeError):
    """The model could not be reached or refused the call."""


@dataclass
class Outcome:
    response: Response
    response_id: str = ""
    run_id: str = ""
    replayed: bool = False
    degraded: bool = False
    model_calls: int = 0
    repairs: int = 0
    cost_micro_usd: int = 0
    latency_ms: int = 0
    dropped: list[str] = field(default_factory=list)
    allowed: set[str] = field(default_factory=set)
    route_reason: str = ""
    trace: list[str] = field(default_factory=list)


def run_turn(db, model, session_id: str, event: Event) -> Outcome:
    event.session_id = session_id
    started = time.monotonic()

    run_id, committed = store.ingest(db, event)          # TX1
    if committed:
        row = store.response(db, committed)
        return Outcome(response=_response_from_row(db, row), response_id=committed,
                       replayed=True, trace=["replayed an already-committed event"])

    rec = Recorder(run_id)
    if hasattr(model, "recorder"):
        model.recorder = rec           # verbatim payloads land beside the run

    snap = snapshot_module.build(db, session_id, event)
    allowed, why = router.allowed_intents(snap)
    trace = [f"route: {', '.join(sorted(allowed))}" + (f" — {why}" if why else "")]

    repair_reason: str | None = None
    calls = repairs = cost = 0
    decision: TurnDecision | None = None
    dropped: list[str] = []

    while calls < MAX_MODEL_CALLS:
        try:
            budget.check(cost, time.monotonic() - started)
        except budget.BudgetExceeded as exc:
            trace.append(f"stopped: {exc}")
            store.add_step(db, run_id, "budget", rejected=str(exc))
            break

        try:
            reply = model.decide(snap, allowed, repair_reason)
        except ProviderError as exc:
            trace.append(f"provider failed: {exc}")
            store.add_step(db, run_id, "provider_error", detail=str(exc)[:400])
            break

        calls += 1
        cost += reply.cost_micro_usd
        store.add_step(db, run_id, "model_call", detail=reply.model,
                       tokens_in=reply.tokens_in, tokens_out=reply.tokens_out,
                       cost_micro_usd=reply.cost_micro_usd)

        candidate = TurnDecision.parse(reply.raw)
        if candidate is None:
            repair_reason = ("the response could not be parsed; return one JSON object with "
                             "state_updates and response")
            repairs += 1
            trace.append("unparseable decision")
            store.add_step(db, run_id, "rejected", rejected=repair_reason)
            if repairs > MAX_REPAIRS:
                break
            continue

        result = validate.validate(candidate, snap, allowed)
        if result.response_ok:
            decision, dropped = candidate, result.dropped
            for reason in dropped:
                trace.append(f"dropped: {reason}")
            # Structure passing is not the same as the agent doing its job.
            for note in behaviour.observe(snap, candidate):
                store.add_step(db, run_id, "behaviour", detail=note)
                trace.append(f"note: {note}")
            trace.append(f"{candidate.response.primary_intent} accepted")
            break

        repairs += 1
        repair_reason = "; ".join(result.response_errors)
        trace.append(f"rejected: {repair_reason[:90]}")
        store.add_step(db, run_id, "rejected", rejected=repair_reason[:500])
        if repairs > MAX_REPAIRS:
            break

    if decision is None:
        decision = TurnDecision(response=fallback.for_event(
            snap, repair_reason or "unavailable", allowed))
        trace.append(f"fallback: {decision.response.primary_intent}")

    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        response_id = store.commit_turn(                  # TX2
            db, session_id, event, run_id, decision, snap.revision,
            dropped=dropped, model_calls=calls, repairs=repairs,
            cost_micro_usd=cost, latency_ms=latency_ms)
    except store.RevisionConflict:
        # Someone committed while this turn was thinking. Re-read and try once
        # more against fresh state rather than overwriting their work.
        trace.append("revision moved; re-evaluated once")
        snap = snapshot_module.build(db, session_id, event)
        allowed, _ = router.allowed_intents(snap)
        result = validate.validate(decision, snap, allowed)
        if not result.response_ok:
            decision = TurnDecision(response=fallback.for_event(snap, "state changed", allowed))
        response_id = store.commit_turn(
            db, session_id, event, run_id, decision, snap.revision,
            dropped=dropped + result.dropped, model_calls=calls, repairs=repairs,
            cost_micro_usd=cost, latency_ms=latency_ms)

    return Outcome(response=decision.response, response_id=response_id, run_id=run_id,
                   degraded=decision.response.degraded, model_calls=calls, repairs=repairs,
                   cost_micro_usd=cost, latency_ms=latency_ms, dropped=dropped,
                   allowed=allowed, route_reason=why, trace=trace)


def _response_from_row(db, row) -> Response:
    import json

    cards = store.suggestions_for(db, row["id"])
    from models import SuggestionCard
    return Response(
        primary_intent=row["primary_intent"], question=row["question"], focus=row["focus"],
        blocks=json.loads(row["blocks_json"] or "[]"),
        suggestions=[SuggestionCard(label=c["label"], detail=c["detail"], id=c["id"])
                     for c in cards],
        degraded=bool(row["degraded"]))

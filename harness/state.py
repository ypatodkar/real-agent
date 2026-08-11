"""The Production record — one object, carried through the whole graph.

Decision (Aug 9): stages receive the entire state rather than declared inputs.
This is the idiomatic LangGraph shape: a typed state flows through the graph and
each node returns a partial update.

The cost of that choice is that "frozen" during a scoped repair becomes a
convention rather than a guarantee — any node *could* write any field. That is
what `harness.node.assert_scope` exists to enforce at runtime instead.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict


def keep_last(_old: Any, new: Any) -> Any:
    """Default reducer: a node's value wins."""
    return new


def accumulate(old: list | None, new: list | None) -> list:
    """For fields that append across rounds rather than replace."""
    return (old or []) + (new or [])


Intent = Literal["explainer", "comedy", "commentary"]
Format = Literal["monologue", "debate"]
Breach = Literal["abort", "degrade", "escalate"]


class Production(TypedDict, total=False):
    # --- request
    run_id: str
    topic: str
    involvement: int          # 0-10; 0 means never interrupt

    # --- stage outputs
    brief: dict               # intent, format, tone, params, cast, confidence
    track: dict               # {track, bpm, beats[], drop_s}
    outline: dict             # beats[], turn_at_s, needs_fact[]
    claims: list[dict]        # claim ledger
    script: dict              # lines[]
    edl: dict                 # shots[] — the artifact
    assets: dict              # resolved sprite refs, generated bg plates
    audio: dict               # measured durations, vo spans
    envelope: dict            # ducking
    render: dict              # path, frame_hash, n_frames

    # --- QC and repair
    violations: list[dict]
    repair_round: int
    repaired: bool            # did the last repair change anything
    applicable_rules: list[str]
    phase: str                # which QC gate last ran: plan | render
    grades: dict              # cached grader verdict + content fingerprint
    outcome: Literal["green", "escalated", "aborted", ""]

    # --- bookkeeping
    spend: Annotated[list[dict], accumulate]   # one entry per stage
    log: Annotated[list[str], accumulate]      # human-readable trace
    degraded: Annotated[list[str], accumulate] # stages that hit their cap


def new_production(topic: str, involvement: int, run_id: str) -> Production:
    return Production(
        run_id=run_id,
        topic=topic,
        involvement=involvement,
        violations=[],
        repair_round=0,
        applicable_rules=[],
        outcome="",
        spend=[],
        log=[],
        degraded=[],
    )


def total_spend(state: Production) -> float:
    return round(sum(e["cost"] for e in state.get("spend", [])), 6)

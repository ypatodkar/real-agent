"""The pipeline topology.

Exactly ARCHITECTURE.md Fig. 1: a straight line with one cycle, the QC repair
loop, bounded at three rounds. Declared as a lookup table and walked by
`harness.runner`.

Nothing here orchestrates. It says what follows what, and the one place that
is not a constant — where QC goes next — is an ordinary function.
"""

from __future__ import annotations

from harness.budget import Ledger
from harness.node import stage
from harness.recorder import Recorder
from harness.runner import Gate, Pipeline
from harness.state import Production

from pipeline import stages, tools

MAX_REPAIR_ROUNDS = 3


def _after_qc_plan(state: Production) -> str:
    """The cheap gate: fix planning errors before any asset is generated."""
    if not state.get("violations"):
        return "gate2"
    if state["repair_round"] >= MAX_REPAIR_ROUNDS:
        return "gate2"          # out of rounds — let the human see it at the gate
    return "repair"


def _after_qc_render(state: Production) -> str:
    if not state.get("violations"):
        return "green"
    if state["repair_round"] >= MAX_REPAIR_ROUNDS:
        return "escalate"
    return "repair"


def _after_repair(state: Production) -> str:
    """Back to whichever gate sent us here. Plan repairs never re-render.

    A repair that fixed nothing means no implementation owns these violations.
    Looping would spend three rounds to reach the same answer, so escalate now
    and ship the report — the failure is legible either way.
    """
    if not state.get("repaired"):
        return "escalate"
    return "qc_plan" if state.get("phase") == "plan" else "compositor"


def _green(state: Production) -> dict:
    return {"outcome": "green", "log": ["outcome: GREEN — all applicable rules pass"]}


def _escalate(state: Production) -> dict:
    return {"outcome": "escalated",
            "log": [f"outcome: ESCALATED after {state['repair_round']} rounds — "
                    f"shipping with {len(state['violations'])} violation(s)"]}


# Asset stages are conceptually independent but run in sequence. Wall-clock is
# not a metric here, and a single ordered path means one composite per round.
TOPOLOGY = {
    "brief":          "gate1",
    "gate1":          "scoring_select",
    "scoring_select": "outline",
    "outline":        "research",
    "research":       "script",
    "script":         "qc_plan",
    "qc_plan":        _after_qc_plan,   # gate2 | repair
    "gate2":          "casting",
    "casting":        "voice",
    "voice":          "envelope",
    "envelope":       "compositor",
    "compositor":     "qc_render",
    "qc_render":      _after_qc_render,  # green | repair | escalate
    "repair":         _after_repair,     # qc_plan | compositor
    "green":          None,
    "escalate":       None,
}

GATES = {
    "gate1": Gate("gate1", ["brief"]),
    "gate2": Gate("gate2", ["brief", "outline", "edl"]),
}

# what each stage is allowed to write — enforced by assert_scope
WRITES = {
    "brief":          {"brief", "applicable_rules"},
    "scoring_select": {"track"},
    "outline":        {"outline"},
    "research":       {"claims"},
    "script":         {"edl", "script"},
    "casting":        {"assets"},
    "voice":          {"audio"},
    "envelope":       {"envelope"},
    "compositor":     {"render"},
    "qc_plan":        {"violations", "phase"},
    "qc_render":      {"violations", "phase", "grades"},
    "repair":         {"edl", "repair_round", "repaired"},
}


def build(run_id: str, ledger: Ledger, recorder: Recorder) -> Pipeline:
    registry = tools.build_registry()

    # A stage may declare what a particular call will cost; see harness.node.
    estimators = {"qc_render": stages.qc_render_estimate}

    nodes = {
        name: stage(name, getattr(stages, name), ledger=ledger, recorder=recorder,
                    registry=registry, writes=writes,
                    estimate=estimators.get(name))
        for name, writes in WRITES.items()
    }
    nodes["green"] = _green
    nodes["escalate"] = _escalate

    return Pipeline(nodes=nodes, gates=GATES, topology=TOPOLOGY, entry="brief")


def describe() -> str:
    """The topology as text, for the docs."""
    lines = []
    for src, dst in TOPOLOGY.items():
        if callable(dst):
            lines.append(f"{src:16} -> (branch) green | repair | escalate")
        elif dst is None:
            lines.append(f"{src:16} -> END")
        else:
            lines.append(f"{src:16} -> {dst}")
    return "\n".join(lines)

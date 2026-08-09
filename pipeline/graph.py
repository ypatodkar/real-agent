"""The LangGraph wiring.

Topology is exactly ARCHITECTURE.md Fig. 1: a straight line with one cycle,
the QC repair loop, bounded at three rounds.

Gates are LangGraph interrupts, governed by the involvement dial. At
involvement 0 the gate is a pass-through — not a bypass, the same node asking
zero questions, which is what keeps eval and product on one code path.
"""

from __future__ import annotations

import json
import pathlib

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from harness.budget import Ledger
from harness.node import stage
from harness.recorder import Recorder
from harness.state import Production

from pipeline import stages, tools

MAX_REPAIR_ROUNDS = 3


def _gate(name: str, fields: list[str]):
    """A gate always shows the whole proposed plan. The dial only decides whether
    it also interrupts to ask about the least-confident fields.

    Resume contract:  Command(resume={"edits": {...}})

    The envelope is not decoration. Resuming with a bare `{}` is falsy, which
    LangGraph reads as "no value supplied" — so the gate re-interrupts and the
    run never advances. `{"edits": {}}` is truthy and means "accepted, unchanged".
    """

    def node(state: Production) -> dict:
        proposal = {f: state.get(f) for f in fields if state.get(f) is not None}

        if state["involvement"] == 0:
            return {"log": [f"{name}: auto-approved, 0 questions (involvement 0)"]}

        response = interrupt({
            "gate": name,
            "proposal": proposal,
            "editable": sorted(proposal),
            "involvement": state["involvement"],
            "resume_with": {"edits": {}},
        })

        edits = response.get("edits", {}) if isinstance(response, dict) else {}
        changed = sorted(edits)
        return {**edits,
                "log": [f"{name}: {'edited ' + str(changed) if changed else 'accepted unchanged'}"]}

    node.__name__ = f"gate_{name}"
    return node


def _after_qc(state: Production) -> str:
    if not state.get("violations"):
        return "green"
    if state["repair_round"] >= MAX_REPAIR_ROUNDS:
        return "exhausted"
    return "repair"


def _green(state: Production) -> dict:
    return {"outcome": "green", "log": ["outcome: GREEN — all applicable rules pass"]}


def _escalate(state: Production) -> dict:
    return {"outcome": "escalated",
            "log": [f"outcome: ESCALATED after {state['repair_round']} rounds — "
                    f"shipping with {len(state['violations'])} violation(s)"]}


def build(run_id: str, ledger: Ledger, recorder: Recorder):
    registry = tools.build_registry()

    def S(name, fn, writes=None):
        return stage(name, fn, ledger=ledger, recorder=recorder,
                     registry=registry, writes=writes)

    g = StateGraph(Production)

    g.add_node("brief", S("brief", stages.brief, {"brief", "applicable_rules"}))
    g.add_node("gate1", _gate("gate1", ["brief"]))
    g.add_node("scoring_select", S("scoring_select", stages.scoring_select, {"track"}))
    g.add_node("outline", S("outline", stages.outline, {"outline"}))
    g.add_node("research", S("research", stages.research, {"claims"}))
    g.add_node("script", S("script", stages.script, {"edl", "script"}))
    g.add_node("gate2", _gate("gate2", ["brief", "outline", "edl"]))
    g.add_node("casting", S("casting", stages.casting, {"assets"}))
    g.add_node("voice", S("voice", stages.voice, {"audio"}))
    g.add_node("envelope", S("envelope", stages.envelope, {"envelope"}))
    g.add_node("compositor", S("compositor", stages.compositor, {"render"}))
    g.add_node("qc", S("qc", stages.qc, {"violations"}))
    # scoped: repair may touch the EDL and the round counter, nothing else
    g.add_node("repair", S("repair", stages.repair, {"edl", "repair_round"}))
    g.add_node("green", _green)
    g.add_node("escalate", _escalate)

    g.add_edge(START, "brief")
    g.add_edge("brief", "gate1")
    g.add_edge("gate1", "scoring_select")
    g.add_edge("scoring_select", "outline")
    g.add_edge("outline", "research")
    g.add_edge("research", "script")
    g.add_edge("script", "gate2")

    # Asset stages are conceptually independent, but they are chained rather than
    # fanned out. A fan-out whose branches are unequal in length (casting is one
    # hop, voice->envelope is two) fires the join node once per branch, so the
    # compositor ran twice. Serialising costs wall-clock we do not care about and
    # buys a correct single composite. Revisit with an explicit join if asset
    # latency ever matters.
    g.add_edge("gate2", "casting")
    g.add_edge("casting", "voice")
    g.add_edge("voice", "envelope")
    g.add_edge("envelope", "compositor")

    g.add_edge("compositor", "qc")
    g.add_conditional_edges("qc", _after_qc, {
        "green": "green",
        "repair": "repair",
        "exhausted": "escalate",
    })
    g.add_edge("repair", "compositor")   # scoped repair, then recompose and re-check
    g.add_edge("green", END)
    g.add_edge("escalate", END)

    return g.compile(checkpointer=MemorySaver())


def mermaid() -> str:
    ledger, rec = Ledger(), Recorder("_diagram")
    return build("_diagram", ledger, rec).get_graph().draw_mermaid()

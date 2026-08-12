"""A small executor for a declared topology.

Replaces the graph framework. The pipeline is a fixed sequence with one
conditional branch and one back edge, which is a `while` loop over a lookup
table — about sixty lines, and every frame in a stack trace is ours.

Two things the framework was doing that had to be rebuilt:

  merge()   state updates are partial dicts, and some fields accumulate rather
            than replace. The reducer for each field is read from the Annotated
            metadata on Production, so state.py stays the single definition.

  Pause     gates need to stop mid-run, hand a proposal out, take edits back and
            continue. That is what generators are for: `yield` at the gate,
            `send()` the edits in. No checkpointer required, because the
            generator *is* the suspended state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Generator, get_args, get_origin, get_type_hints

from harness.state import Production

# name -> next node, or a router that picks one, or None to stop
Topology = dict[str, "str | Callable[[Production], str] | None"]


def _reducers() -> dict[str, Callable[[Any, Any], Any]]:
    """Pull the per-field reducers off Production's Annotated metadata."""
    out: dict[str, Callable[[Any, Any], Any]] = {}
    for name, hint in get_type_hints(Production, include_extras=True).items():
        if get_origin(hint) is Annotated:
            for meta in get_args(hint)[1:]:
                if callable(meta):
                    out[name] = meta
    return out


REDUCERS = _reducers()


def merge(state: Production, update: dict) -> Production:
    """Apply a node's partial update. Annotated fields accumulate; the rest replace."""
    merged = dict(state)
    for key, value in (update or {}).items():
        reducer = REDUCERS.get(key)
        merged[key] = reducer(merged.get(key), value) if reducer else value
    return merged  # type: ignore[return-value]


@dataclass
class Pause:
    """Handed out at a gate. Send a dict of edits back to continue."""

    gate: str
    proposal: dict
    editable: list[str]
    involvement: int


@dataclass
class Gate:
    """A gate shows the whole proposed plan; the dial decides whether it stops.

    At involvement 0 this is a pass-through — not a bypass, the same gate asking
    zero questions. That is what keeps the eval path and the product on one code
    path with nothing to drift.
    """

    name: str
    fields: list[str]

    def proposal(self, state: Production) -> dict:
        return {f: state.get(f) for f in self.fields if state.get(f) is not None}


class StepLimit(RuntimeError):
    """Topology cycled longer than allowed. Guards the repair loop."""


@dataclass
class Pipeline:
    nodes: dict[str, Callable[[Production], dict]]
    gates: dict[str, Gate]
    topology: Topology
    entry: str
    max_steps: int = 60

    def run(self, state: Production) -> Generator[Pause, dict | None, Production]:
        """Walk the topology. Yields at each gate that decides to ask."""
        current: str | None = self.entry

        for _ in range(self.max_steps):
            if current is None:
                return state

            if current in self.gates:
                gate = self.gates[current]
                if state["involvement"] == 0:
                    state = merge(state, {
                        "log": [f"{gate.name}: auto-approved, 0 questions (involvement 0)"]
                    })
                else:
                    edits = yield Pause(
                        gate=gate.name,
                        proposal=gate.proposal(state),
                        editable=sorted(gate.proposal(state)),
                        involvement=state["involvement"],
                    )
                    edits = edits or {}
                    state = merge(state, {
                        **edits,
                        "log": [f"{gate.name}: "
                                f"{'edited ' + str(sorted(edits)) if edits else 'accepted unchanged'}"],
                    })
            else:
                state = merge(state, self.nodes[current](state))

            nxt = self.topology[current]
            current = nxt(state) if callable(nxt) else nxt

        raise StepLimit(f"exceeded {self.max_steps} steps — check the repair loop bound")

    def drive(self, state: Production,
              on_pause: Callable[[Pause], dict] | None = None) -> Production:
        """Run to completion, resolving each gate through `on_pause`.

        With no handler, every gate accepts its proposal unchanged — which is
        what a headless run at involvement > 0 should do.
        """
        gen = self.run(state)
        reply: dict | None = None
        try:
            while True:
                pause = gen.send(reply)
                reply = on_pause(pause) if on_pause else {}
        except StopIteration as stop:
            return stop.value

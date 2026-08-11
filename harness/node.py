"""The wrapper every stage passes through.

    budget check  ->  execute  ->  record  ->  partial state update

Order matters: the budget check runs first, because checking after execution
means the money is already spent.

Also holds `assert_scope`, which is the runtime guard that replaces the
structural one we gave up by passing the whole state object to every node.
A repair on line 4 must leave lines 1-3, 5-9 and every asset untouched; with a
shared state that is a convention, so it gets checked rather than trusted.
"""

from __future__ import annotations

import copy
import time
from typing import Callable, Protocol

from harness.budget import (
    ABORT,
    CAPS,
    DEGRADE,
    ESCALATE,
    BudgetExceeded,
    Ledger,
    RunTotalExceeded,
)
from harness.recorder import Recorder
from harness.state import Production


class StageFn(Protocol):
    def __call__(self, state: Production, ctx: "Ctx") -> dict: ...


class Ctx:
    """What a stage is handed besides the state: budget, recorder, the registry."""

    def __init__(self, ledger: Ledger, recorder: Recorder, stage: str, registry: dict):
        self.ledger = ledger
        self.recorder = recorder
        self.stage = stage
        self._registry = registry
        self.charged = 0.0

    def tool(self, name: str, **args):
        """Only tools in the closed registry are reachable. No open-ended access."""
        if name not in self._registry:
            raise KeyError(f"{name!r} is not in the tool registry")
        result = self._registry[name](**args)
        self.recorder.tool_call(self.stage, name, args, _summarize(result))
        return result

    def spend(self, estimate: float, request: dict, response: dict) -> None:
        """Record a model call and charge it. Budget was already checked."""
        self.ledger.charge(self.stage, estimate)
        self.charged += estimate
        self.recorder.model_call(self.stage, request, response, estimate)

    def note(self, text: str) -> None:
        self.recorder.note(self.stage, text)


class Aborted(Exception):
    """Cheap-and-early stage breached its cap. The run stops."""


def _summarize(value, limit: int = 400):
    s = repr(value)
    return s if len(s) <= limit else s[:limit] + f"...<{len(s)} chars>"


def assert_scope(before: Production, after: dict, allowed: set[str]) -> list[str]:
    """Return the keys a node wrote that it was not allowed to write.

    Coarse by design — key-level, not path-level. It catches the failure that
    actually matters: a repair reaching outside its span and undoing a previous
    round. Tighten to paths if that turns out not to be enough.
    """
    return sorted(
        k for k, v in after.items()
        if k not in allowed and before.get(k) != v
    )


def stage(
    name: str,
    fn: StageFn,
    *,
    ledger: Ledger,
    recorder: Recorder,
    registry: dict,
    writes: set[str] | None = None,
    estimate: Callable[[Production], float] | None = None,
) -> Callable[[Production], dict]:
    """Wrap a stage function into a pipeline node.

    `estimate` lets a stage say what this particular call will cost, rather than
    always reserving the typical figure. A stage that re-runs cheaply — QC on a
    repair round, where the arithmetic is free and the graders are cached — would
    otherwise be refused on its second call by a cap sized for one full pass.
    """

    cap = CAPS[name]

    def node(state: Production) -> dict:
        ctx = Ctx(ledger, recorder, name, registry)
        recorder.stage_start(name, {"keys": sorted(state.keys())})
        t0 = time.time()

        # --- budget, before anything is spent
        est = estimate(state) if estimate else cap.est
        try:
            ledger.check(name, est)
        except (BudgetExceeded, RunTotalExceeded) as exc:
            recorder.breach(name, cap.on_breach, str(exc))

            if cap.on_breach == ABORT:
                raise Aborted(f"{name}: {exc}") from exc
            if cap.on_breach == ESCALATE:
                return {"outcome": "escalated",
                        "log": [f"{name}: repair pool exhausted — shipping with report"]}
            # DEGRADE: run anyway, emit what we have, let QC report the damage
            return {"degraded": [name], "log": [f"{name}: degraded — {exc}"]}

        before = copy.deepcopy(dict(state))
        update = fn(state, ctx) or {}

        if writes is not None:
            stray = assert_scope(before, update, writes | {"log", "spend", "degraded"})
            if stray:
                raise RuntimeError(
                    f"{name} wrote outside its declared scope: {stray}. "
                    f"Allowed: {sorted(writes)}"
                )

        ms = int((time.time() - t0) * 1000)
        recorder.stage_end(name, sorted(update.keys()), ctx.charged, ms)

        update.setdefault("spend", []).append(
            {"stage": name, "cost": ctx.charged, "ms": ms}
        )
        return update

    node.__name__ = f"node_{name}"
    return node

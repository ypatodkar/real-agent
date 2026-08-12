"""Per-stage dollar caps, checked BEFORE execution.

Checking after execution means the money is already gone, so this is the one
thing in the harness that must run first. Breach behaviour differs by stage:
aborting a $0.005 Brief costs nothing, while aborting a $0.025 script pass
discards everything already spent on the run.

Rates are placeholders pending the spike. The proportions are the argument.
"""

from __future__ import annotations

from dataclasses import dataclass

ABORT = "abort"
DEGRADE = "degrade"
ESCALATE = "escalate"

RUN_TOTAL = 0.110


@dataclass(frozen=True)
class Cap:
    dollars: float      # ceiling for this stage across the whole run
    est: float          # expected cost of ONE invocation — what gets pre-checked
    on_breach: str
    note: str = ""


# `est` is what the pre-execution check reserves. Reserving the full cap instead
# would make any stage that runs twice look exhausted on its second call, which
# matters most for repair — a pool sized for three cheap rounds.
CAPS: dict[str, Cap] = {
    "brief":            Cap(0.005, 0.004, ABORT,    "one probe, one call — restarting is free"),
    "scoring_select":   Cap(0.000, 0.000, ABORT,    "library lookup, no model"),
    "outline":          Cap(0.010, 0.008, ABORT,    "one call, small context"),
    "research":         Cap(0.020, 0.017, DEGRADE,  "ship fewer claims, let grounding fail honestly"),
    "script":           Cap(0.025, 0.023, DEGRADE,  "deepest loop; emit best-so-far"),
    "casting":          Cap(0.005, 0.003, DEGRADE,  "sprites are a lookup; bg plates are the cost"),
    "voice":            Cap(0.015, 0.012, DEGRADE,  "~35s of TTS, nearly fixed"),
    "envelope":         Cap(0.005, 0.002, DEGRADE,  "one call, or deterministic"),
    "compositor":       Cap(0.000, 0.000, ABORT,    "ffmpeg on CPU — free"),
    "qc_plan":          Cap(0.000, 0.000, ABORT,    "pure arithmetic on the EDL — the cheap gate, free"),
    "qc_render":        Cap(0.010, 0.009, ABORT,    "two graders. NEVER degrades: a partial judge is worse than none"),
    "repair":           Cap(0.015, 0.004, ESCALATE, "run-level pool — three cheap rounds fit"),
}


class BudgetExceeded(Exception):
    def __init__(self, stage: str, cap: Cap, spent: float, would_be: float):
        self.stage, self.cap, self.spent, self.would_be = stage, cap, spent, would_be
        super().__init__(
            f"{stage}: ${would_be:.4f} would exceed cap ${cap.dollars:.4f} "
            f"(behaviour: {cap.on_breach})"
        )


class RunTotalExceeded(Exception):
    pass


class Ledger:
    """Tracks spend per stage and for the run. Repair is a pool, not per-round."""

    def __init__(self, run_total: float = RUN_TOTAL):
        self.run_total = run_total
        self.by_stage: dict[str, float] = {}

    @property
    def spent(self) -> float:
        return round(sum(self.by_stage.values()), 6)

    def check(self, stage: str, estimate: float) -> None:
        """Raise if this call would breach. Called BEFORE the work happens."""
        cap = CAPS.get(stage)
        if cap is None:
            raise KeyError(f"no cap defined for stage {stage!r} — add one to CAPS")

        would_be = round(self.by_stage.get(stage, 0.0) + estimate, 6)
        if would_be > cap.dollars + 1e-9:
            raise BudgetExceeded(stage, cap, self.by_stage.get(stage, 0.0), would_be)

        if round(self.spent + estimate, 6) > self.run_total + 1e-9:
            raise RunTotalExceeded(
                f"run total ${self.spent + estimate:.4f} exceeds ${self.run_total:.4f}"
            )

    def charge(self, stage: str, actual: float) -> None:
        self.by_stage[stage] = round(self.by_stage.get(stage, 0.0) + actual, 6)

    def report(self) -> list[dict]:
        return [
            {
                "stage": s,
                "spent": round(v, 6),
                "cap": CAPS[s].dollars,
                "headroom": round(CAPS[s].dollars - v, 6),
            }
            for s, v in self.by_stage.items()
        ]

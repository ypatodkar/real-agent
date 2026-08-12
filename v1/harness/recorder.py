"""Trajectory recorder — append-only, verbatim, one file per run.

This is the highest-value thing in the harness. It is what makes replay free:
when only the grading logic changes, recorded runs are re-scored at $0 instead
of being re-executed.

Two rules it must not break:

  1. Model calls AND tool results are both captured, raw. Not a summary.
  2. Grounded-search responses keep their returned text and citation metadata
     verbatim. Retrieval will not reproduce next week; stored evidence will.

LangGraph checkpoints store STATE, which is a different thing and not a
substitute — they record what the run looked like, not what was said to the
model or what came back.
"""

from __future__ import annotations

import json
import pathlib
import time
import uuid
from typing import Any

RUNS = pathlib.Path(__file__).resolve().parent.parent / "runs"


def new_run_id() -> str:
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


class Recorder:
    """One JSONL file per run. Append-only; never rewritten."""

    def __init__(self, run_id: str, root: pathlib.Path = RUNS):
        self.run_id = run_id
        self.dir = root / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trajectory.jsonl"
        self._seq = 0

    def _write(self, kind: str, payload: dict[str, Any]) -> None:
        self._seq += 1
        line = {
            "seq": self._seq,
            "ts": round(time.time(), 3),
            "run_id": self.run_id,
            "kind": kind,
            **payload,
        }
        with self.path.open("a") as f:
            f.write(json.dumps(line, default=str) + "\n")

    # --- what gets recorded -------------------------------------------------

    def stage_start(self, stage: str, inputs: dict) -> None:
        self._write("stage_start", {"stage": stage, "inputs": inputs})

    def model_call(self, stage: str, request: dict, response: dict, cost: float) -> None:
        """Verbatim. `response` must be the raw provider payload, not a parse of it."""
        self._write("model_call", {
            "stage": stage,
            "request": request,
            "response": response,        # includes groundingMetadata when present
            "cost": cost,
        })

    def tool_call(self, stage: str, tool: str, args: dict, result: Any) -> None:
        self._write("tool_call", {
            "stage": stage, "tool": tool, "args": args, "result": result,
        })

    def stage_end(self, stage: str, output_keys: list[str], cost: float, ms: int) -> None:
        self._write("stage_end", {
            "stage": stage, "output_keys": output_keys, "cost": cost, "ms": ms,
        })

    def breach(self, stage: str, behaviour: str, detail: str) -> None:
        self._write("budget_breach", {
            "stage": stage, "behaviour": behaviour, "detail": detail,
        })

    def violation(self, v: dict) -> None:
        self._write("violation", v)

    def note(self, stage: str, text: str) -> None:
        self._write("note", {"stage": stage, "text": text})

    def finish(self, outcome: str, spend: float, summary: dict) -> None:
        self._write("run_end", {"outcome": outcome, "spend": spend, **summary})
        (self.dir / "summary.json").write_text(
            json.dumps({"run_id": self.run_id, "outcome": outcome,
                        "spend": spend, **summary}, indent=2, default=str) + "\n"
        )

    # --- replay -------------------------------------------------------------

    @staticmethod
    def load(run_id: str, root: pathlib.Path = RUNS) -> list[dict]:
        path = root / run_id / "trajectory.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    @staticmethod
    def model_calls(run_id: str, root: pathlib.Path = RUNS) -> list[dict]:
        """Everything a grader needs to re-score without spending a cent."""
        return [e for e in Recorder.load(run_id, root) if e["kind"] == "model_call"]

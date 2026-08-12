"""Run the pipeline.

    python3 run.py                              # involvement 0 — the eval path
    python3 run.py --involvement 5              # gates interrupt and wait
    python3 run.py --topic "why agents fail"

Product mode and eval mode are the same graph. The only difference is one
integer.
"""

from __future__ import annotations

import argparse
import json

from harness.budget import Ledger
from harness.node import Aborted
from harness.recorder import Recorder, new_run_id
from harness.runner import Pause
from harness.state import new_production, total_spend
from pipeline.graph import build, describe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="are AI agents actually useful yet")
    ap.add_argument("--involvement", type=int, default=0)
    ap.add_argument("--graph", action="store_true", help="print the graph and exit")
    args = ap.parse_args()

    run_id = new_run_id()
    ledger = Ledger()
    recorder = Recorder(run_id)
    app = build(run_id, ledger, recorder)

    if args.graph:
        print(describe())
        return 0

    state = new_production(args.topic, args.involvement, run_id)

    print(f"run          {run_id}")
    print(f"topic        {args.topic!r}")
    print(f"involvement  {args.involvement}"
          f"{'  (eval path — no interruptions)' if args.involvement == 0 else ''}\n")

    def on_pause(pause: Pause) -> dict:
        """A real UI collects edits here. This accepts the proposal unchanged."""
        print(f"  [gate] {pause.gate} proposes {pause.editable} — accepting unchanged")
        return {}

    try:
        final = app.drive(state, on_pause)
    except Aborted as exc:
        recorder.finish("aborted", total_spend(state), {"reason": str(exc)})
        print(f"ABORTED  {exc}")
        return 1

    for line in final.get("log", []):
        print(f"  {line}")

    spend = total_spend(final)
    outcome = final.get("outcome") or "incomplete"
    violations = final.get("violations", [])

    print(f"\noutcome      {outcome.upper()}")
    print(f"rounds       {final.get('repair_round', 0)}")
    print(f"spend        ${spend:.4f} of ${ledger.run_total:.3f}")
    if final.get("degraded"):
        print(f"degraded     {final['degraded']}")
    if violations:
        print(f"violations   {[v['rule_id'] for v in violations]}")

    recorder.finish(outcome, spend, {
        "rounds": final.get("repair_round", 0),
        "violations": violations,
        "frame_hash": final.get("render", {}).get("frame_hash"),
        "by_stage": ledger.report(),
    })
    print(f"trajectory   {recorder.path}")
    return 0 if outcome == "green" else 2


if __name__ == "__main__":
    raise SystemExit(main())

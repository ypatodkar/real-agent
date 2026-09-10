"""Verbatim provider payloads, one file per run.

The database records what the turn did. This records what was actually said to
the model and what came back, which is a different question and the only one
that makes a bad turn diagnosable a week later.

Conversation content is the filmmaker's creative IP: these files stay local,
owner-readable, and are never sent anywhere.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

RUNS = pathlib.Path(__file__).parent / "runs"


class Recorder:
    def __init__(self, run_id: str):
        RUNS.mkdir(exist_ok=True)
        try:
            os.chmod(RUNS, 0o700)
        except OSError:
            pass
        self.path = RUNS / f"{run_id}.jsonl"

    def write(self, kind: str, payload) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps({"at": time.time(), "kind": kind, "payload": payload},
                               default=str) + "\n")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

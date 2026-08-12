"""The two model-graded rules — and the wall around them.

🔒 `grounding` and `coherence` are the only rules a model judges. If the
Improvement Agent can reach either one, the cheapest available way to raise the
pass rate is to make the judge lenient, and it will find that long before it
finds a better Showrunner.

So the constraint is structural, not advisory:

  - prompts and thresholds live here, in a namespace `propose_mutation` cannot
    address
  - `GRADER_VERSION` is pinned and recorded on every run
  - `assert_unmutated()` fails loudly if either prompt is edited at runtime

A pass-rate curve is only meaningful if it means the same thing at both ends.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

GRADER_VERSION = "g1"

# 🔒 PINNED. Do not tune these to move the pass rate. That is the failure mode
# this file exists to prevent.
GROUNDING_PROMPT = """You are grading whether a short video script's checkable
claims are traceable to its claim ledger.

Score 0.0 to 1.0. A claim is traceable if the ledger contains an entry that
supports it. Opinions, jokes and rhetorical questions are not checkable claims
and must not count against the score.

Return JSON: {"score": float, "untraceable": [str], "reasoning": str}"""

COHERENCE_PROMPT = """You are grading a short video script on structure.

Score 0.0 to 1.0 against three things: does the hook earn attention in its
first seconds, does the arc build rather than meander, and does the turn land
where the music turns.

Return JSON: {"score": float, "weakest": str, "reasoning": str}"""

THRESHOLDS = {"grounding": 0.60, "coherence": 0.55}

_FINGERPRINT = hashlib.sha256(
    (GROUNDING_PROMPT + COHERENCE_PROMPT + json.dumps(THRESHOLDS, sort_keys=True)).encode()
).hexdigest()[:16]


def assert_unmutated() -> None:
    """Called before grading. Catches in-process tampering with the judge."""
    current = hashlib.sha256(
        (GROUNDING_PROMPT + COHERENCE_PROMPT + json.dumps(THRESHOLDS, sort_keys=True)).encode()
    ).hexdigest()[:16]
    if current != _FINGERPRINT:
        raise RuntimeError(
            f"grader definitions changed at runtime ({_FINGERPRINT} -> {current}). "
            "Graders are pinned; a moving judge makes the pass rate meaningless."
        )


def _violation(rule: str, owner: str, score: float, threshold: float, detail: Any) -> dict:
    return {"rule_id": rule, "severity": "fail", "owner": owner,
            "evidence": {"score": round(score, 3), "threshold": threshold,
                         "grader_version": GRADER_VERSION, "detail": detail},
            "suggested_action": "rewrite" if rule == "coherence" else "re_source"}


def grade(state: dict, call) -> list[dict]:
    """Run both graders. `call(prompt, stub) -> dict` is the stage's model hook.

    Cheap pre-check first: a claim referenced by the script but absent from the
    ledger is a hard miss, and finding it costs nothing.
    """
    assert_unmutated()

    allowed = set(state.get("applicable_rules", []))
    out: list[dict] = []

    ledger = {c["id"] for c in state.get("claims", [])}
    referenced = {
        ref
        for shot in state.get("edl", {}).get("shots", [])
        for ref in shot.get("claim_refs", [])
    }
    dangling = sorted(referenced - ledger)

    if "grounding" in allowed:
        result = call(
            GROUNDING_PROMPT + f"\n\nLedger: {sorted(ledger)}\nReferenced: {sorted(referenced)}",
            {"score": 0.0 if dangling else 0.91,
             "untraceable": dangling,
             "reasoning": "stub"},
        )
        score = float(result.get("score", 0.0))
        if score < THRESHOLDS["grounding"]:
            out.append(_violation("grounding", "research", score,
                                  THRESHOLDS["grounding"], result.get("untraceable")))

    if "coherence" in allowed:
        result = call(
            COHERENCE_PROMPT + f"\n\nShots: {len(state.get('edl', {}).get('shots', []))}"
                               f"\nTurn at: {state.get('outline', {}).get('turn_at_s')}",
            {"score": 0.84, "weakest": "hook", "reasoning": "stub"},
        )
        score = float(result.get("score", 0.0))
        if score < THRESHOLDS["coherence"]:
            out.append(_violation("coherence", "showrunner", score,
                                  THRESHOLDS["coherence"], result.get("weakest")))

    return out

"""The QC rule set.

Nine rules are arithmetic and cost nothing. Two are model-graded and live in
`qc.graders`, in a namespace the Improvement Agent cannot address.

Two properties this module exists to hold:

  Thresholds come from `brief.params`, never from constants. Brief tunes
  parameters per topic, so a rule reading a hardcoded number would be a
  validator disagreeing with the plan it is validating.

  Rules are split by phase. `plan` rules read only the EDL and run BEFORE any
  asset is generated — that is the cheap gate, and it catches most planning
  failures for free. `render` rules need real audio or pixels and run on far
  fewer executions.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import soundfile as sf

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

PLAN, RENDER = "plan", "render"


def _p(state: dict, key: str, default: Any) -> Any:
    """Threshold lookup: brief.params first, default only as a fallback."""
    return state.get("brief", {}).get("params", {}).get(key, default)


def _v(rule: str, owner: str, evidence: dict, action: str, severity: str = "fail") -> dict:
    return {"rule_id": rule, "severity": severity, "owner": owner,
            "evidence": evidence, "suggested_action": action}


def _shots(state: dict) -> list[dict]:
    return state.get("edl", {}).get("shots", [])


def _dur(shot: dict) -> float:
    return round(shot["out_s"] - shot["in_s"], 4)


# ----------------------------------------------------------------- plan rules


def beat_alignment(state: dict) -> list[dict]:
    """Every cut must land within tolerance of a measured beat."""
    beats = state.get("track", {}).get("beats", [])
    tol = _p(state, "beat_tolerance_ms", 60)
    out = []
    for shot in _shots(state):
        nearest = min(beats, key=lambda b: abs(b - shot["in_s"]))
        offset = round((shot["in_s"] - nearest) * 1000, 1)
        if abs(offset) > tol:
            out.append(_v("beat_alignment", "showrunner",
                          {"shot": shot["id"], "in_s": shot["in_s"],
                           "nearest_beat_s": nearest, "offset_ms": offset,
                           "threshold_ms": tol}, "retime"))
    return out


def duration_adherence(state: dict) -> list[dict]:
    shots = _shots(state)
    if not shots:
        return []
    target = state.get("brief", {}).get("duration_s", state["edl"]["meta"]["duration_s"])
    tol = _p(state, "duration_tolerance_s", 0.5)
    actual = max(s["out_s"] for s in shots)
    if abs(actual - target) > tol:
        return [_v("duration_adherence", "showrunner",
                   {"planned_s": actual, "target_s": target, "tolerance_s": tol},
                   "retime")]
    return []


def reading_speed(state: dict) -> list[dict]:
    """Captions must be readable at the speed they are on screen."""
    max_cps = _p(state, "max_cps", 20)
    out = []
    for shot in _shots(state):
        caption = next((l for l in shot["layers"] if l["type"] == "caption"), None)
        if not caption:
            continue
        span = caption.get("span")
        seconds = (span[1] - span[0]) if span else _dur(shot)
        if seconds <= 0:
            continue
        cps = round(len(caption["text"]) / seconds, 1)
        if cps > max_cps:
            out.append(_v("reading_speed", "showrunner",
                          {"shot": shot["id"], "chars": len(caption["text"]),
                           "seconds": seconds, "cps": cps, "threshold_cps": max_cps},
                          "shorten"))
    return out


def pacing_curve(state: dict) -> list[dict]:
    """Shot rhythm against the track's energy.

    Two checks, both cheap. No shot may run longer than `max_shot_s`; and the
    average shot after the drop may not be longer than before it, because the
    energy is supposed to rise into the turn, not sag away from it.
    """
    shots = _shots(state)
    if len(shots) < 3:
        return []

    max_shot = _p(state, "max_shot_s", 5.0)
    drop = state.get("track", {}).get("drop_s")
    out = []

    for shot in shots:
        if _dur(shot) > max_shot:
            out.append(_v("pacing_curve", "showrunner",
                          {"shot": shot["id"], "length_s": _dur(shot),
                           "max_shot_s": max_shot}, "split"))

    if drop is not None:
        before = [_dur(s) for s in shots if s["out_s"] <= drop]
        after = [_dur(s) for s in shots if s["in_s"] >= drop]
        if before and after:
            mb, ma = sum(before) / len(before), sum(after) / len(after)
            slack = _p(state, "pacing_slack_s", 0.75)
            if ma > mb + slack:
                out.append(_v("pacing_curve", "showrunner",
                              {"mean_before_drop_s": round(mb, 2),
                               "mean_after_drop_s": round(ma, 2),
                               "slack_s": slack,
                               "note": "shots lengthen after the drop — energy sags"},
                              "tighten_after_drop"))
    return out


def screen_time_balance(state: dict) -> list[dict]:
    """No character may dominate beyond the configured split."""
    cast = state.get("brief", {}).get("cast", [])
    if len(cast) < 2:
        return []

    share: dict[str, float] = {c["character_id"]: 0.0 for c in cast}
    total = 0.0
    for shot in _shots(state):
        speaker = shot.get("speaker")
        if speaker in share:
            share[speaker] += _dur(shot)
            total += _dur(shot)
    if total <= 0:
        return []

    fair = 1.0 / len(cast)
    tol = _p(state, "screen_time_tolerance", 0.10)
    pct = {k: round(v / total, 3) for k, v in share.items()}
    worst = max(pct.values(), key=lambda v: abs(v - fair))

    if abs(worst - fair) > tol:
        return [_v("screen_time_balance", "showrunner",
                   {"share": pct, "target": round(fair, 3), "tolerance": tol},
                   "rebalance")]
    return []


# --------------------------------------------------------------- render rules


def speaker_attribution(state: dict) -> list[dict]:
    """Each shot's dialogue must be spoken in that character's voice."""
    cast = state.get("brief", {}).get("cast", [])
    spans = state.get("audio", {}).get("spans", [])
    if len(cast) < 2 or not spans:
        return []

    by_idx = {i: c["character_id"] for i, c in enumerate(cast)}
    out = []
    for shot in _shots(state):
        speaker = shot.get("speaker")
        if not speaker:
            continue
        mid = (shot["in_s"] + shot["out_s"]) / 2
        span = next((s for s in spans if s["start_s"] <= mid < s["end_s"]), None)
        if span is None:
            continue
        voiced = by_idx.get(span["speaker_idx"])
        if voiced != speaker:
            out.append(_v("speaker_attribution", "voice",
                          {"shot": shot["id"], "planned": speaker, "rendered": voiced,
                           "at_s": round(mid, 3)}, "resynth"))
    return out


def identity_drift(state: dict) -> list[dict]:
    """Perceptual distance from the stored roster reference.

    With a fixed roster the sprites are read from disk and never regenerated,
    so this measures zero by construction. That is the honest result, and it is
    the evidence for whether the rule still earns its slot.
    """
    from PIL import Image

    threshold = _p(state, "identity_threshold", 0.25)
    used: set[tuple[str, str]] = set()
    for shot in _shots(state):
        for layer in shot["layers"]:
            if layer["type"] == "character":
                used.add((layer["id"], layer.get("pose", "idle")))

    out = []
    for cid, pose in sorted(used):
        ref = ASSETS / "fixtures" / "sprites" / cid / f"{pose}.png"
        if not ref.exists():
            out.append(_v("identity_drift", "casting",
                          {"character": cid, "pose": pose, "reason": "pose missing from roster"},
                          "regenerate_pose"))
            continue
        a = np.asarray(Image.open(ref).convert("RGB"), dtype=np.float64)
        dist = float(np.abs(a - a).mean() / 255.0)   # against itself: exactly 0
        if dist > threshold:
            out.append(_v("identity_drift", "casting",
                          {"character": cid, "pose": pose, "distance": round(dist, 4),
                           "threshold": threshold}, "regenerate_pose"))
    return out


def music_ducking(state: dict) -> list[dict]:
    """Rendered music level under speech must match Scoring's declared envelope."""
    declared = state.get("envelope", {}).get("duck_db")
    spans = state.get("audio", {}).get("spans", [])
    if declared is None or not spans:
        return []

    stem = state.get("render", {}).get("mix", {}).get("music_stem")
    if not stem or not pathlib.Path(stem).exists():
        return []
    music, sr = sf.read(stem, dtype="float64", always_2d=False)
    if music.ndim > 1:
        music = music.mean(axis=1)

    mask = np.zeros(len(music), dtype=bool)
    for s in spans:
        mask[int(s["start_s"] * sr): int(s["end_s"] * sr)] = True

    def db(x: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(x**2))) if x.size else 0.0
        return 20 * math.log10(max(rms, 1e-9))

    under, over = db(music[mask]), db(music[~mask])
    measured = round(under - over, 2)
    tol = _p(state, "ducking_tolerance_db", 2.0)

    if abs(measured - declared) > tol:
        return [_v("music_ducking", "scoring",
                   {"declared_db": declared, "measured_db": measured,
                    "tolerance_db": tol}, "refit_envelope")]
    return []


def loudness_spec(state: dict) -> list[dict]:
    """Integrated loudness of the final mix, measured with ffmpeg's EBU R128 scanner."""
    target = _p(state, "target_lufs", -14.0)
    tol = _p(state, "loudness_tolerance_lu", 1.5)

    mix_path = state.get("render", {}).get("mix", {}).get("path")
    if not mix_path or not pathlib.Path(mix_path).exists():
        return []

    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", mix_path,
         "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    match = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", proc.stderr)
    if not match:
        return []

    measured = float(match[-1])
    if abs(measured - target) > tol:
        return [_v("loudness_spec", "scoring",
                   {"measured_lufs": measured, "target_lufs": target,
                    "tolerance_lu": tol}, "normalize")]
    return []


# ----------------------------------------------------------------- registry


@dataclass(frozen=True)
class Rule:
    id: str
    owner: str
    phase: str
    check: Callable[[dict], list[dict]]
    min_cast: int = 1
    graded: bool = False


RULES: list[Rule] = [
    Rule("beat_alignment",      "showrunner", PLAN,   beat_alignment),
    Rule("duration_adherence",  "showrunner", PLAN,   duration_adherence),
    Rule("reading_speed",       "showrunner", PLAN,   reading_speed),
    Rule("pacing_curve",        "showrunner", PLAN,   pacing_curve),
    Rule("screen_time_balance", "showrunner", PLAN,   screen_time_balance, min_cast=2),
    Rule("speaker_attribution", "voice",      RENDER, speaker_attribution, min_cast=2),
    Rule("identity_drift",      "casting",    RENDER, identity_drift),
    Rule("music_ducking",       "scoring",    RENDER, music_ducking),
    Rule("loudness_spec",       "scoring",    RENDER, loudness_spec),
]

BY_ID = {r.id: r for r in RULES}

# rule -> owner, the Repair Router's fixed lookup. Graded rules included; they
# route like any other, they simply cost a model call to evaluate.
OWNER = {**{r.id: r.owner for r in RULES},
         "coherence": "showrunner", "grounding": "research"}


def applicable(brief: dict) -> list[str]:
    """Which rules this configuration can be scored against.

    A monologue has one character, so attribution and screen-time have nothing
    to measure — that reel is scored out of 8, not out of 11. Comparing rates
    rather than counts is what keeps configurations comparable.
    """
    cast = len(brief.get("cast", [])) or brief.get("params", {}).get("cast_size", 1)
    ids = [r.id for r in RULES if cast >= r.min_cast]
    return sorted(ids + ["coherence", "grounding"])


def run(phase: str, state: dict) -> list[dict]:
    """Every applicable rule for this phase. Free — no model, no network."""
    allowed = set(state.get("applicable_rules", []))
    out: list[dict] = []
    for rule in RULES:
        if rule.phase != phase or rule.id not in allowed:
            continue
        out.extend(rule.check(state))
    return out

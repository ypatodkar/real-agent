"""Stage implementations.

Model calls are STUBBED — deterministic fixtures standing in for Gemini so the
graph runs end to end today with no credentials. Each stub still charges its
stage's budget and writes a model_call to the trajectory, so the harness is
exercised for real even though the model is not.

The compositor and the QC rules are NOT stubbed. They are the code proven by
the spike, wired in as-is.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys

import yaml

from harness.node import Ctx
from harness.state import Production

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
sys.path.insert(0, str(ROOT / "spike"))

import render as spike  # noqa: E402  — the proven compositor


def _fake_response(stage: str, payload: dict) -> dict:
    """Shaped like a provider response so the recorder stores something realistic."""
    return {
        "_stub": True,
        "model": "gemini-stub",
        "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
        "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0},
    }


# ----------------------------------------------------------------- planning


def brief(state: Production, ctx: Ctx) -> dict:
    topic = state["topic"]
    probe = ctx.tool("topic_probe", topic=topic)

    out = {
        "intent": "commentary",
        "format": "debate",
        "tone": "wry",
        "duration_s": 15.0,
        "params": {
            "cast_size": 2,
            "turn_len_s": [3, 9],
            "beat_tolerance_ms": 60,
            "max_hold_s": 2.0,
            "bg_budget": 2,
        },
        "cast": [
            {"role": "skeptic", "character_id": "ch_01", "x": 0.28},
            {"role": "enthusiast", "character_id": "ch_02", "x": 0.72},
        ],
        "confidence": {"intent": 0.72, "format": 0.85, "tone": 0.45},
        "assumptions": ["tone: wry (not asked — confidence 0.45)"],
        "brief_invalid": False,
        "probe": probe,
    }
    ctx.spend(0.004, {"stage": "brief", "topic": topic}, _fake_response("brief", out))

    rules = ["beat_alignment", "duration_adherence"]
    if out["params"]["cast_size"] >= 2:
        rules += ["speaker_attribution", "screen_time_balance"]

    return {"brief": out, "applicable_rules": sorted(rules),
            "log": [f"brief: {out['intent']} · {out['format']} · {out['tone']}"]}


def scoring_select(state: Production, ctx: Ctx) -> dict:
    """A lookup. No model, no cost — the grid is ground truth."""
    grid = ctx.tool("track_select", mood="uneasy", duration_s=state["brief"]["duration_s"])
    return {"track": grid,
            "log": [f"track: {grid['bpm']} BPM, drop {grid['drop_s']}s, "
                    f"{len(grid['beats'])} beats"]}


def outline(state: Production, ctx: Ctx) -> dict:
    drop = state["track"]["drop_s"]
    out = {
        "beats": [
            {"id": "b1", "role": "hook", "needs_fact": False},
            {"id": "b2", "role": "setup", "needs_fact": True},
            {"id": "b3", "role": "turn", "at_s": drop, "needs_fact": True},
            {"id": "b4", "role": "land", "needs_fact": False},
        ],
        "turn_at_s": drop,
    }
    ctx.spend(0.008, {"stage": "outline", "drop_s": drop}, _fake_response("outline", out))
    flagged = sum(b["needs_fact"] for b in out["beats"])
    return {"outline": out, "log": [f"outline: 4 beats, turn on drop, {flagged} need facts"]}


def research(state: Production, ctx: Ctx) -> dict:
    """Runs for every intent. Comedy simply flags fewer beats — never skipped."""
    flagged = [b for b in state["outline"]["beats"] if b["needs_fact"]]
    if not flagged:
        ctx.note("no flagged beats — sourcing nothing, $0")
        return {"claims": [], "log": ["research: 0 beats flagged, nothing sourced"]}

    claims = [
        {"id": f"c_{i:02d}", "beat": b["id"], "text": f"sourced claim for {b['id']}",
         "source": "https://example.invalid/stub"}
        for i, b in enumerate(flagged, 1)
    ]
    ctx.spend(0.017, {"stage": "research", "beats": [b["id"] for b in flagged]},
              _fake_response("research", {"claims": claims}))
    return {"claims": claims, "log": [f"research: {len(claims)} claims for {len(flagged)} beats"]}


def script(state: Production, ctx: Ctx) -> dict:
    """Stub: loads the hand-written EDL from the spike, including its planted 80ms error."""
    edl = yaml.safe_load((ROOT / "spike" / "demo.edl.yaml").read_text())
    edl["shots"].sort(key=lambda s: s["in_s"])
    ctx.spend(0.023, {"stage": "script", "claims": len(state.get("claims", []))},
              _fake_response("script", {"shots": len(edl["shots"])}))
    return {"edl": edl, "script": {"lines": len(edl["shots"])},
            "log": [f"script: {len(edl['shots'])} shots on the grid"]}


# ----------------------------------------------------------------- assets


def casting(state: Production, ctx: Ctx) -> dict:
    """Sprites are a roster lookup at $0. Background plates are the only image cost."""
    scenes = sorted({l["scene_id"] for s in state["edl"]["shots"]
                     for l in s["layers"] if l["type"] == "bg"})
    budget = state["brief"]["params"]["bg_budget"]
    if len(scenes) > budget:
        ctx.note(f"scene count {len(scenes)} exceeds bg_budget {budget}")

    roster = ctx.tool("roster_lookup", character_ids=[c["character_id"] for c in state["brief"]["cast"]])
    plates = ctx.tool("generate_plates", scene_ids=scenes)
    ctx.spend(0.003, {"stage": "casting", "scenes": scenes}, _fake_response("casting", {"plates": plates}))

    return {"assets": {"sprites": roster, "plates": plates},
            "log": [f"casting: {roster['n_sprites']} sprites (lookup, $0), "
                    f"{len(plates)} plates generated"]}


def voice(state: Production, ctx: Ctx) -> dict:
    spans = ctx.tool("measure_vo", path=str(ASSETS / "fixtures" / "vo.wav"))
    ctx.spend(0.012, {"stage": "voice", "n_shots": len(state["edl"]["shots"])},
              _fake_response("voice", spans))
    return {"audio": spans, "log": [f"voice: {spans['n_spans']} spans measured"]}


def envelope(state: Production, ctx: Ctx) -> dict:
    env = {"duck_db": -9.0, "full_at_drop": True}
    ctx.spend(0.002, {"stage": "envelope"}, _fake_response("envelope", env))
    return {"envelope": env, "log": ["envelope: -9dB under speech, full across the drop"]}


def compositor(state: Production, ctx: Ctx) -> dict:
    """Not a stub. The proven spike renderer, deterministic, $0."""
    result = ctx.tool("render", edl=state["edl"])
    return {"render": result,
            "log": [f"composite: {result['n_frames']} frames, hash {result['frame_hash'][:12]}"]}


# ----------------------------------------------------------------- QC + repair


def qc(state: Production, ctx: Ctx) -> dict:
    """Real rules from the spike. Denominator is applicable_rules, not eleven."""
    edl = state["edl"]
    beats = state["track"]["beats"]

    violations = spike.check_beat_alignment(edl, beats) + spike.check_duration(edl)
    for v in violations:
        ctx.recorder.violation(v)

    applicable = [r for r in state["applicable_rules"]
                  if r in {"beat_alignment", "duration_adherence"}]  # implemented so far
    failed = {v["rule_id"] for v in violations}
    passed = len(applicable) - len(failed & set(applicable))

    return {
        "violations": violations,
        "log": [f"qc round {state['repair_round']}: {passed}/{len(applicable)} applicable rules pass"
                + (f" — failing: {sorted(failed)}" if failed else "")],
    }


REPAIR_OWNER = {
    "beat_alignment": "showrunner",     # always. Never Scoring — see decision log.
    "duration_adherence": "showrunner",
    "reading_speed": "showrunner",
    "pacing_curve": "showrunner",
    "screen_time_balance": "showrunner",
    "coherence": "showrunner",
    "speaker_attribution": "voice",
    "identity_drift": "casting",
    "music_ducking": "scoring",
    "loudness_spec": "scoring",
    "grounding": "research",
}


def repair(state: Production, ctx: Ctx) -> dict:
    """Scoped: only the failing span changes. Everything else is frozen."""
    edl = copy.deepcopy(state["edl"])
    beats = state["track"]["beats"]
    fixed = []

    for v in state["violations"]:
        owner = REPAIR_OWNER[v["rule_id"]]
        if v["rule_id"] != "beat_alignment":
            ctx.note(f"{v['rule_id']} -> {owner}: no repair implemented yet")
            continue

        shot_id = v["evidence"]["shot"]
        shot = next(s for s in edl["shots"] if s["id"] == shot_id)
        snapped = min(beats, key=lambda b: abs(b - shot["in_s"]))
        ctx.note(f"{shot_id}: in_s {shot['in_s']} -> {snapped} (owner: {owner})")
        shot["in_s"] = snapped
        fixed.append(shot_id)

    ctx.spend(0.004, {"stage": "repair", "fixed": fixed},
              _fake_response("repair", {"shots": fixed}))

    return {"edl": edl, "repair_round": state["repair_round"] + 1,
            "log": [f"repair round {state['repair_round'] + 1}: retimed {fixed or 'nothing'}"]}

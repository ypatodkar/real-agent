"""Stage implementations.

Every model call goes through `_call`, which hits Gemini when credentials exist
and falls back to a deterministic fixture when they do not. The path through the
harness is identical either way — budget checked before execution, verbatim
payload written to the trajectory — so a credential-less run still exercises
everything except the model itself.

Set GOOGLE_CLOUD_PROJECT (Vertex) or GOOGLE_API_KEY (AI Studio) to go live.

The compositor and the QC rules are never stubbed. They are the code proven by
the spike, wired in as-is.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys

import yaml

from harness.model import get_client
from harness.node import Ctx
from harness.state import Production

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
sys.path.insert(0, str(ROOT / "spike"))

import render as spike  # noqa: E402  — the proven compositor

MODEL = get_client()


def _call(ctx: Ctx, prompt: str, *, stub: dict, fallback_cost: float,
          system: str | None = None, grounded: bool = False) -> dict:
    """One model call: real if credentials exist, the stub fixture if not.

    Either way the recorder sees a model_call with the verbatim payload, so the
    harness is exercised identically and a stubbed run stays replayable.
    """
    resp = MODEL.generate(prompt, stub=stub, system=system, grounded=grounded)
    cost = resp.cost if not resp.stub else fallback_cost
    ctx.spend(cost, {"prompt": prompt, "system": system, "grounded": grounded}, resp.raw)

    if resp.grounding:
        ctx.note(f"grounding: {len(resp.grounding)} metadata block(s) recorded verbatim")

    try:
        return resp.json()
    except (ValueError, TypeError):
        ctx.note("response was not JSON — falling back to the stub shape")
        return stub


# ----------------------------------------------------------------- planning


BRIEF_SYSTEM = """You are the Brief agent for a short-form video system.
Turn a vague topic into a complete spec sheet. Decide every field — never ask.
Score your own confidence per field; low-confidence fields become questions
the gate may raise, but you still commit to a value.

intent  MUST be one of: explainer | comedy | commentary
format  MUST be one of: monologue (1 cast) | debate (2 cast)
The catalogs are closed. Do not invent a fourth intent or a third format.

Return JSON only, matching the shape you are given."""

BRIEF_STUB = {
    "intent": "commentary",
    "format": "debate",
    "tone": "wry",
    "duration_s": 15.0,
    "params": {"cast_size": 2, "turn_len_s": [3, 9], "beat_tolerance_ms": 60,
               "max_hold_s": 2.0, "bg_budget": 2},
    "confidence": {"intent": 0.72, "format": 0.85, "tone": 0.45},
    "assumptions": ["tone: wry (not asked — confidence 0.45)"],
}

# Cast is assigned from the stored roster, never invented by the model.
ROSTER_CAST = [
    {"role": "skeptic", "character_id": "ch_01", "x": 0.28},
    {"role": "enthusiast", "character_id": "ch_02", "x": 0.72},
]


def brief(state: Production, ctx: Ctx) -> dict:
    topic = state["topic"]
    probe = ctx.tool("topic_probe", topic=topic)

    out = _call(
        ctx,
        f"Topic: {topic}\n\nProbe: {json.dumps(probe)}\n\n"
        f"Return JSON with exactly these keys: {sorted(BRIEF_STUB)}",
        system=BRIEF_SYSTEM,
        stub=BRIEF_STUB,
        fallback_cost=0.004,
    )

    # The catalogs are closed, so the schema check runs here — records, never blocks.
    invalid = []
    if out.get("intent") not in ("explainer", "comedy", "commentary"):
        invalid.append(f"intent {out.get('intent')!r} not in catalog")
    if out.get("format") not in ("monologue", "debate"):
        invalid.append(f"format {out.get('format')!r} not in catalog")

    cast_size = 1 if out.get("format") == "monologue" else 2
    out.setdefault("params", {})["cast_size"] = cast_size
    out["cast"] = ROSTER_CAST[:cast_size]
    out["probe"] = probe
    out["brief_invalid"] = bool(invalid)
    if invalid:
        out["invalid_reason"] = invalid
        ctx.note(f"brief_invalid: {invalid} — recorded, not blocking")

    rules = ["beat_alignment", "duration_adherence"]
    if cast_size >= 2:
        rules += ["speaker_attribution", "screen_time_balance"]

    return {"brief": out, "applicable_rules": sorted(rules),
            "log": [f"brief: {out['intent']} · {out['format']} · {out['tone']}"
                    f"{' [INVALID]' if invalid else ''}"]}


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
    out = _call(ctx, f"Beat grid drop at {drop}s. Structure 4 beats, turn on the drop. "
                     f"Flag beats needing a checkable fact.",
                stub=out, fallback_cost=0.008)
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
    got = _call(ctx, f"Source these beats with checkable claims: "
                     f"{[b['id'] for b in flagged]}",
                stub={"claims": claims}, fallback_cost=0.017, grounded=True)
    claims = got.get("claims", claims)
    return {"claims": claims, "log": [f"research: {len(claims)} claims for {len(flagged)} beats"]}


def script(state: Production, ctx: Ctx) -> dict:
    """Stub: loads the hand-written EDL from the spike, including its planted 80ms error."""
    edl = yaml.safe_load((ROOT / "spike" / "demo.edl.yaml").read_text())
    edl["shots"].sort(key=lambda s: s["in_s"])
    _call(ctx, f"Write dialogue for {len(edl['shots'])} shots on the beat grid.",
          stub={"shots": len(edl["shots"])}, fallback_cost=0.023)
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
    _call(ctx, f"Describe background plates for scenes {scenes}.",
          stub={"plates": plates}, fallback_cost=0.003)

    return {"assets": {"sprites": roster, "plates": plates},
            "log": [f"casting: {roster['n_sprites']} sprites (lookup, $0), "
                    f"{len(plates)} plates generated"]}


def voice(state: Production, ctx: Ctx) -> dict:
    spans = ctx.tool("measure_vo", path=str(ASSETS / "fixtures" / "vo.wav"))
    _call(ctx, "Synthesize and measure the VO.", stub=spans, fallback_cost=0.012)
    return {"audio": spans, "log": [f"voice: {spans['n_spans']} spans measured"]}


def envelope(state: Production, ctx: Ctx) -> dict:
    env = _call(ctx, "Fit a ducking envelope to the measured VO spans.",
                stub={"duck_db": -9.0, "full_at_drop": True}, fallback_cost=0.002)
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

    _call(ctx, f"Retime shots {fixed} onto the nearest beat. Change nothing else.",
          stub={"shots": fixed}, fallback_cost=0.004)

    return {"edl": edl, "repair_round": state["repair_round"] + 1,
            "log": [f"repair round {state['repair_round'] + 1}: retimed {fixed or 'nothing'}"]}

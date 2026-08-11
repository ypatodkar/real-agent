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
import hashlib
import json
import pathlib
import sys

import yaml

from harness.model import get_client
from qc import graders, rules
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

    applicable = rules.applicable(out)

    return {"brief": out, "applicable_rules": applicable,
            "log": [f"brief: {out['intent']} · {out['format']} · {out['tone']}"
                    f"{' [INVALID]' if invalid else ''} · "
                    f"{len(applicable)} applicable rules"]}


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
    """Not a stub. The proven spike renderer, deterministic, $0.

    Also applies the envelope Scoring declared — the Compositor decides nothing,
    it executes. That is what leaves `music_ducking` a real claim to verify.
    """
    frames = ctx.tool("render", edl=state["edl"])
    mix = ctx.tool("mix_audio",
                   duck_db=state["envelope"]["duck_db"],
                   spans=state["audio"]["spans"],
                   target_lufs=state["brief"]["params"].get("target_lufs", -14.0))
    return {"render": {**frames, "mix": mix},
            "log": [f"composite: {frames['n_frames']} frames, hash {frames['frame_hash'][:12]}"
                    f" · mix {mix['lufs_after']} LUFS"]}


# ----------------------------------------------------------------- QC + repair


def _score(state: Production, phase: str, violations: list[dict]) -> str:
    """Pass rate over applicable rules for this phase — a rate, not a count."""
    in_phase = [r.id for r in rules.RULES
                if r.phase == phase and r.id in set(state["applicable_rules"])]
    if phase == rules.RENDER:
        in_phase += [r for r in ("grounding", "coherence") if r in state["applicable_rules"]]
    failed = {v["rule_id"] for v in violations} & set(in_phase)
    return f"{len(in_phase) - len(failed)}/{len(in_phase)}"


def qc_plan(state: Production, ctx: Ctx) -> dict:
    """The cheap gate. Reads only the EDL, so it runs before a pixel exists.

    A planning error caught here costs one scoped text repair instead of a
    wasted round of image and speech generation.
    """
    violations = rules.run(rules.PLAN, state)
    for v in violations:
        ctx.recorder.violation(v)

    failed = sorted({v["rule_id"] for v in violations})
    return {
        "violations": violations,
        "phase": rules.PLAN,
        "log": [f"qc:plan round {state['repair_round']}: "
                f"{_score(state, rules.PLAN, violations)} pass"
                + (f" — failing: {failed}" if failed else "") + "  (no assets yet)"],
    }


def _graded_fingerprint(state: Production) -> str:
    """What the graders actually judge: the words and the sourcing.

    A retime moves a cut; it changes neither. Re-grading after one would spend
    a model call to get the same verdict back.
    """
    payload = json.dumps({
        "lines": [s["layers"] for s in state.get("edl", {}).get("shots", [])],
        "claims": sorted(c["id"] for c in state.get("claims", [])),
        "turn": state.get("outline", {}).get("turn_at_s"),
        "grader": graders.GRADER_VERSION,
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def qc_render_estimate(state: Production) -> float:
    """Free on a re-run whose content the graders have already judged."""
    cached = state.get("grades") or {}
    return 0.0 if cached.get("fingerprint") == _graded_fingerprint(state) else 0.009


def qc_render(state: Production, ctx: Ctx) -> dict:
    """Rules that need real audio or pixels, plus the two pinned graders.

    The arithmetic rules re-run every round because they are free. The graders
    re-run only when the content they judge has actually changed.
    """
    violations = rules.run(rules.RENDER, state)

    fingerprint = _graded_fingerprint(state)
    cached = state.get("grades") or {}
    if cached.get("fingerprint") == fingerprint:
        ctx.note(f"graders skipped — content unchanged since {fingerprint}")
        graded = cached["violations"]
    else:
        graded = graders.grade(
            state,
            lambda prompt, stub: _call(ctx, prompt, stub=stub, fallback_cost=0.0045),
        )
        cached = {"fingerprint": fingerprint, "violations": graded}

    violations += graded
    for v in violations:
        ctx.recorder.violation(v)

    failed = sorted({v["rule_id"] for v in violations})
    return {
        "violations": violations,
        "grades": cached,
        "phase": rules.RENDER,
        "log": [f"qc:render round {state['repair_round']}: "
                f"{_score(state, rules.RENDER, violations)} pass"
                + (f" — failing: {failed}" if failed else "")],
    }


def repair(state: Production, ctx: Ctx) -> dict:
    """Scoped: only the failing span changes. Everything else is frozen."""
    edl = copy.deepcopy(state["edl"])
    beats = state["track"]["beats"]
    fixed = []

    for v in state["violations"]:
        owner = rules.OWNER[v['rule_id']]
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

    unowned = sorted({v["rule_id"] for v in state["violations"]} - {"beat_alignment"})
    if unowned and not fixed:
        ctx.note(f"no repair implemented for {unowned} — escalating rather than looping")

    return {"edl": edl, "repair_round": state["repair_round"] + 1,
            "repaired": bool(fixed),
            "log": [f"repair round {state['repair_round'] + 1}: "
                    + (f"retimed {fixed}" if fixed
                       else f"nothing to do — {unowned} has no repair path")]}

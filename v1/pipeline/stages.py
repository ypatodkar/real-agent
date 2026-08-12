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

from typing import Any

import copy
import hashlib
import json
import pathlib
import sys

import yaml

from harness.model import get_client
from pipeline import edl as edl_builder
from qc import graders, rules
from harness.node import Ctx
from harness.state import Production

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
sys.path.insert(0, str(ROOT / "spike"))

import render as spike  # noqa: E402  — the proven compositor

MODEL = get_client()


def _shape_ok(got: Any, want: Any) -> bool:
    """Does `got` have the same shape as the fixture `want`?

    A model asked for JSON returns valid JSON in whatever structure it likes.
    Checking against the fixture means a deviation degrades to the stub instead
    of exploding four stages later on a KeyError.
    """
    if isinstance(want, dict):
        if not isinstance(got, dict) or not set(want).issubset(got):
            return False
        return all(_shape_ok(got[k], v) for k, v in want.items())
    if isinstance(want, list):
        if not isinstance(got, list) or not got:
            return isinstance(got, list)
        return _shape_ok(got[0], want[0]) if want else True
    return True


def _call(ctx: Ctx, prompt: str, *, stub: dict, fallback_cost: float,
          system: str | None = None, grounded: bool = False,
          schema: Any = None, require: set[str] | None = None) -> dict:
    """One model call: real if credentials exist, the stub fixture if not.

    Either way the recorder sees a model_call with the verbatim payload, so the
    harness is exercised identically and a stubbed run stays replayable.
    """
    resp = MODEL.generate(prompt, stub=stub, system=system, grounded=grounded,
                          json_out=not grounded, schema=None if grounded else schema)
    cost = resp.cost if not resp.stub else fallback_cost
    ctx.spend(cost, {"prompt": prompt, "system": system, "grounded": grounded}, resp.raw)

    if resp.grounding:
        ctx.note(f"grounding: {len(resp.grounding)} metadata block(s) recorded verbatim")

    try:
        got = resp.json()
    except (ValueError, TypeError) as exc:
        # Worth shouting about: a silent fallback means the model was called and
        # charged for, and none of what it said was used.
        ctx.note(f"UNPARSEABLE response, using stub instead — {exc}. "
                 f"First 200 chars: {(resp.text or '')[:200]!r}")
        return stub

    # Check only what is actually needed. Demanding every fixture key rejects
    # good responses over optional fields the model reasonably omitted.
    expected = {k: v for k, v in stub.items() if require is None or k in require}
    if not _shape_ok(got, expected):
        ctx.note(f"WRONG SHAPE, using stub instead — wanted keys {sorted(expected)}, "
                 f"got {sorted(got) if isinstance(got, dict) else type(got).__name__}")
        return stub
    return got


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


BRIEF_SCHEMA = {
    "type": "object",
    "required": ["intent", "format", "tone", "duration_s"],
    "properties": {
        "intent":     {"type": "string", "enum": ["explainer", "comedy", "commentary"]},
        "format":     {"type": "string", "enum": ["monologue", "debate"]},
        "tone":       {"type": "string"},
        "duration_s": {"type": "number"},
        "confidence": {"type": "object", "properties": {
            "intent": {"type": "number"}, "format": {"type": "number"},
            "tone": {"type": "number"}}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "params": {
            "type": "object",
            "required": ["turn_len_s", "beat_tolerance_ms", "max_hold_s", "bg_budget"],
            "properties": {
                "turn_len_s":        {"type": "array", "items": {"type": "number"}},
                "beat_tolerance_ms": {"type": "number"},
                "max_hold_s":        {"type": "number"},
                "bg_budget":         {"type": "number"},
            },
        },
    },
}

OUTLINE_SCHEMA = {
    "type": "object",
    "required": ["beats", "turn_at_s"],
    "properties": {
        "beats": {"type": "array", "items": {
            "type": "object",
            "required": ["id", "role", "needs_fact"],
            "properties": {"id": {"type": "string"}, "role": {"type": "string"},
                           "needs_fact": {"type": "boolean"},
                           "at_s": {"type": "number"}}}},
        "turn_at_s": {"type": "number"},
    },
}

SCRIPT_SCHEMA = {
    "type": "object",
    "required": ["lines"],
    "properties": {
        "lines": {"type": "array", "items": {
            "type": "object",
            "required": ["speaker", "text", "emotion"],
            "properties": {"speaker": {"type": "string"}, "text": {"type": "string"},
                           "emotion": {"type": "string"},
                           "claim_refs": {"type": "array", "items": {"type": "string"}}}}},
    },
}


def brief(state: Production, ctx: Ctx) -> dict:
    topic = state["topic"]
    probe = ctx.tool("topic_probe", topic=topic)

    out = _call(
        ctx,
        f"Topic: {topic}\n\nProbe: {json.dumps(probe)}\n\n"
        f"Return JSON with exactly these keys: {sorted(BRIEF_STUB)}",
        system=BRIEF_SYSTEM,
        stub=BRIEF_STUB,
        schema=BRIEF_SCHEMA,
        require={"intent", "format", "tone", "duration_s"},
        fallback_cost=0.004,
    )

    # The catalogs are closed, so the schema check runs here — records, never blocks.
    invalid = []
    if out.get("intent") not in ("explainer", "comedy", "commentary"):
        invalid.append(f"intent {out.get('intent')!r} not in catalog")
    if out.get("format") not in ("monologue", "debate"):
        invalid.append(f"format {out.get('format')!r} not in catalog")

    # The model proposed 150s on one run. Short-form has a hard ceiling, and a
    # duration outside it is a brief error worth recording rather than obeying.
    requested = float(out.get("duration_s") or 15.0)
    out["duration_s"] = max(10.0, min(requested, 60.0))
    if out["duration_s"] != requested:
        invalid.append(f"duration_s {requested}s clamped to {out['duration_s']}s")

    # params are ours: QC reads thresholds from here, so defaults always apply.
    out["params"] = {**BRIEF_STUB["params"], **(out.get("params") or {})}
    out.setdefault("assumptions", [])

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
                stub=out, schema=OUTLINE_SCHEMA, fallback_cost=0.008)
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
    beat_desc = "\n".join(
        f"  {b['id']} ({b.get('role', 'beat')}): what is worth verifying here?"
        for b in flagged
    )
    got = _call(
        ctx,
        f"Topic: {state['topic']}\n\n"
        f"Find one checkable, sourced fact for each of these story beats:\n{beat_desc}\n\n"
        f"Search for real sources. Respond with JSON only, no commentary, in exactly "
        f"this shape:\n"
        f'{{"claims": [{{"id": "c_01", "beat": "<beat id>", "text": "<the claim>", '
        f'"source": "<url>"}}]}}',
        stub={"claims": claims}, fallback_cost=0.017, grounded=True,
    )
    claims = got.get("claims", claims)
    return {"claims": claims, "log": [f"research: {len(claims)} claims for {len(flagged)} beats"]}


SCRIPT_SYSTEM = """You write dialogue for short-form video. You are given a
topic, an intent, a cast, a beat grid and the sourced claims.

Write ONE line per beat. Each line must be speakable in the seconds allotted —
roughly 15 characters per second, so a 2.5s line is about 35 characters. Short
is better than clever.

For a debate, alternate speakers and give them opposing positions. For a
monologue, one voice throughout.

Return JSON: {"lines": [{"speaker": "<character_id>", "text": str,
"emotion": "dry|wry|flat|curious|excited|earnest|amused|surprised|emphatic",
"claim_refs": [str]}]}"""


def _stub_lines(state: Production) -> dict:
    """Topic-derived placeholder dialogue, so stubbed runs still differ by input."""
    topic = state["topic"].rstrip("?.").strip()
    cast = state["brief"]["cast"]
    claims = [c["id"] for c in state.get("claims", [])]

    beats = [
        (f"Everyone has an opinion about {topic}.", "wry", []),
        ("Most of them are guesses.", "dry", claims[:1]),
        (f"So what does {topic} actually come down to?", "curious", []),
        ("Evidence, mostly. And there is some.", "earnest", claims[1:2]),
        ("Which is not the same as a consensus.", "dry", []),
        ("No. But it is a start.", "amused", []),
    ]
    return {"lines": [
        {"speaker": cast[i % len(cast)]["character_id"],
         "text": text, "emotion": emotion}
        for i, (text, emotion, refs) in enumerate(beats)
    ]}


def script(state: Production, ctx: Ctx) -> dict:
    """Dialogue from the model; shot placement from the beat grid.

    Splitting it this way is what makes `beat_alignment` meaningful — cuts land
    on measured beats by construction, so a failure is a genuine planning error
    rather than a model being careless with numbers.
    """
    brief, track = state["brief"], state["track"]
    n_beats = len([b for b in track["beats"] if b < brief["duration_s"]])

    result = _call(
        ctx,
        f"Topic: {state['topic']}\nIntent: {brief['intent']} · {brief['format']} · "
        f"{brief['tone']}\nCast: {[c['character_id'] for c in brief['cast']]}\n"
        f"Duration: {brief['duration_s']}s over {n_beats} beats at "
        f"{track['bpm']} BPM, drop at {track['drop_s']}s\n"
        f"Claims available: {[c['id'] for c in state.get('claims', [])]}",
        system=SCRIPT_SYSTEM,
        stub=_stub_lines(state),
        schema=SCRIPT_SCHEMA,
        require={"lines"},
        fallback_cost=0.023,
    )

    lines = result.get("lines") or _stub_lines(state)["lines"]
    built = edl_builder.build(brief=brief, track=track, lines=lines)

    return {"edl": built, "script": {"lines": lines},
            "log": [f"script: {len(lines)} lines -> {len(built['shots'])} shots "
                    f"on the grid, {len({l['layers'][0]['scene_id'] for l in built['shots']})} scenes"]}


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
    # No model call: plate generation is keyed on scene_id and decides nothing.

    return {"assets": {"sprites": roster, "plates": plates},
            "log": [f"casting: {roster['n_sprites']} sprites (lookup, $0), "
                    f"{len(plates)} plates generated"]}


def voice(state: Production, ctx: Ctx) -> dict:
    spans = ctx.tool("measure_vo", path=str(ASSETS / "fixtures" / "vo.wav"))
    # No model call. Measuring durations is arithmetic — the absorption ladder
    # handles drift, and Voice only wakes a model on escalation.
    return {"audio": spans, "log": [f"voice: {spans['n_spans']} spans measured"]}


def envelope(state: Production, ctx: Ctx) -> dict:
    env = {"duck_db": -9.0, "full_at_drop": True}
    return {"envelope": env, "log": ["envelope: -9dB under speech, full across the drop"]}


def compositor(state: Production, ctx: Ctx) -> dict:
    """Not a stub. The proven spike renderer, deterministic, $0.

    Also applies the envelope Scoring declared — the Compositor decides nothing,
    it executes. That is what leaves `music_ducking` a real claim to verify.
    """
    mix = ctx.tool("mix_audio",
                   duck_db=state["envelope"]["duck_db"],
                   spans=state["audio"]["spans"],
                   target_lufs=state["brief"]["params"].get("target_lufs", -14.0))
    frames = ctx.tool("render", edl=state["edl"], run_id=state["run_id"], audio=mix["path"])
    return {"render": {**frames, "mix": mix},
            "log": [f"composite: {frames['n_frames']} frames · {len(frames['scenes'])} scenes · "
                    f"{mix['lufs_after']} LUFS · {frames['size_kb']} KB "
                    f"-> {pathlib.Path(frames['path']).name}"]}


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
        rule, owner = v["rule_id"], rules.OWNER[v["rule_id"]]
        shot_id = v.get("evidence", {}).get("shot")

        if rule == "beat_alignment":
            edl = edl_builder.retime(edl, beats, shot_id)
            ctx.note(f"{shot_id}: snapped to nearest beat (owner: {owner})")
            fixed.append(f"{shot_id}:retime")

        elif rule == "reading_speed":
            max_cps = v["evidence"]["threshold_cps"]
            new = edl_builder.shorten_caption(edl, shot_id, max_cps)
            if new:
                ctx.note(f"{shot_id}: caption shortened to {len(new)} chars (owner: {owner})")
                fixed.append(f"{shot_id}:shorten")

        else:
            ctx.note(f"{rule} -> {owner}: no repair implemented yet")

    # Retiming and shortening already happened above, in code. A model call here
    # would be billed for confirming arithmetic it did not perform.

    unowned = sorted({v["rule_id"] for v in state["violations"]}
                     - {"beat_alignment", "reading_speed"})
    if unowned and not fixed:
        ctx.note(f"no repair implemented for {unowned} — escalating rather than looping")

    return {"edl": edl, "repair_round": state["repair_round"] + 1,
            "repaired": bool(fixed),
            "log": [f"repair round {state['repair_round'] + 1}: "
                    + (f"retimed {fixed}" if fixed
                       else f"nothing to do — {unowned} has no repair path")]}

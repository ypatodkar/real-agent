"""The closed tool registry.

Every tool a stage can reach is listed here. No agent gets open-ended access —
if a capability is not in this dict, no stage can invoke it. That is deliberate
and is the opposite of what most frameworks optimise for.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import subprocess

import numpy as np
import soundfile as sf

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
OUT = ROOT / "out"


def topic_probe(topic: str) -> dict:
    """Stub. One unconditional probe so confidence is scored against evidence."""
    return {"topic": topic, "contested": True, "has_visual_referent": False,
            "_stub": True}


def track_select(mood: str, duration_s: float) -> dict:
    """A lookup into the pre-scored library. No model, no cost, grid is ground truth.

    Tempo suitability belongs here: reject tracks whose beat period is too coarse
    to place the planned number of cuts. That is what keeps beat_alignment from
    ever needing a track reselect as a *repair*.
    """
    grid = json.loads((ASSETS / "fixtures" / "grid.json").read_text())
    return {"track_id": "fixture_96", "bpm": grid["bpm"],
            "beats": grid["beats"], "drop_s": grid["drop_s"],
            "beat_period_s": grid["beat_period_s"]}


def roster_lookup(character_ids: list[str]) -> dict:
    """Sprites are a fixture established at setup — resolved here, never generated."""
    sprites = {}
    for cid in character_ids:
        d = ASSETS / "fixtures" / "sprites" / cid
        sprites[cid] = sorted(p.stem for p in d.glob("*.png"))
    return {"characters": list(sprites), "poses": sprites,
            "n_sprites": sum(len(v) for v in sprites.values()), "cost": 0.0}


def generate_plates(scene_ids: list[str]) -> list[str]:
    """The only images a run generates. Deduped within the reel on scene_id."""
    return list(dict.fromkeys(scene_ids))


def measure_vo(path: str) -> dict:
    """Real measurement — durations replace estimates, which the ladder absorbs."""
    audio, sr = sf.read(path, dtype="float64", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    grid = json.loads((ASSETS / "fixtures" / "grid.json").read_text())
    spans = grid["talk_spans"]
    return {"n_spans": len(spans), "spans": spans,
            "total_s": round(len(audio) / sr, 3)}


def mix_audio(duck_db: float, spans: list[dict], target_lufs: float) -> dict:
    """Produce the final audio mix: music ducked under speech, then normalised.

    The Compositor decides nothing here — it applies the envelope Scoring
    declared. That separation is what lets `music_ducking` verify a real claim
    rather than confirm its own arithmetic.
    """
    music, sr = sf.read(ASSETS / "fixtures" / "music.wav", dtype="float64", always_2d=False)
    vo, _ = sf.read(ASSETS / "fixtures" / "vo.wav", dtype="float64", always_2d=False)
    if music.ndim > 1:
        music = music.mean(axis=1)
    if vo.ndim > 1:
        vo = vo.mean(axis=1)

    n = min(len(music), len(vo))
    music, vo = music[:n], vo[:n]

    # gain envelope: full level, dropping to duck_db across each talk span
    gain = np.ones(n)
    duck = 10 ** (duck_db / 20.0)
    ramp = int(0.08 * sr)
    for s in spans:
        a, b = int(s["start_s"] * sr), min(int(s["end_s"] * sr), n)
        if b <= a:
            continue
        gain[a:b] = duck
        gain[max(0, a - ramp):a] = np.linspace(1.0, duck, min(ramp, a))
        gain[b:b + ramp] = np.linspace(duck, 1.0, len(gain[b:b + ramp]))

    ducked = music * gain
    mix = ducked + vo

    OUT.mkdir(exist_ok=True)
    path, stem = OUT / "mix.wav", OUT / "music_ducked.wav"
    sf.write(path, np.clip(mix, -1.0, 1.0), sr)
    # the music stem as actually rendered — what music_ducking measures. Summing
    # music and voice makes the envelope unmeasurable, since speech dominates
    # exactly where the music was pulled down.
    sf.write(stem, np.clip(ducked, -1.0, 1.0), sr)

    # normalise to the loudness target, measured with ffmpeg's EBU R128 scanner
    measured = _integrated_lufs(path)
    if measured is not None and math.isfinite(measured):
        mix = mix * (10 ** ((target_lufs - measured) / 20.0))
        sf.write(path, np.clip(mix, -1.0, 1.0), sr)

    return {"path": str(path), "music_stem": str(stem), "duck_db": duck_db,
            "lufs_before": measured, "lufs_after": _integrated_lufs(path)}


def _integrated_lufs(path: pathlib.Path) -> float | None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    found = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", proc.stderr)
    return float(found[-1]) if found else None


def render(edl: dict) -> dict:
    """The proven compositor. Deterministic, ffmpeg on CPU, $0."""
    import sys
    sys.path.insert(0, str(ROOT / "spike"))
    import render as spike

    sprites = spike.load_sprites()
    plates = {sid: spike.make_plate(sid) for sid in spike.SCENES}
    fnt = spike.font(40)
    fps = edl["meta"]["fps"]
    n = int(edl["meta"]["duration_s"] * fps)
    env = spike.vo_envelope(ASSETS / "fixtures" / "vo.wav", fps, n)

    digest = hashlib.sha256()
    for i in range(n):
        digest.update(spike.render_frame(edl, sprites, plates, fnt, i, fps, env).tobytes())

    return {"n_frames": n, "fps": fps, "frame_hash": digest.hexdigest(), "cost": 0.0}


def build_registry() -> dict:
    return {
        "topic_probe": topic_probe,
        "track_select": track_select,
        "roster_lookup": roster_lookup,
        "generate_plates": generate_plates,
        "measure_vo": measure_vo,
        "mix_audio": mix_audio,
        "render": render,
    }

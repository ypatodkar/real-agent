"""Compositor spike — EDL in, mp4 out, no model anywhere.

Proves the four things nothing else in the project has verified:

  1. 30fps sprite compositing over a background
  2. the 5Hz mouth cycle, gated by real VO amplitude
  3. hard cuts landing on measured beats
  4. determinism — same EDL, same frames, byte for byte

Also runs beat_alignment against the grid, which is the first QC rule to exist.

    python3 spike/render.py                 # render + report
    python3 spike/render.py --check-only    # QC and determinism, no encode
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

import numpy as np
import soundfile as sf
import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
OUT_DIR = ROOT / "out"

W, H = 720, 1280          # 9:16. Resolution is free — see ARCHITECTURE.md §2.2
MOUTH_HOLD_FRAMES = 3     # 3 frames on, 3 off, at 30fps = 5Hz
VO_GATE = 0.055           # RMS above which the mouth is considered open
BOB_PX = 7.0              # idle sine bob amplitude

SCENES = {                # stand-ins for Imagen plates, keyed on scene_id
    "sc_studio": ((38, 46, 62), (74, 92, 116), "studio"),
    "sc_data":   ((30, 58, 54), (58, 110, 96), "data"),
}


# ----------------------------------------------------------------- loading


def load_edl(path: pathlib.Path) -> dict:
    edl = yaml.safe_load(path.read_text())
    edl["shots"].sort(key=lambda s: s["in_s"])
    return edl


def load_sprites() -> dict[tuple[str, str], Image.Image]:
    sprites = {}
    for d in sorted((ASSETS / "fixtures" / "sprites").iterdir()):
        if d.is_dir():
            for p in sorted(d.glob("*.png")):
                sprites[(d.name, p.stem)] = Image.open(p).convert("RGBA")
    return sprites


def vo_envelope(path: pathlib.Path, fps: int, n_frames: int) -> np.ndarray:
    """Per-frame RMS of the VO. This is what gates the mouth cycle."""
    audio, sr = sf.read(path, dtype="float64", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    env = np.zeros(n_frames)
    win = int(sr / fps)
    for i in range(n_frames):
        seg = audio[i * win : (i + 1) * win]
        if seg.size:
            env[i] = float(np.sqrt(np.mean(seg**2)))
    return env


def make_plate(scene_id: str) -> Image.Image:
    """Procedural background. Real ones come from Imagen; the interface is the same."""
    if scene_id not in SCENES:                       # stable pick for unseen ids
        key = list(SCENES)[sum(map(ord, scene_id)) % len(SCENES)]
        top, bottom, label = SCENES[key]
    else:
        top, bottom, label = SCENES[scene_id]
    img = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(img)

    for y in range(H):  # vertical gradient
        f = y / H
        d.line(
            [(0, y), (W, y)],
            fill=tuple(int(top[c] + (bottom[c] - top[c]) * f) for c in range(3)),
        )

    if label == "studio":
        d.ellipse([-160, 300, W + 160, H + 260], fill=(52, 64, 86))
        for x in range(0, W, 90):
            d.line([(x, 300), (x, H)], fill=(60, 74, 98), width=2)
    else:
        for i, x in enumerate(range(70, W - 40, 96)):
            bar = 120 + (i * 97) % 320
            d.rounded_rectangle([x, 780 - bar, x + 54, 800], 10, fill=(84, 150, 128))
        d.line([(40, 800), (W - 40, 800)], fill=(120, 190, 165), width=4)

    return img


def font(size: int) -> ImageFont.FreeTypeFont:
    for p in ("/System/Library/Fonts/Helvetica.ttc",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if pathlib.Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


# ----------------------------------------------------------------- QC


def check_beat_alignment(edl: dict, beats: list[float]) -> list[dict]:
    """The first QC rule. Every cut must land within tolerance of a real beat."""
    tol_ms = edl["params"]["beat_tolerance_ms"]
    violations = []

    for shot in edl["shots"]:
        t = shot["in_s"]
        nearest = min(beats, key=lambda b: abs(b - t))
        offset_ms = round((t - nearest) * 1000, 1)

        if abs(offset_ms) > tol_ms:
            violations.append({
                "rule_id": "beat_alignment",
                "severity": "fail",
                "owner": "showrunner",
                "evidence": {
                    "shot": shot["id"],
                    "in_s": t,
                    "nearest_beat_s": nearest,
                    "offset_ms": offset_ms,
                    "threshold_ms": tol_ms,
                },
                "suggested_action": "retime",
            })
    return violations


def check_duration(edl: dict) -> list[dict]:
    target = edl["meta"]["duration_s"]
    actual = max(s["out_s"] for s in edl["shots"])
    if abs(actual - target) > 0.5:
        return [{
            "rule_id": "duration_adherence",
            "severity": "fail",
            "owner": "showrunner",
            "evidence": {"planned_s": actual, "target_s": target, "tolerance_s": 0.5},
            "suggested_action": "retime",
        }]
    return []


# ----------------------------------------------------------------- render


def shot_at(shots: list[dict], t: float) -> dict:
    active = shots[0]
    for s in shots:
        if s["in_s"] <= t:
            active = s
        else:
            break
    return active


def pose_for(layer: dict, shot: dict, frame: int, speaking: bool) -> str:
    """Mouth cycle only for the speaker, and only while VO amplitude is above gate."""
    if layer["id"] == shot.get("speaker") and speaking:
        phase = (frame // MOUTH_HOLD_FRAMES) % 2
        return "talking_open" if phase else "talking"
    return layer["pose"]


def render_frame(
    edl: dict,
    sprites: dict,
    plates: dict,
    fnt: ImageFont.FreeTypeFont,
    frame: int,
    fps: int,
    env: np.ndarray,
) -> Image.Image:
    t = frame / fps
    shot = shot_at(edl["shots"], t)
    speaking = env[frame] > VO_GATE
    xs = {c["character_id"]: c["x"] for c in edl["cast"]}

    bg_layer = next(l for l in shot["layers"] if l["type"] == "bg")
    canvas = plates[bg_layer["scene_id"]].copy()

    for layer in shot["layers"]:
        if layer["type"] != "character":
            continue

        pose = pose_for(layer, shot, frame, speaking)
        sprite = sprites[(layer["id"], pose)]

        is_speaker = layer["id"] == shot.get("speaker")
        scale = 0.92 if is_speaker else 0.78
        sw, sh = int(sprite.width * scale), int(sprite.height * scale)
        sized = sprite.resize((sw, sh), Image.LANCZOS)

        bob = 0.0 if (is_speaker and speaking) else BOB_PX * np.sin(2 * np.pi * 0.5 * t)
        x = int(xs[layer["id"]] * W - sw / 2)
        y = int(H * 0.60 - sh / 2 + bob)
        canvas.paste(sized, (x, y), sized)

    caption = next((l for l in shot["layers"] if l["type"] == "caption"), None)
    if caption:
        draw_caption(canvas, fnt, caption["text"])

    return canvas


def draw_caption(img: Image.Image, fnt: ImageFont.FreeTypeFont, text: str) -> None:
    d = ImageDraw.Draw(img)
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if d.textlength(trial, font=fnt) > W - 110 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    lines.append(cur)

    y = int(H * 0.845) - (len(lines) - 1) * 27
    for line in lines:
        tw = d.textlength(line, font=fnt)
        x = (W - tw) / 2
        for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
            d.text((x + dx, y + dy), line, font=fnt, fill=(12, 14, 18))
        d.text((x, y), line, font=fnt, fill=(255, 255, 255))
        y += 54


# ----------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", default=str(pathlib.Path(__file__).parent / "demo.edl.yaml"))
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    edl = load_edl(pathlib.Path(args.edl))
    grid = json.loads((ASSETS / edl["meta"]["grid"]).read_text())
    beats = grid["beats"]
    fps = edl["meta"]["fps"]
    n_frames = int(edl["meta"]["duration_s"] * fps)

    print(f"EDL          {pathlib.Path(args.edl).name}")
    print(f"config       {edl['meta']['intent']} · {edl['meta']['format']} · {edl['meta']['tone']}")
    print(f"grid         {grid['bpm']} BPM · {grid['beat_period_s']}s/beat · drop {grid['drop_s']}s")
    print(f"render       {W}x{H} @ {fps}fps · {n_frames} frames · {len(edl['shots'])} shots\n")

    # ---- QC before pixels. This is the cheap gate.
    violations = check_beat_alignment(edl, beats) + check_duration(edl)
    applicable = ["beat_alignment", "duration_adherence"]  # implemented so far
    passed = len(applicable) - len({v["rule_id"] for v in violations})

    print(f"QC (pre-render)   {passed}/{len(applicable)} applicable rules pass")
    for v in violations:
        ev = v["evidence"]
        print(f"  FAIL {v['rule_id']:20} {ev}")
        print(f"       -> owner: {v['owner']}, action: {v['suggested_action']}")
    print()

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "qc_report.json").write_text(json.dumps(violations, indent=2) + "\n")

    # ---- frames
    sprites = load_sprites()
    plates = {sid: make_plate(sid) for sid in SCENES}
    fnt = font(40)
    env = vo_envelope(ASSETS / "fixtures" / "vo.wav", fps, n_frames)

    speaking_frames = int((env > VO_GATE).sum())
    print(f"VO gate      {speaking_frames}/{n_frames} frames above {VO_GATE} "
          f"({speaking_frames / n_frames:.0%} mouth-active)")

    digest = hashlib.sha256()
    frames: list[bytes] = []
    for i in range(n_frames):
        raw = render_frame(edl, sprites, plates, fnt, i, fps, env).tobytes()
        digest.update(raw)
        frames.append(raw)

    print(f"frame hash   {digest.hexdigest()[:32]}")

    # ---- determinism: render a second time, compare
    d2 = hashlib.sha256()
    for i in range(n_frames):
        d2.update(render_frame(edl, sprites, plates, fnt, i, fps, env).tobytes())

    identical = d2.hexdigest() == digest.hexdigest()
    print(f"determinism  {'PASS — same EDL, same bytes' if identical else 'FAIL — nondeterministic'}\n")
    if not identical:
        return 1

    if args.check_only:
        return 0

    # ---- encode
    mp4 = OUT_DIR / "spike.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-i", str(ASSETS / "fixtures" / "music.wav"),
        "-i", str(ASSETS / "fixtures" / "vo.wav"),
        "-filter_complex", "[1:a]volume=0.30[m];[2:a]volume=1.0[v];[m][v]amix=inputs=2:duration=first[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", str(mp4),
    ]
    proc = subprocess.run(cmd, input=b"".join(frames), capture_output=True)
    if proc.returncode:
        print(proc.stderr.decode()[:1200], file=sys.stderr)
        return proc.returncode

    print(f"wrote        {mp4}  ({mp4.stat().st_size / 1024:.0f} KB)")
    print(f"             {OUT_DIR / 'qc_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Building the edit decision list.

The Showrunner's job splits cleanly in two. Writing dialogue is judgement and
belongs to a model. Placing that dialogue on a beat grid is arithmetic, and
doing it in code means every cut lands on a measured beat by construction
rather than because a model was asked nicely.

So the model returns lines; this module turns them into shots.

Pose is derived here too, not chosen. The line already carries
`delivery.emotion`, and pose follows from it by lookup — a decision that shows
up in every single frame belongs with the other deterministic ones.
"""

from __future__ import annotations

from typing import Any

# Emotion -> what the LISTENER does while it is said. The speaker is always
# `talking`, because the compositor alternates that with `talking_open` on the
# audio envelope; their pose is only visible between utterances.
LISTENER_POSE = {
    "dry":       "skeptical",
    "wry":       "skeptical",
    "skeptical": "skeptical",
    "flat":      "listening",
    "neutral":   "listening",
    "curious":   "listening",
    "excited":   "nodding",
    "earnest":   "nodding",
    "warm":      "nodding",
    "amused":    "laughing",
    "surprised": "reacting",
    "emphatic":  "reacting",
}

# What a character does in a shot where nobody is speaking.
IDLE_POSE = {"amused": "laughing", "surprised": "reacting", "excited": "gesturing"}


def listener_pose(emotion: str) -> str:
    return LISTENER_POSE.get(emotion, "listening")


def _snap(t: float, beats: list[float]) -> float:
    return min(beats, key=lambda b: abs(b - t))


def build(
    *,
    brief: dict,
    track: dict,
    lines: list[dict],
    fps: int = 30,
) -> dict:
    """Place dialogue on the beat grid and emit a valid EDL.

    Every `in_s` is a beat timestamp, so `beat_alignment` passes by construction.
    When it fails, the planner genuinely misplaced something.
    """
    beats: list[float] = track["beats"]
    duration = float(brief["duration_s"])
    params = brief.get("params", {})
    cast = brief["cast"]
    drop = track.get("drop_s")
    bg_budget = max(1, int(params.get("bg_budget", 2)))

    usable = [b for b in beats if b < duration]
    if not usable or not lines:
        return {"meta": _meta(brief, track, duration, fps), "cast": cast, "shots": []}

    # Divide the grid evenly across lines, on beat boundaries.
    per_shot = max(1, len(usable) // len(lines))
    scenes = [f"sc_{i:02d}" for i in range(bg_budget)]

    shots = []
    for idx, line in enumerate(lines):
        start_i = idx * per_shot
        if start_i >= len(usable):
            break
        in_s = usable[start_i]
        end_i = (idx + 1) * per_shot
        out_s = usable[end_i] if end_i < len(usable) and idx < len(lines) - 1 else duration

        # Scene changes on the drop, so the background turns when the story does.
        scene = scenes[0] if drop is None or in_s < drop else scenes[min(1, len(scenes) - 1)]

        emotion = line.get("emotion", "neutral")
        speaker = line.get("speaker") or (cast[0]["character_id"] if cast else None)

        layers: list[dict[str, Any]] = [{"type": "bg", "scene_id": scene}]
        for member in cast:
            cid = member["character_id"]
            if cid == speaker:
                pose = "talking"
            elif len(cast) > 1:
                pose = listener_pose(emotion)
            else:
                pose = IDLE_POSE.get(emotion, "idle")
            layers.append({"type": "character", "id": cid, "pose": pose})

        layers.append({"type": "caption", "text": line["text"],
                       "span": [round(in_s, 3), round(out_s, 3)]})

        shots.append({
            "id": f"sh_{idx + 1:02d}",
            "in_s": round(in_s, 3),
            "out_s": round(out_s, 3),
            "speaker": speaker,
            "on_beat": True,
            "beat_offset_ms": 0,
            "delivery": {"emotion": emotion},
            "claim_refs": line.get("claim_refs", []),
            "layers": layers,
        })

    return {"meta": _meta(brief, track, duration, fps), "cast": cast, "shots": shots}


def _meta(brief: dict, track: dict, duration: float, fps: int) -> dict:
    return {
        "intent": brief.get("intent"),
        "format": brief.get("format"),
        "tone": brief.get("tone"),
        "fps": fps,
        "duration_s": duration,
        "track": track.get("track_id"),
        "drop_s": track.get("drop_s"),
    }


def retime(edl: dict, beats: list[float], shot_id: str) -> dict:
    """Snap one shot onto the nearest beat. Everything else is frozen."""
    for shot in edl["shots"]:
        if shot["id"] == shot_id:
            shot["in_s"] = round(_snap(shot["in_s"], beats), 3)
            shot["beat_offset_ms"] = 0
            for layer in shot["layers"]:
                if layer["type"] == "caption":
                    layer["span"] = [shot["in_s"], shot["out_s"]]
    return edl


def shorten_caption(edl: dict, shot_id: str, max_cps: float) -> str | None:
    """Trim one caption to a readable length. Returns the new text, or None."""
    for shot in edl["shots"]:
        if shot["id"] != shot_id:
            continue
        seconds = shot["out_s"] - shot["in_s"]
        budget = int(seconds * max_cps)
        for layer in shot["layers"]:
            if layer["type"] != "caption" or len(layer["text"]) <= budget:
                continue
            words, kept = layer["text"].split(), []
            for word in words:
                if len(" ".join(kept + [word])) > budget - 1:
                    break
                kept.append(word)
            layer["text"] = (" ".join(kept) or words[0][:budget]).rstrip(",;:") + "."
            return layer["text"]
    return None

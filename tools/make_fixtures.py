"""Synthesize audio fixtures with a beat grid that is exact by construction.

No detection anywhere. The grid is written, not found, so any beat_alignment
error the compositor spike reports is the compositor's, never the source's.

Emits:
    assets/fixtures/music.wav   click track with an accented downbeat and a drop
    assets/fixtures/vo.wav      speech-shaped amplitude, for the mouth gate
    assets/fixtures/grid.json   {bpm, beats[], drop_s, talk_spans[]}
"""

import json
import pathlib

import numpy as np
import soundfile as sf

SR = 44100
BPM = 96.0
BEATS_PER_BAR = 4
DURATION_S = 16.0
DROP_BAR = 3  # 1-indexed; bar 3 starts at beat 8, i.e. 5.0s in

OUT = pathlib.Path(__file__).resolve().parent.parent / "assets" / "fixtures"


def beat_grid(bpm: float, duration_s: float) -> list[float]:
    """Every beat timestamp, to the sample. This is ground truth."""
    period = 60.0 / bpm
    n = int(duration_s / period)
    return [round(i * period, 6) for i in range(n)]


def click_track(beats: list[float], drop_s: float) -> np.ndarray:
    """Sine blips on every beat, accented downbeats, a bass pulse after the drop."""
    buf = np.zeros(int(DURATION_S * SR), dtype=np.float64)

    for i, t in enumerate(beats):
        downbeat = i % BEATS_PER_BAR == 0
        freq = 1600.0 if downbeat else 1000.0
        amp = 0.55 if downbeat else 0.28
        if t >= drop_s:
            amp *= 1.5

        dur = 0.035
        n = int(dur * SR)
        env = np.exp(-np.linspace(0, 12, n))  # sharp percussive decay
        blip = amp * env * np.sin(2 * np.pi * freq * np.arange(n) / SR)

        start = int(round(t * SR))
        buf[start : start + n] += blip

    # low pulse from the drop onward, so there is something to duck against
    period = 60.0 / BPM
    for t in beats:
        if t < drop_s:
            continue
        n = int(0.18 * SR)
        env = np.exp(-np.linspace(0, 5, n))
        buf[int(round(t * SR)) : int(round(t * SR)) + n] += (
            0.34 * env * np.sin(2 * np.pi * 60.0 * np.arange(n) / SR)
        )

    return np.clip(buf, -1.0, 1.0)


def stub_vo(talk_spans: list[tuple[float, float, int]]) -> np.ndarray:
    """Speech-shaped noise: syllable-rate amplitude modulation inside each span.

    Not intelligible and not meant to be. The compositor gates the mouth cycle on
    this envelope, so what matters is that it rises and falls like speech does.
    """
    rng = np.random.default_rng(7)  # fixed seed — fixtures must be reproducible
    buf = np.zeros(int(DURATION_S * SR), dtype=np.float64)

    for start_s, end_s, _speaker in talk_spans:
        n = int((end_s - start_s) * SR)
        t = np.arange(n) / SR

        syllable = 0.5 + 0.5 * np.sin(2 * np.pi * 4.2 * t - np.pi / 2)
        phrase = 0.65 + 0.35 * np.sin(2 * np.pi * 0.4 * t)
        carrier = rng.normal(0, 1, n)

        # crude formant shaping so it reads as voice rather than hiss
        b = np.array([0.18, 0.34, 0.28, 0.14, 0.06])
        carrier = np.convolve(carrier, b, mode="same")

        seg = 0.42 * syllable * phrase * carrier
        fade = int(0.02 * SR)
        seg[:fade] *= np.linspace(0, 1, fade)
        seg[-fade:] *= np.linspace(1, 0, fade)

        buf[int(start_s * SR) : int(start_s * SR) + n] += seg

    return np.clip(buf, -1.0, 1.0)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    beats = beat_grid(BPM, DURATION_S)
    drop_s = beats[(DROP_BAR - 1) * BEATS_PER_BAR]

    # Talk spans sit on beat boundaries — the Showrunner would place them there.
    talk_spans = [
        (beats[0], beats[4], 0),
        (beats[4], beats[8], 1),
        (beats[8], beats[12], 0),  # first line after the drop
        (beats[12], beats[16], 1),
        (beats[16], beats[20], 0),
    ]

    sf.write(OUT / "music.wav", click_track(beats, drop_s), SR)
    sf.write(OUT / "vo.wav", stub_vo(talk_spans), SR)

    (OUT / "grid.json").write_text(
        json.dumps(
            {
                "source": "synthetic — grid is exact by construction, not detected",
                "bpm": BPM,
                "beats_per_bar": BEATS_PER_BAR,
                "duration_s": DURATION_S,
                "beat_period_s": round(60.0 / BPM, 6),
                "beats": beats,
                "drop_s": drop_s,
                "talk_spans": [
                    {"start_s": a, "end_s": b, "speaker_idx": s}
                    for a, b, s in talk_spans
                ],
            },
            indent=2,
        )
        + "\n"
    )

    print(f"bpm            {BPM}")
    print(f"beat period    {60.0 / BPM:.6f}s")
    print(f"beats          {len(beats)} over {DURATION_S}s")
    print(f"drop           {drop_s:.6f}s (bar {DROP_BAR})")
    print(f"talk spans     {len(talk_spans)}")
    print(f"written        {OUT}")


if __name__ == "__main__":
    main()

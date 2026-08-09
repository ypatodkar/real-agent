# Compositor spike

Proves the bottom of the pipeline with no model, no API key and no network.

```bash
python3 tools/make_fixtures.py     # click track + stub VO + exact beat grid
python3 tools/make_sprites.py      # 2 characters x 9 poses
python3 spike/render.py            # EDL -> out/spike.mp4 + out/qc_report.json
python3 spike/render.py --check-only   # QC and determinism, skip the encode
```

Everything under `assets/fixtures/` and `out/` is generated and gitignored.
Regenerate rather than commit.

## What it establishes

| | |
|---|---|
| **30fps sprite compositing** | 450 frames over 6 shots, two characters, procedural plates |
| **5Hz mouth cycle** | 3 frames closed / 3 open, gated on real VO amplitude at 0.055 RMS |
| **Beat-accurate cuts** | Shot boundaries measured against the grid |
| **Determinism** | Frames hashed across two renders; identical or the run fails |
| **`beat_alignment`** | First QC rule, implemented and firing |

## The grid is written, not detected

`make_fixtures.py` synthesizes audio *from* a beat grid rather than finding a grid
in audio. Every beat sits on a known sample, so any alignment error the spike
reports belongs to the compositor and never to the source. That property is why
the spike can run before the real music library exists.

## The planted violation

`sh_04` starts at **7.580s** against a beat at **7.500s** — 80ms late, over the
60ms threshold. It is wrong on purpose, so the QC gate has something true to
catch:

```
QC (pre-render)   1/2 applicable rules pass
  FAIL beat_alignment  {'shot': 'sh_04', 'offset_ms': 80.0, 'threshold_ms': 60}
       -> owner: showrunner, action: retime
```

Move `in_s` to `7.500` and it passes. That is the whole repair loop in miniature:
a typed violation, an owner, a suggested action, a scoped fix.

## Known stubs

- Sprites are PIL primitives standing in for the Imagen roster.
- Backgrounds are procedural gradients standing in for generated plates.
- The VO is amplitude-modulated noise, not speech. Only its envelope matters.
- Frames are built in PIL and piped to ffmpeg for encoding, rather than
  composited by ffmpeg filters. Determinism holds either way; revisit if
  per-frame Python becomes the bottleneck at longer durations.

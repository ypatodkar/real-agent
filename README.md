# Second Unit

*Working title — the crew that shoots everything the main unit doesn't.*

**An agent harness that produces short-form video and proves it meets spec.**

Give it a topic. It decides the format, researches the subject, writes a multi-character script, casts and voices the speakers, scores it, cuts it to the beat — then runs eleven automated quality checks and repairs its own mistakes until the result passes.

The video is the output. **The pass rate is the product.**

---

## The problem

Two problems, actually, and they're the same problem at different altitudes.

**For creators:** short-form video is assembled, not authored. Someone writes a script, records voice, finds footage, picks music, and then spends the real time on the tedious part — making cuts land on beats, keeping captions readable, keeping the music out of the way of the voice, hitting a platform's duration and loudness spec. That work is mechanical, unglamorous, and it is where the hours go.

**For agent systems:** generating a plausible video is easy and generating a *correct* one is not. Almost every AI video tool produces one output and hopes. Nothing checks whether the cuts are on the beat, whether the captions can actually be read at that speed, whether the character in shot 6 is the same person as in shot 2, or whether the script's claims are traceable to anything real. Without those checks there is no way to say whether the system is getting better — only whether the last demo looked good.

This project treats the second problem as the interesting one.

---

## What makes it different

Most short-form video generators are a straight pipeline: topic in, video out, one shot, no verification. This one inverts the emphasis.

| | Typical generator | This |
|---|---|---|
| Output | An `.mp4` | An **edit decision list**, rendered deterministically |
| Quality | Whatever came out | Eleven checks, nine of them free arithmetic |
| On failure | Ship it anyway | Route the violation to the agent that caused it, repair, re-check |
| Characters | Regenerated per shot, drift visible | **A fixed roster, drawn once and reviewed** — drift impossible by construction |
| Evidence it works | A good-looking demo | Pass rate across a scenario bank, tracked over time |

The consequence is that the system can answer a question most agent demos can't: **is it actually improving, and by how much?**

---

## How it works

Seven agents. Six make a video, one improves the other six.

```
topic → Brief → [gate] → Scoring:select → Showrunner:outline → Research
                                                                   ↓
                          ship ← QC ← Compositor ←──── Showrunner:script → [gate]
                                  │        ↑                                  ↓
                                  │   Scoring:envelope ← Voice ←──────── Casting
                                  └─────── repair ──────┘
```

- **Brief** turns a vague topic into a full proposed plan — intent, format, tone, cast, voices, duration — every field decided and every field editable.
- **Scoring** picks a track *first*, from a pre-scored library, and hands over an exact beat grid and the drop position.
- **Showrunner** outlines the arc against that drop, flags which beats need facts, then writes the dialogue and cuts once **Research** has sourced them.
- **Casting** and **Voice** produce assets; measured audio durations are absorbed by holds and slack, not by re-planning.
- **Compositor** is deterministic — same edit list, same bytes, every time.
- **QC** measures eleven rules and emits typed violations. **Repair** routes each to its owner and rewrites *only the failing span*, up to three rounds.

Two gates, both on text, and how chatty they are is one number: **`involvement: 0–10`**. At 10 it confirms nearly every creative call with you; at 0 it asks nothing and runs start to finish untouched. `involvement: 0` isn't a separate headless mode — it's the same agent asking zero questions, which is exactly what makes the eval sweep trustworthy.

**Full system design — agents, contracts, decision log: [ARCHITECTURE.md](ARCHITECTURE.md)**

---

## Intent, format, tone

"Make a video about AI agents" admits many different videos. The system resolves that into three choices, proposes all of them at once, and lets you change any of them. No wizard — you see the whole plan, already decided.

**Intent** — what it's trying to accomplish. Three, closed, each putting fact-checking in a different regime:

| Intent | Grounding | |
|---|---|---|
| **Explainer** | Most claims sourced | Education, news, how-things-work |
| **Comedy** | Few or none — a low score is *correct* | Skits, bits, satire |
| **Commentary** | Arguable claims, sources back a position | Opinion, analysis |

**Format** — how it's presented. Two, closed: **monologue** (1 speaker) and **debate** (2). **Tone** — engaging, professional, funny, dramatic, casual.

The catalogs are closed — Brief cannot invent a fourth intent or a third format, which is what keeps pass rate comparable across runs. What it *can* do is tune parameters within a configuration's legal range. **The QC rules then read their thresholds from the brief rather than from constants** — the alternative is a validator that disagrees with the plan it's validating.

Pass rate is tracked per intent and format, and never pooled across intents: they carry different thresholds, and Brief picks the intent, so a pooled number would hand Brief a lever on its own grade. The expectation is that it falls as cast size rises — measuring exactly how much is one of the more interesting things this project can report.

---

## What gets measured

| Metric | What it tells you |
|---|---|
| QC pass rate, per intent × format | Whether the planner handles complexity |
| Repair rounds to green | Planning quality — better plans need fewer repairs |
| Cost per finished reel | Whether the system is getting cheaper as it gets better |
| Override rate at each gate | Where the agent's judgment is weakest |
| Violation frequency by rule | Where to aim the next improvement |

### Results

> _Not yet populated — waiting on the eval sweep._
> This section will carry the before-and-after curve from the improvement loop, and it is the headline of the project. Everything else is scaffolding for it.

---

## Stack

| | |
|---|---|
| Models | Gemini (planning, grading, grounded search), Imagen (characters, backgrounds), Gemini TTS |
| Music | Pre-scored library — beat grids and drop positions verified once, offline |
| Orchestration | LangGraph — no model in it; it decides what runs when |
| Observability | Grafana Cloud, via the Grafana MCP server at runtime |
| Rendering | ffmpeg — deterministic, no model in the loop |

Built for [Agentic Cinema](https://agentic-cinema.devpost.com/), Grafana track. Every model call goes to Google — Gemini for planning and grading, Imagen for images, Gemini TTS for speech. Orchestration is [LangGraph](https://langchain-ai.github.io/langgraph/), which generates nothing and simply decides what runs when, the same way ffmpeg composites and librosa measures.

**Three choices do most of the work on cost and trustworthiness.**

Nothing is generated per frame. Each character is nine drawings — eight poses and a mouth-open variant — animated by arithmetic: a 5Hz talk cycle gated by audio amplitude, a bob at idle, hard cuts on beats. Frames are composited by ffmpeg on CPU, so **output frame rate is free**; 24, 30 and 60fps cost identically. Renders run at 30fps, picked because it divides cleanly into the syllable band — not because it's cheaper.

The cast is a fixture, established once at setup with you choosing and re-prompting until the characters are right, then stored. A roster of three is 27 drawings for every video the project will ever make, so identity consistency is structural rather than probabilistic — and character cost leaves the per-run budget entirely. **Backgrounds are the variable**: generated fresh every run, never reused across reels, because with the cast fixed they carry all of a reel's visual identity.

Music is selected, not generated. A generated track needs beat detection, and detection error produces alignment failures that have nothing to do with planning quality — which makes the headline metric untrustworthy. With a pre-scored library the grid is ground truth, so **every beat-alignment failure is a real planning failure.** Generation can be swapped back in later; the interface downstream is `{track, grid, drop_s}` either way.

---

## Status

**In development.**

| | |
|---|---|
| ✅ | Compositor — 30fps sprite animation, beat-accurate cuts, deterministic render |
| ✅ | Harness — budget enforced before execution, verbatim trajectory recording, closed tool registry |
| ✅ | Pipeline runs end to end; the repair loop closes on a real violation |
| 🟡 | Two of eleven QC rules implemented |
| ⬜ | The pre-scored music library — synthetic fixtures stand in for now |
| ⬜ | Eval sweep, Grafana panels, the improvement loop |

The harness is intended to be usable on its own. It lives in its own package with no video-domain imports — short-form video is the first thing driving it, not the only thing it can drive.

---

## License

_To be added before submission — Apache-2.0 or MIT._

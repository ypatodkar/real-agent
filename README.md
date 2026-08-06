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
| Characters | Regenerated per shot, drift visible | Generated once, cached, animated — drift impossible by construction |
| Evidence it works | A good-looking demo | Pass rate across a scenario bank, tracked over time |

The consequence is that the system can answer a question most agent demos can't: **is it actually improving, and by how much?**

---

## How it works

Seven agents. Six make a video, one improves the other six.

```
topic → Brief → [gate] → Research → Showrunner → [gate] → Casting ┐
                                                        Voice     ├→ Compositor → QC → ship
                                                        Scoring   ┘        ↑        │
                                                                           └─ repair ┘
```

- **Brief** turns a vague topic into a spec sheet — format, cast, duration, tone.
- **Research** builds a claim ledger with sources, so the script isn't invented.
- **Showrunner** writes the dialogue, assigns lines to characters, and decides where every cut and the music drop land.
- **Casting**, **Voice**, and **Scoring** produce the assets in parallel.
- **Compositor** is deterministic — same edit list, same bytes, every time.
- **QC** measures eleven rules and emits typed violations. **Repair** routes each one to its owner and tries again, up to three rounds.

Two human gates, both on text, both with a programmatic auto-approve path — which is what lets the whole thing run unattended for evaluation.

**Full system design, with diagrams: [ARCHITECTURE.md](ARCHITECTURE.md)**

---

## Formats

A format is not a prompt — it's a set of constraints the planner has to satisfy, and that the QC gate verifies.

| Format | Cast | Constraints |
|---|---|---|
| Solo explainer | 1 | VO-continuous, hook ≤ 3s, one idea per shot |
| Two-host podcast | 2 | Turn-taking, 4–12s turns, one interruption |
| Interview | 2 | Asymmetric knowledge — host asks, guest answers |
| Debate | 2 | Opposing positions, tension escalating to the drop |
| Skit | 2–3 | Scene continuity, a setup and a turn |

Pass rate is tracked per format. The expectation is that it falls as cast size rises — measuring exactly how much is one of the more interesting things this project can report.

---

## What gets measured

| Metric | What it tells you |
|---|---|
| QC pass rate, by format | Whether the planner handles complexity |
| Repair rounds to green | Planning quality — better plans need fewer repairs |
| Cost per finished reel | Whether the system is getting cheaper as it gets better |
| Override rate at each gate | Where the agent's judgment is weakest |
| Violation frequency by rule | Where to aim the next improvement |

### Results

> _Not yet populated — the eval harness lands in the week of Aug 21._
> This section will carry the before-and-after curve from the improvement loop, and it is the headline of the project. Everything else is scaffolding for it.

---

## Stack

| | |
|---|---|
| Models | Gemini (planning, grading), Imagen (characters), Gemini TTS, Lyria (music) |
| Agents | Google Cloud Agent Builder / ADK |
| Observability | Grafana Cloud, via the Grafana MCP server at runtime |
| Rendering | ffmpeg — deterministic, no model in the loop |

Built for [Agentic Cinema](https://agentic-cinema.devpost.com/), Grafana track. The hackathon restricts the stack to Google AI services, so there are no other model providers or agent frameworks in this repo by design.

Character sprites are generated once and cached rather than using generated video per shot. That decision is about consistency first and cost second — but the cost difference is roughly 30×, which is what makes running hundreds of eval scenarios affordable.

---

## Status

**In development.** Targeting submission September 7, 2026.

| | |
|---|---|
| Aug 4–6 | Spike — Gemini → Grafana MCP, quota and pricing confirmed |
| Aug 7–13 | Harness core, single-narrator spine end to end |
| Aug 14–20 | QC gate and the repair loop |
| Aug 21–27 | Second speaker, eval harness, first pass-rate number |
| Aug 28–Sep 3 | Improvement loop, deployment |
| Sep 4–5 | Demo, writeup, submit |

The harness is intended to be usable on its own. It lives in its own package with no video-domain imports — short-form video is the first thing driving it, not the only thing it can drive.

---

## License

_To be added before submission — Apache-2.0 or MIT._

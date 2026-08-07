# Agent Design Worksheet

> Working doc. One section per agent — headlines only, filled in as we decide.
> Companion to [ARCHITECTURE.md](ARCHITECTURE.md), which says *that* these agents exist.
> This one says **what each of them actually is**: purpose, contract, trigger conditions, and the decisions still open.

**Status key:** ⬜ not started · 🟡 in design · ✅ locked

| # | Agent | Status | Owns the decision |
|---|---|---|---|
| 1 | [Brief](#1--brief-agent) | ✅ shape locked | What kind of video this should be |
| 2 | [Research](#2--research-agent) | ✅ shape locked | What is true and where it came from |
| 3 | [Showrunner](#3--showrunner) | ✅ shape locked | What gets said, by whom, and where the cuts land |
| 4 | [Casting](#4--casting-agent) | ✅ shape locked | What the characters look like, consistently |
| 5 | [Voice](#5--voice-agent) | ✅ shape locked | How each line sounds and how long it really takes |
| 6 | [Scoring](#6--scoring-agent) | ✅ shape locked | The track, the beat grid, the drop, the ducking |
| 7 | [Improvement](#7--improvement-agent) | ✅ shape locked | Which knob to turn next, and why |

**All seven have a locked shape.** What remains open per agent is budgets and a handful of tactical questions, listed in each section. The three that changed the architecture are marked ⚠️ — [Research](#2--research-agent), [Showrunner](#3--showrunner), [Scoring](#6--scoring-agent) — and are consolidated in [Revised pipeline](#️-revised-pipeline).

**✅ Sibling docs are in sync** as of Aug 6. Both were updated to match every decision below:

| Document | What changed |
|---|---|
| ARCHITECTURE.md — Fig. 1 | Redrawn: `6a` first, two-pass Showrunner with Research between, absorption ladder, scoped repairs |
| ARCHITECTURE.md — load-bearing decisions | Involvement dial documented; the Gate 2 softening stated explicitly rather than left implicit |
| ARCHITECTURE.md — agent + QC tables | Split stages, `track_select`, thresholds sourced from `brief.params`, graders marked 🔒 |
| ARCHITECTURE.md — contracts | Brief gains `params`/`confidence`/`brief_invalid`; EDL shot gains `desc` and `hold_s` |
| ARCHITECTURE.md — not-agents table | Absorption ladder, Voice's hot path, and track selection added |
| ARCHITECTURE.md — Fig. 4 | Guardrails, worst-rule targeting, and the grader-immutability constraint |
| README | Pipeline diagram, involvement dial, formats note, stack table — Lyria out, library in |

> Keep this direction of travel: decisions land here first, then propagate. If the two ever disagree again, **this doc is newer**.

---

## ⚠️ Revised pipeline

Two decisions below changed the topology in [ARCHITECTURE.md](ARCHITECTURE.md) Fig. 1. **This is now the canonical order** — Fig. 1 must be redrawn to match.

```mermaid
flowchart TD
    U["Topic + involvement 0–10"] --> A1["1 · Brief"]
    A1 --> G1{{"GATE 1 — brief"}}
    G1 --> S6["6a · Scoring — select<br/>track + exact grid + drop"]
    S6 --> S1["3a · Showrunner — outline<br/>beats, arc, turn on the drop"]
    S1 --> A2["2 · Research<br/>sources flagged beats"]
    A2 --> S2["3b · Showrunner — script<br/>dialogue + EDL, cuts on real beats"]
    S2 --> G2{{"GATE 2 — plan"}}
    G2 --> A4["4 · Casting"]
    G2 --> A5["5 · Voice"]
    A5 --> NG["Absorption ladder<br/>hold · steal slack · escalate"]
    NG --> S6B["6b · Scoring — envelope<br/>ducking fitted to real VO spans"]
    A4 --> CP["7 · Compositor<br/>deterministic"]
    S6B --> CP
    NG --> CP
    CP --> QC{"8 · QC Gate"}
    QC -- "violations" --> RR["9 · Repair Router<br/>scoped repairs only"]
    RR -. .-> S2
    QC -- "green" --> OUT["Deliverable + QC report"]
```

**What moved, and why**

| Change | Reason |
|---|---|
| Showrunner splits into **outline** and **script**, Research between | You can't know which facts you need before you know the script |
| **Scoring splits too** — `6a` select before the outline, `6b` envelope after Voice | The grid is needed early; the ducking envelope can't exist until the VO spans do |
| `6a` runs **first**, ahead of the outline | Scoring proposes the drop from the track; the outline places its turn there |
| Retime becomes a **deterministic absorption ladder**, not an agent edge | Arithmetic, not judgment. No model call on the hot path. |

> **The cost of moving Scoring: Gate 2 is no longer the last gate before spending.** Music generation now happens *before* the human reviews the plan, which softens load-bearing decision #3 — *gates sit where changes are cheap*. Two things make it acceptable: music is the cheapest generated asset, and it caches hard on `(bpm, mood, duration)`, so re-running a topic at a new duration is usually free. Worth knowing you made this trade rather than discovering it in the billing console.

---

## 💰 Cost model — the $100 constraint

Total credit is **$100**. That is not a footnote; it is a design constraint that outranks several decisions made above, and it should be the first thing checked against any new idea.

### What actually costs money

Rendering is `ffmpeg` on CPU. **It is free and effectively unlimited.**

| Costs money | Free |
|---|---|
| Gemini planning + grading calls | Every rendered frame |
| Imagen generations (sprites, backgrounds) | Output FPS — 24, 30, 60, identical cost |
| TTS synthesis | Resolution, aspect ratio, re-encodes |
| Grounded search | Re-rendering the same EDL |
| | Animating, holding, cutting, transitions |

**The consequence: output FPS is not a cost decision at all.** A 30fps reel is encoded from roughly five sprites per character — you do not generate thirty images per second, you hold and cycle a handful and move them programmatically. Doubling the frame rate costs nothing. Adding one distinct drawing costs a generation.

This is the first load-bearing decision paying off: because the EDL is the artifact and the render is deterministic, **re-rendering is $0**. Every "make it 60fps", "make it square for Instagram", "try it at 1080p" is a free re-run of the compositor. Only re-*planning* spends.

### The budget, per reel

Rates below are **placeholders — confirm them in the Aug 4–6 spike** and update this table. The arithmetic is what matters.

| Line item | Per 35s reel | Caches? | Warm-cache cost |
|---|---|---|---|
| Gemini planning (brief, outline, script, retries) | ~8 calls | no | `$?` |
| Grounded search (Research) | ~4 calls | no | `$?` |
| QC graders (grounding, coherence) | 2 Flash calls | **replay-free** | `$?` |
| TTS | ~35s speech | no | `$?` |
| Character sprites | ~10 images | **~95% hit** | ≈ $0 |
| **Backgrounds** | **8–12 images** | ⚠️ **poor** | `$?` ← the problem |
| Music | 0 | library | $0 |
| Compositing | every frame | n/a | **$0** |

**Target: ≤ $0.10 per warm-cache eval reel.** That buys 100-run sweeps at ~$10 each — six sweeps plus development headroom. Above ~$0.30/reel you get two sweeps and the improvement loop has nothing to prove itself against, which costs you the headline result.

### ⚠️ Backgrounds are now the largest uncontrolled cost

The decision to have the Showrunner describe a background per shot was made before the budget was on the table, and it is the one that doesn't survive contact with it. Per-shot descriptions are near-unique by construction, so they barely cache — 8–12 fresh generations on *every* run, including all 100 runs of a sweep, forever.

Characters were optimised down to nearly zero and backgrounds would then quietly become the most expensive stage in the pipeline. On a 100-run sweep this is plausibly a third of the entire budget.

**Three ways out, cheapest first:**

| Option | Cost per reel | Cost |
|---|---|---|
| Small background library, picked by id | **$0** | Scenes stop matching content |
| Library + 1 hero background per reel | ~1 generation | Mostly reusable, one bespoke shot |
| Keep per-shot, cap at 3 distinct + cache on `hash(desc, style)` | ~3 generations | Still 30× the library option |

Recommend the **library**, with per-shot generation kept as a flag you can switch on for the three demo reels that get submitted. The eval sweep never needs bespoke backgrounds — it needs a stable denominator and a cheap run.

### ✅ Hard per-stage caps, checked before execution

No phase pools. Every stage has a ceiling, the harness checks it *before* spending, and the run has a hard total. This is the version where no single bad run can eat the budget — the failure mode being guarded against is one runaway trajectory at 3am, not gradual overspend.

**Recommended allocation — adjust these numbers, they're a starting point.** Rates need confirming in the spike; the *proportions* are the argument.

| Stage | Cap | On breach | Why this share |
|---|---|---|---|
| 1 · Brief | `$0.005` | **abort** | One probe, one short call. Cheapest stage in the system, and it's the head — aborting costs nothing. |
| 6a · Scoring select | `$0.000` | — | A library lookup. No model, no cost, no cap needed. |
| 3a · Showrunner outline | `$0.010` | **abort** | One call over a small context. Still cheap enough to restart. |
| 2 · Research | `$0.020` | **degrade** | ~4 grounded calls, the priciest text stage. On breach, ship fewer sourced claims and let `grounding` fail honestly. |
| 3b · Showrunner script | `$0.025` | **degrade** | Deep validator loop, largest context, most retries. The most expensive stage — and the one where extra spend most reliably buys quality. On breach, emit best-so-far and let QC catch it. |
| 4 · Casting | `$0.005` | **degrade** | ≈$0 warm. This cap is really an amortised allowance for cold archetypes. |
| 5 · Voice | `$0.015` | **degrade** | ~35s of TTS. Nearly fixed — it barely varies by run. |
| 6b · Scoring envelope | `$0.005` | **degrade** | One call, or deterministic. |
| 8 · QC graders | `$0.010` | **abort** | Two Flash calls. Never degrade a grader — a partial judge is worse than no judge. |
| 9 · Repair pool | `$0.015` | **escalate** | Run-level pool, not per-round. Scoped repairs are small; three rounds fit. On breach, ship with the violation report. |
| **RUN TOTAL** | **`$0.110`** | **abort** | Hard ceiling. Below the $0.10 target once Casting runs warm. |

**Three things about this table are deliberate.**

**Breach behaviour differs by stage, and that's the point.** Aborting a $0.005 Brief is free; aborting a $0.025 script pass throws away everything already spent on the run. So cheap early stages abort and expensive late ones degrade — emit what they have and let QC report the damage. That's the same philosophy as the exhausted repair loop: partial output with an honest account beats nothing.

**QC never degrades.** It's the only stage where running out of money must not produce a softer answer, because the pass rate is the product. Out of budget at the gate means the run is void, not passing.

**Repair is a pool, not a per-round cap.** Round costs vary a lot — a resynth is trivial, a script rewrite isn't. A shared pool lets three cheap repairs through or stops after one expensive one, which is the correct behaviour in both cases.

> **Cold-start is a separate line.** The first run using a new archetype generates 6 sprites and blows the Casting cap by design. Track that as a **one-time per-archetype cost**, outside the per-run budget — otherwise the first run of every sweep aborts and you spend a morning debugging a working system.

### Replay is a budget instrument, not just an engineering nicety

When only grading logic changes, recorded trajectories are re-scored **at zero cost**. Half the "sweeps" in the improvement loop never spend a cent. This is worth building properly in week one precisely because of the $100 ceiling — it is the difference between six sweeps and twenty.

---

## Shared contract

Every agent in this system is the same shape. If an agent needs an exception here, that exception is a design decision worth writing down.

| Slot | Meaning |
|---|---|
| **Input** | The typed document it reads. Never free text after stage 1. |
| **Output** | The typed document it writes. This is what downstream agents see. |
| **Tools** | Fixed registry. No agent gets an open-ended tool. |
| **Loop depth** | Shallow (1–2 calls) · Medium · Deep (iterates against a validator) |
| **Called when** | Its trigger in the forward pass |
| **Repair inbox** | Which violation rules route back to it, and what it's allowed to change |
| **Budget** | Step cap, dollar cap, wall clock |
| **Failure mode** | What it looks like when this agent is the thing that's broken |

### ✅ Repairs are scoped, everywhere

**A repair changes only the span that failed. Everything else is frozen.** A violation on line 4 rewrites line 4 — not the script.

```
violation: duration_adherence, evidence { line: 4 }
   ↓
SHOWRUNNER receives:  line 4 + the violation + the constraint
             frozen:  lines 1-3, 5-9, the EDL, every other asset
   ↓
one line changes. everything downstream of it is re-derived,
nothing else is re-generated.
```

Three things this buys, and they compound: repairs are cheap (a fraction of the context, a fraction of the tokens), they can't introduce collateral drift into parts that already passed, and **round 2 of a repair loop can't undo round 1** — which is the failure mode that makes unbounded repair loops thrash.

**System-wide, not a Voice special case.** Casting regenerates the outlier pose, not the sprite set. Voice resynthesizes the line, not the track. Research re-sources the claim, not the ledger.

**Open, system-wide:**
- ⬜ Is state passed as a single growing `Production` object, or does each agent read only its declared inputs?
- ⬜ Where does the run's config live — one YAML per run, or a config diffable across eval sweeps?

---

## The involvement dial

✅ **Locked.** A single run-level integer, `involvement: 0–10`, that governs how much the system asks the human before deciding for them.

This is the mechanism that satisfies load-bearing decision #4 — *every gate has an auto-approve path* — without a second code path. **`involvement: 0` is not a special headless mode. It is the same agent, asking zero questions.** The eval sweep runs at 0 and the product ships at 5. There is nothing to keep in sync.

**How the dial resolves to behavior.** The agent scores its own confidence per field, then asks about the lowest-confidence fields until its question budget runs out.

| Dial | Question budget | Ask if confidence below | Feels like |
|---|---|---|---|
| **0** | 0 | — | Vending machine. Topic in, brief out, no interruption. |
| **3** | 1 | 0.35 | Asks only when genuinely stuck. |
| **5** | 2 | 0.60 | Asks about the one or two real forks. Default. |
| **8** | 4 | 0.85 | Checks most creative calls with you. |
| **10** | 6 | 0.99 | Collaborator. Confirms nearly everything, including what it's sure about. |

```
involvement → (question_budget, ask_threshold)
              ↓
  fields ranked by confidence, ascending
              ↓
  ask about fields where conf < ask_threshold,
  stopping at question_budget
              ↓
  everything unasked is decided and shown
  as an editable assumption chip
```

**Why the chips still matter at every level.** Even at 10, unasked fields appear as assumption chips at the gate. The dial controls what gets asked *proactively*; the gate always shows everything decided. A user at 0 who wants to intervene still can — they just weren't interrupted.

**The metric this unlocks.** Override rate at Gate 1, plotted against involvement level, is a direct read on where the agent's judgment is weakest. If users at involvement 0 override the format 40% of the time, the format heuristic is bad — and you learn that without anyone filing a bug.

**✅ Scope: the dial governs every gate.** One scalar, applied at Gate 1 *and* Gate 2. At 0 the entire pipeline runs untouched end to end; at 10 the Showrunner also checks its arc and drop placement with you before any asset is generated.

| Dial | Gate 1 — brief | Gate 2 — plan |
|---|---|---|
| 0 | silent | silent |
| 5 | ~2 questions | plan shown, ~2 questions |
| 10 | ~6 questions | ~6 questions on arc, drop, cuts |

This is the version that keeps *one* code path. Making Gate 2 always-shown would have forced a separate headless bypass for it, and that bypass is exactly the kind of eval-only branch that drifts out of sync with the product.

**Open:**
- ⬜ Is confidence a self-reported number from the model, or derived from something more honest — e.g. agreement across two samples?
- ⬜ Per-run value, or a saved user default that a run can override?
- ⬜ Does the question budget refill at Gate 2, or is it one shared budget across the whole run?

---

## 1 · Brief Agent

**One-liner:** The producer taking the order. Turns "Voynich manuscript" into a spec sheet.

| Slot | Decision |
|---|---|
| Input | `topic: str` · `involvement: 0–10` · optional pinned fields |
| Output | Brief document — every field carries a confidence and a one-line rationale |
| Tools | `format_catalog` (read) · `topic_probe` (1 search, always) · `duration_policy` (read) |
| Loop depth | Shallow — probe, infer, ask, emit |
| Called when | Once, at the head of the pipeline. Never re-run. |
| Repair inbox | **None** — gated, never repaired. A bad brief is a failed run, not a repaired one. |
| Budget | `$0.005` · **abort** on breach |
| Failure mode | Picks a format the topic can't carry. Shows up downstream as coherence failures the Showrunner can't fix by retiming. |

### ✅ Locked

**Format = catalog + parameters.** Five base formats, each with hard constraints. Brief picks one and *tunes its parameters* within the legal range — turn length, cast size, hook budget.

```yaml
format: debate              # from the closed catalog
params:
  cast_size:     2          # catalog allows 2
  turn_len_s:    [3, 9]     # Brief narrowed from catalog's [4, 12]
  hook_budget_s: 2.5
```

> **The consequence, and it's load-bearing:** QC rules must read their thresholds *from the brief*, not from constants. `reading_speed`, `duration_adherence`, and the turn-length check all become `assert x within brief.params.*`. Write the rules this way from the first one — retrofitting parameterized thresholds into hardcoded rules is the same expensive mistake as retrofitting multi-speaker.

**Always probe once.** One `topic_probe` search before deciding, unconditionally. It returns a topic shape — contested? visually rich? factually dense? — and that shape drives both the format choice and the per-field confidence scores that the involvement dial reads.

```
"Voynich manuscript"
   → topic_probe
   ← { contested: high, visual: high, factual_density: medium }
   → format: debate (conf 0.85) · visual: flat_vector (conf 0.9)
   → tone: wry (conf 0.45)  ← lowest confidence, gets asked first
```

Unconditional beats conditional-on-confidence here: it's one cheap call, it makes the trajectory the same shape on every run, and it means confidence is computed *after* seeing evidence rather than from the topic string alone.

**Gate 1 auto-approves unconditionally.** No blocking validator. A malformed brief flows downstream and gets caught by QC as some other rule's violation — which stress-tests the rest of the system rather than hiding behind a guard.

**Schema check runs, but never blocks.** ✅ An illegal brief (`cast_size: 3` on a `debate`) would otherwise surface downstream as a *Showrunner* violation — and the Improvement Agent would then aim its next mutation at the wrong prompt. So the validator runs and records, and nothing else:

```yaml
brief_invalid: true
reason: "cast_size 3 illegal for format debate (allows 2)"
# run continues. QC violations on this run are tagged
# caused_by: brief, and excluded from the Showrunner's metric.
```

Free to compute, and it's what keeps per-agent attribution honest — which is the input the whole self-improvement loop depends on.

**Pinning: coarse fields only.** ✅ The user may pin `format`, `duration_s`, `cast_size` before the run. Everything creative — tone, visual direction, personas, music intent, and all `params` — stays Brief's to decide. Pinned fields are hard constraints, not hints.

> **Follow-on:** the scenario bank can't use pinning to inject a known-good brief for eval isolation, since that needs *every* field. That's a harness concern instead — the runner injects a brief document and skips stage 1 entirely. Worth building when the eval harness lands (week of Aug 21), not before.

### Open
1. ⬜ Confidence scoring mechanism *(shared with the dial — see above)*
2. ⬜ Step cap — how many calls before abort *(dollar cap is set)*

---

## 2 · Research Agent

**One-liner:** The fact-checker. Writes down true things with sources, so the script isn't invented.

| Slot | Decision |
|---|---|
| Input | Beat outline from Showrunner pass 1 — specifically, the beats flagged `needs_fact` |
| Output | Claim ledger, keyed by beat |
| Tools | Gemini grounded search (Google Search tool) · `contradiction_check` |
| Loop depth | Deep — one grounded call per open beat, iterating until every flagged beat is answered or declared unanswerable |
| Called when | Always. Every format, every run — no exceptions. |
| Repair inbox | `grounding` → re-source the specific failing claim |
| Budget | `$0.020` · **degrade** on breach — ship fewer claims, let `grounding` fail honestly |
| Failure mode | Returns confident claims that aren't in the cited source. Invisible without verification. |

### ⚠️ This changes Fig. 1

**Locked: outline → research → script.** The Showrunner runs in **two passes**, and Research sits between them. You cannot know which facts you need until you know what the script is about, and a broad up-front sweep spends money on claims the script never uses.

```
BRIEF → GATE 1
   ↓
SHOWRUNNER pass 1 — beat outline, each beat flagged needs_fact
   ↓
RESEARCH — sources exactly the flagged beats
   ↓
SHOWRUNNER pass 2 — dialogue + EDL, written against the ledger
   ↓
GATE 2
```

> **Fig. 1 in [ARCHITECTURE.md](ARCHITECTURE.md) is now wrong** — it draws `Research → Showrunner` as a single hop. Update it to a two-pass Showrunner with Research nested between. Not urgent, but it must happen before the diagram is used to explain the system to anyone else.

The cost is one extra Showrunner call. What you buy: every search is targeted, nothing is researched and discarded, and the script's shape is driven by the brief rather than by whatever the search happened to surface.

### ✅ Locked

**Claim bar: checkable assertions only.** Dates, numbers, names, attributions, causal claims. Opinions, jokes, transitions, and framing are unsourced by design.

```
"Carbon dated to the early 1400s"   → CLAIM   c_03
"Nobody has ever decoded it"        → CLAIM   c_04
"Which is completely wild"          → not a claim
"So here's my theory..."            → not a claim
```

Expect roughly 6–10 ledger entries for a 35-second reel. Small enough to read at a glance, which is what makes the ledger usable at Gate 2.

**Always runs, always graded — no format exemptions.** A skit gets researched and grounded exactly like an explainer. One uniform pipeline, no `if factual` branches anywhere in the code.

> **Watch this one.** Skits will score low on grounding, and that drags down skit pass rate for a reason that has nothing to do with whether the skit is good. That's legitimate signal *if* you read it as "this format is less grounded" — it becomes a problem if it swamps the more interesting per-format differences. If it does, the fix is already available: move the grounding threshold into `brief.params`, same as every other QC threshold. Don't build that until the data says you need it.

**Gemini grounded search.** Use the built-in Google Search tool rather than hand-rolled `search` + `fetch`. Dramatically less code, and it's the same stack constraint the hackathon imposes anyway.

> **The catch, and the mitigation.** Grounded search makes retrieval a black box, which collides with the replay guarantee in ARCHITECTURE.md — *"when only your grading logic changes, re-score recorded runs at zero cost."* Retrieval won't reproduce; the same query next week returns different results. **Fix: the trajectory recorder must persist the returned text and `groundingMetadata` verbatim, not just the query.** Then re-scoring replays against stored evidence and stays free, even though re-*running* the search wouldn't reproduce. This is a requirement on the recorder, and it's cheap only if built in from the start.

### Open
1. ⬜ Verification pass — does Research confirm a claim actually appears in its cited source, or trust the grounding metadata?
2. ⬜ What happens to a beat Research can't source? Drop the beat, soften it, or flag it and let the Showrunner decide?
3. ⬜ Does `contradiction_check` survive, or is it dead weight at this ledger size?
4. ⬜ Step cap — how many grounded calls before it gives up *(dollar cap is set at `$0.020`)*

---

## 3 · Showrunner

**One-liner:** Writer and director. The most important agent in the system.

**✅ One agent, two passes.** Same prompt, same tools, same metric — the input shape selects the pass. Two agents would have let you attribute a bad reel to structure vs. prose, but it doubles the prompt surface the Improvement Agent has to search, and in a one-month build that's the wrong trade. If the metrics later show structure and prose failing independently, split it then.

| | **3a · Outline** | **3b · Script** |
|---|---|---|
| Input | Brief | Outline + claim ledger + real beat grid |
| Output | Beats, arc, drop position, `needs_fact` flags | Dialogue lines + EDL |
| Called when | After Gate 1 | After Research and Scoring both return |
| Loop depth | Shallow | Deep — iterates against `format_validator` before emitting |

| Slot | Decision |
|---|---|
| Tools | `format_validator` · `beat_grid` (read, real) · `duration_estimate` · `shot_budget` |
| Repair inbox | timing · structure · reading speed · pacing · screen-time balance · coherence |
| Budget | `3a` `$0.010` **abort** · `3b` `$0.025` **degrade** — emit best-so-far, let QC catch it |
| Failure mode | Writes to the estimate rather than the constraint — a plan that validates but doesn't survive real audio. |

### ✅ Retime is deterministic — no model call

Voice measures real durations; the retime that follows is pure arithmetic. Real durations replace estimates, shots absorb the delta, cuts snap to the nearest real beat.

```
t_est 3.4  →  t_actual 3.9   (+0.5s)
        ↓  arithmetic only
shift sh_04.out_s by +0.5
snap to nearest beat        → offset 18ms ✓
recheck total duration      → 35.4s, in range ✓
        ↓
$0, instant, reproducible — no model in the loop
```

> **What happens when the nudge can't absorb it?** Nothing special — and that's the nice part. If the shift blows the duration budget or drives a shot under minimum length, the numbers simply fail QC as `duration_adherence`, and the Repair Router sends it to the Showrunner with the evidence attached. **The escalation path already exists; it's the repair loop.** No inline fallback, no second code path, and the failure gets counted as a repair round like everything else.

### ✅ Cuts are planned against a real beat grid

Scoring runs *before* the script pass, so the Showrunner cuts to beats that actually exist in the track rather than to an assumed tempo.

```
GATE 1 → OUTLINE ─┬→ RESEARCH  → ledger      ─┬→ SCRIPT
                  └→ SCORING   → [0.51, 1.02, ┘
                                  1.48, 1.99…]
```

The alternative — plan on a nominal grid, then time-stretch the track to match — makes the music serve the edit, and it audibly degrades a generated track. Planning against real beats costs wall-clock time and nothing else.

### Open
1. ⬜ Does the outline pass propose the drop position, or does Brief's `music_intent.drop_at_s` fix it?
2. ⬜ Shot boundaries: one shot per line, or can a line span shots / a shot hold multiple lines?
3. ⬜ On repair, does it see the full prior script or only the violating span? *(instance of the system-wide question)*
4. ⬜ Step cap — how many validator iterations before it emits anyway *(dollar cap is set at `$0.025`)*

---

## 4 · Casting Agent

**One-liner:** The character designer. Draws each character once, keeps them recognizable forever.

| Slot | Decision |
|---|---|
| Input | Brief cast list + EDL (pose requests, background descriptions) |
| Output | Sprite set per character + rendered backgrounds, all content-hashed |
| Tools | `imagen_generate` · `identity_distance` · `cache_lookup` |
| Loop depth | Medium — generate, measure drift, regenerate outliers |
| Called when | After Gate 2, in parallel with Voice |
| Repair inbox | `identity_drift` → regenerate the outlier pose only |
| Budget | `$0.005` **degrade** · cold-archetype sprite generation is a **separate one-time line**, outside the per-run cap |
| Failure mode | Cache key too specific — silently correct output at 5× the cost. Won't show up in QC at all, only in the bill. |

### ✅ Cache key: archetype by default, override when it matters

```
default   key = hash(archetype, style, pose)
          "skeptic" + flat_vector → sprite_a1b2
          reused across voynich, bigfoot, roswell, bermuda…

override  brief sets distinct: true on a character
          key = hash(archetype, style, pose, descriptor, seed)
          fresh generation, topic-specific look
```

This is the decision that makes a 100-run eval sweep affordable. The sweep runs all-default, so a hundred topics share a handful of archetypes and the hit rate lands near 95%. The override exists for the one reel where the character genuinely has to look like a specific person — and because it's opt-in, it can never quietly wreck the sweep economics.

> Put **cache hit rate on the dashboard from day one**, not as a nice-to-have. It's the single largest lever on cost per reel, and a regression here is invisible in every other metric.

### ✅ Poses: fixed core, bounded extras

```
core     [talking, listening, reacting, gesturing, idle]
         every character, always, cached and shared

extras   max 2 per reel, requested by the Showrunner
         for a specific moment — facepalm, pointing, …
```

The core set keeps the EDL validatable against a fixed enum *before any pixel is generated* — that's a free QC check on the plan. Extras stay bounded so the marginal cost per reel is knowable in advance rather than a function of how expressive the Showrunner felt.

### ✅ Animation: 6 sprites per character, moved by arithmetic

**Nothing is animated by a model.** Five poses plus one mouth-open variant, cached forever, moved around by the compositor.

```
sprites per character = 5 core poses + 1 mouth-open = 6      ← the only cost
                                                               one-time, then cached

motion, all free:
  talking  →  alternate base/mouth-open, gated by audio amplitude
  idle     →  sine bob, ~2px, 0.5Hz
  emphasis →  slight scale pulse
  entrance →  slide + ease
  beats    →  hard cut between poses
```

### ✅ 30fps, with a 3-frame mouth hold

FPS costs nothing, so it is chosen entirely on how it divides.

| | |
|---|---|
| **Render** | `30fps` — pinned in config, recorded per run, part of the render hash |
| **Mouth hold** | `3 frames` → 10 state-changes/sec → **5Hz talk cycle** |
| **Frame duration** | `33.3ms` |

Natural speech runs ~4–7 syllables/sec, so the mouth should cycle in that band. At 30fps a 3-frame hold lands at 5Hz and divides evenly, so the alternation never judders; a 2-frame hold gives 7.5Hz for more energetic delivery. At 24fps the same holds give 4Hz and 6Hz — the slow end reads as floppy. 30 gives both usable options cleanly.

**`mouth_hold_frames` is a brief parameter, default 3** — same pattern as every other threshold, and a legitimate thing for the Improvement Agent to tune.

> **The real constraint: frame duration is a floor on beat-alignment precision.** Cuts quantize to frame boundaries, so at 30fps a cut can sit up to **±16.7ms** off its beat regardless of how good the planner is. Against a 60ms `beat_alignment` threshold that consumes about a quarter of the budget — acceptable. But **tighten that threshold below ~35ms and the frame rate becomes the limiting factor rather than the Showrunner**, at which point the metric is measuring the encoder. If you need the headroom, 60fps halves the quantization error to ±8.3ms.

**Render time is the only thing 60fps actually costs.** Credits are unaffected; ffmpeg CPU and file size double, across every run of every sweep. That's a throughput decision, not a budget one — so run sweeps at 30 and **re-render the three submission reels at 60 if it looks better.** Re-rendering is free and requires no re-planning, which is the first load-bearing decision paying off again.

A higher sprite count would make the animation smoother and worse; limited animation is the correct look for flat-vector, and 6 drawings is enough to sell it.

Deliberately *not* doing viseme lip-sync. Layered bodies and mouth shapes would give real phoneme accuracy for 4 more images per character — genuinely affordable — but it adds a compositing layer, an audio-analysis step, and a new class of sync bug, in exchange for detail that is invisible at short-form scale on a phone. Revisit only if the reels look wrong in a way that traces to the mouth.

### ✅ Backgrounds: library by default, generation behind a flag

⚠️ **This reverses the earlier decision**, and the $100 ceiling is why. Per-shot descriptions are near-unique by construction, so they barely cache — 8–12 fresh generations on *every* run of *every* sweep. Characters had just been optimised to nearly zero; backgrounds would have silently become the most expensive stage in the pipeline.

```yaml
# default — eval sweeps, and most product runs
layers:
  - { type: character, id: skeptic, pose: talking, x, y, scale }
  - { type: bg, asset: bg_02 }              # library id — $0
  - { type: caption, span: [12.4, 16.0] }

# --bespoke-bg — the three demo reels that get submitted
  - { type: bg, desc: "dim library, manuscript on table" }
```

Both paths stay in the code; the flag chooses. Sweeps get a stable, free denominator, and the reels a human actually watches get bespoke art. Cap the generated path at **3 distinct backgrounds per reel**, keyed on `hash(desc, style)`, so even the flag has a ceiling.

> **A side benefit worth noticing.** With a library, backgrounds cannot drift — nothing generates them. The open question about extending `identity_drift` to background groups disappears on the default path, and only applies under the flag, where you're watching the output by hand anyway.

### Open
1. ⬜ What is "canonical" for drift measurement — the first generated pose, or a dedicated reference render?
2. ⬜ How many backgrounds in the library, and are they hand-made or generated once offline?

---

## 5 · Voice Agent

**One-liner:** The voice director. Routes each line to its character and measures what it really costs in seconds.

| Slot | Decision |
|---|---|
| Input | Script lines + brief cast (voice ids already assigned) |
| Output | Audio segments + measured durations + slack report |
| Tools | `tts_synthesize` · `measure_duration` · `loudness_normalize` |
| Loop depth | **Zero on the hot path** — pure function. Model call only on escalation. |
| Called when | After Gate 2, in parallel with Casting |
| Repair inbox | `speaker_attribution` · prosody → resynth **that line only** |
| Budget | `$0.015` · **degrade** — near-fixed per run, it barely varies |
| Failure mode | Silent prosody flatness — technically correct audio that sounds like a robot reading a list. No QC rule catches this. |

### ✅ Deterministic by default, model call only on escalation

```python
# hot path — no model, reproducible, free to replay
for line in script:
    voice = brief.cast[line.speaker].voice     # already assigned
    audio = tts(line.text, voice, line.delivery)
    dur   = measure(audio)

# escalation — rare
if drift_exceeds_absorption(line):  →  model call
if repair_arrives(prosody):         →  model call
```

One prompt exists but is rarely invoked. This keeps the common path cheap and replayable while leaving somewhere for the Improvement Agent to tune prosody handling.

**Voice ids are assigned by Brief, at cast time.** ✅ Locked before any planning, visible at Gate 1, and the Showrunner writes knowing how each character sounds.

> **One gap this leaves.** Nothing checks that two voices are *distinguishable*. In a two-hander, casting two similar voices produces a reel where you can't tell who's speaking — and `speaker_attribution` won't catch it, because attribution verifies each line went to its assigned voice, not that the assignment was audible. Cheap fix: a distinctness check in Brief when it picks the cast. Filed below rather than decided.

### ✅ The absorption ladder

Synthesized speech is never the length you estimated. Rather than escalating on every drift, absorb what's absorbable — and the two directions absorb differently.

| Drift | Direction | Handling | Cost |
|---|---|---|---|
| ≤ beat tolerance | either | Snap to nearest beat — the existing deterministic nudge | free |
| ≤ `max_hold_s` | **under**run — line came out short | **Hold the last frame** or extend the transition into the next shot | free |
| ≤ `max_hold_s` | **over**run — line ran long | Absorb from adjacent slack: shorten neighbouring holds and inter-turn pauses | free |
| > `max_hold_s` | either | **Escalate — scoped.** Showrunner rewrites *that line only* | 1 call |

```
line 4:  est 3.4s  →  actual 5.8s   (+2.4s over)
   ↓
adjacent slack available? 0.9s  →  not enough
   ↓
2.4s > max_hold_s (2.0)  →  ESCALATE
   ↓
SHOWRUNNER: rewrite line 4 only, target ≤ 3.5s spoken
            lines 1-3, 5-9 and the EDL stay frozen
```

**`max_hold_s` is a brief parameter, not a constant** — same pattern as every other threshold. Default `2.0` per the decision above.

> **Tune this one downward with data.** In a 35-second reel, a 2-second freeze-frame is 6% of the runtime held on a static image, and short-form audiences read holds past roughly half a second as a stall. The `pacing_curve` rule will independently flag reels that lean on long holds, which is the check working correctly — so let the eval sweep find the real number rather than arguing it now. Starting at 2.0 and watching `pacing_curve` violations is the right order of operations.

### Open
1. ⬜ Voice distinctness check at cast time — Brief's job, in or out?
2. ⬜ Loudness: normalize per segment, or only on the final mix?
3. ⬜ Step cap — resynth attempts per line before shipping it long *(dollar cap is set at `$0.015`)*

---

## 6 · Scoring Agent

**One-liner:** The music supervisor. Picks the track, hands over an exact beat grid, and keeps the music out of the way of the voice.

### ⚠️ This agent also has to split in two

Two of the decisions below collide on ordering. Scoring must run **before** the script pass to supply the beat grid — but the ducking envelope depends on where the VO spans are, which nobody knows until the script is written and Voice has measured it. One stage cannot do both.

So Scoring splits the same way the Showrunner did:

| | **6a · Select** | **6b · Envelope** |
|---|---|---|
| Runs | After Gate 1, parallel with Research | After Voice measures |
| Input | Brief `music_intent` + target duration | Track + measured VO spans + drop position |
| Output | Track, exact beat grid, drop position | Ducking envelope |
| Nature | Near-deterministic — a library lookup | Where the actual judgment lives |
| Repair inbox | `beat_offset` → reselect | `music_ducking` · `loudness_spec` → refit |

| Slot | Decision |
|---|---|
| Tools | `track_select` · `envelope_fit` · `loudness_measure` |
| Budget | `6a` `$0.000` — a lookup · `6b` `$0.005` **degrade** |
| Failure mode | An envelope that's technically correct and musically dead — ducking so hard the track disappears. |

### ✅ Pre-scored library, not generated music

A small set of tracks with beat grids and drop positions **measured once, exactly, offline**.

```
library/
  driving_120bpm.wav   grid: exact   drop: 22.0s
  tense_98bpm.wav      grid: exact   drop: 18.4s
  warm_105bpm.wav      grid: exact   drop: 24.1s

track_select(mood, duration, drop_hint) → lookup, $0
```

This is quietly one of the highest-leverage decisions in the doc. Beat detection error was going to be a standing source of `beat_alignment` failures that had nothing to do with planning quality — the exact thing that makes a metric untrustworthy. With a library the grid is ground truth, so **every beat-alignment failure is a real planning failure**. Free, exact, and perfectly reproducible on replay.

> **Two updates this forces.** Lyria comes out of the stack table in [README.md](README.md) and [ARCHITECTURE.md](ARCHITECTURE.md) — or moves to a "future work" line. And the `music_generate` tool becomes `track_select`. The interface downstream is `{track, grid, drop_s}` either way, so generation can be swapped back in later without touching anything else.

### ✅ Scoring proposes the drop, the outline confirms

Music-led. Scoring picks the track, reports its natural drop position, and the Showrunner's outline places its emotional turn there.

```
BRIEF: music_intent { mood: tense, drop_at_s: ~22 }   ← a hint for selection
   ↓
6a SELECT → tense_98bpm.wav, drop at 18.4s            ← the real number
   ↓
OUTLINE places the turn at 18.4s
   ↓
SCRIPT written to land the turn on that beat
```

Brief's `drop_at_s` demotes from constraint to selection hint. The turn lands on a real musical event instead of an arbitrary timestamp, which is the difference between a reel that feels scored and one that feels soundtracked.

### ✅ Scoring fits an explicit envelope

Not a fixed sidechain — a real curve, deeper under dense dialogue, lighter under a single word, and released *before* the drop so it lands at full level.

```yaml
envelope:
  - { span: [0.0,  2.1],  gain: 1.00 }   # no VO — full
  - { span: [2.1,  6.4],  gain: 0.35 }   # dense dialogue
  - { span: [6.4,  7.0],  gain: 0.60 }   # single word, lighter touch
  - { span: [17.9, 18.4], gain: 1.00 }   # released early for the drop
```

The Compositor applies it and decides nothing — which keeps the Compositor deterministic, as required. `music_ducking` then verifies the rendered dB deltas match the envelope, so the rule is checking a real claim rather than confirming its own arithmetic.

### Open
1. ⬜ How many tracks in the library at minimum? *(mood coverage vs. every reel sounding the same)*
2. ⬜ If no track's drop is near the hint — reselect, or offset the track's start?
3. ⬜ Does 6b get a model call, or is `envelope_fit` a deterministic function of VO density?

---

## 7 · Improvement Agent

**One-liner:** Makes no videos. Reads how the other six performed and proposes exactly one change.

| Slot | Decision |
|---|---|
| Input | Grafana aggregates via MCP + the best and worst trajectory on the target metric |
| Output | One mutation proposal, from a fixed search space |
| Tools | Grafana MCP (read-only) · `propose_mutation` · `regression_gate` |
| Loop depth | Shallow — rank, diagnose, propose once |
| Called when | Offline only, after an eval sweep completes |
| Repair inbox | n/a |
| Budget | `$0.020` per proposal · **sweep budget = `scenarios × $0.110`**, declared and enforced before the sweep starts |
| Failure mode | **Reward hacking** — improving the metric without improving the video. See the hard constraint below. |

### ✅ Search space: thresholds and prompts

```
MUTABLE
  brief.params defaults        (max_hold_s, turn_len_s, hook_budget_s…)
  QC thresholds                (beat tolerance ms, chars/sec, drift distance)
  shot_budget heuristics
  agent prompt variants        (A/B, one agent at a time)

FROZEN
  pipeline topology            tool registry
  repair routing + round cap   the Compositor
  ── and, non-negotiably ──    the grader prompts
```

### 🔒 Hard constraint: it cannot touch what grades it

Two of the eleven QC rules — `grounding` and `coherence` — are model-graded. **The Improvement Agent must never be able to mutate a grader prompt or a grader threshold.** If it can, the cheapest way to raise the pass rate is to make the judge lenient, and it will find that before it finds a better Showrunner.

This isn't hypothetical caution; it's the standard failure mode of any system that optimizes against its own evaluator. Enforce it structurally: grader prompts live in a separate, versioned namespace that `propose_mutation` has no write path to. Pin them for the whole project and record the version on every run, so a pass-rate curve always means the same thing at both ends.

### ✅ One mutation per sweep, aimed at the worst rule

The agent doesn't get free choice of target. It must aim at whichever rule fails most often, which forces a defensible reason for every proposal.

```
violation frequency, last 100 runs
  beat_alignment    31%   ← aim here
  reading_speed     12%
  identity_drift     4%
        ↓
propose ONE beat-related mutation
        ↓
A/B against baseline, same scenario bank
        ↓
re-rank, next sweep
```

Strict one-at-a-time A/B is slower than batching, and it's what lets the writeup say *this change caused this gain* rather than *things got better*. For a project whose product is the pass rate, that distinction is the whole claim.

### ✅ Auto-promote inside guardrails

```
PROMOTE automatically iff
    pass_rate         improved
AND cost_per_reel     did not rise
AND no single rule    regressed by more than 2%

otherwise → becomes a proposal for a human
```

Autonomous enough to run overnight — which is the demo — with a floor that stops it from trading cost or a specific rule away for an aggregate gain. Every promotion is logged with its before/after curve.

### ✅ Reads aggregates plus one exemplar pair

```
1 · MCP → violation_freq_by_rule, pass_rate_by_format, cost_p50
2 · pull the BEST and WORST trajectory on the target metric
3 · diff them — what actually differed?
4 · propose
```

Bounded, predictable context, and most of the diagnostic value of reading failure logs. Aggregates alone tell it *where* it hurts; the exemplar pair is what lets it say *why*.

### Open
1. ⬜ Sweep size — how many scenarios before a pass-rate delta is trustworthy?
2. ⬜ Where do promoted configs live, and how does a run record which config produced it?
3. ⬜ Who stops a sweep mid-flight, and on what signal *(sweep budget formula is set)*

---

## Decision log

*Every locked answer lands here with a date, so the eval sweep and this doc never disagree.*

| Date | Agent | Decision | Why |
|---|---|---|---|
| Aug 5 | System | `involvement: 0–10` dial resolves to (question budget, ask threshold) | Interactive and headless become the same code path. `involvement: 0` *is* the eval-sweep mode. |
| Aug 5 | Brief | Closed format catalog, parameters tunable within it | Keeps pass-rate-by-format comparable across runs while letting the planner adapt per topic. |
| Aug 5 | Brief | QC thresholds read from `brief.params`, never constants | Direct consequence of the above. Cheap now, expensive to retrofit. |
| Aug 5 | Brief | One unconditional `topic_probe` before deciding | Confidence gets scored against evidence, not vibes. Same trajectory shape every run. |
| Aug 5 | Brief | Gate 1 auto-approve accepts unconditionally | Stress-tests downstream QC instead of hiding brief errors behind a guard. |
| Aug 5 | System | The dial governs **every** gate, not just Gate 1 | Avoids a headless-only bypass for Gate 2 — the eval-only branch most likely to drift from the product. |
| Aug 5 | Brief | Schema check runs as a non-blocking `brief_invalid` label | Keeps per-agent violation attribution honest, which is the Improvement Agent's only input. |
| Aug 5 | Brief | Users pin coarse fields only — format, duration, cast size | Creative fields stay the agent's job. Eval-time brief injection is a harness concern, not a user surface. |
| Aug 5 | **System** | **Showrunner runs two passes, Research sits between them** | You can't know which facts you need before you know the script. Targets every search. **Fig. 1 must be redrawn.** |
| Aug 5 | Research | Claim bar = checkable assertions only | ~6–10 entries per reel. Small enough to actually read at Gate 2. |
| Aug 5 | Research | Runs for every format including fiction, graded uniformly | No `if factual` branch anywhere. Low skit grounding is accepted as real signal. |
| Aug 5 | Research | Gemini grounded search over hand-rolled search + fetch | Less code, same stack constraint. **Requires the recorder to persist returned text + groundingMetadata** or replay breaks. |
| Aug 6 | Showrunner | One agent, two passes — not two agents | Half the prompt surface for the Improvement Agent to search. Split later only if the metrics justify it. |
| Aug 6 | Showrunner | Retime is deterministic arithmetic, no model call | Free and reproducible. When the nudge can't absorb the drift, the existing repair loop is the escalation path. |
| Aug 6 | **System** | **Scoring moves ahead of Gate 2, parallel with Research** | The Showrunner cuts against a real detected beat grid. **Trade: Gate 2 no longer precedes all asset spend.** |
| Aug 6 | Casting | Cache key = archetype + style, with opt-in `distinct: true` | ~95% hit rate on an eval sweep. The override can't wreck sweep economics because it's opt-in. |
| Aug 6 | Casting | 5 core poses for everyone, max 2 Showrunner-requested extras | Core keeps the EDL validatable against a fixed enum for free. Extras keep marginal cost knowable. |
| Aug 6 | Casting | Backgrounds described per shot by the Showrunner, rendered by Casting | Expressive over cheap. **Requires a per-reel background cap and a `desc`-keyed cache**, or it becomes the costliest stage. |
| Aug 6 | Voice | Deterministic on the hot path; model call only on escalation | Reproducible and free to replay, with one prompt left for the Improvement Agent to tune. |
| Aug 6 | Voice | Voice ids assigned by Brief at cast time | Locked before planning, visible at Gate 1. Leaves a distinctness gap — filed. |
| Aug 6 | Voice | Absorption ladder: hold/transition under `max_hold_s`, else escalate | Most drift never reaches the repair loop. `max_hold_s` is a brief param, default 2.0. |
| Aug 6 | **System** | **Repairs are scoped — only the failing span changes** | Cheap, no collateral drift, and round 2 can't undo round 1. Applies to every agent, not just Voice. |
| Aug 6 | Scoring | Pre-scored library, not generated music | Beat grid becomes ground truth, so every `beat_alignment` failure is a real planning failure. **Lyria leaves the stack.** |
| Aug 6 | Scoring | Scoring proposes the drop; the outline places its turn there | The turn lands on a real musical event. Brief's `drop_at_s` demotes to a selection hint. |
| Aug 6 | Scoring | Explicit ducking envelope, applied by a deciding-nothing Compositor | `music_ducking` verifies a real claim instead of confirming its own arithmetic. |
| Aug 6 | **System** | **Scoring splits: `6a` select runs first of all, `6b` envelope runs after Voice** | Forced by the above — the grid is needed before planning, the envelope can't exist before the VO spans. |
| Aug 6 | Improvement | Search space = thresholds + prompt variants; topology and routing frozen | Expressive enough to find real wins, small enough to sweep honestly in a month. |
| Aug 6 | Improvement | 🔒 **Grader prompts and thresholds are unmutable** | Otherwise the cheapest way to raise the pass rate is to make the judge lenient — and it will find that first. |
| Aug 6 | Improvement | One mutation per sweep, aimed at the highest-frequency violation | Strict A/B is what lets the writeup claim *this change caused this gain*. |
| Aug 6 | Improvement | Auto-promote iff pass rate ↑, cost not ↑, no rule regressed >2% | Runs unattended overnight — the demo — with a floor against trading cost for aggregate gain. |
| Aug 6 | Improvement | Reads MCP aggregates + best/worst trajectory pair | Bounded context, most of the diagnostic value of reading failure logs. |
| Aug 7 | Casting | 6 sprites per character — 5 poses + 1 mouth-open — moved by arithmetic | Output FPS is free; only distinct drawings cost. Limited animation is the right look for flat-vector. |
| Aug 7 | Compositor | **30fps**, 3-frame mouth hold (5Hz cycle), pinned in config and recorded per run | Divides evenly into the syllable band. Sets a ±16.7ms floor on beat precision — fine against a 60ms threshold, limiting below ~35ms. |
| Aug 7 | Casting | ⚠️ **Backgrounds revert to a library**; per-shot generation moves behind `--bespoke-bg` | The $100 ceiling. Per-shot descriptions barely cache — they'd have become the costliest stage right after characters hit ~$0. |
| Aug 7 | **System** | **Hard per-stage dollar caps, checked before execution; no phase pools** | Guards against one runaway trajectory at 3am, not gradual overspend. Run total `$0.110`. |
| Aug 7 | System | Breach behaviour differs by stage: cheap-and-early **abort**, expensive-and-late **degrade** | Aborting a $0.005 Brief is free; aborting a $0.025 script pass discards everything already spent. |
| Aug 7 | System | **QC graders never degrade** — out of budget voids the run | The pass rate is the product. A partial judge is worse than no judge. |
| Aug 7 | System | Repair is a run-level **pool**, not a per-round cap | Round costs vary wildly. A pool allows three cheap repairs or stops after one expensive one. |
| Aug 7 | System | Cold-archetype sprite generation is tracked **outside** the per-run budget | Otherwise the first run of every sweep aborts and you debug a working system. |

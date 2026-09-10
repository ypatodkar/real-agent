# Build log — the Interview

Working checklist. Design lives in [claude-agent-plan.md](claude-agent-plan.md);
this is the order of operations and the record of what is done.

**Rule for this build:** every phase before 3 must be provable with no API key.
The loop is easy and the prompt is hard — so everything mechanical is finished
and tested before a prompt is written.

**Status:** Phases 0–3 complete. 18 tests pass offline. The loop runs live.
Next: Phase 4, the CLI.

---

## Phase 0 — Fixtures: the spec by example

Ten conversations, drawn from real transcripts in `../store/`, each naming what
the editor should do. These are the specification; the router table and the
validation list are derived from them.

- [x] `fixtures/conversations.py` — ten cases with expected intents
- [x] Cases cover: opening, concrete answer, vague answer, explicit options
      request, suggestion selection, direct question to the editor, contradiction,
      repeated "I don't know", skip, outline-ready
- [x] Each case names the allowed intents and the facts that should be staged
- [x] Loadable without a database or a model

**Done when:** the ten cases are data, not prose, and every later phase can run
against them.

---

## Phase 1 — Schemas and storage

- [x] `schema.sql` — sessions with revision, events with idempotency key,
      responses, suggestions + selections, facts with evidence and status,
      gaps, readiness assessments, runs, steps, telemetry outbox
- [x] `models.py` — typed `Event`, `TurnDecision`, `Response`, `Fact`,
      `GapChange`, `Readiness`, `Suggestion`; parse and serialise, no I/O
- [x] `store.py` — the only module that writes SQL
- [x] Facts are append-only with Active / Superseded / Retracted
- [x] Suggestions carry stable IDs and a status
- [x] `readiness_assessments` is append-only
- [x] Revision increments on commit
- [x] Tests: round-trip every model, supersede a fact, replay an event by key

**Done when:** the data model holds a whole conversation with no model involved.

---

## Phase 2 — The harness, on a stub

The entire reliability story, offline and deterministic.

- [x] `snapshot.py` — one function builds the only context the model sees
- [x] `router.py` — event kind → allowed intents; options can only suggest
- [x] `continuity.py` — conflicts computed before the prompt, into the snapshot
- [x] `validate.py` — structural checks, each component independent
- [x] `fallback.py` — event-specific, never a question for an options request
- [x] `harness.py` — TX1 → snapshot → model → validate → repair → TX2
- [x] Revision check at commit; stale revision re-evaluates once
- [x] Idempotent replay returns the committed response
- [x] Partial failure rule: valid response commits, invalid updates dropped
      and recorded; readiness is all-or-nothing
- [x] Tests: all ten fixtures pass with a stub model, plus replay, revision
      conflict, repair, and every fallback row

**Done when:** ten conversations run end to end, offline, in milliseconds.

---

## Phase 3 — The model adapter and the prompt

- [x] `model.py` — discriminated JSON action schema, not native tool calling;
      Vertex / AI Studio / stub; token counts and micro-USD cost
- [x] `budget.py` — reserved before every call, micro-USD, wall-clock ceiling
- [x] `recorder.py` — verbatim payloads, one file per run
- [x] `prompt.py` — system brief and the snapshot rendering
- [x] `behaviour.py` wired into the loop: facts-per-answer, consecutive-suggestion
      count, readiness movement — recorded on the run, never blocking
- [x] First live turn end to end
- [x] Repair rate measured over real turns

**Done when:** a live turn takes one model call and stages the facts its answer
settled. — **met.**

### Measured, 2026-09-02, gemini-2.5-flash

Eight turns, covering start, three answers, a direct question to the editor, a
contradiction, an options request and a skip:

| | First run | After the prompt fix |
|---|---|---|
| Model calls per turn | 1.25 | 1.00 |
| Repair rate | 25% | 0 of 4 on the two intents that had failed |
| Cost per turn | 959 micro-USD | 696 micro-USD |
| Median latency | 6.5s | 3.1s |
| p95 latency | 8.2s | — |
| Degraded turns | 0 of 8 | 0 of 4 |

Both repairs had one cause: the model wrote its prose into `question` instead of
into `blocks`, which overflowed the 320-character limit and left the required
block missing. One paragraph in the prompt — "`question` holds one question and
nothing else" — removed both.

**p95 of 8.2s is over the 6s target.** Not addressed yet; the worst turn was a
skip at 15s, which is provider variance rather than extra calls.

---

## Phase 4 — CLI

- [ ] `cli.py` — emits typed events, prints every step, shows cost and timing
- [ ] `/options`, `/skip`, `/revise`, `/trace`, `/quit`
- [ ] Resume by session id

**Done when:** a full interview can be run and watched from the terminal.

---

## Phase 5 — HTTP server and UI

- [ ] Threaded server; GET is read-only and never advances the interview
- [ ] Structured events from buttons; no synthetic prose in the transcript
- [ ] Pending → Working → Committed states; previous response stays visible
- [ ] Suggestion cards send IDs; composer text survives an options request
- [ ] Resumable active interviews from server state

**Done when:** a refresh costs nothing and changes nothing.

---

## Phase 6 — Speech to text

Voice is an input adapter, not an agent mode. In this build from the start.

- [ ] `speech.py` — WebSocket bridge to Google Cloud STT v2, streaming
- [ ] Browser sends `{type:"start", sample_rate}`, then LINEAR16 frames, then
      `{type:"stop"}`; server returns interim and final transcripts
- [ ] Audio is never written to disk
- [ ] Mic enabled only after a successful handshake
- [ ] States surfaced: unavailable, permission, connecting, listening,
      finishing, complete, failed
- [ ] Transcript lands in the composer, editable, never auto-submitted
- [ ] Conflicting controls disabled while capturing

**Done when:** dictation fills the composer and the writer still presses Send.

---

## Phase 7 — Evaluation and metrics

- [ ] The ten fixtures run against the live model as a scored suite
- [ ] Scored: invention, semantic repetition, contradiction handling,
      suggestion usefulness, readiness evidence, appropriate stopping
- [ ] Metrics: repair rate, facts per answer, p95 latency, micro-USD per turn
- [ ] Replay from recorder files at zero cost

**Done when:** a prompt change can be shown to improve or regress the interview.

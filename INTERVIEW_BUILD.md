# Second Unit Interview — Build Runbook

**Purpose:** This is the implementation contract and living checklist for the
new Interview-only application. It is written for the coding agent building the
system. Check an item only after its behavior is implemented and verified.

**Build started:** 2026-09-02  
**Scope:** Interview, speech-to-text, and the Outline handoff/UI shell.  
**Source product design:** `CODEX_AGENTIC_FILM_HARNESS.md`

## 1. Non-negotiable outcome

Build a persistent Story Editor that helps a filmmaker turn an early idea into
enough accepted, visible story material for a first outline. It must feel like
an informed collaborator, not a questionnaire. It may ask, reflect, coach, and
offer selectable ideas. It must never turn an unselected model idea into story
canon.

The complete interaction boundary is:

```text
one explicit filmmaker event
→ persist the event
→ load canonical state
→ make one typed Story Editor decision
→ validate it
→ atomically publish one response and supported state updates
→ stop and wait for the filmmaker
```

## 2. Hard scope boundary

### 2.1 Included now

- Persistent Interview projects and sessions in SQLite.
- Complete resumable conversation history.
- One focused opening set whose size Gemini chooses from 3–10 based on the
  clarity of the starting material.
- A simple, model-written explanation of at most 18 words under every opening
  question.
- One-screen answers submitted together as one typed event.
- Collapsible questions with persistent, question-specific idea branches.
- A post-interview summary and navigable Outline workspace shell.
- Typed, idempotent filmmaker events.
- A bounded Story Editor decision loop.
- Questions, reflections, coaching, selectable suggestions, and outline
  recommendations.
- Story facts with filmmaker evidence.
- Open story gaps and evidence-backed readiness.
- A compact assisted UI with suggestions as checkboxes.
- Editable speech-to-text input for both the starting idea and later messages.
- Offline deterministic behavior when no model credentials are configured.
- Tests for persistence, event identity, validation, canon, failure recovery,
  and API behavior.

### 2.2 Deliberately deferred

- Outline generation/agent logic and every later production stage.
- A global supervisor agent or multiple creative agents.
- Web research and external task/calendar tools.
- Vector databases and embeddings.
- Grafana and external content telemetry.
- Background autonomous conversation.
- Automatic acceptance of assistant-authored story material.
- Audio retention, speaker identification, and automatic message submission.

## 3. Product rules the implementation must enforce

1. The filmmaker is the only authority who can accept a creative suggestion.
2. Button actions are typed events, never fabricated chat sentences.
3. Every active story fact cites a writer message or accepted suggestion.
4. A normal event gets one model call and at most one repair attempt.
5. No database transaction remains open during a model or speech-provider call.
6. GET requests are read-only.
7. Repeating an event ID replays its stored result instead of running twice.
8. The opening response contains the complete agent-sized question set. Later
   responses contain no more than one main question.
9. Requesting options must produce two or three structured options or a clear,
   options-specific retry state.
10. Selecting a checkbox does nothing until **Use selected** is pressed.
11. The agent may recommend an outline but cannot start it.
12. Voice transcription remains editable and is never sent automatically.
13. A provider failure cannot erase the filmmaker's event or partially update
    canonical story state.
14. The application must work locally without model credentials using an
    honest deterministic fallback.
15. Gemini chooses the smallest useful set from 3–10. That resulting count is
    the internal pacing boundary, not a measure of story readiness.
16. The filmmaker can finish the Interview at any time. Open gaps are carried
    into the outline rather than blocking progress.

## 4. Technical shape

### 4.1 Runtime

- Python 3.10-compatible standard-library HTTP application.
- `ThreadingHTTPServer` so one slow request does not block all browser traffic.
- Loopback-only binding plus strict `Host` and `Origin` validation to prevent
  DNS-rebinding or cross-origin access to the unauthenticated local API.
- SQLite in WAL mode, foreign keys enabled, and a busy timeout.
- Vanilla HTML, CSS, and JavaScript with no build step.
- Optional Gemini adapter through the installed `google-genai` package.
- Optional Google Cloud Speech-to-Text V2 adapter through the installed
  `google-cloud-speech` package.

### 4.2 Module boundaries

```text
codex-second-unit/
├── INTERVIEW_BUILD.md          living runbook
├── README.md                   setup and run instructions
├── requirements.txt            explicit runtime dependencies
├── run.py                       application entry point
├── second_unit/
│   ├── domain.py               event and TurnDecision contracts
│   ├── database.py             schema, transactions, and repository reads
│   ├── model.py                Gemini and deterministic adapters
│   ├── agent.py                prompt, routing, fallback, and validation
│   ├── service.py              idempotent two-transaction event harness
│   ├── speech.py               same-server speech transcription adapter
│   └── server.py               JSON API and static-file delivery
├── static/
│   ├── index.html
│   ├── app.js
│   ├── pcm-worklet.js
│   └── style.css
└── tests/
    ├── test_database.py
    ├── test_domain.py
    ├── test_model.py
    ├── test_run.py
    ├── test_service.py
    ├── test_server.py
    └── test_speech.py
```

The web server must not contain creative decision logic. The model adapter must
not write state. The agent stages a decision in memory. The service validates
and commits it through the repository.

## 5. Canonical data model

### 5.1 `interview_sessions`

- `id`, `title`, `seed`, `storytelling_format`, `involvement_mode`
- `question_target`: stores the size Gemini chose after the opening response;
  the wider 3–20 constraint remains for database compatibility
- `status`: `active`, `ready_for_outline`, `archived`
- `revision`: optimistic concurrency counter
- `readiness_score`, `readiness_reason`
- `current_response_id`
- timestamps

### 5.2 `interview_events`

- Stable `id` supplied by the client and globally unique.
- `session_id`, `kind`, exact JSON payload, `expected_revision`.
- Lifecycle: `pending`, `processing`, `completed`, `failed_retryable`.
- Stored result revision and safe error when applicable.
- Events are immutable after ingest except for lifecycle/result fields.

Supported initial event kinds:

- `interview_started`
- `questionnaire_submitted`
- `question_ideas_requested`
- `message_submitted`
- `suggestions_requested`
- `suggestions_selected`
- `suggestions_rejected`
- `question_skipped`
- `reflection_confirmed`
- `response_continued`
- `continue_interview`
- `interview_finished`
- `decision_revised`

`message_submitted` also carries the filmmaker's explicit purpose: `answer`,
`question`, `correction`, `nuance`, or `instruction`.

### 5.3 `assistant_responses`

- `id`, `session_id`, `event_id`, sequence, primary intent.
- Short guidance/reflection/coaching text.
- Zero or one main question.
- Listening target and focus label.
- Creation timestamp.

### 5.4 `interview_questions`

- Stable question ID, parent response ID, position, question text, focus, and
  the plain-language explanation shown to the filmmaker.
- A question set is immutable once published so saved answers always refer to
  the exact prompt the filmmaker saw.

### 5.5 `suggestions`

- Stable suggestion ID and parent response ID.
- Label, detail, and dramatic consequence.
- Status: `shown`, `selected`, `rejected`, `superseded`.
- Resolution event and optional writer modification.

### 5.6 `story_facts`

- Atomic fact text.
- Status: `active`, `superseded`, `retracted`.
- Source event ID and an exact evidence excerpt.
- Facts proposed by the model without writer evidence are rejected.

### 5.7 `story_gaps` and `readiness_assessments`

- Gaps have stable IDs, a concrete description, impact, lifecycle, and evidence.
- Readiness assessments are append-only and cover protagonist/subject, pressure
  or change, visible events, and ending/final image.
- The session caches only the latest validated readiness state.

### 5.8 `agent_runs`

- One run per processed event.
- Status, base revision, call count, model/backend, prompt version, timings,
  token usage, and safe error.
- Raw story prompts/responses are not persisted in the normal build.

## 6. API contract

### 6.1 Reads

- `GET /api/health` — backend and speech capability, with no secret values.
- `GET /api/interviews` — resumable sessions newest first.
- `GET /api/interviews/{id}` — full canonical session projection.

All reads are side-effect free.

### 6.2 Commands

- `POST /api/interviews` — creates a session and processes
  `interview_started`.
- `POST /api/interviews/{id}/events` — accepts one typed event with:

```json
{
  "event_id": "client-generated UUID",
  "expected_revision": 3,
  "kind": "message_submitted",
  "payload": {"text": "The exact filmmaker message"}
}
```

- `POST /api/speech/transcribe` — accepts a bounded browser recording and
  returns transcript text. It uses the same HTTP server and therefore cannot
  collide with a fixed WebSocket port.

### 6.3 HTTP behavior

- `200`: replayed or successfully processed event.
- `201`: new Interview created.
- `400`: malformed event or unsupported action.
- `403`: request did not come through an allowed loopback host/origin.
- `404`: unknown session.
- `409`: expected revision is stale or another run owns the session.
- `413`: message or audio exceeds the configured limit.
- `422`: valid JSON whose creative action references invalid state.
- `503`: configured provider is unavailable; event remains recoverable.

Text-model provider failures normally produce a conservative local response
and a successful event. A `503` is reserved for an unexpected turn failure
that could not be safely published.

## 7. Story Editor contract

### 7.1 Snapshot automatically supplied

- Seed, format, involvement mode, internal question budget, questions asked, state, and
  revision.
- Latest event.
- Recent responses and exact filmmaker events.
- Recent question history.
- Active facts with sources.
- Current gaps and latest readiness evidence.
- Suggestion history and statuses.

The agent does not call a tool merely to load ordinary state.

### 7.2 One typed `TurnDecision`

```text
TurnDecision
├── response
│   ├── intent: ask | reflect | coach | suggest | recommend_outline
│   ├── guidance
│   ├── suggestions[0..3]
│   ├── question (zero or one)
│   ├── questions[0..question_target]
│   │   └── text + simple explanation + focus
│   ├── focus
│   └── listening_for
├── fact_candidates[]
│   └── text + source_event_id + evidence
├── gap_changes[]
│   └── open | resolve + description + evidence
└── readiness
    └── score + reason + four evidence lenses + recommend_outline
```

### 7.3 Routing policy

1. Honor explicit filmmaker action.
2. Answer a direct filmmaker question before asking another.
3. Preserve corrections and revisions.
4. Surface contradictions before developing dependent material.
5. Address the highest-impact missing visible story decision.
6. Offer concrete help when the filmmaker is uncertain or stuck.
7. Recommend outlining only when the story can support visible scenes.
8. Generate the full agent-sized question set on the opening turn. After its
   answers arrive, synthesize or help without beginning another question round;
   an explicit continue action may request one follow-up.

Hard routes:

- `interview_started` → `ask_questions` with the smallest useful 3–10 count.
- `question_ideas_requested` → `offer_suggestions` inside the addressed
  question branch without replacing the active questionnaire.
- `questionnaire_submitted` → synthesize, coach, reflect, suggest, or recommend;
  never silently begin a second questionnaire.
- `suggestions_requested` → `suggest` only.
- `suggestions_selected` → preserve and validate IDs before agent work.
- `suggestions_rejected` → do not repeat substantially identical options.
- `question_skipped` → change focus instead of rephrasing the same question.
- `continue_interview` → return state to `active` and continue.
- `interview_finished` → stop immediately without a model call and preserve
  accepted material plus open gaps for the outline.

### 7.4 Validator

Validate before publication:

- known response intent and legal event-to-intent route
- 3–10 distinct, single-focus opening questions, with the count selected by the
  model from story clarity rather than a user setting
- one short plain-language explanation for every opening question
- at most one non-compound question on later turns, stored in the question field
- no near-duplicate recent question
- no automatic question after the filmmaker's target without an explicit
  continuation event
- two or three distinct suggestions when intent is `suggest`
- stable and unique suggestion IDs
- consequence/detail present for every suggestion
- all selected/rejected IDs belong to unresolved suggestions
- every fact source exists and is filmmaker-authored or explicitly accepted
- evidence excerpt appears in its source material
- no unselected suggestion is promoted to a fact
- readiness score is bounded and reason/evidence are present
- outline recommendation has no unresolved material contradiction

Response, facts, gaps, and readiness validate as separate components. If the
response is valid but an optional state component fails, preserve the response,
drop only the invalid component, and persist the exact diagnostic. Response
errors receive one repair prompt; if repair fails, use an event-specific local
fallback and commit no model-proposed facts or gaps. Unchanged readiness should
be returned as `null`, especially on options and coaching turns.
Each provider request has a hard deadline and output-token cap. Only one local
server process may own a given database, preventing startup recovery in a
second process from invalidating a live turn.

## 8. Speech-to-text design

### 8.1 User experience

1. User presses the microphone beside a textarea.
2. Browser asks for microphone permission.
3. UI clearly shows `recording` and a timer; send controls are disabled.
4. User presses stop, or capture stops at the configured duration.
5. Browser sends one bounded audio blob to `/api/speech/transcribe`.
6. Google Speech-to-Text returns text.
7. Transcript is appended to existing draft text and remains editable.
8. Nothing is submitted until the user presses **Send**.

### 8.2 Reliability and privacy

- Use an `AudioWorklet` to collect mono PCM16 in memory. This is directly
  compatible with explicit Google decoding and avoids browser codec variance.
- Use one HTTP origin; no second port or long-lived local speech socket.
- Cap recording duration and request bytes both client- and server-side.
- Never write audio to disk or SQLite.
- Reject unsupported content types explicitly.
- Report configuration, permission, empty-audio, provider, and timeout failures
  differently.
- Do not log transcript content in request logs.
- Expose microphone controls only when the health endpoint reports speech
  configured and the browser supports `AudioWorklet`, `AudioContext`, and
  `getUserMedia`.

## 9. UI behavior

The application is one calm workspace rather than many setup screens:

- Top stage line with **Interview** active and later stages visibly locked.
- No question-count setup or progress meter; readiness and open gaps communicate
  useful progress instead.
- Left history drawer for starting and resuming Interviews.
- Center conversation timeline with compact readable typography.
- Assistant response blocks adapted to intent.
- One scrollable opening questionnaire with simple help text, editable typed or
  voice answers, optional blanks, and one **Submit answers** action.
- Accordion behavior keeps one question open at a time. Each question has a
  persistent **Give me ideas** branch whose suggestions remain proposals until
  the filmmaker places one in an answer and submits it.
- Submitting the questionnaire closes its answer controls and shows a compact
  handoff summary of answers plus unresolved questions.
- The summary is a checkpoint, not a forced transition: the filmmaker can edit
  and safely replace the latest answer set, keep developing, or continue.
- The summary follows the conversation scroll and keeps its answer list
  collapsed until the filmmaker chooses **Review answers**.
- **Continue to Outline** finishes the Interview deterministically and opens a
  dedicated Outline shell populated with interview source material. Outline
  generation remains visibly disabled until its agent is implemented, and the
  shell does not preselect acts, sequences, or another story structure.
- Outline provides an explicit **Back to Interview** control, clickable stages,
  and browser Back/Forward history. Its Interview source is collapsed instead
  of permanently consuming a sidebar.
- Suggestion cards with real checkboxes and a single **Use selected** action.
- Persistent composer supporting answer, question, correction, or instruction.
- An always-visible **Finish interview** action; readiness is advice, not a gate.
- **Give me options** preserves draft text and sends a typed event.
- Right story-state panel for accepted facts, current gaps, and readiness reason.
- Pending turns are polled after reload. Failed/retryable and revision-conflict
  states preserve input and expose a retry path.
- On reload, restore the last session from server state; local storage is only a
  convenience pointer plus per-session unsent text, purpose, checkbox choices,
  and suggestion note.

## 10. Verification strategy

### 10.1 Unit tests

- Domain parsing and size bounds.
- TurnDecision validation and route restrictions.
- Question-repeat detection.
- Suggestion identity and selection validation.
- Fact evidence/provenance validation.
- Readiness/state transition rules.
- Speech content-type and size validation without calling Google.

### 10.2 Repository and service tests

- Migration is repeatable.
- Version-one databases gain the question target without losing sessions.
- Session projection reconstructs exact history.
- Duplicate event ID replays one result.
- Stale revision cannot publish.
- Provider failure leaves event retryable and state unmodified.
- Suggestion selection accepts only chosen IDs.
- Unselected suggestions never become facts.
- GET requests do not change row counts or revision.

### 10.3 HTTP tests

- Health, list, create, read, and event endpoints.
- Malformed JSON and payload limits.
- Static application delivery and missing-path behavior.
- Speech unavailable response does not expose credentials.

### 10.4 Manual browser acceptance

- Create an Interview and receive the complete story-specific question set.
- Check that each question has a useful simple explanation, answer the set in
  one submission, then ask for help, request options, and use checkboxes.
- Reload and resume the exact current conversation.
- Open a second tab and verify stale writes receive a recoverable conflict.
- Record speech, stop, edit transcript, and send manually.
- Deny microphone permission and recover without reloading.
- Run without model credentials and verify honest fallback behavior.

## 11. Living implementation checklist

### 11.1 Discovery and decisions

- [x] Confirm the new build lives only in `codex-second-unit`.
- [x] Review the current Interview prompt, session model, API, UI, and tests.
- [x] Review the current Google streaming speech bridge and its fixed-port
  failure mode.
- [x] Confirm Python 3.10, SQLite, `google-genai`, and Google Speech V2 are
  available in the current environment.
- [x] Choose a single Story Editor with deterministic validation.
- [x] Choose same-server HTTP speech transcription instead of a fixed secondary
  WebSocket port.

### 11.2 Foundation

- [x] Create the package and static application structure.
- [x] Define immutable event and TurnDecision domain types.
- [x] Create SQLite migrations and configured connections.
- [x] Implement repository reads and short transaction helpers.
- [x] Add fixture builders and deterministic IDs for tests.

### 11.3 Agent loop

- [x] Implement snapshot construction.
- [x] Implement event routing and limits.
- [x] Persist and enforce the Story Editor's generated question pacing boundary.
- [x] Generate and persist the complete opening question set in one turn.
- [x] Let the Story Editor choose a bounded 3–10 question count from the seed.
- [x] Generate a simple explanation alongside every opening question.
- [x] Implement the Story Editor system prompt and structured response schema.
- [x] Implement Gemini and deterministic model adapters.
- [x] Implement independent TurnDecision validation.
- [x] Preserve valid responses when optional facts, gaps, or readiness fail.
- [x] Persist validation and fallback diagnostics with completed agent runs.
- [x] Implement event-specific fallbacks.
- [x] Implement TX1 ingest, out-of-transaction decision, and TX2 publish.
- [x] Implement duplicate replay and revision conflict behavior.

### 11.4 API and UI

- [x] Implement read-only health, session-list, and session-detail endpoints.
- [x] Implement create-session and typed-event endpoints.
- [x] Implement full history and resume behavior.
- [x] Build the compact Interview workspace.
- [x] Remove the question-count setup and progress meter from the filmmaker UI.
- [x] Add the one-screen questionnaire and submit its answers as one event.
- [x] Make questions collapsible and add isolated per-question idea branches.
- [x] Add the post-interview handoff summary and close the answer composer.
- [x] Let the filmmaker revise the latest submitted answer set from the summary.
- [x] Build the navigable Outline workspace shell without fake generation.
- [x] Keep the Outline shell structure-neutral until its agent proposes a form.
- [x] Add a deterministic Finish Interview exit that carries open gaps forward.
- [x] Build intent-aware assistant blocks and checkbox suggestions.
- [x] Preserve composer drafts across options and failures.
- [x] Build story-facts, gaps, and readiness panels.

### 11.5 Speech-to-text

- [x] Implement Google Speech V2 batch transcription without audio persistence.
- [x] Implement bounded same-origin audio upload.
- [x] Implement microphone recording, stop, timer, and UI state machine.
- [x] Append editable transcript without auto-submit.
- [x] Add unavailable, permission, timeout, empty, and provider failure states.

### 11.6 Quality and release

- [x] Add domain and validator tests.
- [x] Add database and service recovery tests.
- [x] Add HTTP tests.
- [x] Run all tests successfully.
- [x] Start the server and pass health checks.
- [ ] Complete manual browser acceptance for the core path.
- [ ] Complete manual speech acceptance with configured Google credentials.
- [x] Document exact setup, run, and troubleshooting steps.
- [x] Reconcile every checked item against observed behavior.

## 12. Build log

### 2026-09-02

- Established the Interview-only boundary.
- Audited the current code and preserved only architectural lessons.
- Chose canonical SQLite events, a bounded single-agent decision, stable
  suggestion identity, and same-server speech upload.
- Created this runbook before implementation, as required.

### 2026-09-04

- Built the isolated Python, SQLite, HTML, CSS, and JavaScript application.
- Added the bounded Story Editor prompt, deterministic fallbacks, exact canon
  provenance, readiness evidence checks, typed actions, current-response
  validation, idempotent replay, and crash recovery.
- Added same-origin PCM16 speech capture and Google Speech-to-Text V2 with no
  audio persistence or automatic message submission.
- Added per-Interview drafts, complete timeline/history, checkbox suggestions,
  pending-turn polling, saved-turn retry, and project-switch isolation.
- Passed 94 automated tests and a real offline server smoke test covering
  health, static UI delivery, Interview creation, options, selection, and canon.
- Added loopback-only serving, same-origin request enforcement, model and
  speech deadlines, and one-process-per-database ownership.
- Added the missing question-count bar, persisted its 3–20 target in SQLite,
  exposed live progress, and made the agent pause at the target unless the
  filmmaker explicitly continues.
- Manual browser interaction and live microphone transcription remain
  deliberately unchecked until performed with a real browser and microphone.

### 2026-09-05

- Split validation into response, facts, gaps, and readiness components so an
  unsupported readiness quote cannot discard valid model options or coaching.
- Told the model to omit unchanged readiness; strengthened validation against
  compound questions and questions hidden inside guidance.
- Made deterministic fallbacks quote the current story context and answer the
  common subplot question directly instead of returning generic boilerplate.
- Persisted validation diagnostics in `agent_runs.safe_error` and exposed them
  on each timeline event for reproducible debugging.
- Migrated the live database to include `question_target`, added a UI fallback
  for old serialized sessions, and verified both options and direct coaching
  against the configured Vertex model on port 8010 without fallback.
- Added an always-available, model-independent finish action so the filmmaker
  can enter the outline with unresolved gaps instead of being trapped in more
  interview questions.
- Rewired the opening from sequential questions to one Gemini-generated set.
  Every question now includes simple help text, has a stable SQLite identity,
  supports its own voice/typed answer, and the completed answers are submitted
  together as a single `questionnaire_submitted` event.
- Removed the question-count slider and numeric progress display because the
  full set is already visible and question totals are not story readiness.
- Made Gemini choose the smallest useful question set from 3–10, and added an
  accordion workflow with persistent `question_ideas_requested` branches that
  never replace the main questionnaire.
- Added the Interview summary-to-Outline transition. The Outline screen now
  displays the seed, accepted answers, and carried-forward gaps; generation is
  disabled until the Outline agent is built.

### 2026-09-07

- Turned the handoff summary into a real choice point: **Continue to Outline**,
  **Edit answers**, or **Keep developing**.
- Added typed `questionnaire_revised` events. A revision targets only the latest
  submitted answer set, replaces facts and gaps derived from it, and preserves
  the original questionnaire plus its question-specific idea branches.
- Removed the placeholder Opening/Development/Turn/Ending lanes. The future
  Outline agent must propose a suitable structure before editable beats appear.
- Audited the Interview-to-Outline UI: compacted the handoff, moved it into the
  normal conversation scroll, collapsed the Outline source, added an explicit
  back control, and made browser Back/Forward restore the selected stage.

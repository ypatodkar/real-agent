# Second Unit — Outline Foundation

## 1. Current scope

This phase implements the deterministic foundation for Outline. It does not yet
ask Gemini to propose structures or render editable beat cards in the browser.

Implemented now:

1. A versioned SQLite Outline attached one-to-one to an Interview.
2. Multiple structure proposals with one filmmaker-selected structure.
3. Ordered, editable, approval-aware story beats.
4. Recoverable beat archiving rather than destructive deletion.
5. Immutable snapshots after every successful Outline mutation.
6. Typed internal tool contracts for the future Outline agent and UI.
7. Same-origin typed HTTP routes for reading the handoff and running Outline tools.
8. Real structure proposal cards and filmmaker selection. Preset structures need
   only one click; their labels and explanations are supplied by the system.
   Only a custom structure asks the filmmaker to describe anything.
9. A bounded Gemini Outline-agent call that proposes 3–10 story-specific beats.
10. Atomic beat-set validation and persistence: invalid model output saves no beats.
11. Beat cards with editing, reordering, acceptance, rejection, recoverable removal,
    and filmmaker-created beats.
12. Explicit whole-Outline approval. An approved Outline must be reopened before
    any structure or beat can change.

## 2. Data model

### 2.1 `outlines`

Owns the Outline revision, lifecycle status, and selected structure. Its
revision is separate from the Interview revision so the two stages cannot
silently overwrite one another.

### 2.2 `outline_structures`

Stores up to five proposed forms, such as acts, sequences, visual progression,
narration-led, hybrid, or custom. A proposal records its origin and acceptance
state. Only one can be accepted at a time.

### 2.3 `outline_beats`

Stores ordered story beats with title, visible action summary, dramatic
purpose, origin, source references, and approval state. Deletion changes a beat
to `archived`; it does not erase it.

### 2.4 `outline_approvals`

Records explicit filmmaker decisions separately from the current object state.
This preserves who accepted, rejected, or reopened a structure or beat.

### 2.5 `outline_versions`

Stores a full JSON snapshot at revision 0 and after each successful mutation.
Failed validation and stale writes create no version and no partial state.

## 3. Internal tool boundary

The tool boundary is implemented in `second_unit/outline.py`.

1. `read_interview_handoff` — read-only Interview answers, facts, gaps, and the
   current Outline.
2. `propose_story_structure` — add one visible proposal; it cannot select it.
3. `select_story_structure` — filmmaker-only acceptance of a structure.
4. `create_beat` — add an ordered beat. Agent beats stay proposed; filmmaker
   beats are accepted.
5. `edit_beat` — update selected fields without allowing an agent to rewrite a
   filmmaker-decided beat.
6. `move_beat` — atomically reorder and normalize positions.
7. `archive_beat` — recoverable soft deletion.
8. `record_beat_decision` — filmmaker-only accept, reject, or reopen.

Every write requires `expected_revision`. Tool caller identity is supplied by
the harness, never trusted from model arguments. This prevents an agent from
claiming to be the filmmaker to approve its own work.

## 4. Next build step

1. Let Gemini recommend a small set of story-specific structures through
   `propose_story_structure`; it must not choose one for the filmmaker.
2. Add focused regenerate/alternative-beat workflows without overwriting a reviewed
   outline.
3. Add continuity and production-scope checks without blocking creative choices.
4. Build the Screenplay handoff from the approved Outline snapshot.

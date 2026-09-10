# Second Unit — Interview

This folder contains the clean Interview-only build. It is independent of the
older application in the repository.

Implemented here:

- persistent SQLite Interview history
- one bounded Story Editor decision per filmmaker event
- component-level validation that preserves valid responses when optional state updates fail
- one Gemini-sized opening question set, with a simple explanation under every question
- one-submit questionnaire answers, coaching, reflections, structured options, and readiness
- a focused opening question set with no question-count setup or progress meter
- collapsible questions with a saved, question-specific **Give me ideas** branch
- a compact, editable Interview handoff and reversible stage navigation
- a structure-neutral Outline workspace with its source material collapsed by default
- versioned structure proposal cards and explicit filmmaker structure selection
- Gemini-generated, editable beat proposals with per-beat filmmaker decisions
- explicit Outline approval before screenplay handoff
- an approved-Outline-only Screenplay workspace with four assisted writing modes
- atomic Gemini scene drafting, versioned scene editing, and explicit Screenplay approval
- an approved-Screenplay-only Scene Breakdown workspace with editable, scene-grouped requirements
- Google Maps location scouting by production base and maximum driving time
- an always-available Finish Interview action that carries open questions forward
- suggestion checkboxes with stable IDs and explicit acceptance
- evidence-backed story facts
- retry-safe events and optimistic session revisions
- bounded model calls and one-process-per-database protection
- same-origin Google Speech-to-Text with no audio storage
- deterministic offline behavior when no model is available
- persisted per-turn validation diagnostics for debugging model fallbacks

The Outline workspace does not assume acts or a fixed beat shape. Its controlled
local API can save up to five structure proposals and only a filmmaker action can
select one. After selection, Gemini can propose an atomic set of editable story
beats. Only the filmmaker can accept beats and approve the complete Outline.
Screenplay review and the first Production Breakdown slice are built. Export,
scheduling, shot lists, and readiness are not built yet. See
[`SCREENPLAY_BUILD.md`](SCREENPLAY_BUILD.md) and [`BREAKDOWN_BUILD.md`](BREAKDOWN_BUILD.md).

The versioned SQLite schema and controlled internal Outline tools are now in
place. Their current scope and next step are documented in
[`OUTLINE_BUILD.md`](OUTLINE_BUILD.md).

## Run

Python 3.10 works, although Python 3.11 or newer avoids Google's Python 3.10
end-of-support warning.

```bash
cd "/Users/yashpatodkar/Documents/AI agents Project/codex-second-unit"
python -m pip install -r requirements.txt
python run.py
```

Open <http://127.0.0.1:8010>.

The new app defaults to port `8010`, so it can coexist with the older app on
`8000`. There is no speech server on `8001`. The unauthenticated server is
intentionally loopback-only and rejects cross-origin requests.

Use another port if necessary:

```bash
python run.py --port 8011
```

Run without either Google service while developing the UI:

```bash
SECOND_UNIT_OFFLINE=1 python run.py
```

Offline mode uses the deterministic Story Editor fallback and disables the
microphone honestly. Typing, persistence, options, selections, and history
still work.

## Google configuration

The application reads `codex-second-unit/.env` first and the repository's
parent `.env` second. Existing environment variables always win.

For Vertex AI:

```text
SECOND_UNIT_BACKEND=vertex
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

Vertex and Speech-to-Text use Application Default Credentials. Existing local
ADC from `gcloud auth application-default login` is sufficient. Speech also
requires the Cloud Speech-to-Text API to be enabled for that project.

For Gemini Developer API text generation:

```text
SECOND_UNIT_BACKEND=aistudio
GOOGLE_API_KEY=your-key
```

Location scouting uses separate browser and server credentials:

```text
MAPS_BROWSER_KEY=browser-restricted-key
MAPS_SERVER_KEY=server-restricted-key
```

Restrict the browser key by HTTP referrer and allow only Maps JavaScript API.
Never expose the server key; allow it to call Places API (New), Routes API, and
Geocoding API. The older `GOOGLE_MAPS_BROWSER_KEY` and
`GOOGLE_MAPS_SERVER_KEY` names remain supported as compatibility aliases.

The app stores Google Place IDs and shortlist state in SQLite. Names, addresses,
coordinates, route distance, and route duration remain transient search results.

An AI Studio key does not configure Google Cloud Speech-to-Text. Voice typing
still needs `GOOGLE_CLOUD_PROJECT` and ADC.

Optional overrides:

```text
SECOND_UNIT_TEXT_MODEL=gemini-2.5-flash
SECOND_UNIT_TEXT_TIMEOUT_SECONDS=20
SECOND_UNIT_SPEECH_MODEL=latest_long
SECOND_UNIT_SPEECH_TIMEOUT_SECONDS=60
SECOND_UNIT_PORT=8010
SECOND_UNIT_DATABASE=/absolute/path/to/interviews.db
```

## Voice behavior

The browser captures mono PCM16 for at most 55 seconds and posts it to the same
HTTP server. The server sends the in-memory bytes to Google Speech-to-Text V2,
returns the transcript, and discards the audio. Audio is never written to a
file or SQLite.

The transcript is appended to the existing draft and remains editable. It is
never submitted to the Story Editor automatically.

If the microphone is disabled, hover it for the configuration reason. Typing
always remains available.

## Data

The default database is:

```text
codex-second-unit/data/interviews.db
```

All canonical conversation state is stored there. Browser storage keeps only
the last active Interview ID and per-Interview unsent UI state (draft text,
message mode, and unresolved checkbox choices) as a convenience.

Gemini chooses the smallest useful set between 3 and 10 questions, then explains
each question in simple language. The questions form an accordion so the
filmmaker can work through one at a time. Every question has its own **Give me
ideas** branch; those ideas stay attached to that question and can be copied
into the editable answer. The filmmaker submits the answers together, and
uncertain answers may be left blank. After that, the Story Editor synthesizes
and helps instead of starting another round.
The filmmaker can still request options, ask for help, explicitly continue, or
finish with open gaps. Perfect readiness is never required to leave Interview.

## Troubleshooting

- **Port already in use:** stop the older process or run `python run.py --port
  8011`. This app never needs port `8001`.
- **Database already open:** use the existing Second Unit process, stop it, or
  pass a different `--database`. One process owns a database at a time so
  startup recovery cannot invalidate a live turn.
- **Microphone disabled:** check `GOOGLE_CLOUD_PROJECT`, Application Default
  Credentials, and the Speech-to-Text API. The health check reports local
  configuration; Google credentials, quota, and API enablement are confirmed
  by the first transcription.
- **Microphone denied:** allow microphone access for `127.0.0.1` in the browser
  site settings, then reload. Typed input remains available throughout.
- **Python 3.10 warning:** it is currently a dependency warning, not an app
  failure. Upgrade to Python 3.11+ before Google's stated October 2026 cutoff.

## Tests

```bash
python -m unittest discover -s tests -v
node --check static/app.js
node --check static/pcm-worklet.js
```

The living implementation contract and checklist are in
[`INTERVIEW_BUILD.md`](INTERVIEW_BUILD.md).

# Second Unit — current flow

**What is actually built and running, as of 2026-08-31 (branch `consolidate-architecture`).**

This is a description of the code as it stands, not the plan. Where the code and
`SCOPE.md` disagree, the code wins here — the gaps are listed at the bottom, and
they are the honest starting list for the Screenplay stage.

Rendered with Mermaid: VS Code previews it with the Markdown Preview Mermaid
extension, GitHub renders it natively.

---

## 1. System map

```mermaid
flowchart LR
    subgraph browser["Browser — no build step, plain JS"]
        UI["index.html<br/>4 stage views:<br/>start · interview · outline · future"]
        JS["app.js<br/>render, post, speech capture"]
        CSS["style.css"]
    end

    subgraph server["app/server.py — stdlib HTTPServer, single-threaded, :8000"]
        H["Handler<br/>do_GET / do_POST"]
        NQ["next_question<br/>the ask loop"]
        WU["write_up<br/>outline assembly"]
        ST["state_of<br/>one JSON shape for the whole UI"]
    end

    subgraph core["core/"]
        INT["interview.py<br/>SYSTEM prompt, shapes,<br/>build_prompt, parse, Session"]
        SW["scriptwright.py<br/>readiness, outline prompt,<br/>SCENES_SCHEMA, parse"]
        MOD["model.py<br/>backend selection + generate"]
        TEL["telemetry.py<br/>Loki write · MCP read"]
        SPE["speech.py<br/>WebSocket ↔ STT bridge"]
        DB["database.py<br/>schema + migrate"]
    end

    subgraph ext["External"]
        VTX["Vertex AI<br/>gemini-2.5-flash"]
        LOKI["Grafana Cloud Loki<br/>push API"]
        MCP["Grafana MCP server<br/>stdio, LogQL"]
        STT["Google Cloud STT v2"]
    end

    subgraph disk["store/"]
        SESS["p_*.json<br/>live session"]
        OUT["p_*-outline.json<br/>outline"]
        SQL["second_unit.sqlite3"]
    end

    UI --> JS
    JS -->|"fetch /api/*"| H
    JS -.->|"ws://127.0.0.1:8001 PCM frames"| SPE
    SPE <--> STT

    H --> NQ
    H --> WU
    H --> ST
    NQ --> INT
    WU --> SW
    INT --> MOD
    SW --> MOD
    MOD --> VTX
    NQ -->|"rank_shapes_sync"| TEL
    H -->|"record outcome"| TEL
    TEL --> LOKI
    TEL --> MCP
    MCP --> LOKI

    H <--> SESS
    WU --> OUT
    H <--> SQL
    DB -.->|"creates schema at import"| SQL

    style ext fill:#f6f6f4,stroke:#ccc
    style disk fill:#f6f6f4,stroke:#ccc
```

The one surprise in that picture: **`model.py` never talks to `telemetry.py`.**
Question quality and cost are measured through Loki, but the model call itself
is not instrumented — no token counts, no latency, no cost per turn.

---

## 2. The interview loop

Every writer action goes through the same shape: record what happened, ask
Grafana what has worked, ask Gemini for the next move, validate it hard, save.

```mermaid
sequenceDiagram
    autonumber
    participant W as Writer
    participant JS as app.js
    participant S as server.py
    participant G as Grafana MCP
    participant M as Vertex
    participant L as Loki
    participant F as store/p_*.json

    W->>JS: Answer / Skip / Give me options
    JS->>S: POST /api/project/{id}/answer
    Note over S: turn.answer = text<br/>turn.words = len(split)

    S->>L: record(Outcome) — shape, stage, words, seconds
    Note right of L: words written is the signal:<br/>a good question produces paragraphs

    S->>G: rank_shapes_sync for stage dynamic
    G-->>S: shape ids ordered by words produced

    loop up to 3 attempts
        S->>M: build_prompt + QUESTION_SCHEMA, temp 0.9
        M-->>S: JSON assessment
        S->>S: interview.parse — validate
        Note over S: rejects: leading question,<br/>two questions, near-duplicate,<br/>missing suggestions when demanded
    end

    alt parse succeeded
        S->>S: apply_assessment — readiness, gaps, established
    else 3 failures or ProviderError
        S->>S: fallback_question(shape) — hand-written, degraded: true
    end

    S->>F: save session
    S-->>JS: state_of(session, asked)
    JS->>W: question + guidance + suggestion cards
```

### What can stop the loop

```mermaid
flowchart TD
    A["POST /answer"] --> B{"ceiling reached?"}
    B -->|"ai_led ≥5 · collaborative ≥9"| C["complete = true<br/>no question, no options"]
    B -->|no| D["ask the model"]
    D --> E{"model says<br/>should_continue?"}
    E -->|no, and enough answers| C
    E -->|yes| F["next question"]

    G["Give me options<br/>request_suggestions=true"] -->|"force = true"| D

    style G fill:#e8f0ea,stroke:#5a7a63
    style C fill:#f3e6e6,stroke:#a06a6a
```

The green path is the fix from earlier today: an explicit options request sets
`force`, so it outranks both the ceiling and a completion the model calls on
that same turn. Without it, the button silently ended the interview.

---

## 3. Project lifecycle

```mermaid
stateDiagram-v2
    [*] --> Start: open app
    Start --> Interviewing: POST /api/project<br/>seed + format + involvement

    Interviewing --> Interviewing: /answer · /skip
    Interviewing --> Interviewing: /answer with request_suggestions
    Interviewing --> Complete: model stops, or ceiling hit
    Complete --> Interviewing: /continue — force=true

    Interviewing --> Outlined: /write-up<br/>only if readiness() passes
    Complete --> Outlined: /write-up
    Outlined --> Outlined: /edit-outline
    Outlined --> Approved: /approve-outline
    Approved --> Future: production flow nav<br/>screenplay · breakdown · schedule · shots

    Interviewing --> Draft: /abort — summarize and archive
    Complete --> Draft: /abort
    Draft --> [*]

    note right of Future
        Placeholder screens only.
        Nothing downstream is built.
    end note

    note right of Outlined
        readiness() minimums by mode:
        ai_led 2 answers / 20 words
        collaborative 3 / 40
        author_led 4 / 60
    end note
```

---

## 4. Where state actually lives today

Three stores, split by accident of history rather than by design.

```mermaid
erDiagram
    SESSION_JSON ||--o{ TURN_JSON : "turns[]"
    SESSION_JSON ||--o| OUTLINE_JSON : "p_*-outline.json"
    SESSION_JSON ||--o| DRAFTS_ROW : "archived on /abort"

    SESSION_JSON {
        string project_id PK "p_ plus uuid hex"
        string seed
        string storytelling_format "narrated dialogue_led hybrid not_sure"
        int involvement "0-100, derives collaboration_mode"
        float readiness "0-1, READY_THRESHOLD 0.80"
        string readiness_reason
        json critical_gaps
        json established
        int ready_streak
        bool complete
        bool outline_approved
        float updated
    }

    TURN_JSON {
        string shape_id "focus label, also the telemetry key"
        string stage "always dynamic"
        string question
        string guidance
        string response_kind "question reflection suggestions coach"
        json suggestions "id label detail"
        string answer
        int words "the telemetry signal"
        bool skipped
    }

    OUTLINE_JSON {
        string title
        string logline
        json scenes "n slug who action why missing from"
        json gaps
        json ai_added "AI-authored disclosure"
        json unsupported "audit findings"
    }

    DRAFTS_ROW {
        string id PK
        string project_id
        string title
        string summary
        real readiness
        json established_json
        json transcript_json
        real created_at
    }
```

| Store | Path | Holds | Written by |
|---|---|---|---|
| Session files | `store/p_*.json` | the live interview, every turn | `save()` on every request |
| Outline files | `store/p_*-outline.json` | assembled outline | `write_up()`, `save_outline_edit()` |
| SQLite | `store/second_unit.sqlite3` | **only** the `drafts` table | `archive_draft()` on `/abort` |
| Loki | Grafana Cloud | one log line per answered question | `telemetry.record()` |

`drafts` is defined inline in [server.py:45-65](app/server.py#L45-L65), not in
`database.py`. It is the only table in the file that anything reads or writes.

---

## 5. The schema that exists but is idle

`core/database.py` provisions the full production schema at import and then
nothing touches it. This is the target the app has not migrated onto yet — and
the Screenplay stage is where it starts to matter, because `scenes` already
hangs off `screenplay_version_id`.

```mermaid
erDiagram
    projects ||--|| project_settings : has
    projects ||--o{ interview_sessions : has
    interview_sessions ||--o{ interview_turns : has
    projects ||--o{ artifacts : owns
    artifacts ||--o{ artifact_versions : "immutable versions"
    artifacts ||--o{ artifact_dependencies : "stale propagation"
    artifact_versions ||--o{ scenes : "screenplay_version_id"
    projects ||--o{ scenes : has
    scenes ||--o{ scene_elements : tagged
    production_elements ||--o{ scene_elements : tagged
    projects ||--o{ production_elements : has
    projects ||--o{ shoot_days : has
    shoot_days ||--o{ scheduled_scenes : holds
    scenes ||--o{ scheduled_scenes : "scheduled into"
    scenes ||--o{ shots : covered_by
    projects ||--o{ tasks : has
    shoot_days ||--o{ tasks : "due on"
    tasks ||--o{ task_dependencies : blocks
    tasks ||--o{ task_links : "links to any entity"
    projects ||--o{ approvals : gates
    projects ||--o{ assistant_proposals : proposes
    projects ||--o{ agent_runs : records
    projects ||--o{ activity_events : "append-only log"

    projects {
        text id PK
        text phase "development screenplay preproduction production post completed"
        text assistant_mode "observe propose auto_low_risk"
        int target_runtime_minutes
    }
    artifacts {
        text id PK
        text kind
        text status "working in_review approved superseded archived"
        text current_version_id
    }
    artifact_versions {
        text id PK
        int version "UNIQUE per artifact"
        text body_json
        text storage_uri
        text content_hash "UNIQUE per artifact"
        text source "human assistant import system"
    }
    artifact_dependencies {
        text artifact_id PK
        text depends_on_artifact_id PK
        int stale "0/1 — nothing sets this yet"
    }
    scenes {
        text id PK
        text screenplay_version_id FK
        text scene_number "TEXT — allows 12A inserts"
        text slugline
        int page_eighths
        real estimated_minutes
        text status "planned scheduled shot omitted"
    }
    shots {
        text id PK
        text scene_id FK
        text priority "essential useful optional"
        text status "planned ready shot dropped"
    }
    approvals {
        text id PK
        text entity_type
        text entity_version_id "names the exact version"
        text decision "pending approved rejected changes_requested"
    }
    assistant_proposals {
        text id PK
        text status "pending accepted modified rejected expired"
        text risk_level "low medium high critical"
    }
    agent_runs {
        text id PK
        text trigger_type
        text workflow
        int cost_cents
    }
```

---

## 6. Wired vs. present-but-unused

| Piece | State |
|---|---|
| Dynamic interview, suggestions, dictation | **live** |
| Outline assembly, edit, approve | **live** |
| Drafts, history drawer | **live** |
| Loki telemetry write + MCP shape ranking | **live** |
| `database.py` production schema | created every boot, **never read or written** |
| `recorder.py` — verbatim trajectory capture | **not imported anywhere** |
| `budget.py` — pre-flight dollar caps | **not imported anywhere** |
| Screenplay, breakdown, schedule, shots, readiness | **placeholder screens only** |
| Import a screenplay | **not built** — the start-screen card is disabled |

### Consequences worth knowing before the next stage

1. **Two sources of truth.** Live projects are JSON files; the schema that
   everything downstream assumes is SQLite. Screenplay is the first artifact
   with real downstream consumers, so this is where the split stops being free.
2. **`HTTPServer` is single-threaded.** One Gemini call blocks every other
   request, including static files. A screenplay generation pass is far longer
   than a question — this will be felt.
3. **No cost or latency telemetry.** `budget.py` was written for exactly the
   case the screenplay stage introduces: a long, expensive pass where aborting
   halfway wastes everything already spent.
4. **`artifact_dependencies.stale` is unused.** Screenplay → breakdown is the
   first real edge. Proving the propagation once makes stages 3–6 nearly free.

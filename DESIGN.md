# Second Unit

**Prep for short films.** Two things: it helps you find your story by asking
questions, then it turns the script into a shooting plan.

For people making a 5–20 minute short, often doing four jobs themselves.

---

## The idea

Every AI writing tool generates. You type a premise, it writes your story.
The result is the model's film, not yours.

**This one interrogates.** It asks the questions a good script editor asks —
the ones that make you realise what you actually meant — and never proposes the
answer. You do the thinking. It makes sure you think about the right things.

> **Example.** You say: *"a short about a locksmith."*
>
> A generator writes you three logline options.
>
> This asks: *"What can he open that he wishes he couldn't?"*
>
> The second one is how a film gets made.

Then, when there is a script, it does the unglamorous half: tag every element,
group scenes into shooting days, cost it out. Days of work, done in minutes.

---

## Core modes

| | **Develop** | **Break down** |
|---|---|---|
| Input | A fragment — a premise, an image, nothing | A finished script |
| What it does | Asks one sharp question at a time | Tags cast, props, locations, wardrobe, VFX, stunts |
| Output | Scenes, then a script | Schedule, budget, call sheets |
| Feels like | A script editor | An assistant director |

You can start at either end. Bring a script and skip Develop; brainstorm and
flow straight into breakdown.

### Storytelling format in Develop

Before the interview begins, the writer chooses how the audience will hear the
story. This is project context, not just a UI preference, and should shape the
questions asked and every later artifact.

| Format | Meaning | Interview emphasis |
|---|---|---|
| Narrated | Narration carries most of the story, with optional dialogue | voice, point of view, reliability, relationship between words and images |
| Dialogue-led | Characters carry the story through conversation | speakers, tension, subtext, and conversational turns |
| Hybrid | Narration and character dialogue both carry substantial parts | what narration reveals versus what dialogue dramatizes |
| Not sure yet | The form has not been chosen | questions that help the writer discover the appropriate form |

The format choice belongs immediately after **Brainstorm a story** and before
the starting-idea field. It must be persisted with the project and included in
the interviewer and outline prompts.

### Creative involvement

The brainstorm start screen also asks **How involved do you want to be?** with
a continuous control from **AI takes the lead** to **I shape every choice**.
This is separate from outline readiness: it is the writer's authorship contract
with the system.

| Range | Behavior |
|---|---|
| 0–33 · AI-led | Dynamically stop when enough direction exists, usually after 2–4 questions. A five-answer safety ceiling prevents a supposedly quick interview from dragging on. Then develop a complete outline, preserve every supplied decision, and disclose every AI-added detail. |
| 34–66 · Collaborative | Dynamically stop once the important decisions are established, usually after 4–7 questions, with a nine-answer safety ceiling. The AI may bridge minor connective gaps but cannot invent a new central character, goal, conflict, turn, or ending. Disclose every addition. |
| 67–100 · Author-led | Use the fully dynamic interview. The AI asks contextual questions but never supplies story decisions; the outline is assembled only from the writer's material. |

The interviewer itself never embeds suggestions in its questions in any mode.
Permission to add material applies only when producing the outline. AI-added
material is displayed separately in the result and preserved in story history.
The number of questions is never the normal stopping signal: after every answer,
the agent decides whether another question would materially improve the result.
The ceilings exist only for predictable effort and cost when the writer has
explicitly requested low or moderate involvement.

---

## Future option — create a video

Once a script and production plan are ready, offer an optional **Create a
video** phase. This is a later milestone, not part of the current brainstorm
definition of done.

The first useful version creates an approximately two-minute proof-of-concept:

- a sequence of generated or supplied still images
- restrained slideshow motion and transitions
- a generated narrator voice when the selected format uses narration
- character dialogue where the script calls for it
- captions, music and basic sound design
- a final MP4 that can be previewed and downloaded

It should reuse the complete project context already gathered: the writer's
answers, storytelling format, outline, script, characters, locations, visual
direction, breakdown and timing. The user should review the visual plan and
voice choices before rendering so video generation remains an explicit,
optional step.

Proposed product flow:

```text
Choose a path
├── Brainstorm → Interview → Outline → Script
└── Bring a script ─────────────────────┘
                                      ↓
                              Production breakdown
                                      ↓
                         Optional: Create a 2-minute video
```

Deferred decisions for that milestone: image-generation model, narration/TTS
voice controls, dialogue voice casting, music licensing, per-render cost cap,
shot regeneration, captions, and final hosting/storage.

---

## Where Grafana earns its place

*The track requires the Grafana Cloud MCP server to be called at runtime, so it
has to change what the agent does — not decorate the result.*

**Questions are measured.** Every question the interviewer asks is logged with
what happened next: how much the writer wrote, whether they changed direction,
whether they stalled and asked to move on.

After every answer, the agent reassesses the complete conversation: what the
writer has established, what remains critically unclear, and whether an honest
scene outline can be assembled without invention. It then asks about the
highest-impact uncertainty in that particular story. There is no fixed sequence
of premise, character, conflict, structure and scene stages.

Before choosing its next question, the agent also queries Grafana:

> *"At the premise stage, for a thriller — which question shapes have actually
> produced writing?"*

Grafana answers with historically productive focus labels. This is weak
evidence for the agent, not a prescribed choice: current story context wins.
The outcome is logged and feeds the next writer.

> **Example.** *"What does he think he is owed?"* has a 70% follow-through rate
> in thrillers. *"Describe your antagonist's backstory"* sits at 20%. The agent
> asks the first.

That is the whole point of the product improving as it is used, and it is a
genuine runtime dependency rather than a panel.

The loop recommends an outline when readiness is at least 0.80, no critical
gaps remain, at least four substantive answers and 60 words exist, and the
model reaches the same conclusion twice consecutively. The writer may create
an outline earlier (with gaps shown), ask another question after readiness, or
abort and archive the work at any time. Question count alone never completes
or disables an interview; cost control belongs in the model-call budget rather
than in an arbitrary story-length limit. The most recent 15 turns are sent as
active model context while the complete transcript remains stored.

Readiness is an assessment of the complete current story, not a progress bar
that only moves upward. It may decrease when a new answer changes the story or
introduces a consequential uncertainty—for example, revealing that the
narrator has been lying may make the story richer while making its visible
events less settled. A lower score is therefore valid only when the assessment
explains what changed and names the new critical gap. A substantial drop
(15 percentage points or more) should be rejected or reassessed unless at least
one new, concrete critical gap is returned. The UI should present the reason
for the change so the writer never sees an unexplained score fluctuation.

### Abort, archive, and restart

The writer can choose **Save as draft & start over** at any point in the
interview. This is an intentional exit, not an error or deletion. Before the
new session begins, the system performs one final whole-conversation
reassessment and saves a draft containing:

- the starting idea and complete question/answer transcript
- an accurate summary using only the writer's material
- supported story facts and unresolved critical gaps
- recalculated outline readiness and its explanation
- the archive timestamp and originating project ID

Drafts are stored in `store/second_unit.sqlite3`. SQLite is the durable index
for archived work; active sessions remain JSON files for now. If the model is
unavailable during abort, the latest assessment and verbatim answers are saved
deterministically so the user's work is never lost. After a successful archive,
the browser clears the active-project pointer and returns to the initial path
selection as a new chat.

The persistent header includes **Story history**. It combines completed
outlines and archived drafts in reverse chronological order. Opening an item
shows the starting idea, the question-and-answer trace, supported facts,
readiness and unresolved gaps; completed work also shows its scene outline.
History is read-only and does not replace the active interview.

### Voice answers

The answer field offers optional low-latency voice typing. Browser microphone
audio is captured as mono LINEAR16 PCM and sent in small frames over a WebSocket
to the Python backend, which bridges it to Google Cloud Speech-to-Text V2's
bidirectional streaming API. Interim text appears while the writer speaks;
final text remains editable and is never submitted automatically.

Audio is never persisted. A stream stops on user request, after sustained
silence, or at the two-minute hard limit. The microphone disables itself when
Speech-to-Text or Google credentials are unavailable. Production deployment
must proxy the speech WebSocket behind the same HTTPS origin rather than expose
the development `port + 1` socket directly.

**Second use, simpler:** budgets. Costs from previous breakdowns inform the next
estimate — *night exteriors historically run 40% over their first estimate.*

---

## Stack

Google Cloud only, per the rules.

| | |
|---|---|
| Model | Gemini via Vertex — questions, tagging, scheduling |
| Telemetry | Grafana Cloud, via MCP, read *and* written at runtime |
| Frontend | Plain HTML, CSS, JS — no framework |
| Backend | Python standard library |
| Storage | JSON per project on disk |

**No agent framework.** The rules forbid non-Google ones, and the flow is a
conversation plus a short linear pipeline — a loop and five functions.

---

## Taken from v1

Three files, because they encode things that were expensive to learn:

- `model.py` — Vertex auth, the console error codes that look alike and need
  opposite fixes, JSON-mode handling, schema enforcement
- `recorder.py` — append-only run log, verbatim payloads
- `budget.py` — spend caps checked before the call, not after

Everything else is new. v1's quality checks were about video and do not
transfer; what transfers is the habit of checking output against declared rules.

---

## What "done" looks like

A judge, in three minutes:

1. Types a fragment of an idea
2. Is asked three questions, and answers them
3. Watches scenes appear from their own answers
4. Clicks **Break down**
5. Gets elements tagged, a two-day shooting schedule, and a budget
6. Sees the Grafana panel showing which questions are working across all users

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

## Two modes

| | **Develop** | **Break down** |
|---|---|---|
| Input | A fragment — a premise, an image, nothing | A finished script |
| What it does | Asks one sharp question at a time | Tags cast, props, locations, wardrobe, VFX, stunts |
| Output | Scenes, then a script | Schedule, budget, call sheets |
| Feels like | A script editor | An assistant director |

You can start at either end. Bring a script and skip Develop; brainstorm and
flow straight into breakdown.

---

## Where Grafana earns its place

*The track requires the Grafana Cloud MCP server to be called at runtime, so it
has to change what the agent does — not decorate the result.*

**Questions are measured.** Every question the interviewer asks is logged with
what happened next: how much the writer wrote, whether they changed direction,
whether they stalled and asked to move on.

Before choosing its next question, the agent queries Grafana:

> *"At the premise stage, for a thriller — which question shapes have actually
> produced writing?"*

Grafana answers with what has worked. The agent picks accordingly, and logs the
outcome, which feeds the next writer.

> **Example.** *"What does he think he is owed?"* has a 70% follow-through rate
> in thrillers. *"Describe your antagonist's backstory"* sits at 20%. The agent
> asks the first.

That is the whole point of the product improving as it is used, and it is a
genuine runtime dependency rather than a panel.

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

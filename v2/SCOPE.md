# Second Unit — Product Scope

**Status:** authoritative scope for the current build  
**Updated:** 2026-08-31

Second Unit is an agentic production harness for a 5–20 minute short film. It
stays with the filmmaker from the first interview through a production-ready
shooting plan, then follows deadlines, scheduled shoots, blockers, and changes.

The product is not primarily a chatbot and it is not currently an autonomous
film generator. The film project is the durable unit of state. The assistant
observes that state, proposes bounded actions, records decisions, and keeps
downstream work consistent as the project changes.

## Current outcome

The first complete product path is:

```text
Interview
→ Outline
→ Screenplay import or generation
→ Scene breakdown
→ Shooting schedule
→ Shot list
→ Production readiness report
→ Continuous deadline and shoot follow-through
```

A filmmaker can stop at any stage, resume later, inspect the source and status
of every artifact, and see what requires attention next.

## Product principles

1. **The database is canonical.** Chat history is context, not project state.
2. **Artifacts are versioned.** Approved work is never silently overwritten.
3. **Suggestions are not decisions.** Suggested, approved, scheduled, and
   completed are distinct states.
4. **Changes have consequences.** When an upstream artifact changes, affected
   downstream artifacts are marked stale and selectively regenerated.
5. **Continuous means event-driven.** The supervisor wakes for a change,
   deadline, scheduled check-in, or user request; it does finite work and stops.
6. **Rules establish readiness.** AI can explain risk and propose remedies but
   cannot waive legal, safety, consent, or required production checks.
7. **Humans retain authority.** Creative approval, external communication,
   spending, bookings, and consequential schedule changes require permission.

## Users

The primary user is a filmmaker producing a short with a small crew and often
performing several roles. Collaboration and role-based crew accounts are later
scope; the initial system may store an owner name on tasks without authentication.

## In scope

### Development

- Start or resume a film project.
- Choose storytelling format and creative involvement.
- Conduct the existing context-driven story interview.
- Preserve the full visible assistant/writer conversation. The Story Editor
  offers suggestions and develops story material whenever it would help.
- Preserve transcripts, established facts, gaps, and readiness assessments.
- Create, revise, approve, and archive an outline.

### Screenplay

- Import a screenplay or generate one from an approved outline.
- Store immutable screenplay versions.
- Identify scenes and stable scene identifiers.
- Estimate page count and runtime.
- Record review status, approvals, and change summaries.

Rich screenplay authoring and industry-perfect pagination are not required for
the first vertical slice.

### Breakdown

- Extract and confirm cast, locations, props, wardrobe, makeup, vehicles,
  sound concerns, special equipment, VFX, stunts, and safety concerns.
- Trace extracted elements back to a scene and source text.
- Allow human correction, confirmation, and rejection.
- Mark breakdowns stale after relevant screenplay changes.

### Scheduling

- Create shoot days and assign ordered scenes.
- Track dates, unit, location, call time, wrap estimate, and status.
- Detect resource, cast, location, timing, and dependency conflicts.
- Propose schedule changes and show their consequences before application.
- Require approval before consequential schedule changes take effect.

### Shot planning

- Generate and edit scene-based shot lists.
- Track framing, angle, movement, action, coverage, equipment, setup estimate,
  priority, and completion status.
- Highlight missing essential coverage.
- Preserve human-approved shot-list versions.

### Tasks, deadlines, and shoots

- Create tasks with owners, deadlines, priority, blockers, and dependencies.
- Link tasks to scenes, shoot days, artifacts, or production elements.
- Show due-soon, overdue, blocked, and completed work.
- Track shoot-day status and scene/shot completion.
- Run finite daily, deadline, and shoot-related supervisor check-ins.
- Produce a prioritized production brief and assistant proposals.

Actual reminders outside the app require a later notification integration.

### Production readiness

- Calculate readiness by shoot day and category.
- Distinguish ready, at-risk, and blocked states.
- Explain each missing requirement and link it to the responsible task.
- Recalculate after relevant state changes.
- Never treat missing legal or safety requirements as optional because an AI
  expresses confidence.

### Assistant and orchestration

One **Production Supervisor** reads canonical state and invokes bounded
workflows: Story Editor, Screenplay Analyst, Breakdown Coordinator, Scheduler,
Shot Planner, and Readiness Auditor. These are workflow responsibilities, not
necessarily independent persistent agents.

The Supervisor may automatically perform reversible, internal work such as
analysis, draft creation, conflict detection, readiness calculation, task
suggestion, version archiving, and marking dependent artifacts stale.

It must propose and await review for schedule changes, deadline changes,
ownership changes, artifact approval, meaningful breakdown changes, and other
actions that alter the production plan.

It must always receive explicit authorization before changing approved story
material, committing spend, booking resources, contacting people, publishing
documents, deleting material, or overriding safety/legal requirements.

## Primary UI

The primary navigation is a persistent production flow:

```text
Story → Screenplay → Breakdown → Schedule → Shot List → Readiness
```

Users may inspect any stage. Completed stages remain revisitable, the current
stage is highlighted, and downstream stages show when upstream changes make
them stale. Story contains the Interview and Outline views; the remaining flow
steps each open their own focused workspace.

The first release groups work into five main workspace concerns:

1. Production dashboard
2. Story and screenplay
3. Scene breakdown
4. Schedule and shot lists
5. Readiness and tasks

A persistent assistant panel provides questions, explanations, proposals, and
approval actions in the context of the open workspace.

## Data and audit requirements

- SQLite is the initial database.
- Foreign keys are enabled and migrations are forward-only.
- Timestamps are stored as ISO-8601 UTC strings; project timezone controls display.
- Every artifact has immutable versions and provenance.
- Assistant proposals are stored separately from accepted state.
- Approvals name the exact entity or artifact version under review.
- Agent runs capture trigger, input snapshot, outcome, cost, and failure status.
- Material state changes create append-only activity events.
- Large binary assets may live outside SQLite and be referenced by storage URI.
- Secrets, raw media, and microphone audio are not stored in the project database.

## Explicitly out of scope for this milestone

- Autonomous generation of a final film or final edit
- Footage ingest, proxy generation, NLE integration, and post-production workflow
- Casting marketplaces, payroll, contracts, insurance, and accounting
- Location, actor, crew, equipment, or travel booking
- Sending email, SMS, calendar invitations, or crew messages
- Weather and traffic monitoring without an approved integration
- Multi-user authentication and granular crew permissions
- Native mobile applications
- Festival submission, distribution, marketing, or rights management
- Treating AI judgment as legal, safety, or financial approval

These may become later phases, but should not shape the first schema beyond
stable IDs, versioned artifacts, tasks, external storage references, and events.

## Definition of done for the vertical slice

A user can create a project, finish or import the story material, approve an
outline and screenplay, confirm a scene breakdown, schedule scenes into shoot
days, approve a shot list, and receive a live readiness report. When an approved
screenplay version changes, the system marks the affected breakdown, schedule,
shot list, and readiness artifacts stale. The Supervisor surfaces the resulting
work, deadlines, blockers, and proposals without silently changing approved
creative or production decisions.

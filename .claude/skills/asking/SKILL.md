---
name: asking
description: Use at any decision point — choosing an approach, a library, a name, a scope, a tradeoff, or when there is more than one reasonable way to do something. Governs when to ask the user rather than decide alone, and how to write the options they choose between.
---

# Asking rather than deciding

## The default is ask

When there is a real fork, **put it to the user.** Do not quietly pick the one
you prefer and mention it afterwards. They want to be in the decision, not
informed of it.

This overrides the usual instinct to "make routine judgment calls yourself."
The bar for asking is low here — if you find yourself weighing two options,
that weighing is the signal to ask.

**Ask about:**

- Architecture and approach — frameworks, structure, where logic lives
- Tradeoffs — cheaper vs better, faster vs safer, simple vs flexible
- Scope — how far to take something, what to leave out
- Anything that would be expensive to reverse later
- Anything where you notice yourself constructing a justification

**Still don't ask about:** formatting, variable names, import order, which line
to put a comment on. That is noise, not collaboration.

**The test:** if you would feel the need to explain your choice afterwards, ask
first instead.

## Why this matters here

Decisions made alone in this project have gone wrong: a framework that turned
out to be banned by the rules, a visual approach chosen for a cost ceiling that
was five times looser than assumed, a cost model off by an order of magnitude.
Each was defensible in isolation and each cost real time.

## Writing the options

Options are read quickly. Write them so the choice is obvious without needing
background.

**Describe what happens to the user, not what the code does.**

> **Weak:** "Declared typed inputs per stage with a protocol boundary"
>
> **Good:** "Each step only gets the data it asked for. Safer — a bug in one
> step can't damage another. Costs a bit more wiring."

**Rules for option text:**

- Plain words. No jargon unless it is genuinely the clearest term.
- Say the **upside and the cost** — an option with no downside is not being
  described honestly.
- Keep each description to one or two short sentences.
- Make the labels concrete: "Keep the sprites, replace the art" beats
  "Option A".
- Never write an obviously-wrong filler option to pad the list.

**Always recommend one.** Put it first, mark it, and say in one line why. The
user still chooses — but they should not have to reconstruct your reasoning to
do it.

## When a question can wait

If something is blocking, ask now. If it is not, keep working on the parts that
do not depend on the answer and raise it at the natural moment. Do not stall the
whole task on a question that only affects a later step.

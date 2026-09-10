"""The brief, and how the snapshot is put in front of the model.

Written by hand for this build. Two lessons from the prototype are encoded here
rather than left to chance:

  - it must be told that staging nothing is a correct outcome, or a mandatory
    gathering instruction turns every turn into busywork;
  - it must be told when NOT to offer suggestions, or with the no-assist rules
    gone it offers them on every single turn.
"""

from __future__ import annotations

import json

SYSTEM = """You are a story editor developing a 5 to 20 minute short film with
its writer. You see the whole project state on every turn; you never have to
remember it.

YOUR TURN IS ONE OBJECT. Return every state update the turn settles and exactly
one response, together. There is no second chance to add a fact you left out,
and no reason to split the work across calls.

STATE UPDATES
  facts     One atomic fact per entry, each quoting the writer's own words as
            evidence. Record only what the writer wrote or explicitly chose.
            You may develop ideas freely — but ideas live in suggestions until
            the writer selects one. They never become facts by assertion.
            An answer that settles nothing produces no facts. That is correct
            and expected; do not invent work.
  gaps      Open a gap for a decision that blocks visible scenes. Resolve one
            only with evidence.
  readiness Four lenses: who carries the film, what creates pressure, what
            visible sequence exists, what ending it earns. Each needs evidence
            from the material. Include it when your view actually moved.

WHERE THE WORDS GO. `question` holds one question and nothing else — no
preamble, no explanation, under 320 characters. Every other sentence you want to
say goes in `blocks`, as {kind: reflection | coaching | note, text}. Writing a
paragraph into `question` is the most common way to get rejected.

YOUR RESPONSE — exactly one intent:
  ask_question       one question built from a concrete detail in their latest
                     answer. Actions, images, choices, reversals, consequences —
                     not labels, biography or theme. One decision at a time.
  offer_suggestions  two or three concrete possibilities, each with one sentence
                     on how it changes the film. You MUST also set `question` to
                     a short invitation to choose, combine or reject them — cards
                     with no question are rejected. Offer when they ask, when they
                     are stuck, or when a fork is genuinely easier to react to
                     than an open question. Do not offer every turn: a writer who
                     is producing material needs the next good question instead.
  reflect_and_confirm  state what you believe they mean when it matters and could
                     be wrong, in a `reflection` block. Use it to surface a
                     contradiction rather than choosing which version is true.
  coach_writer       answer a question they asked you, or explain briefly why a
                     choice matters, in a `coaching` block. Craft advice is not a
                     story fact.
  recommend_outline  only with all four readiness lenses evidenced.

Write like a person. Warm, direct, curious, short. Use their words back at them.
Concrete beats abstract every time. No empty praise."""


def render(snapshot, allowed: set[str], repair: str | None = None) -> str:
    s = snapshot
    parts = [
        f"Involvement: {s.session['involvement']}/100 ({s.involvement_mode}). "
        f"Storytelling format: {s.session['storytelling_format']}.",
    ]
    if s.session.get("seed"):
        parts.append(f"They started with: {s.session['seed']}")

    if s.facts:
        parts.append("Established (their words, already agreed):\n- "
                     + "\n- ".join(f["text"] for f in s.facts))
    if s.gaps:
        parts.append("Open gaps:\n- " + "\n- ".join(g["text"] for g in s.gaps))

    if s.conflicts:
        lines = [f"{c['fact']!r} (shares: {', '.join(c['shared'])})" for c in s.conflicts]
        parts.append("POSSIBLE CONTRADICTION with what they just said — raise it, "
                     "do not choose for them:\n- " + "\n- ".join(lines))

    if s.turns:
        body = []
        for turn in s.turns:
            answer = ""
            if turn.get("answer_payload"):
                try:
                    answer = json.loads(turn["answer_payload"]).get("text") or ""
                except json.JSONDecodeError:
                    answer = ""
            body.append(f"YOU ({turn['primary_intent']}): {turn['question'] or '—'}"
                        + (f"\nWRITER: {answer}" if answer else ""))
        parts.append("Recent turns:\n\n" + "\n\n".join(body))

    if s.recent_questions:
        parts.append("Do not repeat or lightly rephrase any of these:\n- "
                     + "\n- ".join(s.recent_questions))

    if s.suggestions_open:
        cards = [f"[{c['status']}] {c['label']} — {c['detail']}" for c in s.suggestions_open]
        parts.append("Ideas currently on the table:\n- " + "\n- ".join(cards))

    parts.append(_event_line(s))

    if s.readiness:
        parts.append(f"Your last readiness view: {'ready' if s.readiness['ready'] else 'not ready'}"
                     f" — {s.readiness['reason']}")

    parts.append("Allowed response intents this turn: " + ", ".join(sorted(allowed))
                 + ". Any other intent will be rejected.")

    if repair:
        parts.insert(0, f"YOUR LAST ATTEMPT WAS REJECTED — fix exactly this:\n{repair}\n"
                        "Return the whole decision again, keeping everything that was fine.")
    return "\n\n".join(parts)


def _event_line(s) -> str:
    event = s.event
    if event.kind == "interview_started":
        return "THE WRITER JUST STARTED. Open the interview."
    if event.kind == "suggestions_requested":
        return ("THE WRITER PRESSED 'GIVE ME OPTIONS'. They have not answered the question. "
                "Offer possibilities for the decision in front of them, and set `question` "
                "to a short invitation to choose, combine or reject them.")
    if event.kind == "suggestions_selected":
        note = event.payload.get("note") or ""
        return ("THE WRITER CHOSE AN IDEA" + (f", adding: {note}" if note else "")
                + ". It is theirs now — record it and build on it.")
    if event.kind == "suggestions_rejected":
        return "THE WRITER REJECTED EVERY IDEA. Do not offer near-identical ones again."
    if event.kind == "question_skipped":
        return "THE WRITER SKIPPED THE QUESTION. Go somewhere genuinely different."
    if event.kind == "continue_interview":
        return "THE WRITER CHOSE TO KEEP DEVELOPING rather than outline."
    if event.kind == "decision_revised":
        return f"THE WRITER IS CORRECTING AN EARLIER DECISION: {event.text}"
    return f"THE WRITER JUST WROTE:\n{event.text}"

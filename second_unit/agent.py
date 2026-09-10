"""The bounded Story Editor decision loop."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from .domain import (
    ALLOWED_INTENTS,
    MAX_GENERATED_QUESTIONS,
    MIN_GENERATED_QUESTIONS,
    TURN_DECISION_SCHEMA,
    GapChange,
    InterviewEvent,
    PlannedQuestion,
    SuggestionCandidate,
    TurnDecision,
    event_can_establish_facts,
    parse_turn_decision,
    question_similarity,
    validate_decision,
    validate_decision_components,
)
from .model import ModelResult, ModelUnavailable


PROMPT_VERSION = "interview-story-editor-5"

SYSTEM_PROMPT = """You are Second Unit's Story Editor, developing a short film
with its filmmaker. Be an active, concrete creative collaborator—not a form and
not an interrogation.

Choose the single response intent that helps most. On the opening turn, decide
how many questions this specific story needs and create the complete set at
once. On later turns you may ask one sharp
follow-up only after an explicit continue action, reflect an uncertain interpretation, answer or coach
the filmmaker, offer two or three selectable ideas, or recommend moving to an
outline. Honor the filmmaker's explicit request before following your own plan.
If the filmmaker asks you a direct question, help them before asking anything
new. If they are stuck, give usable possibilities rather than another blank-page
question.

Ground responses in exact details from the conversation. Prefer visible action,
images, choices, reversals, pressure, and consequences over biography or labels.
Never repeat a recent question. Outside the opening structured set, ask at most
one main question. Do not join two interrogatives with "and" or "or" inside it.
Guidance must be declarative; put a later follow-up in the question field so
the interface can treat it correctly.

OPENING QUESTION SET:
- Choose between 3 and 10 questions. Use the smallest number that can clarify
  the story enough for a first outline; never add filler to reach a count.
- Each question asks for one decision only and is grounded in their story.
- Give each question a one-sentence explanation of 18 words or fewer. Use
  everyday words and say what the answer helps us decide. Prefer patterns like
  "Your answer helps us decide..." or "This shows us...".
- Order the set from core story decisions to optional detail. Do not ask for
  information already present in the starting idea.
Treat the generated set size as a pacing boundary, not a readiness score or
forced ending. After the set, pause questions and keep helping without
interrogating. Ask beyond it only after an explicit continue action. Aim for a
usable outline handoff without chasing exhaustive detail.

QUESTION HELP BRANCHES:
- When help is requested for one planned question, return two or three concrete
  answer directions tailored only to that question and the current story.
- The draft answer is context, not accepted canon.
- Do not ask another question and do not alter the main questionnaire.

CREATIVE AUTHORITY:
- Assistant suggestions are proposals, not story facts.
- Only a filmmaker message, revision, confirmed reflection, or explicitly
  selected suggestion can support a fact candidate.
- Every fact candidate must cite the current event ID and quote an exact excerpt
  from the CURRENT SOURCE MATERIAL supplied in the prompt.
- The fact text itself must be an exact excerpt from that accepted filmmaker
  material. A filmmaker question is not a source of story facts.
- Do not create a fact merely because you suggested it.
- Surface contradictions instead of silently choosing a version.

SUGGESTIONS:
- Return two or three distinct, selectable cards.
- Each card has a short label and a concrete detail explaining its dramatic
  effect. Do not hide options in general prose.

READINESS:
Assess four lenses independently: who or what carries the film; what pressure or
change drives it; whether visible events can form a sequence; and whether an
ending/final image is supported. Mark each supported, uncertain, or missing and
cite an exact excerpt from accepted story material when supported. Return
readiness as null unless newly accepted material changes a lens or you are
recommending the outline. Do not re-assert unchanged readiness on options,
questions, or coaching turns. Recommend an outline only when all four are
supported and no blocking contradiction remains. Never impose a maximum number
of Interview answers.

Return only the requested JSON contract."""


@dataclass(frozen=True)
class AgentOutcome:
    decision: TurnDecision
    meta: dict[str, Any]
    used_fallback: bool = False
    validation_errors: tuple[str, ...] = ()


def event_source_text(event: InterviewEvent, references: dict[str, Any]) -> str:
    payload = event.payload
    if event.kind == "interview_started":
        return payload["seed"]
    if event.kind == "message_submitted":
        return payload["text"]
    if event.kind in {"questionnaire_submitted", "questionnaire_revised"}:
        return "\n".join(item["text"] for item in payload["answers"])
    if event.kind == "question_ideas_requested":
        return payload.get("draft_answer", "")
    if event.kind in {"suggestions_selected", "suggestions_rejected"}:
        options = "\n".join(
            f"{item['label']}: {item['detail']}"
            for item in references.get("suggestions", [])
        )
        note = payload.get("note", "")
        if event.kind == "suggestions_selected" and note:
            return note
        return "\n".join(part for part in (options, note) if part)
    if event.kind == "reflection_confirmed":
        response = references.get("response") or {}
        return "\n".join(
            part for part in (response.get("guidance", ""), payload.get("note", ""))
            if part
        )
    if event.kind in {"response_continued", "question_skipped"}:
        return payload.get("note", "")
    if event.kind == "decision_revised":
        return payload["replacement_text"]
    return payload.get("note", "")


def _prompt_snapshot(snapshot: dict[str, Any], *, excluded_event_id: str = "") -> dict[str, Any]:
    session = snapshot["session"]
    events = []
    for item in snapshot["recent_events"]:
        events.append({
            "event_id": item["id"],
            "kind": item["kind"],
            "payload": item["payload"],
            "assistant": None if not item.get("response_id") else {
                "response_id": item["response_id"],
                "intent": item.get("intent"),
                "guidance": item.get("guidance"),
                "question": item.get("question"),
                "focus": item.get("focus"),
                "questions": item.get("questions", []),
            },
        })
    return {
        "session": {
            "id": session["id"],
            "revision": session["revision"],
            "status": session["status"],
            "title": session["title"],
            "seed": session["seed"],
            "storytelling_format": session["storytelling_format"],
            "involvement_mode": session["involvement_mode"],
            "question_target": session["question_target"],
            "questions_asked": snapshot["questions_asked"],
            "readiness_score": session["readiness_score"],
            "readiness_reason": session["readiness_reason"],
        },
        "recent_conversation": events,
        "active_facts": [
            {"id": item["id"], "text": item["text"], "evidence": item["evidence"]}
            for item in snapshot["facts"]
            if item.get("source_event_id") != excluded_event_id
        ],
        "open_gaps": [
            {"id": item["id"], "description": item["description"], "impact": item["impact"]}
            for item in snapshot["gaps"]
            if item.get("opened_event_id") != excluded_event_id
        ],
        "suggestion_history": [
            {
                "id": item["id"], "label": item["label"],
                "detail": item["detail"], "status": item["status"],
            }
            for item in snapshot["suggestions"]
        ],
        "recent_questions": snapshot["recent_questions"],
    }


def build_prompt(snapshot: dict[str, Any], event: InterviewEvent,
                 references: dict[str, Any], repair_errors: list[str] | None = None) -> str:
    format_guidance = {
        "narrated": "Test what the narrator knows, why this voice is telling it, and what images do instead of words.",
        "dialogue_led": "Test speakers, subtext, changing conversational power, and what remains visual.",
        "hybrid": "Clarify what narration owns and what conflict dialogue must dramatize.",
        "not_sure": "Use the material to reveal the best form; do not force an early format choice.",
    }
    involvement_guidance = {
        "ai_led": "Take initiative and offer concrete directions often, but keep proposals outside canon until selected.",
        "collaborative": "Balance focused questions, useful reflection, coaching, and concrete options.",
        "author_led": "Protect consequential choices for the filmmaker while still answering and helping when asked.",
    }
    session = snapshot["session"]
    source = event_source_text(event, references)
    required = sorted(ALLOWED_INTENTS[event.kind])
    excluded_event_id = (
        event.payload.get("target_event_id", "")
        if event.kind == "questionnaire_revised"
        else ""
    )
    prompt_snapshot = _prompt_snapshot(snapshot, excluded_event_id=excluded_event_id)
    if event.kind == "interview_started":
        prompt_snapshot["session"].pop("question_target", None)
        prompt_snapshot["session"].pop("questions_asked", None)
    parts = [
        f"EVENT: {event.kind} ({event.id})",
        "Allowed response intents for this event: " + ", ".join(required),
        f"CURRENT SOURCE MATERIAL (the only source for new fact candidates):\n{source or '(none)'}",
        "Storytelling guidance: " + format_guidance[session["storytelling_format"]],
        "Collaboration guidance: " + involvement_guidance[session["involvement_mode"]],
        "CANONICAL SNAPSHOT:\n" + json.dumps(
            prompt_snapshot, ensure_ascii=False, indent=2
        ),
    ]
    if event.kind != "interview_started":
        parts.append(
            f"Question pacing: the generated set contains {session['question_target']} "
            f"questions and {snapshot['questions_asked']} questions currently exist. After "
            "the opening set, do not ask another unless the event is response_continued or "
            "continue_interview. Never call this a safety limit."
        )
    if event.kind == "suggestions_requested":
        parts.append("The filmmaker explicitly requested options. You MUST use offer_suggestions.")
    if event.kind == "question_ideas_requested":
        question = references.get("question") or {}
        parts.append(
            "The filmmaker requested help inside one questionnaire branch. You MUST use "
            "offer_suggestions and return 2 or 3 possible answer directions. Do not ask a "
            "question. Keep the ideas specific to this planned question:\n"
            + json.dumps({
                "question": question.get("text", ""),
                "simple_explanation": question.get("explanation", ""),
                "draft_answer": event.payload.get("draft_answer", ""),
            }, ensure_ascii=False, indent=2)
        )
    if event.kind == "message_submitted" and event.payload.get("purpose") == "question":
        parts.append("The filmmaker asked a direct question. Answer or coach before asking anything new.")
    if event.kind == "interview_started":
        parts.append(
            f"Choose the smallest useful count from {MIN_GENERATED_QUESTIONS} to "
            f"{MAX_GENERATED_QUESTIONS} based on what is already clear or missing. Create "
            "that complete structured set now. Use ask_questions, leave response.question "
            "empty, and give every question an everyday-language explanation of 18 words or fewer."
        )
    if event.kind == "questionnaire_submitted":
        parts.append(
            "The filmmaker answered the question set. Synthesize and help; do not start "
            "another interview round. Leave readiness null unless these answers change it."
        )
    if event.kind == "questionnaire_revised":
        parts.append(
            "The filmmaker revised the existing questionnaire answers. Treat this complete "
            "answer set as replacing the previous submission. Synthesize the revision; do "
            "not start another interview round. Reassess the four readiness lenses from the "
            "updated accepted material."
        )
    if repair_errors:
        parts.append(
            "Your previous decision was rejected for exactly these reasons. Correct them without changing valid material:\n- "
            + "\n- ".join(repair_errors)
        )
    parts.append("Return one TurnDecision as JSON.")
    return "\n\n".join(parts)


class StoryEditor:
    def __init__(self, model: Any):
        self.model = model

    def decide(self, snapshot: dict[str, Any], event: InterviewEvent,
               references: dict[str, Any]) -> AgentOutcome:
        if event.kind == "interview_finished":
            return AgentOutcome(
                TurnDecision(
                    intent="coach_writer",
                    guidance=(
                        "The interview is complete. Your accepted story decisions and any open "
                        "questions are saved so the outline can carry them forward."
                    ),
                    question="",
                    focus="interview complete",
                    listening_for="outline development",
                    readiness=None,
                ),
                {
                    "attempts": 0,
                    "backend": "deterministic",
                    "model": "finish_interview",
                    "prompt_tokens": 0,
                    "output_tokens": 0,
                    "prompt_version": PROMPT_VERSION,
                    "used_fallback": False,
                    "validation_errors": [],
                    "dropped_components": [],
                },
            )
        source = event_source_text(event, references)
        accepted_source_texts = [
            item["text"]
            for item in snapshot["facts"]
            if item.get("text")
            and not (
                event.kind == "questionnaire_revised"
                and item.get("source_event_id") == event.payload["target_event_id"]
            )
        ]
        if event_can_establish_facts(event) and source:
            accepted_source_texts.append(source)
        errors: list[str] = []
        last_result: ModelResult | None = None
        attempts = 0
        for attempt in range(2):
            try:
                attempts += 1
                last_result = self.model.decide(
                    system=SYSTEM_PROMPT,
                    prompt=build_prompt(
                        snapshot, event, references,
                        repair_errors=errors if attempt else None,
                    ),
                    schema=TURN_DECISION_SCHEMA,
                )
                decision = parse_turn_decision(last_result.payload)
                if event.kind == "question_ideas_requested":
                    # A help branch is advisory UI state. It cannot update canon,
                    # gaps, or readiness until the filmmaker submits an answer.
                    decision = replace(
                        decision, facts=(), gap_changes=(), readiness=None
                    )
                components = validate_decision_components(
                    decision,
                    event=event,
                    recent_questions=snapshot["recent_questions"],
                    source_text=source,
                    active_gap_ids=[item["id"] for item in snapshot["gaps"]],
                    accepted_source_texts=accepted_source_texts,
                    question_target=snapshot["session"]["question_target"],
                    questions_asked=snapshot["questions_asked"],
                )
                errors = [
                    error
                    for name in ("response", "facts", "gaps", "readiness")
                    for error in components[name]
                ]
                if errors and decision.question and all(
                    "question target has been reached" in item for item in errors
                ):
                    # The pause is right; discarding the turn with it is not. The
                    # model's facts and readiness came from the filmmaker's answers
                    # and are still valid — only the extra question is unwanted.
                    # An ask_question with no question is not a valid response, so
                    # the paused turn becomes coaching — which is what the local
                    # fallback would have produced anyway, minus the lost work.
                    decision = replace(
                        decision,
                        question="",
                        intent="coach_writer" if decision.intent == "ask_question"
                                else decision.intent,
                    )
                    errors = validate_decision(
                        decision,
                        event=event,
                        recent_questions=snapshot["recent_questions"],
                        source_text=source,
                        active_gap_ids=[item["id"] for item in snapshot["gaps"]],
                        accepted_source_texts=accepted_source_texts,
                        question_target=snapshot["session"]["question_target"],
                        questions_asked=snapshot["questions_asked"],
                    )
                if not errors:
                    return AgentOutcome(decision, {
                        "attempts": attempts,
                        "backend": last_result.backend,
                        "model": last_result.model,
                        "prompt_tokens": last_result.prompt_tokens,
                        "output_tokens": last_result.output_tokens,
                        "prompt_version": PROMPT_VERSION,
                        "used_fallback": False,
                        "validation_errors": [],
                        "dropped_components": [],
                    })

                # State updates are optional side effects of a turn. Preserve a
                # valid filmmaker-facing response when only facts, gaps, or
                # readiness fail evidence validation.
                if not components["response"]:
                    dropped = {
                        name for name in ("facts", "gaps", "readiness")
                        if components[name]
                    }
                    if dropped:
                        partial = replace(
                            decision,
                            facts=() if "facts" in dropped else decision.facts,
                            gap_changes=() if "gaps" in dropped else decision.gap_changes,
                            readiness=None if "readiness" in dropped else decision.readiness,
                        )
                        partial_errors = validate_decision(
                            partial,
                            event=event,
                            recent_questions=snapshot["recent_questions"],
                            source_text=source,
                            active_gap_ids=[item["id"] for item in snapshot["gaps"]],
                            accepted_source_texts=accepted_source_texts,
                            question_target=snapshot["session"]["question_target"],
                            questions_asked=snapshot["questions_asked"],
                        )
                        if not partial_errors:
                            return AgentOutcome(
                                partial,
                                {
                                    "attempts": attempts,
                                    "backend": last_result.backend,
                                    "model": last_result.model,
                                    "prompt_tokens": last_result.prompt_tokens,
                                    "output_tokens": last_result.output_tokens,
                                    "prompt_version": PROMPT_VERSION,
                                    "used_fallback": False,
                                    "validation_errors": errors,
                                    "dropped_components": sorted(dropped),
                                },
                                validation_errors=tuple(errors),
                            )
            except ModelUnavailable as exc:
                errors = [str(exc)]
                break
            except ValueError as exc:
                errors = [str(exc)]
                if attempt == 0:
                    continue
                break

        fallback = fallback_decision(snapshot, event, references)
        fallback_errors = validate_decision(
            fallback,
            event=event,
            recent_questions=snapshot["recent_questions"],
            source_text=source,
            active_gap_ids=[item["id"] for item in snapshot["gaps"]],
            accepted_source_texts=accepted_source_texts,
            question_target=snapshot["session"]["question_target"],
            questions_asked=snapshot["questions_asked"],
        )
        if fallback_errors:
            raise RuntimeError("local Story Editor fallback was invalid: " + "; ".join(fallback_errors))
        return AgentOutcome(
            fallback,
            {
                "attempts": attempts,
                "backend": getattr(last_result, "backend", "local_fallback"),
                "model": getattr(last_result, "model", "deterministic"),
                "prompt_tokens": getattr(last_result, "prompt_tokens", 0),
                "output_tokens": getattr(last_result, "output_tokens", 0),
                "prompt_version": PROMPT_VERSION,
                "used_fallback": True,
                "validation_errors": list(errors),
                "dropped_components": [],
            },
            used_fallback=True,
            validation_errors=tuple(errors),
        )


def _fresh_question(snapshot: dict[str, Any], candidates: list[str]) -> str:
    recent = snapshot["recent_questions"]
    emergency = [
        "Which physical object could reveal the next change?",
        "How could the location visibly shift in the next beat?",
        "Whose choice could change the balance of the scene now?",
        "What sound or silence could alter this moment?",
        "Which consequence should arrive sooner than expected?",
        "What might the audience notice before the characters do?",
        "Which entrance or exit would transform this scene?",
        "What action would make the pressure impossible to ignore?",
        "Which detail could return later with a different meaning?",
        "Where could the next reversal become visible?",
    ]
    for candidate in [*candidates, *emergency]:
        if all(question_similarity(candidate, old) < 0.82 for old in recent):
            return candidate
    # The validator only compares the last eight questions and this pool has ten
    # deliberately different shapes, so this is unreachable unless that
    # contract changes. Failing here is safer than publishing a known repeat.
    raise RuntimeError("no non-repeating local fallback question is available")


def _story_context(snapshot: dict[str, Any], *, maximum: int = 220) -> str:
    facts = [item.get("text", "").strip() for item in snapshot["facts"]]
    material = " ".join(item for item in facts[-3:] if item)
    if not material:
        material = snapshot["session"]["seed"].strip()
    if len(material) <= maximum:
        return material
    return material[:maximum].rsplit(" ", 1)[0] + "…"


def _fallback_question_count(snapshot: dict[str, Any]) -> int:
    """Choose a conservative set size when Gemini is unavailable."""
    word_count = len(snapshot["session"]["seed"].split())
    if word_count <= 12:
        return 8
    if word_count <= 28:
        return 6
    return 5


def _planned_question_context(snapshot: dict[str, Any], question_id: str) -> str:
    for item in reversed(snapshot["recent_events"]):
        for question in item.get("questions", []):
            if question.get("id") == question_id:
                return question.get("text", "this story question")
    return "this story question"


def fallback_decision(
    snapshot: dict[str, Any],
    event: InterviewEvent,
    references: dict[str, Any] | None = None,
) -> TurnDecision:
    story_context = _story_context(snapshot)
    question_target = snapshot["session"]["question_target"]
    target_reached = snapshot["questions_asked"] >= question_target
    if event.kind == "interview_started":
        generated_count = _fallback_question_count(snapshot)
        bank = (
            ("What is the film mainly about?", "Name the person, group, place, or subject we should follow.", "story carrier"),
            ("What does the central figure want during the film?", "Say the immediate goal that gives the story direction.", "goal"),
            ("What makes that goal difficult now?", "Name the pressure or obstacle that creates movement.", "pressure"),
            ("What is the first image the audience sees?", "Describe one visible opening moment so the film can begin clearly.", "opening image"),
            ("What important choice changes the situation?", "Choose the decision that pushes the story into its next beat.", "turning choice"),
            ("What consequence follows from that choice?", "Describe what visibly becomes different, harder, or surprising.", "consequence"),
            ("What should the audience understand without being told?", "Name the feeling or idea the images and actions should communicate.", "subtext"),
            ("What is the final image of the film?", "Describe what we last see so the ending has a clear destination.", "ending"),
            ("Where does most of the film happen?", "Name the main place so scenes can be shaped around it.", "location"),
            ("How much time passes inside the story?", "Choose the story timespan so the pacing stays focused.", "timespan"),
            ("Who else must appear on screen?", "List only people who change the central action.", "supporting characters"),
            ("What does the central figure risk losing?", "State the cost of failure so the choice matters.", "stakes"),
            ("What physical detail should return more than once?", "Pick an object, sound, or image that can connect the scenes.", "motif"),
            ("What should remain unexplained?", "Protect any mystery the film should leave with the audience.", "mystery"),
            ("Whose point of view controls what we learn?", "Choose whose experience guides the camera and information.", "point of view"),
            ("What changes emotionally by the end?", "Describe the inner shift that the final action should reveal.", "emotional change"),
            ("Which moment would be hardest to film?", "Flag a practical challenge early so the outline can stay achievable.", "production constraint"),
            ("What tone should every scene protect?", "Choose the feeling that keeps the short consistent.", "tone"),
            ("What should the audience notice before the central figure does?", "Name useful visual information that can create tension.", "audience knowledge"),
            ("What single moment must stay in the film?", "Identify the essential beat the outline should build around.", "essential moment"),
        )
        return TurnDecision(
            intent="ask_questions",
            guidance=f"I turned “{story_context}” into one short question set. Answer briefly, skip what is undecided, or finish whenever you have enough.",
            questions=tuple(
                PlannedQuestion(text, explanation, focus)
                for text, explanation, focus in bank[:generated_count]
            ),
            focus="complete interview",
            listening_for="the filmmaker's answers to the question set",
            readiness=None,
        )

    if event.kind == "question_ideas_requested":
        planned_question = (references or {}).get("question", {}).get("text") or (
            _planned_question_context(snapshot, event.payload["question_id"])
        )
        question_topic = planned_question.rstrip("?")
        return TurnDecision(
            intent="offer_suggestions",
            guidance=f"Here are three possible directions for the question about “{question_topic}”.",
            suggestions=(
                SuggestionCandidate(
                    "Make it a visible choice",
                    f"Answer “{planned_question}” with a choice the central figure makes on screen.",
                ),
                SuggestionCandidate(
                    "Build from the main image",
                    f"Use “{story_context}” to answer through one concrete image, action, or sound.",
                ),
                SuggestionCandidate(
                    "Connect it to the ending",
                    f"Choose an answer that changes what the final image of “{story_context}” can mean.",
                ),
            ),
            question="",
            focus="question help",
            listening_for="the filmmaker's own answer",
            readiness=None,
        )

    if event.kind == "suggestions_requested":
        return TurnDecision(
            intent="offer_suggestions",
            guidance=f"Here are three possibilities built from your film as it stands: “{story_context}”",
            suggestions=(
                SuggestionCandidate(
                    "Turn the encounter into a choice",
                    f"Using “{story_context}”, make the central figure choose whether to engage, conceal it, or walk away; the choice creates the next visible beat.",
                ),
                SuggestionCandidate(
                    "Disrupt the existing routine",
                    f"Let something inside “{story_context}” break the normal pattern, then show one concrete consequence before the scene ends.",
                ),
                SuggestionCandidate(
                    "Echo this image at the end",
                    f"Return to a changed version of “{story_context}” as the final image so the short has a visual destination without adding another plotline.",
                ),
            ),
            question="" if target_reached else _fresh_question(snapshot, [
                    "Which direction feels closest to the film you want?",
                    "Which option would you keep, combine, or reshape?",
                ]),
            focus="creative direction",
            listening_for="a selected or adapted direction",
            readiness=None,
        )

    purpose = event.payload.get("purpose")
    if event.kind == "message_submitted" and purpose == "question":
        filmmaker_question = event.payload.get("text", "")
        if "subplot" in filmmaker_question.lower():
            guidance = (
                f"No—a short built around “{story_context}” does not need a subplot. "
                "First deepen the main encounter and its consequence. Add a second thread only if it "
                "changes the central choice or pays off in the same ending; otherwise it will dilute the short."
            )
        else:
            guidance = (
                f"For this film—“{story_context}”—judge that choice by what it changes on screen. "
                "Keep the version that sharpens the central action or consequence, and leave any "
                "extra idea as optional until it earns a visible payoff."
            )
        return TurnDecision(
            intent="coach_writer",
            guidance=guidance,
            question="",
            focus="filmmaker question",
            listening_for="the filmmaker's next instruction or creative decision",
            readiness=None,
        )

    if target_reached and event.kind not in {"response_continued", "continue_interview"}:
        return TurnDecision(
            intent="coach_writer",
            guidance=(
                f"We’ve reached your {question_target}-question target. I’ll pause the interview here "
                "so this stays useful rather than feeling like an interrogation. You can finish and carry "
                "open questions into the outline, ask me for options, or continue with another question."
            ),
            question="",
            focus="interview pacing",
            listening_for="the filmmaker's next direction",
            readiness=None,
        )

    if event.kind == "suggestions_rejected":
        question = _fresh_question(snapshot, [
            "What quality were those options missing—tone, character, realism, or something else?",
            "What should the next set of possibilities avoid?",
        ])
        guidance = "None of those choices are part of the film. Let’s use what felt wrong to aim the next ideas better."
        focus = "recalibrating options"
    elif event.kind == "suggestions_selected":
        question = _fresh_question(snapshot, [
            "What does that choice make someone do next that we can see?",
            "Which visible consequence follows from the direction you chose?",
        ])
        guidance = "That direction is now part of the working story. Let’s turn it into visible action."
        focus = "consequence"
    elif event.kind == "question_skipped":
        question = _fresh_question(snapshot, [
            "What is the one image you already know belongs in this film?",
            "Where does the situation become impossible to ignore?",
            "What is the last thing the audience sees?",
        ])
        guidance = "We can leave that undecided and approach the film from another angle."
        focus = "new direction"
    elif event.kind == "continue_interview":
        question = _fresh_question(snapshot, [
            "Which part still feels least like your film?",
            "What do you want to discover before outlining?",
            "Which moment needs another possibility?",
        ])
        guidance = "We’ll keep developing it. Nothing has been locked by the outline stage."
        focus = "continued development"
    elif event.kind == "decision_revised":
        question = _fresh_question(snapshot, [
            "What visible moment changes because of this revision?",
            "Which scene now plays differently because of that correction?",
        ])
        guidance = "The earlier decision remains in history, and this replacement is now the active direction."
        focus = "revision consequence"
    elif event.kind == "reflection_confirmed":
        question = _fresh_question(snapshot, [
            "What action would let the audience understand that without being told?",
            "Where could that confirmed idea become visible on screen?",
        ])
        guidance = "Good—I'll treat that interpretation as confirmed and help make it visible."
        focus = "visual proof"
    elif event.kind == "response_continued":
        question = _fresh_question(snapshot, [
            "What happens next that changes the situation?",
            "Which character choice creates the next scene?",
            "What becomes harder after this moment?",
        ])
        guidance = "Let’s carry that thought into the next concrete beat."
        focus = "next beat"
    else:
        question = _fresh_question(snapshot, [
            "What happens next that we can actually see?",
            "What choice makes the situation harder?",
            "What would the audience remember as an image?",
            "What changes by the end of this moment?",
        ])
        guidance = "That gives us a direction. Let’s make its next consequence visible."
        focus = "visible action"

    return TurnDecision(
        intent="ask_question",
        guidance=guidance,
        question=question,
        focus=focus,
        listening_for="a concrete action, image, choice, or consequence",
        readiness=None,
    )

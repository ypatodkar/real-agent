"""Typed contracts for the Interview event loop.

This module contains no database, provider, or HTTP code.  Untrusted JSON is
turned into small immutable objects here before it reaches the harness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping


EVENT_KINDS = {
    "interview_started",
    "message_submitted",
    "questionnaire_submitted",
    "questionnaire_revised",
    "question_ideas_requested",
    "suggestions_requested",
    "suggestions_selected",
    "suggestions_rejected",
    "question_skipped",
    "reflection_confirmed",
    "response_continued",
    "continue_interview",
    "interview_finished",
    "decision_revised",
}

MESSAGE_PURPOSES = {"answer", "question", "correction", "nuance", "instruction"}
RESPONSE_INTENTS = {
    "ask_question",
    "ask_questions",
    "offer_suggestions",
    "reflect_and_confirm",
    "coach_writer",
    "recommend_outline",
}
READINESS_LENSES = ("carrier", "pressure", "visible_sequence", "ending")
LENS_STATUSES = {"supported", "uncertain", "missing"}
GAP_IMPACTS = {"blocking"}

ALLOWED_INTENTS = {
    "interview_started": {"ask_questions"},
    "message_submitted": RESPONSE_INTENTS,
    "questionnaire_submitted": {
        "coach_writer", "offer_suggestions", "reflect_and_confirm", "recommend_outline"
    },
    "questionnaire_revised": {
        "coach_writer", "offer_suggestions", "reflect_and_confirm", "recommend_outline"
    },
    "question_ideas_requested": {"offer_suggestions"},
    "suggestions_requested": {"offer_suggestions"},
    "suggestions_selected": {
        "ask_question", "reflect_and_confirm", "coach_writer", "recommend_outline"
    },
    "suggestions_rejected": {"ask_question", "offer_suggestions", "coach_writer"},
    "question_skipped": {"ask_question", "offer_suggestions", "coach_writer"},
    "reflection_confirmed": {"ask_question", "coach_writer", "recommend_outline"},
    "response_continued": {"ask_question", "offer_suggestions", "coach_writer", "recommend_outline"},
    "continue_interview": {"ask_question", "offer_suggestions", "coach_writer"},
    "interview_finished": {"coach_writer"},
    "decision_revised": {"ask_question", "reflect_and_confirm", "coach_writer"},
}

MAX_MESSAGE_CHARS = 12_000
MAX_NOTE_CHARS = 2_000
MAX_TITLE_CHARS = 160
MIN_QUESTION_TARGET = 3
MAX_QUESTION_TARGET = 20
DEFAULT_QUESTION_TARGET = 8
MIN_GENERATED_QUESTIONS = 3
MAX_GENERATED_QUESTIONS = 10
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


class DomainError(ValueError):
    """A typed client or model contract failure."""

    def __init__(self, message: str, *, code: str = "invalid_input", field: str = ""):
        super().__init__(message)
        self.code = code
        self.field = field

    def as_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": str(self)}
        if self.field:
            result["field"] = self.field
        return result


@dataclass(frozen=True)
class InterviewEvent:
    id: str
    session_id: str
    expected_revision: int
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SuggestionCandidate:
    label: str
    detail: str


@dataclass(frozen=True)
class PlannedQuestion:
    text: str
    explanation: str
    focus: str = "story"


@dataclass(frozen=True)
class FactCandidate:
    text: str
    source_event_id: str
    evidence: str


@dataclass(frozen=True)
class GapChange:
    action: str
    description: str = ""
    gap_id: str = ""
    impact: str = "blocking"
    evidence: str = ""


@dataclass(frozen=True)
class LensAssessment:
    status: str
    evidence: str = ""


@dataclass(frozen=True)
class ReadinessDecision:
    lenses: dict[str, LensAssessment]
    reason: str
    recommend_outline: bool = False

    @property
    def score(self) -> float:
        supported = sum(
            self.lenses[name].status == "supported" for name in READINESS_LENSES
        )
        return supported / len(READINESS_LENSES)


@dataclass(frozen=True)
class TurnDecision:
    intent: str
    guidance: str = ""
    question: str = ""
    focus: str = "story"
    listening_for: str = ""
    suggestions: tuple[SuggestionCandidate, ...] = field(default_factory=tuple)
    questions: tuple[PlannedQuestion, ...] = field(default_factory=tuple)
    facts: tuple[FactCandidate, ...] = field(default_factory=tuple)
    gap_changes: tuple[GapChange, ...] = field(default_factory=tuple)
    readiness: ReadinessDecision | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "response": {
                "intent": self.intent,
                "guidance": self.guidance,
                "question": self.question,
                "focus": self.focus,
                "listening_for": self.listening_for,
                "suggestions": [
                    {"label": item.label, "detail": item.detail}
                    for item in self.suggestions
                ],
                "questions": [
                    {
                        "text": item.text,
                        "explanation": item.explanation,
                        "focus": item.focus,
                    }
                    for item in self.questions
                ],
            },
            "fact_candidates": [
                {
                    "text": item.text,
                    "source_event_id": item.source_event_id,
                    "evidence": item.evidence,
                }
                for item in self.facts
            ],
            "gap_changes": [
                {
                    "action": item.action,
                    "description": item.description,
                    "gap_id": item.gap_id,
                    "impact": item.impact,
                    "evidence": item.evidence,
                }
                for item in self.gap_changes
            ],
            "readiness": None if self.readiness is None else {
                "lenses": {
                    name: {
                        "status": self.readiness.lenses[name].status,
                        "evidence": self.readiness.lenses[name].evidence,
                    }
                    for name in READINESS_LENSES
                },
                "reason": self.readiness.reason,
                "recommend_outline": self.readiness.recommend_outline,
            },
        }


def _text(value: Any, field_name: str, *, required: bool = False,
          maximum: int = MAX_MESSAGE_CHARS) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise DomainError(f"{field_name} must be text", field=field_name)
    result = value.strip()
    if required and not result:
        raise DomainError(f"{field_name} is required", field=field_name)
    if len(result) > maximum:
        raise DomainError(
            f"{field_name} is too long (maximum {maximum} characters)",
            code="payload_too_large",
            field=field_name,
        )
    return result


def _id(value: Any, field_name: str) -> str:
    value = _text(value, field_name, required=True, maximum=128)
    if not ID_RE.fullmatch(value):
        raise DomainError(
            f"{field_name} must be an opaque 8–128 character identifier",
            field=field_name,
        )
    return value


def parse_event(session_id: str, value: Mapping[str, Any]) -> InterviewEvent:
    if not isinstance(value, Mapping):
        raise DomainError("event body must be an object")
    unknown = set(value) - {"event_id", "expected_revision", "kind", "payload"}
    if unknown:
        raise DomainError(
            "unknown event field(s): " + ", ".join(sorted(unknown)),
            field="event",
        )
    event_id = _id(value.get("event_id"), "event_id")
    session_id = _id(session_id, "session_id")
    kind = _text(value.get("kind"), "kind", required=True, maximum=60)
    if kind not in EVENT_KINDS - {"interview_started"}:
        raise DomainError(f"unsupported event kind: {kind}", field="kind")
    revision = value.get("expected_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise DomainError("expected_revision must be a non-negative integer",
                          field="expected_revision")
    payload = validate_event_payload(
        kind, value["payload"] if "payload" in value else {}
    )
    return InterviewEvent(event_id, session_id, revision, kind, payload)


def make_start_event(session_id: str, event_id: str, *, seed: str,
                     title: str, storytelling_format: str,
                     involvement_mode: str,
                     question_target: int = DEFAULT_QUESTION_TARGET) -> InterviewEvent:
    payload = validate_event_payload("interview_started", {
        "seed": seed,
        "title": title,
        "storytelling_format": storytelling_format,
        "involvement_mode": involvement_mode,
        "question_target": question_target,
    })
    return InterviewEvent(
        _id(event_id, "event_id"), _id(session_id, "session_id"), 0,
        "interview_started", payload,
    )


def validate_event_payload(kind: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DomainError("payload must be an object", field="payload")
    payload: dict[str, Any] = {}

    if kind == "interview_started":
        _reject_unknown(raw, {
            "seed", "title", "storytelling_format", "involvement_mode",
            "question_target",
        })
        payload["seed"] = _text(raw.get("seed"), "seed", required=True)
        payload["title"] = _text(
            raw.get("title") or "Untitled film", "title", required=True,
            maximum=MAX_TITLE_CHARS,
        )
        story_format = _text(
            raw.get("storytelling_format") or "not_sure",
            "storytelling_format", maximum=32,
        )
        if story_format not in {"narrated", "dialogue_led", "hybrid", "not_sure"}:
            raise DomainError("invalid storytelling_format", field="storytelling_format")
        involvement = _text(
            raw.get("involvement_mode") or "collaborative",
            "involvement_mode", maximum=32,
        )
        if involvement not in {"ai_led", "collaborative", "author_led"}:
            raise DomainError("invalid involvement_mode", field="involvement_mode")
        payload["storytelling_format"] = story_format
        payload["involvement_mode"] = involvement
        question_target = raw.get("question_target", DEFAULT_QUESTION_TARGET)
        if (
            not isinstance(question_target, int)
            or isinstance(question_target, bool)
            or not MIN_QUESTION_TARGET <= question_target <= MAX_QUESTION_TARGET
        ):
            raise DomainError(
                f"question_target must be an integer between {MIN_QUESTION_TARGET} and {MAX_QUESTION_TARGET}",
                field="question_target",
            )
        payload["question_target"] = question_target

    elif kind == "message_submitted":
        _reject_unknown(raw, {"text", "purpose", "response_id"})
        payload["text"] = _text(raw.get("text"), "text", required=True)
        purpose = _text(raw.get("purpose") or "answer", "purpose", maximum=32)
        if purpose not in MESSAGE_PURPOSES:
            raise DomainError("invalid message purpose", field="purpose")
        payload["purpose"] = purpose
        if raw.get("response_id"):
            payload["response_id"] = _id(raw.get("response_id"), "response_id")

    elif kind in {"questionnaire_submitted", "questionnaire_revised"}:
        allowed = {"answers", "response_id"} if kind == "questionnaire_submitted" else {
            "answers", "questionnaire_response_id", "target_event_id"
        }
        _reject_unknown(raw, allowed)
        answers = raw.get("answers")
        if not isinstance(answers, list) or not 1 <= len(answers) <= MAX_QUESTION_TARGET:
            raise DomainError(
                f"answers must contain one to {MAX_QUESTION_TARGET} answered questions",
                field="answers",
            )
        cleaned_answers: list[dict[str, str]] = []
        total_chars = 0
        seen: set[str] = set()
        for index, item in enumerate(answers):
            if not isinstance(item, Mapping):
                raise DomainError("each answer must be an object", field=f"answers.{index}")
            _reject_unknown(item, {"question_id", "text"})
            question_id = _id(item.get("question_id"), f"answers.{index}.question_id")
            if question_id in seen:
                raise DomainError("question IDs must be unique", field="answers")
            seen.add(question_id)
            text = _text(item.get("text"), f"answers.{index}.text", required=True, maximum=4_000)
            total_chars += len(text)
            cleaned_answers.append({"question_id": question_id, "text": text})
        if total_chars > MAX_MESSAGE_CHARS:
            raise DomainError("combined answers are too long", field="answers")
        payload["answers"] = cleaned_answers
        if kind == "questionnaire_submitted":
            payload["response_id"] = _id(raw.get("response_id"), "response_id")
        else:
            payload["questionnaire_response_id"] = _id(
                raw.get("questionnaire_response_id"), "questionnaire_response_id"
            )
            payload["target_event_id"] = _id(
                raw.get("target_event_id"), "target_event_id"
            )

    elif kind == "question_ideas_requested":
        _reject_unknown(raw, {"question_id", "response_id", "draft_answer"})
        payload["question_id"] = _id(raw.get("question_id"), "question_id")
        payload["response_id"] = _id(raw.get("response_id"), "response_id")
        payload["draft_answer"] = _text(
            raw.get("draft_answer"), "draft_answer", maximum=4_000
        )

    elif kind in {"suggestions_selected", "suggestions_rejected"}:
        _reject_unknown(raw, {"suggestion_ids", "note"})
        ids = raw.get("suggestion_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 3:
            raise DomainError("suggestion_ids must contain one to three IDs",
                              field="suggestion_ids")
        cleaned = [_id(item, "suggestion_ids") for item in ids]
        if len(set(cleaned)) != len(cleaned):
            raise DomainError("suggestion_ids must be unique", field="suggestion_ids")
        payload["suggestion_ids"] = cleaned
        payload["note"] = _text(raw.get("note"), "note", maximum=MAX_NOTE_CHARS)

    elif kind in {"question_skipped", "reflection_confirmed", "response_continued"}:
        _reject_unknown(raw, {"response_id", "note"})
        payload["response_id"] = _id(raw.get("response_id"), "response_id")
        payload["note"] = _text(raw.get("note"), "note", maximum=MAX_NOTE_CHARS)

    elif kind == "decision_revised":
        _reject_unknown(raw, {"target_event_id", "replacement_text"})
        payload["target_event_id"] = _id(raw.get("target_event_id"), "target_event_id")
        payload["replacement_text"] = _text(
            raw.get("replacement_text"), "replacement_text", required=True
        )

    elif kind in {"suggestions_requested", "continue_interview", "interview_finished"}:
        _reject_unknown(raw, {"response_id"})
        payload["response_id"] = (
            _id(raw.get("response_id"), "response_id")
            if raw.get("response_id") else ""
        )
    else:
        raise DomainError(f"unsupported event kind: {kind}", field="kind")

    return payload


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise DomainError(
            "unknown payload field(s): " + ", ".join(sorted(unknown)),
            field="payload",
        )


def parse_turn_decision(value: Mapping[str, Any]) -> TurnDecision:
    if not isinstance(value, Mapping):
        raise DomainError("model decision must be an object", code="invalid_model_output")
    response = value.get("response")
    if not isinstance(response, Mapping):
        raise DomainError("decision.response must be an object", code="invalid_model_output")

    intent = _text(response.get("intent"), "response.intent", required=True, maximum=60)
    if intent not in RESPONSE_INTENTS:
        raise DomainError("unknown response intent", code="invalid_model_output",
                          field="response.intent")
    guidance = _text(response.get("guidance"), "response.guidance", maximum=1_200)
    question = _text(response.get("question"), "response.question", maximum=400)
    focus = _text(response.get("focus") or "story", "response.focus", maximum=80)
    listening_for = _text(
        response.get("listening_for"), "response.listening_for", maximum=240
    )

    suggestions: list[SuggestionCandidate] = []
    raw_suggestions = response.get("suggestions")
    if not isinstance(raw_suggestions, list):
        raise DomainError("response.suggestions must be a list", code="invalid_model_output")
    for item in raw_suggestions:
        if not isinstance(item, Mapping):
            raise DomainError("each suggestion must be an object", code="invalid_model_output")
        suggestions.append(SuggestionCandidate(
            label=_text(item.get("label"), "suggestion.label", required=True, maximum=100),
            detail=_text(item.get("detail"), "suggestion.detail", required=True, maximum=320),
        ))

    questions: list[PlannedQuestion] = []
    raw_questions = response.get("questions", [])
    if not isinstance(raw_questions, list):
        raise DomainError("response.questions must be a list", code="invalid_model_output")
    for item in raw_questions:
        if not isinstance(item, Mapping):
            raise DomainError("each planned question must be an object", code="invalid_model_output")
        questions.append(PlannedQuestion(
            text=_text(item.get("text"), "planned_question.text", required=True, maximum=400),
            explanation=_text(
                item.get("explanation"), "planned_question.explanation",
                required=True, maximum=200,
            ),
            focus=_text(item.get("focus") or "story", "planned_question.focus", maximum=80),
        ))

    facts: list[FactCandidate] = []
    raw_facts = value.get("fact_candidates")
    if not isinstance(raw_facts, list):
        raise DomainError("fact_candidates must be a list", code="invalid_model_output")
    for item in raw_facts:
        if not isinstance(item, Mapping):
            raise DomainError("each fact candidate must be an object", code="invalid_model_output")
        facts.append(FactCandidate(
            text=_text(item.get("text"), "fact.text", required=True, maximum=500),
            source_event_id=_id(item.get("source_event_id"), "fact.source_event_id"),
            evidence=_text(item.get("evidence"), "fact.evidence", required=True, maximum=500),
        ))

    changes: list[GapChange] = []
    raw_changes = value.get("gap_changes")
    if not isinstance(raw_changes, list):
        raise DomainError("gap_changes must be a list", code="invalid_model_output")
    for item in raw_changes:
        if not isinstance(item, Mapping):
            raise DomainError("each gap change must be an object", code="invalid_model_output")
        action = _text(item.get("action"), "gap.action", required=True, maximum=20)
        if action not in {"open", "resolve"}:
            raise DomainError("gap action must be open or resolve", code="invalid_model_output")
        impact = _text(item.get("impact") or "blocking", "gap.impact", maximum=40)
        if impact not in GAP_IMPACTS:
            raise DomainError(
                "unsupported gap impact", code="invalid_model_output",
                field="gap.impact",
            )
        changes.append(GapChange(
            action=action,
            description=_text(item.get("description"), "gap.description", maximum=400),
            gap_id=_text(item.get("gap_id"), "gap.gap_id", maximum=128),
            impact=impact,
            evidence=_text(item.get("evidence"), "gap.evidence", maximum=500),
        ))

    readiness = _parse_readiness(value.get("readiness"))
    return TurnDecision(
        intent=intent,
        guidance=guidance,
        question=question,
        focus=focus,
        listening_for=listening_for,
        suggestions=tuple(suggestions),
        questions=tuple(questions),
        facts=tuple(facts),
        gap_changes=tuple(changes),
        readiness=readiness,
    )


def _parse_readiness(raw: Any) -> ReadinessDecision | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise DomainError("readiness must be an object", code="invalid_model_output")
    raw_lenses = raw.get("lenses")
    if not isinstance(raw_lenses, Mapping):
        raise DomainError("readiness.lenses must be an object", code="invalid_model_output")
    lenses: dict[str, LensAssessment] = {}
    for name in READINESS_LENSES:
        item = raw_lenses.get(name)
        if not isinstance(item, Mapping):
            raise DomainError(f"readiness lens {name} is required", code="invalid_model_output")
        status = _text(item.get("status"), f"readiness.{name}.status",
                       required=True, maximum=20)
        if status not in LENS_STATUSES:
            raise DomainError(f"invalid readiness status for {name}", code="invalid_model_output")
        evidence = _text(item.get("evidence"), f"readiness.{name}.evidence", maximum=500)
        lenses[name] = LensAssessment(status, evidence)
    recommend_outline = raw.get("recommend_outline")
    if not isinstance(recommend_outline, bool):
        raise DomainError(
            "readiness.recommend_outline must be a boolean",
            code="invalid_model_output",
        )
    return ReadinessDecision(
        lenses=lenses,
        reason=_text(raw.get("reason"), "readiness.reason", required=True, maximum=600),
        recommend_outline=recommend_outline,
    )


def normalize_story_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", value.lower()))


def question_similarity(left: str, right: str) -> float:
    a, b = normalize_story_text(left), normalize_story_text(right)
    if not a or not b:
        return 0.0
    sequence = SequenceMatcher(None, a, b).ratio()
    at, bt = set(a.split()), set(b.split())
    jaccard = len(at & bt) / max(1, len(at | bt))
    return max(sequence, jaccard)


def event_can_establish_facts(event: InterviewEvent) -> bool:
    """Whether the filmmaker event contains accepted creative material.

    A question asks the editor for help, and an instruction directs the editor;
    neither is an assertion about the film. Keeping this rule in the
    deterministic boundary prevents a model from turning either into canon.
    """
    if event.kind == "message_submitted":
        return event.payload.get("purpose") in {"answer", "correction", "nuance"}
    return event.kind in {
        "interview_started",
        "questionnaire_submitted",
        "questionnaire_revised",
        "suggestions_selected",
        "reflection_confirmed",
        "decision_revised",
    }


def validate_decision(decision: TurnDecision, *, event: InterviewEvent,
                      recent_questions: Iterable[str], source_text: str,
                      active_gap_ids: Iterable[str],
                      accepted_source_texts: Iterable[str] = (),
                      question_target: int | None = None,
                      questions_asked: int = 0) -> list[str]:
    """Return independent, exact repair reasons without mutating state."""
    components = validate_decision_components(
        decision,
        event=event,
        recent_questions=recent_questions,
        source_text=source_text,
        active_gap_ids=active_gap_ids,
        accepted_source_texts=accepted_source_texts,
        question_target=question_target,
        questions_asked=questions_asked,
    )
    return [
        error
        for name in ("response", "facts", "gaps", "readiness")
        for error in components[name]
    ]


def validate_decision_components(
    decision: TurnDecision,
    *,
    event: InterviewEvent,
    recent_questions: Iterable[str],
    source_text: str,
    active_gap_ids: Iterable[str],
    accepted_source_texts: Iterable[str] = (),
    question_target: int | None = None,
    questions_asked: int = 0,
) -> dict[str, list[str]]:
    """Validate response and state components independently.

    The component boundary lets the agent keep a useful response when, for
    example, only an optional readiness assessment has unsupported evidence.
    """
    errors: dict[str, list[str]] = {
        "response": [],
        "facts": [],
        "gaps": [],
        "readiness": [],
    }
    active_gap_ids = set(active_gap_ids)
    if decision.intent not in ALLOWED_INTENTS[event.kind]:
        errors["response"].append(
            f"intent {decision.intent!r} is not allowed for event {event.kind!r}"
        )
    if (
        event.kind == "message_submitted"
        and event.payload.get("purpose") == "question"
        and decision.intent != "coach_writer"
    ):
        errors["response"].append("a direct filmmaker question must be answered with coach_writer")
    if decision.question.count("?") > 1:
        errors["response"].append("the response contains more than one main question")
    if "?" in decision.guidance:
        errors["response"].append(
            "guidance cannot contain a question; use the main question field"
        )
    if decision.question and re.search(
        r"\b(?:and|or)\s+(?:how|what|why|where|when|who|which|does|do|is|are|will|would|could|should|can)\b",
        decision.question,
        flags=re.IGNORECASE,
    ):
        errors["response"].append("the response contains a compound main question")
    if decision.intent == "ask_question" and not decision.question:
        errors["response"].append("ask_question requires one question")
    if decision.intent == "ask_questions":
        if decision.question:
            errors["response"].append("ask_questions uses the structured questions list")
        if not MIN_GENERATED_QUESTIONS <= len(decision.questions) <= MAX_GENERATED_QUESTIONS:
            errors["response"].append(
                f"ask_questions requires {MIN_GENERATED_QUESTIONS} to "
                f"{MAX_GENERATED_QUESTIONS} planned questions"
            )
    elif decision.questions:
        errors["response"].append("structured questions require ask_questions intent")
    normalized_planned: list[str] = []
    for item in decision.questions:
        if item.text.count("?") != 1:
            errors["response"].append("each planned question requires one question mark")
        if re.search(
            r"\b(?:and|or)\s+(?:how|what|why|where|when|who|which|does|do|is|are|will|would|could|should|can)\b",
            item.text,
            flags=re.IGNORECASE,
        ):
            errors["response"].append("a planned question contains two questions")
        normalized = normalize_story_text(item.text)
        if normalized in normalized_planned:
            errors["response"].append("planned questions must be distinct")
        normalized_planned.append(normalized)
        if len(item.explanation.split()) > 18:
            errors["response"].append("planned question explanations must use 18 words or fewer")
    if (
        event.kind == "message_submitted"
        and event.payload.get("purpose") == "question"
        and decision.question
    ):
        errors["response"].append(
            "a direct filmmaker question must be answered without asking another question"
        )
    if (
        decision.question
        and question_target is not None
        and questions_asked >= question_target
        and event.kind not in {"response_continued", "continue_interview"}
    ):
        errors["response"].append(
            "the filmmaker's question target has been reached; pause or help without another question"
        )
    if decision.intent == "offer_suggestions" and not 2 <= len(decision.suggestions) <= 3:
        errors["response"].append("offer_suggestions requires two or three suggestion cards")
    if decision.suggestions and not 2 <= len(decision.suggestions) <= 3:
        errors["response"].append("a response containing suggestions needs two or three cards")
    labels = [normalize_story_text(item.label) for item in decision.suggestions]
    if len(labels) != len(set(labels)):
        errors["response"].append("suggestion labels must be distinct")
    for old in recent_questions:
        if decision.question and question_similarity(decision.question, old) >= 0.82:
            errors["response"].append("the main question repeats a recent question")
            break

    source_normalized = normalize_story_text(source_text)
    can_establish_facts = event_can_establish_facts(event)
    if decision.facts and not can_establish_facts:
        errors["facts"].append(
            f"event {event.kind!r} cannot establish story facts"
        )
    for fact in decision.facts:
        if fact.source_event_id != event.id:
            errors["facts"].append(
                f"fact {fact.text!r} must cite the current filmmaker event"
            )
        evidence = normalize_story_text(fact.evidence)
        if not evidence or evidence not in source_normalized:
            errors["facts"].append(
                f"fact {fact.text!r} does not contain an exact excerpt from its source"
            )
        fact_text = normalize_story_text(fact.text)
        if not fact_text or fact_text not in source_normalized:
            errors["facts"].append(
                f"fact {fact.text!r} is not an exact excerpt from accepted filmmaker material"
            )

    known_gaps = active_gap_ids
    for change in decision.gap_changes:
        if change.impact not in GAP_IMPACTS:
            errors["gaps"].append(f"unsupported gap impact {change.impact!r}")
        if change.action == "open" and not change.description:
            errors["gaps"].append("opening a gap requires a concrete description")
        if change.action == "resolve" and change.gap_id not in known_gaps:
            errors["gaps"].append(f"cannot resolve unknown active gap {change.gap_id!r}")

    if decision.readiness:
        evidence_sources = [
            normalize_story_text(item) for item in accepted_source_texts
            if normalize_story_text(item)
        ]
        if can_establish_facts and source_normalized:
            evidence_sources.append(source_normalized)
        for name, lens in decision.readiness.lenses.items():
            if lens.status == "supported" and not lens.evidence:
                errors["readiness"].append(f"supported readiness lens {name!r} requires evidence")
            elif lens.status == "supported":
                evidence = normalize_story_text(lens.evidence)
                if not any(evidence in source for source in evidence_sources):
                    errors["readiness"].append(
                        f"supported readiness lens {name!r} must cite an exact excerpt "
                        "from accepted story material"
                    )
        all_supported = all(
            decision.readiness.lenses[name].status == "supported"
            for name in READINESS_LENSES
        )
        if decision.readiness.recommend_outline and not all_supported:
            errors["readiness"].append("outline recommendation requires all four readiness lenses")
        if decision.intent == "recommend_outline" and not all_supported:
            errors["readiness"].append("recommend_outline requires all four readiness lenses")
        if decision.intent == "recommend_outline" and not decision.readiness.recommend_outline:
            errors["readiness"].append("recommend_outline intent requires the readiness recommendation flag")
        if decision.readiness.recommend_outline and decision.intent != "recommend_outline":
            errors["readiness"].append("the readiness recommendation flag requires recommend_outline intent")
        resolved = {
            change.gap_id for change in decision.gap_changes
            if change.action == "resolve"
        }
        remaining_gaps = active_gap_ids - resolved
        opens_blocking = any(
            change.action == "open" and change.impact == "blocking"
            for change in decision.gap_changes
        )
        if decision.readiness.recommend_outline and (remaining_gaps or opens_blocking):
            errors["readiness"].append("outline recommendation cannot leave a blocking story gap open")
    elif decision.intent == "recommend_outline":
        errors["readiness"].append("recommend_outline requires a readiness assessment")
    return errors


TURN_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["response", "fact_candidates", "gap_changes", "readiness"],
    "properties": {
        "response": {
            "type": "object",
            "required": ["intent", "guidance", "question", "focus", "listening_for", "suggestions", "questions"],
            "properties": {
                "intent": {"type": "string", "enum": sorted(RESPONSE_INTENTS)},
                "guidance": {"type": "string"},
                "question": {"type": "string"},
                "focus": {"type": "string"},
                "listening_for": {"type": "string"},
                "suggestions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["label", "detail"],
                        "properties": {
                            "label": {"type": "string"},
                            "detail": {"type": "string"},
                        },
                    },
                },
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["text", "explanation", "focus"],
                        "properties": {
                            "text": {"type": "string"},
                            "explanation": {
                                "type": "string",
                                "description": "One everyday-language sentence of 18 words or fewer explaining what the answer helps decide.",
                            },
                            "focus": {"type": "string"},
                        },
                    },
                },
            },
        },
        "fact_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "source_event_id", "evidence"],
                "properties": {
                    "text": {"type": "string"},
                    "source_event_id": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        },
        "gap_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["action", "description", "gap_id", "impact", "evidence"],
                "properties": {
                    "action": {"type": "string", "enum": ["open", "resolve"]},
                    "description": {"type": "string"},
                    "gap_id": {"type": "string"},
                    "impact": {"type": "string", "enum": sorted(GAP_IMPACTS)},
                    "evidence": {"type": "string"},
                },
            },
        },
        "readiness": {
            "type": "object",
            "required": ["lenses", "reason", "recommend_outline"],
            "properties": {
                "lenses": {
                    "type": "object",
                    "required": list(READINESS_LENSES),
                    "properties": {
                        name: {
                            "type": "object",
                            "required": ["status", "evidence"],
                            "properties": {
                                "status": {"type": "string", "enum": sorted(LENS_STATUSES)},
                                "evidence": {"type": "string"},
                            },
                        }
                        for name in READINESS_LENSES
                    },
                },
                "reason": {"type": "string"},
                "recommend_outline": {"type": "boolean"},
            },
        },
    },
}

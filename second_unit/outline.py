"""Versioned, deterministic tools for the Outline stage.

The model may propose operations through these contracts, but it never writes
SQLite directly. Every mutation is validated, revision-checked, transactional,
and captured as a complete outline snapshot.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .database import NotFound, Repository


STRUCTURE_KINDS = {
    "acts", "sequences", "visual_progression", "narration_led", "hybrid", "custom"
}
STRUCTURE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "kind": "visual_progression",
        "label": "Visual progression",
        "description": (
            "Build the film around visible actions, images, and turning points. "
            "Useful when the audience should understand the story by watching it unfold."
        ),
    },
    {
        "kind": "sequences",
        "label": "Sequences",
        "description": (
            "Group the film into connected movements, each with its own goal or change. "
            "Useful for journeys, chapters, locations, or shifts in time."
        ),
    },
    {
        "kind": "acts",
        "label": "Acts",
        "description": (
            "Shape the film through setup, mounting pressure, and a decisive ending. "
            "Useful when the story has a clear dramatic problem and turning point."
        ),
    },
    {
        "kind": "narration_led",
        "label": "Narration-led",
        "description": (
            "Let a spoken voice guide the story while images reveal, support, or challenge it. "
            "Useful for memory, confession, reflection, or an unseen storyteller."
        ),
    },
    {
        "kind": "hybrid",
        "label": "Hybrid",
        "description": (
            "Combine visual passages, character scenes, and narration where each works best. "
            "Useful when the film needs more than one storytelling mode."
        ),
    },
    {
        "kind": "custom",
        "label": "Custom",
        "description": (
            "Describe a different organizing idea when none of these shapes fits the film."
        ),
    },
)
STRUCTURE_PRESETS = {
    option["kind"]: option for option in STRUCTURE_OPTIONS if option["kind"] != "custom"
}
ACTORS = {"filmmaker", "agent", "system"}
BEAT_ORIGINS = {"filmmaker", "agent", "interview"}


@dataclass(frozen=True)
class OutlineToolError(ValueError):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            value["details"] = self.details
        return value


class OutlineRevisionConflict(OutlineToolError):
    def __init__(self, current_revision: int):
        super().__init__(
            "stale_outline_revision",
            f"outline changed; current revision is {current_revision}",
            {"current_revision": current_revision},
        )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _text(value: Any, field: str, *, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise OutlineToolError("invalid_outline_tool_input", f"{field} must be text")
    cleaned = " ".join(value.strip().split())
    if required and not cleaned:
        raise OutlineToolError("invalid_outline_tool_input", f"{field} is required")
    if len(cleaned) > maximum:
        raise OutlineToolError(
            "invalid_outline_tool_input", f"{field} must be {maximum} characters or fewer"
        )
    return cleaned


def _identifier(value: Any, field: str) -> str:
    cleaned = _text(value, field, maximum=128)
    if len(cleaned) < 8 or not all(character.isalnum() or character in "_.:-" for character in cleaned):
        raise OutlineToolError("invalid_outline_tool_input", f"{field} is not a valid identifier")
    return cleaned


def _expected_revision(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OutlineToolError(
            "invalid_outline_tool_input", "expected_revision must be a non-negative integer"
        )
    return value


def _actor(value: Any) -> str:
    actor = _text(value, "actor", maximum=20)
    if actor not in ACTORS:
        raise OutlineToolError("invalid_outline_tool_input", "actor is not supported")
    return actor


OUTLINE_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "read_interview_handoff",
        "description": "Read accepted Interview material, open decisions, and current Outline state.",
        "mutates": False,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id"],
            "properties": {"session_id": {"type": "string"}},
        },
    },
    {
        "name": "propose_story_structure",
        "description": "Add one visible structure proposal without selecting it for the filmmaker.",
        "mutates": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id", "expected_revision", "kind", "label", "rationale"],
            "properties": {
                "session_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "kind": {"type": "string", "enum": sorted(STRUCTURE_KINDS)},
                "label": {"type": "string", "maxLength": 100},
                "rationale": {"type": "string", "maxLength": 1200},
            },
        },
    },
    {
        "name": "select_story_structure",
        "description": "Accept a proposed structure after an explicit filmmaker action.",
        "mutates": True,
        "requires_filmmaker_action": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id", "expected_revision", "structure_id"],
            "properties": {
                "session_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "structure_id": {"type": "string"},
                "note": {"type": "string", "maxLength": 1000},
            },
        },
    },
    {
        "name": "create_beat",
        "description": "Create an ordered beat under the accepted structure; agent-created beats remain proposals.",
        "mutates": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id", "expected_revision", "title", "summary"],
            "properties": {
                "session_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "title": {"type": "string", "maxLength": 160},
                "summary": {"type": "string", "maxLength": 2000},
                "purpose": {"type": "string", "maxLength": 800},
                "position": {"type": "integer", "minimum": 1},
                "source_refs": {
                    "type": "array", "maxItems": 20, "uniqueItems": True,
                    "items": {"type": "string"},
                },
            },
        },
    },
    {
        "name": "create_beat_batch",
        "description": "Atomically save one complete set of proposed beats under the selected structure.",
        "mutates": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id", "expected_revision", "beats"],
            "properties": {
                "session_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "beats": {
                    "type": "array", "minItems": 1, "maxItems": 12,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["title", "summary", "purpose", "source_refs"],
                        "properties": {
                            "title": {"type": "string", "maxLength": 160},
                            "summary": {"type": "string", "maxLength": 2000},
                            "purpose": {"type": "string", "maxLength": 800},
                            "source_refs": {
                                "type": "array", "maxItems": 20, "uniqueItems": True,
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
    {
        "name": "edit_beat",
        "description": "Edit a beat while protecting accepted material from silent agent rewrites.",
        "mutates": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id", "expected_revision", "beat_id"],
            "properties": {
                "session_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "beat_id": {"type": "string"},
                "title": {"type": "string", "maxLength": 160},
                "summary": {"type": "string", "maxLength": 2000},
                "purpose": {"type": "string", "maxLength": 800},
            },
            "anyOf": [{"required": ["title"]}, {"required": ["summary"]}, {"required": ["purpose"]}],
        },
    },
    {
        "name": "move_beat",
        "description": "Move a beat and normalize all active beat positions atomically.",
        "mutates": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id", "expected_revision", "beat_id", "position"],
            "properties": {
                "session_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "beat_id": {"type": "string"},
                "position": {"type": "integer", "minimum": 1},
            },
        },
    },
    {
        "name": "archive_beat",
        "description": "Soft-delete a beat so its history remains recoverable.",
        "mutates": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id", "expected_revision", "beat_id"],
            "properties": {
                "session_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "beat_id": {"type": "string"},
            },
        },
    },
    {
        "name": "record_beat_decision",
        "description": "Accept, reject, or reopen a beat after an explicit filmmaker action.",
        "mutates": True,
        "requires_filmmaker_action": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id", "expected_revision", "beat_id", "decision"],
            "properties": {
                "session_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "beat_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["accepted", "rejected", "reopened"]},
                "note": {"type": "string", "maxLength": 1000},
            },
        },
    },
    {
        "name": "record_outline_decision",
        "description": "Approve or reopen the complete Outline after an explicit filmmaker action.",
        "mutates": True,
        "requires_filmmaker_action": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["session_id", "expected_revision", "decision"],
            "properties": {
                "session_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "decision": {"type": "string", "enum": ["accepted", "reopened"]},
                "note": {"type": "string", "maxLength": 1000},
            },
        },
    },
)


class OutlineToolkit:
    """Controlled Outline operations for UI actions and future agent calls."""

    def __init__(self, repository: Repository):
        self.repository = repository

    def execute(
        self, name: str, arguments: Mapping[str, Any], *, caller: str = "agent"
    ) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise OutlineToolError("invalid_outline_tool_input", "tool arguments must be an object")
        caller = _actor(caller)
        methods = {
            "read_interview_handoff": self.read_interview_handoff,
            "propose_story_structure": self.propose_story_structure,
            "select_story_structure": self.select_story_structure,
            "create_beat": self.create_beat,
            "create_beat_batch": self.create_beat_batch,
            "edit_beat": self.edit_beat,
            "move_beat": self.move_beat,
            "archive_beat": self.archive_beat,
            "record_beat_decision": self.record_beat_decision,
            "record_outline_decision": self.record_outline_decision,
        }
        method = methods.get(name)
        if not method:
            raise OutlineToolError("unknown_outline_tool", f"unknown Outline tool: {name}")
        call_arguments = dict(arguments)
        if "actor" in call_arguments or "origin" in call_arguments:
            raise OutlineToolError(
                "invalid_outline_tool_input", "caller identity cannot be supplied in tool arguments"
            )
        if name in {
            "select_story_structure", "edit_beat", "move_beat", "archive_beat",
            "record_beat_decision", "record_outline_decision",
        }:
            call_arguments["actor"] = caller
        elif name == "propose_story_structure":
            call_arguments["actor"] = caller
        elif name in {"create_beat", "create_beat_batch"}:
            call_arguments["origin"] = "interview" if caller == "system" else caller
        try:
            return method(**call_arguments)
        except TypeError as exc:
            raise OutlineToolError(
                "invalid_outline_tool_input", f"invalid arguments for {name}"
            ) from exc

    def read_interview_handoff(self, session_id: str) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        state = self.repository.get_session(session_id)
        timeline = state["timeline"]
        questions = {
            question["id"]: question
            for item in timeline
            for question in ((item.get("response") or {}).get("questions") or [])
        }
        submission = next((
            item for item in reversed(timeline)
            if item["event"]["status"] == "completed"
            and item["event"]["kind"] in {"questionnaire_submitted", "questionnaire_revised"}
        ), None)
        answers: list[dict[str, Any]] = []
        answered_ids: set[str] = set()
        if submission:
            for answer in submission["event"]["payload"].get("answers", []):
                question = questions.get(answer["question_id"], {})
                answered_ids.add(answer["question_id"])
                answers.append({
                    "question_id": answer["question_id"],
                    "question": question.get("text", "Interview question"),
                    "answer": answer["text"],
                    "source_event_id": submission["event"]["id"],
                })
        questionnaire_ids: list[str] = []
        if submission:
            payload = submission["event"]["payload"]
            response_id = payload.get("response_id") or payload.get("questionnaire_response_id")
            questionnaire = next((
                item.get("response") for item in timeline
                if (item.get("response") or {}).get("id") == response_id
            ), None)
            questionnaire_ids = [item["id"] for item in (questionnaire or {}).get("questions", [])]

        return {
            "interview": {
                "id": state["session"]["id"],
                "title": state["session"]["title"],
                "seed": state["session"]["seed"],
                "storytelling_format": state["session"]["storytelling_format"],
                "involvement_mode": state["session"]["involvement_mode"],
                "status": state["session"]["status"],
                "revision": state["session"]["revision"],
            },
            "accepted_facts": state["facts"],
            "answers": answers,
            "open_questions": [
                {"question_id": item, "question": questions[item]["text"]}
                for item in questionnaire_ids if item not in answered_ids and item in questions
            ],
            "open_gaps": state["gaps"],
            "structure_options": [dict(option) for option in STRUCTURE_OPTIONS],
            "outline": self.read_outline(session_id, create=False),
        }

    def read_outline(self, session_id: str, *, create: bool = True) -> dict[str, Any] | None:
        session_id = _identifier(session_id, "session_id")
        with self.repository.transaction(immediate=create) as db:
            outline = self._outline_for_session(db, session_id, create=create)
            return self._snapshot(db, outline["id"]) if outline else None

    def propose_story_structure(
        self, session_id: str, expected_revision: int, kind: str,
        label: str, rationale: str, actor: str = "agent",
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        expected_revision = _expected_revision(expected_revision)
        actor = _actor(actor)
        kind = _text(kind, "kind", maximum=40)
        if kind not in STRUCTURE_KINDS:
            raise OutlineToolError("invalid_outline_tool_input", "structure kind is not supported")
        label = _text(label, "label", maximum=100)
        rationale = _text(rationale, "rationale", maximum=1200)
        with self.repository.transaction(immediate=True) as db:
            outline = self._outline_for_session(db, session_id, create=True)
            self._check_revision(outline, expected_revision)
            count = db.execute(
                "SELECT COUNT(*) FROM outline_structures WHERE outline_id = ?", (outline["id"],)
            ).fetchone()[0]
            if count >= 5:
                raise OutlineToolError("outline_limit", "an outline can hold at most five structure proposals")
            structure_id = _new_id("structure")
            db.execute(
                """INSERT INTO outline_structures
                   (id, outline_id, position, kind, label, rationale, origin)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (structure_id, outline["id"], count + 1, kind, label, rationale, actor),
            )
            return self._finish_mutation(
                db, outline, expected_revision, "propose_story_structure", actor
            )

    def select_story_structure(
        self, session_id: str, expected_revision: int, structure_id: str,
        note: str = "", actor: str = "filmmaker",
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        expected_revision = _expected_revision(expected_revision)
        structure_id = _identifier(structure_id, "structure_id")
        actor = _actor(actor)
        note = _text(note, "note", maximum=1000, required=False)
        if actor != "filmmaker":
            raise OutlineToolError("filmmaker_action_required", "only the filmmaker can select a structure")
        with self.repository.transaction(immediate=True) as db:
            outline = self._outline_for_session(db, session_id, create=False)
            if not outline:
                raise OutlineToolError("outline_not_found", "outline has not been created")
            self._check_revision(outline, expected_revision)
            structure = self._structure(db, outline["id"], structure_id)
            other_beats = db.execute(
                """SELECT COUNT(*) FROM outline_beats
                   WHERE outline_id = ? AND structure_id != ? AND status = 'active'""",
                (outline["id"], structure_id),
            ).fetchone()[0]
            if other_beats:
                raise OutlineToolError(
                    "outline_structure_in_use",
                    "archive beats from the current structure before selecting another one",
                )
            db.execute(
                """UPDATE outline_structures SET approval_status =
                       CASE WHEN id = ? THEN 'accepted' ELSE 'rejected' END,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE outline_id = ?""",
                (structure_id, outline["id"]),
            )
            db.execute(
                "UPDATE outlines SET active_structure_id = ? WHERE id = ?",
                (structure_id, outline["id"]),
            )
            self._record_approval(
                db, outline["id"], "structure", structure["id"], "accepted", actor, note
            )
            return self._finish_mutation(
                db, outline, expected_revision, "select_story_structure", actor
            )

    def create_beat(
        self, session_id: str, expected_revision: int, title: str, summary: str,
        purpose: str = "", position: int | None = None, origin: str = "agent",
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        expected_revision = _expected_revision(expected_revision)
        title = _text(title, "title", maximum=160)
        summary = _text(summary, "summary", maximum=2000)
        purpose = _text(purpose, "purpose", maximum=800, required=False)
        origin = _text(origin, "origin", maximum=20)
        if origin not in BEAT_ORIGINS:
            raise OutlineToolError("invalid_outline_tool_input", "beat origin is not supported")
        if source_refs is not None and not isinstance(source_refs, list):
            raise OutlineToolError("invalid_outline_tool_input", "source_refs must be a list")
        refs = [_identifier(item, "source_refs") for item in (source_refs or [])]
        if len(refs) > 20 or len(set(refs)) != len(refs):
            raise OutlineToolError("invalid_outline_tool_input", "source_refs must contain unique IDs")
        with self.repository.transaction(immediate=True) as db:
            outline = self._outline_for_session(db, session_id, create=False)
            if not outline:
                raise OutlineToolError("outline_not_found", "outline has not been created")
            self._check_revision(outline, expected_revision)
            structure_id = outline["active_structure_id"]
            if not structure_id:
                raise OutlineToolError("outline_structure_required", "select a structure before creating beats")
            ordered = self._active_beat_ids(db, outline["id"])
            target = len(ordered) + 1 if position is None else position
            if not isinstance(target, int) or isinstance(target, bool) or not 1 <= target <= len(ordered) + 1:
                raise OutlineToolError("invalid_outline_tool_input", "position is outside the beat list")
            beat_id = _new_id("beat")
            approval = "accepted" if origin == "filmmaker" else "proposed"
            db.execute(
                """INSERT INTO outline_beats
                   (id, outline_id, structure_id, position, title, summary, purpose,
                    origin, approval_status, source_refs_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    beat_id, outline["id"], structure_id, len(ordered) + 10001,
                    title, summary, purpose, origin, approval,
                    json.dumps(refs, ensure_ascii=False),
                ),
            )
            ordered.insert(target - 1, beat_id)
            self._set_beat_order(db, outline["id"], ordered)
            if approval == "accepted":
                self._record_approval(
                    db, outline["id"], "beat", beat_id, "accepted", "filmmaker", ""
                )
            version_actor = "system" if origin == "interview" else origin
            return self._finish_mutation(
                db, outline, expected_revision, "create_beat", version_actor
            )

    def create_beat_batch(
        self, session_id: str, expected_revision: int,
        beats: list[Mapping[str, Any]], origin: str = "agent",
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        expected_revision = _expected_revision(expected_revision)
        origin = _text(origin, "origin", maximum=20)
        if origin not in BEAT_ORIGINS:
            raise OutlineToolError("invalid_outline_tool_input", "beat origin is not supported")
        if not isinstance(beats, list) or not 1 <= len(beats) <= 12:
            raise OutlineToolError(
                "invalid_outline_tool_input", "beats must contain between one and twelve items"
            )
        cleaned: list[dict[str, Any]] = []
        titles: set[str] = set()
        for index, beat in enumerate(beats, 1):
            if not isinstance(beat, Mapping):
                raise OutlineToolError(
                    "invalid_outline_tool_input", f"beat {index} must be an object"
                )
            unknown = set(beat) - {"title", "summary", "purpose", "source_refs"}
            if unknown:
                raise OutlineToolError(
                    "invalid_outline_tool_input", f"beat {index} contains unsupported fields"
                )
            title = _text(beat.get("title"), f"beat {index} title", maximum=160)
            normalized_title = title.casefold()
            if normalized_title in titles:
                raise OutlineToolError(
                    "invalid_outline_tool_input", "beat titles must be distinct"
                )
            titles.add(normalized_title)
            summary = _text(beat.get("summary"), f"beat {index} summary", maximum=2000)
            purpose = _text(
                beat.get("purpose", ""), f"beat {index} purpose",
                maximum=800, required=False,
            )
            refs_value = beat.get("source_refs", [])
            if not isinstance(refs_value, list):
                raise OutlineToolError(
                    "invalid_outline_tool_input", f"beat {index} source_refs must be a list"
                )
            refs = [_identifier(item, f"beat {index} source_refs") for item in refs_value]
            if len(refs) > 20 or len(set(refs)) != len(refs):
                raise OutlineToolError(
                    "invalid_outline_tool_input", f"beat {index} source_refs must contain unique IDs"
                )
            cleaned.append({
                "title": title, "summary": summary, "purpose": purpose,
                "source_refs": refs,
            })

        with self.repository.transaction(immediate=True) as db:
            outline = self._required_outline(db, session_id)
            self._check_revision(outline, expected_revision)
            structure_id = outline["active_structure_id"]
            if not structure_id:
                raise OutlineToolError(
                    "outline_structure_required", "select a structure before creating beats"
                )
            existing = db.execute(
                "SELECT COUNT(*) FROM outline_beats WHERE outline_id = ? AND status = 'active'",
                (outline["id"],),
            ).fetchone()[0]
            if existing:
                raise OutlineToolError(
                    "outline_beats_exist", "archive the current beats before generating a new set"
                )
            approval = "accepted" if origin == "filmmaker" else "proposed"
            for position, beat in enumerate(cleaned, 1):
                beat_id = _new_id("beat")
                db.execute(
                    """INSERT INTO outline_beats
                       (id, outline_id, structure_id, position, title, summary, purpose,
                        origin, approval_status, source_refs_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        beat_id, outline["id"], structure_id, position,
                        beat["title"], beat["summary"], beat["purpose"], origin,
                        approval, json.dumps(beat["source_refs"], ensure_ascii=False),
                    ),
                )
                if approval == "accepted":
                    self._record_approval(
                        db, outline["id"], "beat", beat_id, "accepted", "filmmaker", ""
                    )
            version_actor = "system" if origin == "interview" else origin
            return self._finish_mutation(
                db, outline, expected_revision, "create_beat_batch", version_actor
            )

    def edit_beat(
        self, session_id: str, expected_revision: int, beat_id: str,
        title: str | None = None, summary: str | None = None,
        purpose: str | None = None, actor: str = "filmmaker",
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        expected_revision = _expected_revision(expected_revision)
        beat_id = _identifier(beat_id, "beat_id")
        actor = _actor(actor)
        if title is None and summary is None and purpose is None:
            raise OutlineToolError("invalid_outline_tool_input", "at least one beat field must change")
        updates: dict[str, str] = {}
        if title is not None:
            updates["title"] = _text(title, "title", maximum=160)
        if summary is not None:
            updates["summary"] = _text(summary, "summary", maximum=2000)
        if purpose is not None:
            updates["purpose"] = _text(purpose, "purpose", maximum=800, required=False)
        with self.repository.transaction(immediate=True) as db:
            outline = self._required_outline(db, session_id)
            self._check_revision(outline, expected_revision)
            beat = self._beat(db, outline["id"], beat_id)
            if actor != "filmmaker" and beat["approval_status"] != "proposed":
                raise OutlineToolError(
                    "decided_material_protected", "the agent cannot rewrite a filmmaker-decided beat"
                )
            assignments = ", ".join(f"{field} = ?" for field in updates)
            db.execute(
                f"UPDATE outline_beats SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*updates.values(), beat_id),
            )
            return self._finish_mutation(db, outline, expected_revision, "edit_beat", actor)

    def move_beat(
        self, session_id: str, expected_revision: int, beat_id: str,
        position: int, actor: str = "filmmaker",
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        expected_revision = _expected_revision(expected_revision)
        beat_id = _identifier(beat_id, "beat_id")
        actor = _actor(actor)
        with self.repository.transaction(immediate=True) as db:
            outline = self._required_outline(db, session_id)
            self._check_revision(outline, expected_revision)
            self._beat(db, outline["id"], beat_id)
            ordered = self._active_beat_ids(db, outline["id"])
            if not isinstance(position, int) or isinstance(position, bool) or not 1 <= position <= len(ordered):
                raise OutlineToolError("invalid_outline_tool_input", "position is outside the beat list")
            if actor != "filmmaker":
                accepted = db.execute(
                    """SELECT COUNT(*) FROM outline_beats
                       WHERE outline_id = ? AND status = 'active' AND approval_status != 'proposed'""",
                    (outline["id"],),
                ).fetchone()[0]
                if accepted:
                    raise OutlineToolError(
                        "decided_material_protected",
                        "the agent cannot reorder an outline containing filmmaker-decided beats",
                    )
            ordered.remove(beat_id)
            ordered.insert(position - 1, beat_id)
            self._set_beat_order(db, outline["id"], ordered)
            return self._finish_mutation(db, outline, expected_revision, "move_beat", actor)

    def archive_beat(
        self, session_id: str, expected_revision: int, beat_id: str,
        actor: str = "filmmaker",
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        expected_revision = _expected_revision(expected_revision)
        beat_id = _identifier(beat_id, "beat_id")
        actor = _actor(actor)
        with self.repository.transaction(immediate=True) as db:
            outline = self._required_outline(db, session_id)
            self._check_revision(outline, expected_revision)
            beat = self._beat(db, outline["id"], beat_id)
            if actor != "filmmaker" and beat["approval_status"] != "proposed":
                raise OutlineToolError(
                    "decided_material_protected", "the agent cannot archive a filmmaker-decided beat"
                )
            db.execute(
                """UPDATE outline_beats SET status = 'archived', archived_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (beat_id,),
            )
            self._set_beat_order(db, outline["id"], self._active_beat_ids(db, outline["id"]))
            return self._finish_mutation(db, outline, expected_revision, "archive_beat", actor)

    def record_beat_decision(
        self, session_id: str, expected_revision: int, beat_id: str,
        decision: str, note: str = "", actor: str = "filmmaker",
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        expected_revision = _expected_revision(expected_revision)
        beat_id = _identifier(beat_id, "beat_id")
        actor = _actor(actor)
        note = _text(note, "note", maximum=1000, required=False)
        if actor != "filmmaker":
            raise OutlineToolError("filmmaker_action_required", "only the filmmaker can decide on a beat")
        if decision not in {"accepted", "rejected", "reopened"}:
            raise OutlineToolError("invalid_outline_tool_input", "beat decision is not supported")
        with self.repository.transaction(immediate=True) as db:
            outline = self._required_outline(db, session_id)
            self._check_revision(outline, expected_revision)
            self._beat(db, outline["id"], beat_id)
            status = "proposed" if decision == "reopened" else decision
            db.execute(
                """UPDATE outline_beats SET approval_status = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (status, beat_id),
            )
            self._record_approval(
                db, outline["id"], "beat", beat_id, decision, actor, note
            )
            return self._finish_mutation(
                db, outline, expected_revision, "record_beat_decision", actor
            )

    def record_outline_decision(
        self, session_id: str, expected_revision: int, decision: str,
        note: str = "", actor: str = "filmmaker",
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session_id")
        expected_revision = _expected_revision(expected_revision)
        actor = _actor(actor)
        note = _text(note, "note", maximum=1000, required=False)
        if actor != "filmmaker":
            raise OutlineToolError(
                "filmmaker_action_required", "only the filmmaker can decide on the Outline"
            )
        if decision not in {"accepted", "reopened"}:
            raise OutlineToolError(
                "invalid_outline_tool_input", "Outline decision is not supported"
            )
        with self.repository.transaction(immediate=True) as db:
            outline = self._required_outline(db, session_id)
            self._check_revision(outline, expected_revision, allow_approved=True)
            if decision == "accepted":
                if not outline["active_structure_id"]:
                    raise OutlineToolError(
                        "outline_structure_required", "select a structure before approving the Outline"
                    )
                decisions = db.execute(
                    """SELECT approval_status, COUNT(*) AS amount FROM outline_beats
                       WHERE outline_id = ? AND status = 'active'
                       GROUP BY approval_status""",
                    (outline["id"],),
                ).fetchall()
                totals = {row["approval_status"]: row["amount"] for row in decisions}
                if not totals or totals.get("proposed", 0) or totals.get("rejected", 0):
                    raise OutlineToolError(
                        "outline_not_ready",
                        "accept or archive every active beat before approving the Outline",
                    )
                new_status = "approved"
            else:
                new_status = "draft"
            self._record_approval(
                db, outline["id"], "outline", outline["id"], decision, actor, note
            )
            return self._finish_mutation(
                db, outline, expected_revision, "record_outline_decision", actor,
                status=new_status,
            )

    def _outline_for_session(self, db: Any, session_id: str, *, create: bool) -> Any:
        session = db.execute(
            "SELECT id FROM interview_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            raise NotFound("Interview not found")
        outline = db.execute(
            "SELECT * FROM outlines WHERE session_id = ?", (session_id,)
        ).fetchone()
        if outline or not create:
            return outline
        outline_id = _new_id("outline")
        db.execute("INSERT INTO outlines (id, session_id) VALUES (?, ?)", (outline_id, session_id))
        outline = db.execute("SELECT * FROM outlines WHERE id = ?", (outline_id,)).fetchone()
        self._save_version(db, outline_id, 0, "outline_created", "system")
        return outline

    def _required_outline(self, db: Any, session_id: str) -> Any:
        outline = self._outline_for_session(db, session_id, create=False)
        if not outline:
            raise OutlineToolError("outline_not_found", "outline has not been created")
        return outline

    @staticmethod
    def _check_revision(
        outline: Any, expected_revision: int, *, allow_approved: bool = False,
    ) -> None:
        if outline["revision"] != expected_revision:
            raise OutlineRevisionConflict(outline["revision"])
        if outline["status"] == "approved" and not allow_approved:
            raise OutlineToolError(
                "outline_approved", "reopen the approved Outline before changing it"
            )

    @staticmethod
    def _structure(db: Any, outline_id: str, structure_id: str) -> Any:
        row = db.execute(
            "SELECT * FROM outline_structures WHERE id = ? AND outline_id = ?",
            (structure_id, outline_id),
        ).fetchone()
        if not row:
            raise OutlineToolError("outline_structure_not_found", "structure proposal does not exist")
        return row

    @staticmethod
    def _beat(db: Any, outline_id: str, beat_id: str) -> Any:
        row = db.execute(
            """SELECT * FROM outline_beats
               WHERE id = ? AND outline_id = ? AND status = 'active'""",
            (beat_id, outline_id),
        ).fetchone()
        if not row:
            raise OutlineToolError("outline_beat_not_found", "active beat does not exist")
        return row

    @staticmethod
    def _active_beat_ids(db: Any, outline_id: str) -> list[str]:
        return [
            row["id"] for row in db.execute(
                """SELECT id FROM outline_beats
                   WHERE outline_id = ? AND status = 'active' ORDER BY position, rowid""",
                (outline_id,),
            )
        ]

    @staticmethod
    def _set_beat_order(db: Any, outline_id: str, beat_ids: list[str]) -> None:
        db.execute(
            """UPDATE outline_beats SET position = position + 100000
               WHERE outline_id = ? AND status = 'active'""",
            (outline_id,),
        )
        for position, beat_id in enumerate(beat_ids, 1):
            db.execute(
                "UPDATE outline_beats SET position = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (position, beat_id),
            )

    @staticmethod
    def _record_approval(
        db: Any, outline_id: str, target_kind: str, target_id: str,
        decision: str, actor: str, note: str,
    ) -> None:
        db.execute(
            """INSERT INTO outline_approvals
               (id, outline_id, target_kind, target_id, decision, actor, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_new_id("approval"), outline_id, target_kind, target_id, decision, actor, note),
        )

    def _finish_mutation(
        self, db: Any, outline: Any, expected_revision: int,
        operation: str, actor: str, *, status: str = "draft",
    ) -> dict[str, Any]:
        new_revision = expected_revision + 1
        updated = db.execute(
            """UPDATE outlines SET revision = ?, status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND revision = ?""",
            (new_revision, status, outline["id"], expected_revision),
        )
        if updated.rowcount != 1:
            current = db.execute(
                "SELECT revision FROM outlines WHERE id = ?", (outline["id"],)
            ).fetchone()
            raise OutlineRevisionConflict(current["revision"])
        self._save_version(db, outline["id"], new_revision, operation, actor)
        return self._snapshot(db, outline["id"])

    def _save_version(
        self, db: Any, outline_id: str, revision: int,
        operation: str, actor: str,
    ) -> None:
        snapshot = self._snapshot(db, outline_id)
        db.execute(
            """INSERT INTO outline_versions
               (id, outline_id, revision, operation, actor, snapshot_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                _new_id("outline_version"), outline_id, revision, operation, actor,
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            ),
        )

    @staticmethod
    def _snapshot(db: Any, outline_id: str) -> dict[str, Any]:
        outline = db.execute("SELECT * FROM outlines WHERE id = ?", (outline_id,)).fetchone()
        if not outline:
            raise OutlineToolError("outline_not_found", "outline has not been created")
        structures = [dict(row) for row in db.execute(
            """SELECT id, position, kind, label, rationale, origin, approval_status,
                      created_at, updated_at
               FROM outline_structures WHERE outline_id = ? ORDER BY position""",
            (outline_id,),
        )]
        beats = []
        for row in db.execute(
            """SELECT id, structure_id, position, title, summary, purpose, origin,
                      approval_status, source_refs_json, created_at, updated_at
               FROM outline_beats
               WHERE outline_id = ? AND status = 'active' ORDER BY position, rowid""",
            (outline_id,),
        ):
            beat = dict(row)
            beat["source_refs"] = json.loads(beat.pop("source_refs_json"))
            beats.append(beat)
        return {
            "id": outline["id"],
            "session_id": outline["session_id"],
            "revision": outline["revision"],
            "status": outline["status"],
            "active_structure_id": outline["active_structure_id"],
            "structures": structures,
            "beats": beats,
            "created_at": outline["created_at"],
            "updated_at": outline["updated_at"],
        }

"""Controlled, versioned operations for the Screenplay stage."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .database import Repository
from .outline import OutlineToolkit


SCREENPLAY_MODES = (
    {"mode": "visual", "label": "Mostly visual", "description": "Action and images carry the film; speech is used sparingly."},
    {"mode": "narration_led", "label": "Narration-led", "description": "A narrator guides the film while the images add meaning."},
    {"mode": "character_led", "label": "Character-led", "description": "Characters carry the film through behavior and dialogue."},
    {"mode": "hybrid", "label": "Hybrid", "description": "Visual action, dialogue, and narration share the storytelling."},
)


@dataclass(frozen=True)
class ScreenplayError(ValueError):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        value = {"code": self.code, "message": self.message}
        if self.details:
            value["details"] = self.details
        return value


class ScreenplayRevisionConflict(ScreenplayError):
    def __init__(self, revision: int):
        super().__init__(
            "stale_screenplay_revision", f"screenplay changed; current revision is {revision}",
            {"current_revision": revision},
        )


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _text(value: Any, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ScreenplayError("invalid_screenplay_input", f"{field} must be text")
    value = value.strip()
    if required and not value:
        raise ScreenplayError("invalid_screenplay_input", f"{field} is required")
    if len(value) > maximum:
        raise ScreenplayError("invalid_screenplay_input", f"{field} is too long")
    return value


class ScreenplayToolkit:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.outlines = OutlineToolkit(repository)

    def read_handoff(self, session_id: str) -> dict[str, Any]:
        handoff = self.outlines.read_interview_handoff(session_id)
        outline = handoff.get("outline")
        return {
            "interview": handoff["interview"],
            "outline": outline,
            "available": bool(outline and outline["status"] == "approved"),
            "modes": [dict(item) for item in SCREENPLAY_MODES],
            "screenplay": None if not outline else self.read(outline["id"]),
        }

    def read(self, outline_id: str) -> dict[str, Any] | None:
        with self.repository.transaction() as db:
            row = db.execute("SELECT id FROM screenplays WHERE outline_id = ?", (outline_id,)).fetchone()
            return self._snapshot(db, row["id"]) if row else None

    def choose_mode(
        self, session_id: str, expected_revision: int, mode: str,
    ) -> dict[str, Any]:
        if mode not in {item["mode"] for item in SCREENPLAY_MODES}:
            raise ScreenplayError("invalid_screenplay_input", "screenplay mode is not supported")
        with self.repository.transaction(immediate=True) as db:
            outline = self._approved_outline(db, session_id)
            screenplay = db.execute(
                "SELECT * FROM screenplays WHERE outline_id = ?", (outline["id"],)
            ).fetchone()
            if not screenplay:
                if expected_revision != 0:
                    raise ScreenplayRevisionConflict(0)
                screenplay_id = _id("screenplay")
                db.execute(
                    "INSERT INTO screenplays (id, outline_id, mode) VALUES (?, ?, ?)",
                    (screenplay_id, outline["id"], mode),
                )
                screenplay = db.execute("SELECT * FROM screenplays WHERE id = ?", (screenplay_id,)).fetchone()
                self._version(db, screenplay_id, 0, "screenplay_created", "system")
            else:
                self._check(screenplay, expected_revision)
                if db.execute(
                    "SELECT COUNT(*) FROM screenplay_scenes WHERE screenplay_id = ? AND status='active'",
                    (screenplay["id"],),
                ).fetchone()[0]:
                    raise ScreenplayError("screenplay_scenes_exist", "reopen and remove scenes before changing mode")
                db.execute("UPDATE screenplays SET mode = ? WHERE id = ?", (mode, screenplay["id"]))
            return self._finish(db, screenplay, expected_revision, "choose_mode", "filmmaker")

    def create_scene_batch(
        self, session_id: str, expected_revision: int,
        scenes: list[Mapping[str, Any]], actor: str = "agent",
    ) -> dict[str, Any]:
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 20:
            raise ScreenplayError("invalid_screenplay_input", "scenes must contain 1–20 items")
        cleaned = [self._scene(item, index) for index, item in enumerate(scenes, 1)]
        with self.repository.transaction(immediate=True) as db:
            outline = self._approved_outline(db, session_id)
            screenplay = self._required(db, outline["id"])
            self._check(screenplay, expected_revision)
            if not screenplay["mode"]:
                raise ScreenplayError("screenplay_mode_required", "choose a screenplay mode first")
            if db.execute(
                "SELECT COUNT(*) FROM screenplay_scenes WHERE screenplay_id=? AND status='active'",
                (screenplay["id"],),
            ).fetchone()[0]:
                raise ScreenplayError("screenplay_scenes_exist", "screenplay scenes already exist")
            beat_ids = {row["id"] for row in db.execute(
                "SELECT id FROM outline_beats WHERE outline_id=? AND status='active' AND approval_status='accepted'",
                (outline["id"],),
            )}
            for position, scene in enumerate(cleaned, 1):
                if scene["beat_id"] not in beat_ids:
                    raise ScreenplayError("invalid_screenplay_input", f"scene {position} cites an unaccepted beat")
                db.execute(
                    """INSERT INTO screenplay_scenes
                       (id, screenplay_id, beat_id, position, heading, action, narration,
                        dialogue_json, approval_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed')""",
                    (
                        _id("scene"), screenplay["id"], scene["beat_id"], position,
                        scene["heading"], scene["action"], scene["narration"],
                        json.dumps(scene["dialogue"], ensure_ascii=False),
                    ),
                )
            return self._finish(db, screenplay, expected_revision, "create_scene_batch", actor)

    def edit_scene(
        self, session_id: str, expected_revision: int, scene_id: str,
        heading: str, action: str, narration: str, dialogue: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        cleaned = self._scene({
            "beat_id": "beat_placeholder", "heading": heading, "action": action,
            "narration": narration, "dialogue": dialogue,
        }, 1)
        with self.repository.transaction(immediate=True) as db:
            outline = self._approved_outline(db, session_id)
            screenplay = self._required(db, outline["id"])
            self._check(screenplay, expected_revision)
            row = db.execute(
                "SELECT id FROM screenplay_scenes WHERE id=? AND screenplay_id=? AND status='active'",
                (scene_id, screenplay["id"]),
            ).fetchone()
            if not row:
                raise ScreenplayError("screenplay_scene_not_found", "scene does not exist")
            db.execute(
                """UPDATE screenplay_scenes SET heading=?, action=?, narration=?, dialogue_json=?,
                   approval_status='accepted', updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (cleaned["heading"], cleaned["action"], cleaned["narration"], json.dumps(cleaned["dialogue"]), scene_id),
            )
            return self._finish(db, screenplay, expected_revision, "edit_scene", "filmmaker")

    def record_screenplay_decision(
        self, session_id: str, expected_revision: int, decision: str,
    ) -> dict[str, Any]:
        if decision not in {"accepted", "reopened"}:
            raise ScreenplayError(
                "invalid_screenplay_input", "screenplay decision must be accepted or reopened"
            )
        with self.repository.transaction(immediate=True) as db:
            outline = self._approved_outline(db, session_id)
            screenplay = self._required(db, outline["id"])
            self._check(screenplay, expected_revision, allow_approved=True)
            if decision == "accepted":
                scene_count = db.execute(
                    "SELECT COUNT(*) FROM screenplay_scenes WHERE screenplay_id=? AND status='active'",
                    (screenplay["id"],),
                ).fetchone()[0]
                if not scene_count:
                    raise ScreenplayError(
                        "screenplay_not_ready", "generate at least one scene before approving the screenplay"
                    )
                status = "approved"
                db.execute(
                    "UPDATE screenplay_scenes SET approval_status='accepted', updated_at=CURRENT_TIMESTAMP "
                    "WHERE screenplay_id=? AND status='active'",
                    (screenplay["id"],),
                )
            else:
                in_use = db.execute(
                    "SELECT id FROM production_breakdowns WHERE screenplay_id=? AND status!='archived'",
                    (screenplay["id"],),
                ).fetchone()
                if in_use:
                    raise ScreenplayError(
                        "screenplay_in_use",
                        "the screenplay cannot be reopened after its production breakdown has started",
                    )
                status = "draft"
            next_revision = expected_revision + 1
            updated = db.execute(
                "UPDATE screenplays SET revision=?, status=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND revision=?",
                (next_revision, status, screenplay["id"], expected_revision),
            )
            if updated.rowcount != 1:
                current = db.execute(
                    "SELECT revision FROM screenplays WHERE id=?", (screenplay["id"],)
                ).fetchone()
                raise ScreenplayRevisionConflict(current["revision"])
            self._version(
                db, screenplay["id"], next_revision,
                "approve_screenplay" if decision == "accepted" else "reopen_screenplay",
                "filmmaker",
            )
            return self._snapshot(db, screenplay["id"])

    @staticmethod
    def _scene(value: Mapping[str, Any], index: int) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ScreenplayError("invalid_screenplay_input", f"scene {index} must be an object")
        dialogue = value.get("dialogue", [])
        if not isinstance(dialogue, list) or len(dialogue) > 40:
            raise ScreenplayError("invalid_screenplay_input", f"scene {index} dialogue is invalid")
        lines = []
        for line in dialogue:
            if not isinstance(line, Mapping):
                raise ScreenplayError("invalid_screenplay_input", f"scene {index} dialogue line is invalid")
            lines.append({
                "character": _text(line.get("character"), "character", 80),
                "line": _text(line.get("line"), "dialogue line", 1000),
            })
        return {
            "beat_id": _text(value.get("beat_id"), "beat_id", 128),
            "heading": _text(value.get("heading"), "scene heading", 160),
            "action": _text(value.get("action"), "scene action", 5000),
            "narration": _text(value.get("narration", ""), "narration", 3000, False),
            "dialogue": lines,
        }

    @staticmethod
    def _approved_outline(db: Any, session_id: str) -> Any:
        row = db.execute("SELECT * FROM outlines WHERE session_id=?", (session_id,)).fetchone()
        if not row or row["status"] != "approved":
            raise ScreenplayError("approved_outline_required", "approve the Outline before starting the screenplay")
        return row

    @staticmethod
    def _required(db: Any, outline_id: str) -> Any:
        row = db.execute("SELECT * FROM screenplays WHERE outline_id=?", (outline_id,)).fetchone()
        if not row:
            raise ScreenplayError("screenplay_not_found", "choose a screenplay mode first")
        return row

    @staticmethod
    def _check(screenplay: Any, revision: int, *, allow_approved: bool = False) -> None:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ScreenplayError("invalid_screenplay_input", "expected_revision must be a non-negative integer")
        if screenplay["revision"] != revision:
            raise ScreenplayRevisionConflict(screenplay["revision"])
        if screenplay["status"] == "approved" and not allow_approved:
            raise ScreenplayError("screenplay_approved", "reopen the screenplay before changing it")

    def _finish(self, db: Any, screenplay: Any, revision: int, operation: str, actor: str) -> dict[str, Any]:
        next_revision = revision + 1
        updated = db.execute(
            "UPDATE screenplays SET revision=?, status='draft', updated_at=CURRENT_TIMESTAMP WHERE id=? AND revision=?",
            (next_revision, screenplay["id"], revision),
        )
        if updated.rowcount != 1:
            current = db.execute("SELECT revision FROM screenplays WHERE id=?", (screenplay["id"],)).fetchone()
            raise ScreenplayRevisionConflict(current["revision"])
        self._version(db, screenplay["id"], next_revision, operation, actor)
        return self._snapshot(db, screenplay["id"])

    def _version(self, db: Any, screenplay_id: str, revision: int, operation: str, actor: str) -> None:
        db.execute(
            "INSERT INTO screenplay_versions (id, screenplay_id, revision, operation, actor, snapshot_json) VALUES (?, ?, ?, ?, ?, ?)",
            (_id("screenplay_version"), screenplay_id, revision, operation, actor, json.dumps(self._snapshot(db, screenplay_id), ensure_ascii=False)),
        )

    @staticmethod
    def _snapshot(db: Any, screenplay_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM screenplays WHERE id=?", (screenplay_id,)).fetchone()
        scenes = []
        for item in db.execute(
            "SELECT * FROM screenplay_scenes WHERE screenplay_id=? AND status='active' ORDER BY position",
            (screenplay_id,),
        ):
            scene = dict(item)
            scene["dialogue"] = json.loads(scene.pop("dialogue_json"))
            scenes.append(scene)
        return {
            "id": row["id"], "outline_id": row["outline_id"],
            "revision": row["revision"], "mode": row["mode"], "status": row["status"],
            "scenes": scenes, "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

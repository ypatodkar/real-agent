"""Versioned production-requirement tools for approved screenplay scenes."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

from .database import Repository
from .screenplay import ScreenplayToolkit


BREAKDOWN_CATEGORIES = (
    {"category": "cast", "label": "Cast", "description": "Characters or performers needed on set."},
    {"category": "location", "label": "Location", "description": "The place or set required for the scene."},
    {"category": "prop", "label": "Props", "description": "Objects handled or featured on camera."},
    {"category": "wardrobe", "label": "Wardrobe", "description": "Clothing, uniforms, and continuity changes."},
    {"category": "makeup", "label": "Makeup", "description": "Hair, makeup, prosthetics, or appearance continuity."},
    {"category": "vehicle", "label": "Vehicles", "description": "Picture vehicles or transport shown on camera."},
    {"category": "animal", "label": "Animals", "description": "Animals, handlers, or creature requirements."},
    {"category": "sound", "label": "Sound", "description": "Important production sound or playback needs."},
    {"category": "effect", "label": "Effects", "description": "Practical, visual, weather, or special effects."},
    {"category": "equipment", "label": "Equipment", "description": "Special camera, lighting, grip, or recording gear."},
    {"category": "crew", "label": "Crew", "description": "Specialist crew required beyond the core team."},
    {"category": "time", "label": "Time of day", "description": "Day, night, golden hour, or continuity constraints."},
    {"category": "risk", "label": "Risk / logistics", "description": "Safety, access, permits, noise, water, crowds, or complexity."},
)
CATEGORY_KEYS = {item["category"] for item in BREAKDOWN_CATEGORIES}


@dataclass(frozen=True)
class BreakdownError(ValueError):
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


class BreakdownRevisionConflict(BreakdownError):
    def __init__(self, revision: int):
        super().__init__(
            "stale_breakdown_revision", f"breakdown changed; current revision is {revision}",
            {"current_revision": revision},
        )


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _text(value: Any, field: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise BreakdownError("invalid_breakdown_input", f"{field} must be text")
    cleaned = value.strip()
    if required and not cleaned:
        raise BreakdownError("invalid_breakdown_input", f"{field} is required")
    if len(cleaned) > maximum:
        raise BreakdownError("invalid_breakdown_input", f"{field} is too long")
    return cleaned


class BreakdownToolkit:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.screenplays = ScreenplayToolkit(repository)

    def read_handoff(self, session_id: str) -> dict[str, Any]:
        handoff = self.screenplays.read_handoff(session_id)
        screenplay = handoff.get("screenplay")
        return {
            "interview": handoff["interview"],
            "screenplay": screenplay,
            "available": bool(screenplay and screenplay["status"] == "approved"),
            "categories": [dict(item) for item in BREAKDOWN_CATEGORIES],
            "breakdown": None if not screenplay else self.read(screenplay["id"]),
        }

    def read(self, screenplay_id: str) -> dict[str, Any] | None:
        with self.repository.transaction() as db:
            row = db.execute(
                "SELECT id FROM production_breakdowns WHERE screenplay_id=?",
                (screenplay_id,),
            ).fetchone()
            return self._snapshot(db, row["id"]) if row else None

    def create_item_batch(
        self, session_id: str, expected_revision: int,
        items: list[Mapping[str, Any]], actor: str = "agent",
    ) -> dict[str, Any]:
        if not isinstance(items, list) or not 1 <= len(items) <= 120:
            raise BreakdownError(
                "invalid_breakdown_input", "breakdown must contain 1–120 requirements"
            )
        cleaned = [self._item(item, index) for index, item in enumerate(items, 1)]
        with self.repository.transaction(immediate=True) as db:
            screenplay = self._approved_screenplay(db, session_id)
            breakdown = db.execute(
                "SELECT * FROM production_breakdowns WHERE screenplay_id=?",
                (screenplay["id"],),
            ).fetchone()
            if not breakdown:
                if expected_revision != 0:
                    raise BreakdownRevisionConflict(0)
                breakdown_id = _id("breakdown")
                db.execute(
                    "INSERT INTO production_breakdowns (id, screenplay_id) VALUES (?, ?)",
                    (breakdown_id, screenplay["id"]),
                )
                breakdown = db.execute(
                    "SELECT * FROM production_breakdowns WHERE id=?", (breakdown_id,)
                ).fetchone()
                self._version(db, breakdown_id, 0, "breakdown_created", "system")
            else:
                self._check(breakdown, expected_revision)
                existing = db.execute(
                    "SELECT COUNT(*) FROM breakdown_items WHERE breakdown_id=? AND status='active'",
                    (breakdown["id"],),
                ).fetchone()[0]
                if existing:
                    raise BreakdownError(
                        "breakdown_items_exist", "production requirements already exist"
                    )
            scene_ids = self._scene_ids(db, screenplay["id"])
            for position, item in enumerate(cleaned, 1):
                if item["scene_id"] not in scene_ids:
                    raise BreakdownError(
                        "invalid_breakdown_input",
                        f"requirement {position} cites an unknown screenplay scene",
                    )
                db.execute(
                    """INSERT INTO breakdown_items
                       (id, breakdown_id, scene_id, position, category, name, details)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _id("breakdown_item"), breakdown["id"], item["scene_id"],
                        position, item["category"], item["name"], item["details"],
                    ),
                )
            return self._finish(
                db, breakdown, expected_revision, "create_item_batch", actor
            )

    def add_item(
        self, session_id: str, expected_revision: int, scene_id: str,
        category: str, name: str, details: str = "",
    ) -> dict[str, Any]:
        item = self._item({
            "scene_id": scene_id, "category": category, "name": name, "details": details,
        }, 1)
        with self.repository.transaction(immediate=True) as db:
            screenplay = self._approved_screenplay(db, session_id)
            breakdown = self._required(db, screenplay["id"])
            self._check(breakdown, expected_revision)
            if item["scene_id"] not in self._scene_ids(db, screenplay["id"]):
                raise BreakdownError("invalid_breakdown_input", "scene does not belong to this screenplay")
            position = db.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM breakdown_items WHERE breakdown_id=?",
                (breakdown["id"],),
            ).fetchone()[0]
            db.execute(
                """INSERT INTO breakdown_items
                   (id, breakdown_id, scene_id, position, category, name, details, approval_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'accepted')""",
                (
                    _id("breakdown_item"), breakdown["id"], item["scene_id"], position,
                    item["category"], item["name"], item["details"],
                ),
            )
            return self._finish(db, breakdown, expected_revision, "add_item", "filmmaker")

    def replace_item_batch(
        self, session_id: str, expected_revision: int,
        items: list[Mapping[str, Any]], actor: str = "agent",
    ) -> dict[str, Any]:
        """Atomically replace active generated requirements during regeneration."""
        if not isinstance(items, list) or not 1 <= len(items) <= 120:
            raise BreakdownError(
                "invalid_breakdown_input", "breakdown must contain 1–120 requirements"
            )
        cleaned = [self._item(item, index) for index, item in enumerate(items, 1)]
        with self.repository.transaction(immediate=True) as db:
            screenplay = self._approved_screenplay(db, session_id)
            breakdown = self._required(db, screenplay["id"])
            self._check(breakdown, expected_revision)
            scene_ids = self._scene_ids(db, screenplay["id"])
            for position, item in enumerate(cleaned, 1):
                if item["scene_id"] not in scene_ids:
                    raise BreakdownError(
                        "invalid_breakdown_input",
                        f"requirement {position} cites an unknown screenplay scene",
                    )
            db.execute(
                """UPDATE breakdown_items SET status='archived',
                   archived_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                   WHERE breakdown_id=? AND status='active'""",
                (breakdown["id"],),
            )
            for position, item in enumerate(cleaned, 1):
                db.execute(
                    """INSERT INTO breakdown_items
                       (id, breakdown_id, scene_id, position, category, name, details)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _id("breakdown_item"), breakdown["id"], item["scene_id"],
                        position, item["category"], item["name"], item["details"],
                    ),
                )
            return self._finish(
                db, breakdown, expected_revision, "replace_item_batch", actor
            )

    def edit_item(
        self, session_id: str, expected_revision: int, item_id: str,
        category: str, name: str, details: str = "",
    ) -> dict[str, Any]:
        cleaned = self._item({
            "scene_id": "scene_placeholder", "category": category,
            "name": name, "details": details,
        }, 1)
        with self.repository.transaction(immediate=True) as db:
            screenplay = self._approved_screenplay(db, session_id)
            breakdown = self._required(db, screenplay["id"])
            self._check(breakdown, expected_revision)
            row = db.execute(
                "SELECT id FROM breakdown_items WHERE id=? AND breakdown_id=? AND status='active'",
                (item_id, breakdown["id"]),
            ).fetchone()
            if not row:
                raise BreakdownError("breakdown_item_not_found", "production requirement not found")
            db.execute(
                """UPDATE breakdown_items SET category=?, name=?, details=?,
                   approval_status='accepted', updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (cleaned["category"], cleaned["name"], cleaned["details"], item_id),
            )
            return self._finish(db, breakdown, expected_revision, "edit_item", "filmmaker")

    def archive_item(
        self, session_id: str, expected_revision: int, item_id: str,
    ) -> dict[str, Any]:
        with self.repository.transaction(immediate=True) as db:
            screenplay = self._approved_screenplay(db, session_id)
            breakdown = self._required(db, screenplay["id"])
            self._check(breakdown, expected_revision)
            updated = db.execute(
                """UPDATE breakdown_items SET status='archived', archived_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP WHERE id=? AND breakdown_id=? AND status='active'""",
                (item_id, breakdown["id"]),
            )
            if updated.rowcount != 1:
                raise BreakdownError("breakdown_item_not_found", "production requirement not found")
            return self._finish(db, breakdown, expected_revision, "archive_item", "filmmaker")

    def record_breakdown_decision(
        self, session_id: str, expected_revision: int, decision: str,
    ) -> dict[str, Any]:
        if decision not in {"accepted", "reopened"}:
            raise BreakdownError(
                "invalid_breakdown_input", "breakdown decision must be accepted or reopened"
            )
        with self.repository.transaction(immediate=True) as db:
            screenplay = self._approved_screenplay(db, session_id)
            breakdown = self._required(db, screenplay["id"])
            self._check(breakdown, expected_revision, allow_approved=True)
            item_count = db.execute(
                "SELECT COUNT(*) FROM breakdown_items WHERE breakdown_id=? AND status='active'",
                (breakdown["id"],),
            ).fetchone()[0]
            if decision == "accepted" and not item_count:
                raise BreakdownError(
                    "breakdown_not_ready", "add production requirements before approving the breakdown"
                )
            status = "approved" if decision == "accepted" else "draft"
            if decision == "accepted":
                db.execute(
                    "UPDATE breakdown_items SET approval_status='accepted', updated_at=CURRENT_TIMESTAMP "
                    "WHERE breakdown_id=? AND status='active'",
                    (breakdown["id"],),
                )
            next_revision = expected_revision + 1
            updated = db.execute(
                "UPDATE production_breakdowns SET revision=?, status=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND revision=?",
                (next_revision, status, breakdown["id"], expected_revision),
            )
            if updated.rowcount != 1:
                current = db.execute(
                    "SELECT revision FROM production_breakdowns WHERE id=?", (breakdown["id"],)
                ).fetchone()
                raise BreakdownRevisionConflict(current["revision"])
            self._version(
                db, breakdown["id"], next_revision,
                "approve_breakdown" if decision == "accepted" else "reopen_breakdown",
                "filmmaker",
            )
            return self._snapshot(db, breakdown["id"])

    @staticmethod
    def _item(value: Mapping[str, Any], index: int) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise BreakdownError("invalid_breakdown_input", f"requirement {index} must be an object")
        category = _text(value.get("category"), "category", 40)
        if category not in CATEGORY_KEYS:
            raise BreakdownError(
                "invalid_breakdown_input", f"requirement {index} has an unsupported category"
            )
        return {
            "scene_id": _text(value.get("scene_id"), "scene_id", 128),
            "category": category,
            "name": _text(value.get("name"), "requirement name", 180),
            "details": _text(value.get("details", ""), "requirement details", 2000, required=False),
        }

    @staticmethod
    def _scene_ids(db: Any, screenplay_id: str) -> set[str]:
        return {row["id"] for row in db.execute(
            "SELECT id FROM screenplay_scenes WHERE screenplay_id=? AND status='active'",
            (screenplay_id,),
        )}

    @staticmethod
    def _approved_screenplay(db: Any, session_id: str) -> Any:
        row = db.execute(
            """SELECT s.* FROM screenplays s
               JOIN outlines o ON o.id=s.outline_id
               WHERE o.session_id=? AND s.status='approved'""",
            (session_id,),
        ).fetchone()
        if not row:
            raise BreakdownError(
                "approved_screenplay_required",
                "approve the screenplay before starting its production breakdown",
            )
        return row

    @staticmethod
    def _required(db: Any, screenplay_id: str) -> Any:
        row = db.execute(
            "SELECT * FROM production_breakdowns WHERE screenplay_id=?", (screenplay_id,)
        ).fetchone()
        if not row:
            raise BreakdownError("breakdown_not_found", "generate the production breakdown first")
        return row

    @staticmethod
    def _check(breakdown: Any, revision: int, *, allow_approved: bool = False) -> None:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise BreakdownError(
                "invalid_breakdown_input", "expected_revision must be a non-negative integer"
            )
        if breakdown["revision"] != revision:
            raise BreakdownRevisionConflict(breakdown["revision"])
        if breakdown["status"] == "approved" and not allow_approved:
            raise BreakdownError("breakdown_approved", "reopen the breakdown before changing it")

    def _finish(
        self, db: Any, breakdown: Any, revision: int, operation: str, actor: str,
    ) -> dict[str, Any]:
        next_revision = revision + 1
        updated = db.execute(
            "UPDATE production_breakdowns SET revision=?, status='draft', updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND revision=?",
            (next_revision, breakdown["id"], revision),
        )
        if updated.rowcount != 1:
            current = db.execute(
                "SELECT revision FROM production_breakdowns WHERE id=?", (breakdown["id"],)
            ).fetchone()
            raise BreakdownRevisionConflict(current["revision"])
        self._version(db, breakdown["id"], next_revision, operation, actor)
        return self._snapshot(db, breakdown["id"])

    def _version(
        self, db: Any, breakdown_id: str, revision: int, operation: str, actor: str,
    ) -> None:
        db.execute(
            """INSERT INTO breakdown_versions
               (id, breakdown_id, revision, operation, actor, snapshot_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                _id("breakdown_version"), breakdown_id, revision, operation, actor,
                json.dumps(self._snapshot(db, breakdown_id), ensure_ascii=False),
            ),
        )

    @staticmethod
    def _snapshot(db: Any, breakdown_id: str) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM production_breakdowns WHERE id=?", (breakdown_id,)
        ).fetchone()
        scenes = []
        scene_rows = db.execute(
            """SELECT sc.id, sc.position, sc.heading
               FROM screenplay_scenes sc
               WHERE sc.screenplay_id=? AND sc.status='active'
               ORDER BY sc.position""",
            (row["screenplay_id"],),
        ).fetchall()
        location_links: dict[str, list[dict[str, str]]] = {}
        for candidate in db.execute(
            """SELECT c.breakdown_item_id, c.place_id, c.status
               FROM location_candidates c
               JOIN location_searches s ON s.id=c.search_id
               WHERE s.breakdown_id=? AND s.status='active'
               ORDER BY c.created_at, c.id""",
            (breakdown_id,),
        ):
            place_id = candidate["place_id"]
            location_links.setdefault(candidate["breakdown_item_id"], []).append({
                "place_id": place_id,
                "status": candidate["status"],
                "maps_url": (
                    "https://www.google.com/maps/search/?api=1&query=Google"
                    f"&query_place_id={quote(place_id, safe='')}"
                ),
            })
        for scene in scene_rows:
            items = [dict(item) for item in db.execute(
                """SELECT id, scene_id, position, category, name, details,
                          approval_status, created_at, updated_at
                   FROM breakdown_items
                   WHERE breakdown_id=? AND scene_id=? AND status='active'
                   ORDER BY position""",
                (breakdown_id, scene["id"]),
            )]
            for item in items:
                item["location_links"] = location_links.get(item["id"], [])
            scenes.append({
                "scene_id": scene["id"], "position": scene["position"],
                "heading": scene["heading"], "items": items,
            })
        return {
            "id": row["id"], "screenplay_id": row["screenplay_id"],
            "revision": row["revision"], "status": row["status"],
            "scenes": scenes, "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

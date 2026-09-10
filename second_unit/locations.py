"""Google Maps-backed, policy-conscious location suggestions for Breakdown."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .database import Repository
from .model import load_env


LOGGER = logging.getLogger("second_unit.locations")
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTES_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"


@dataclass(frozen=True)
class LocationError(ValueError):
    code: str
    message: str
    http_status: int = 400

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class GoogleMapsClient:
    def __init__(
        self, api_key: str,
        requester: Callable[[str, dict[str, Any], dict[str, str]], Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.requester = requester or self._post

    def search_places(
        self, query: str, latitude: float, longitude: float, radius_meters: float,
    ) -> list[dict[str, Any]]:
        response = self.requester(PLACES_URL, {
            "textQuery": query,
            "pageSize": 8,
            "locationBias": {"circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": radius_meters,
            }},
            "rankPreference": "DISTANCE",
        }, {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,"
                "places.location,places.rating,places.googleMapsUri"
            ),
        })
        places = response.get("places", []) if isinstance(response, Mapping) else []
        results = []
        for place in places:
            location = place.get("location") or {}
            display_name = place.get("displayName") or {}
            if (
                not place.get("id")
                or not isinstance(location.get("latitude"), (int, float))
                or not isinstance(location.get("longitude"), (int, float))
            ):
                continue
            results.append({
                "place_id": place["id"],
                "name": display_name.get("text") or "Suggested location",
                "address": place.get("formattedAddress") or "",
                "latitude": location["latitude"],
                "longitude": location.get("longitude"),
                "rating": place.get("rating"),
                "maps_uri": place.get("googleMapsUri") or "",
            })
        return results

    def route_matrix(self, origin_place_id: str, destination_place_ids: list[str]) -> dict[str, dict[str, int]]:
        if not destination_place_ids:
            return {}
        response = self.requester(ROUTES_URL, {
            "origins": [{"waypoint": {"placeId": origin_place_id}}],
            "destinations": [
                {"waypoint": {"placeId": place_id}} for place_id in destination_place_ids
            ],
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_UNAWARE",
        }, {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "destinationIndex,status,condition,distanceMeters,duration",
        })
        elements = response if isinstance(response, list) else []
        routes: dict[str, dict[str, int]] = {}
        for element in elements:
            index = element.get("destinationIndex")
            if not isinstance(index, int) or not 0 <= index < len(destination_place_ids):
                continue
            if element.get("condition") not in {None, "ROUTE_EXISTS"}:
                continue
            duration = str(element.get("duration") or "0s").removesuffix("s")
            try:
                seconds = max(0, int(float(duration)))
                distance = max(0, int(element.get("distanceMeters") or 0))
            except (TypeError, ValueError):
                continue
            routes[destination_place_ids[index]] = {
                "duration_seconds": seconds, "distance_meters": distance,
            }
        return routes

    @staticmethod
    def _post(url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode("utf-8", errors="replace")
            LOGGER.error("Google Maps request failed status=%s response=%s", exc.code, detail)
            raise LocationError(
                "maps_provider_error",
                "Google Maps could not return location suggestions. Check the enabled APIs and key restrictions.",
                502,
            ) from exc
        except (OSError, ValueError) as exc:
            LOGGER.error("Google Maps request failed type=%s error=%s", type(exc).__name__, str(exc))
            raise LocationError(
                "maps_provider_error",
                "Google Maps is temporarily unavailable. Please try again.",
                503,
            ) from exc


class LocationScoutService:
    def __init__(self, repository: Repository, client: GoogleMapsClient | None = None) -> None:
        load_env()
        # Dedicated Maps credentials take precedence. The GOOGLE_MAPS_* names
        # and shared GOOGLE_API_KEY remain compatibility fallbacks only.
        shared_key = os.environ.get("GOOGLE_API_KEY", "")
        self.browser_key = (
            os.environ.get("MAPS_BROWSER_KEY")
            or os.environ.get("GOOGLE_MAPS_BROWSER_KEY")
            or shared_key
        )
        server_key = (
            os.environ.get("MAPS_SERVER_KEY")
            or os.environ.get("GOOGLE_MAPS_SERVER_KEY")
            or shared_key
        )
        self.uses_shared_key = bool(
            shared_key and self.browser_key == shared_key and server_key == shared_key
        )
        self.repository = repository
        self.client = client or (GoogleMapsClient(server_key) if server_key else None)

    def config(self) -> dict[str, Any]:
        return {
            "available": bool(self.browser_key and self.client),
            "browser_key": self.browser_key,
            "uses_shared_key": self.uses_shared_key,
        }

    def search(self, session_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise LocationError("maps_not_configured", "Google Maps is not configured.", 503)
        allowed = {
            "base_place_id", "base_latitude", "base_longitude", "max_travel_minutes",
        }
        if not isinstance(value, Mapping) or set(value) != allowed:
            raise LocationError("invalid_location_input", "Location search fields are incomplete.")
        place_id = value.get("base_place_id")
        if not isinstance(place_id, str) or not place_id.strip() or len(place_id) > 500:
            raise LocationError("invalid_location_input", "Choose a production base from the suggestions.")
        try:
            latitude = float(value["base_latitude"])
            longitude = float(value["base_longitude"])
            minutes = int(value["max_travel_minutes"])
        except (TypeError, ValueError) as exc:
            raise LocationError("invalid_location_input", "Travel location or time is invalid.") from exc
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and 5 <= minutes <= 180):
            raise LocationError("invalid_location_input", "Travel location or time is outside the supported range.")
        breakdown, requirements = self._requirements(session_id)
        if not requirements:
            raise LocationError(
                "location_requirements_missing",
                "Add at least one Location requirement to the scene breakdown first.",
                422,
            )
        radius = float(min(50_000, max(5_000, minutes * 1_000)))
        candidate_groups = []
        all_places: dict[str, dict[str, Any]] = {}
        for requirement in requirements:
            query = f"{requirement['name']} {requirement['details']}".strip()[:300]
            places = self.client.search_places(query, latitude, longitude, radius)
            for place in places:
                all_places.setdefault(place["place_id"], place)
            candidate_groups.append({
                "breakdown_item_id": requirement["id"],
                "requirement": requirement["name"],
                "scene_heading": requirement["heading"],
                "place_ids": [place["place_id"] for place in places],
            })
        place_ids = list(all_places)
        routes = self.client.route_matrix(place_id.strip(), place_ids)
        maximum_seconds = minutes * 60
        groups = []
        persisted: list[tuple[str, str]] = []
        for group in candidate_groups:
            candidates = []
            for candidate_id in group.pop("place_ids"):
                if candidate_id not in routes or routes[candidate_id]["duration_seconds"] > maximum_seconds:
                    continue
                place = dict(all_places[candidate_id])
                place.update(routes[candidate_id])
                candidates.append(place)
            candidates.sort(key=lambda item: item["duration_seconds"])
            candidates = candidates[:5]
            persisted.extend((group["breakdown_item_id"], item["place_id"]) for item in candidates)
            groups.append({**group, "candidates": candidates})
        search_id = self._save_search(
            breakdown["id"], place_id.strip(), minutes, persisted
        )
        return {
            "search_id": search_id,
            "max_travel_minutes": minutes,
            "groups": groups,
            "attribution": "Google Maps",
        }

    def set_shortlisted(
        self, session_id: str, search_id: str, place_id: str,
        breakdown_item_id: str, shortlisted: bool,
    ) -> dict[str, Any]:
        with self.repository.transaction(immediate=True) as db:
            row = db.execute(
                """SELECT c.id FROM location_candidates c
                   JOIN location_searches s ON s.id=c.search_id
                   JOIN production_breakdowns b ON b.id=s.breakdown_id
                   JOIN screenplays sp ON sp.id=b.screenplay_id
                   JOIN outlines o ON o.id=sp.outline_id
                   WHERE o.session_id=? AND s.id=? AND c.place_id=?
                     AND c.breakdown_item_id=? AND s.status='active'""",
                (session_id, search_id, place_id, breakdown_item_id),
            ).fetchone()
            if not row:
                raise LocationError("location_candidate_not_found", "Location suggestion not found.", 404)
            status = "shortlisted" if shortlisted else "suggested"
            db.execute(
                "UPDATE location_candidates SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, row["id"]),
            )
        return {"place_id": place_id, "status": status}

    def _requirements(self, session_id: str) -> tuple[Any, list[Any]]:
        with self.repository.transaction() as db:
            breakdown = db.execute(
                """SELECT b.* FROM production_breakdowns b
                   JOIN screenplays s ON s.id=b.screenplay_id
                   JOIN outlines o ON o.id=s.outline_id
                   WHERE o.session_id=?""",
                (session_id,),
            ).fetchone()
            if not breakdown:
                raise LocationError(
                    "breakdown_required", "Generate the scene breakdown before scouting locations.", 422
                )
            requirements = db.execute(
                """SELECT i.id, i.name, i.details, sc.heading
                   FROM breakdown_items i
                   JOIN screenplay_scenes sc ON sc.id=i.scene_id
                   WHERE i.breakdown_id=? AND i.status='active' AND i.category='location'
                   ORDER BY sc.position, i.position""",
                (breakdown["id"],),
            ).fetchall()
            return breakdown, requirements

    def _save_search(
        self, breakdown_id: str, base_place_id: str, minutes: int,
        candidates: list[tuple[str, str]],
    ) -> str:
        search_id = _id("location_search")
        with self.repository.transaction(immediate=True) as db:
            db.execute(
                "UPDATE location_searches SET status='archived' WHERE breakdown_id=? AND status='active'",
                (breakdown_id,),
            )
            db.execute(
                """INSERT INTO location_searches
                   (id, breakdown_id, base_place_id, max_travel_minutes)
                   VALUES (?, ?, ?, ?)""",
                (search_id, breakdown_id, base_place_id, minutes),
            )
            for item_id, place_id in candidates:
                db.execute(
                    """INSERT OR IGNORE INTO location_candidates
                       (id, search_id, breakdown_item_id, place_id)
                       VALUES (?, ?, ?, ?)""",
                    (_id("location_candidate"), search_id, item_id, place_id),
                )
        return search_id

"""Tentative timeline event builder."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from private_memory_agent.retrieval import REDACTED_TEXT
from private_memory_agent.storage import initialize_database

EVENT_TYPE_TENTATIVE = "tentative"
DEFAULT_TIMEZONE = "Asia/Tokyo"
DEFAULT_WINDOW_MINUTES = 180


@dataclass(frozen=True)
class EventCandidate:
    """Normalized event-building evidence candidate."""

    source_table: str
    source_id: int
    source_kind: str
    occurred_at: datetime
    evidence_id: str
    participants: tuple[str, ...] = ()
    places: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    confidence: float = 0.5


@dataclass(frozen=True)
class TentativeEvent:
    """A timeline hypothesis before persistence."""

    title: str
    start_at: str
    end_at: str
    evidence_ids: tuple[str, ...]
    participants: tuple[str, ...]
    places: tuple[str, ...]
    topics: tuple[str, ...]
    confidence: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class EventBuildResult:
    """Count-only event build result safe for CLI output."""

    events_created: int
    events_existing: int
    evidence_candidates: int
    evidence_links_created: int
    timezone: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "events_created": self.events_created,
            "events_existing": self.events_existing,
            "evidence_candidates": self.evidence_candidates,
            "evidence_links_created": self.evidence_links_created,
            "timezone": self.timezone,
        }


class EventBuilder:
    """Build tentative events from local metadata and extracted text structure."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        timezone: str = DEFAULT_TIMEZONE,
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
    ) -> None:
        if window_minutes <= 0:
            raise ValueError("window_minutes must be positive")
        self.db_path = Path(db_path).expanduser()
        self.timezone_name = timezone
        self.timezone = _zoneinfo(timezone)
        self.window = timedelta(minutes=window_minutes)

    def build(self, *, limit: int | None = None) -> EventBuildResult:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")

        storage = initialize_database(self.db_path)
        links_created = 0
        created = 0
        existing = 0
        try:
            candidates = self.collect_candidates(storage)
            events = self.build_tentative_events(candidates)
            if limit is not None:
                events = events[:limit]
            existing_group_keys = _existing_event_group_keys(storage)
            for event in events:
                group_key = str(event.metadata["group_key"])
                if group_key in existing_group_keys:
                    existing += 1
                    continue
                with storage.transaction():
                    event_id = storage.events.insert_event(
                        event_type=EVENT_TYPE_TENTATIVE,
                        title=event.title,
                        description="Tentative local timeline event hypothesis.",
                        started_at=event.start_at,
                        ended_at=event.end_at,
                        confidence=event.confidence,
                        metadata_json=json.dumps(
                            event.metadata,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                    links_created += _insert_event_links(storage, event_id, event.evidence_ids)
                existing_group_keys.add(group_key)
                created += 1
            return EventBuildResult(
                events_created=created,
                events_existing=existing,
                evidence_candidates=len(candidates),
                evidence_links_created=links_created,
                timezone=self.timezone_name,
            )
        finally:
            storage.close()

    def collect_candidates(self, storage) -> tuple[EventCandidate, ...]:
        annotations = _load_text_annotations(storage)
        candidates: list[EventCandidate] = []
        candidates.extend(self._media_candidates(storage))
        candidates.extend(self._line_candidates(storage, annotations))
        candidates.extend(self._note_candidates(storage, annotations))
        candidates.sort(
            key=lambda item: (
                item.occurred_at,
                item.source_table,
                item.source_id,
            ),
        )
        return tuple(candidates)

    def build_tentative_events(
        self,
        candidates: tuple[EventCandidate, ...] | list[EventCandidate],
    ) -> list[TentativeEvent]:
        groups = _group_candidates(list(candidates), self.window)
        return [self._event_from_group(group) for group in groups]

    def _media_candidates(self, storage) -> list[EventCandidate]:
        rows = storage.connection.execute(
            """
            SELECT id, taken_at, modified_at, metadata_json
            FROM media_items
            WHERE is_excluded = 0
              AND COALESCE(taken_at, modified_at, '') != ''
            ORDER BY id
            """,
        ).fetchall()
        candidates: list[EventCandidate] = []
        for row in rows:
            occurred_at = self._parse_timestamp(row["taken_at"] or row["modified_at"])
            if occurred_at is None:
                continue
            metadata = _safe_json_object(row["metadata_json"])
            annotation_signals = _media_annotation_signals(storage, int(row["id"]))
            places = _dedupe(
                (*_place_candidates_from_metadata(metadata), *annotation_signals["places"]),
            )
            topics = _dedupe(annotation_signals["topics"])
            candidates.append(
                EventCandidate(
                    source_table="media_items",
                    source_id=int(row["id"]),
                    source_kind="photos",
                    occurred_at=occurred_at,
                    evidence_id=f"media_items:{int(row['id'])}",
                    places=places,
                    topics=topics,
                    confidence=0.7,
                ),
            )
        return candidates

    def _line_candidates(
        self,
        storage,
        annotations: dict[tuple[str, int], dict[str, Any]],
    ) -> list[EventCandidate]:
        rows = storage.connection.execute(
            """
            SELECT id, sender_id, sent_at
            FROM line_messages
            WHERE is_excluded = 0
              AND COALESCE(sent_at, '') != ''
            ORDER BY id
            """,
        ).fetchall()
        candidates: list[EventCandidate] = []
        for row in rows:
            occurred_at = self._parse_timestamp(row["sent_at"])
            if occurred_at is None:
                continue
            annotation = annotations.get(("line_messages", int(row["id"])), {})
            participants = _dedupe((*_participants_from_annotation(annotation), row["sender_id"]))
            candidates.append(
                EventCandidate(
                    source_table="line_messages",
                    source_id=int(row["id"]),
                    source_kind="line",
                    occurred_at=occurred_at,
                    evidence_id=f"line_messages:{int(row['id'])}",
                    participants=participants,
                    places=_places_from_annotation(annotation),
                    topics=_topics_from_annotation(annotation),
                    confidence=float(annotation.get("confidence") or 0.65),
                ),
            )
        return candidates

    def _note_candidates(
        self,
        storage,
        annotations: dict[tuple[str, int], dict[str, Any]],
    ) -> list[EventCandidate]:
        rows = storage.connection.execute(
            """
            SELECT id,
                   COALESCE(updated_at_source, created_at_source, updated_at) AS occurred_at
            FROM notes
            WHERE is_excluded = 0
              AND COALESCE(updated_at_source, created_at_source, updated_at, '') != ''
            ORDER BY id
            """,
        ).fetchall()
        candidates: list[EventCandidate] = []
        for row in rows:
            occurred_at = self._parse_timestamp(row["occurred_at"])
            if occurred_at is None:
                continue
            annotation = annotations.get(("notes", int(row["id"])), {})
            candidates.append(
                EventCandidate(
                    source_table="notes",
                    source_id=int(row["id"]),
                    source_kind="notes",
                    occurred_at=occurred_at,
                    evidence_id=f"notes:{int(row['id'])}",
                    participants=_participants_from_annotation(annotation),
                    places=_places_from_annotation(annotation),
                    topics=_topics_from_annotation(annotation),
                    confidence=float(annotation.get("confidence") or 0.55),
                ),
            )
        return candidates

    def _event_from_group(self, group: list[EventCandidate]) -> TentativeEvent:
        start = min(item.occurred_at for item in group)
        end = max(item.occurred_at for item in group)
        participants = _top_values(item.participants for item in group)
        places = _top_values(item.places for item in group)
        topics = _top_values(item.topics for item in group)
        evidence_ids = tuple(item.evidence_id for item in group)
        source_counts = dict(Counter(item.source_kind for item in group))
        confidence = _event_confidence(
            group,
            participants=participants,
            places=places,
            topics=topics,
        )
        group_key = _group_key(start, evidence_ids)
        title = _event_title(start, topics)
        metadata = {
            "status": EVENT_TYPE_TENTATIVE,
            "timezone": self.timezone_name,
            "group_key": group_key,
            "evidence_ids": list(evidence_ids),
            "participants": list(participants),
            "places": list(places),
            "topics": list(topics),
            "source_counts": source_counts,
            "identity_assertions": False,
            "hypothesis": True,
        }
        return TentativeEvent(
            title=title,
            start_at=start.isoformat(timespec="seconds"),
            end_at=end.isoformat(timespec="seconds"),
            evidence_ids=evidence_ids,
            participants=participants,
            places=places,
            topics=topics,
            confidence=confidence,
            metadata=metadata,
        )

    def _parse_timestamp(self, value: str | None) -> datetime | None:
        return parse_timestamp(value, self.timezone)


def build_events(
    db_path: Path | str,
    *,
    timezone: str = DEFAULT_TIMEZONE,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    limit: int | None = None,
) -> EventBuildResult:
    return EventBuilder(
        db_path,
        timezone=timezone,
        window_minutes=window_minutes,
    ).build(limit=limit)


def list_events(
    db_path: Path | str,
    *,
    limit: int = 50,
    include_excluded: bool = False,
    redact_private: bool = True,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    storage = initialize_database(db_path)
    try:
        where = "" if include_excluded else "WHERE is_excluded = 0"
        rows = storage.connection.execute(
            f"""
            SELECT *
            FROM events
            {where}
            ORDER BY COALESCE(started_at, created_at), id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_event_row_to_dict(row, redact_private=redact_private) for row in rows]
    finally:
        storage.close()


def parse_timestamp(value: str | None, timezone: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            parsed = datetime.fromisoformat(text)
        else:
            parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    else:
        parsed = parsed.astimezone(timezone)
    return parsed


def _group_candidates(
    candidates: list[EventCandidate],
    window: timedelta,
) -> list[list[EventCandidate]]:
    groups: list[list[EventCandidate]] = []
    for candidate in candidates:
        if not groups:
            groups.append([candidate])
            continue
        current = groups[-1]
        if _can_merge(current, candidate, window):
            current.append(candidate)
        else:
            groups.append([candidate])
    return groups


def _can_merge(group: list[EventCandidate], candidate: EventCandidate, window: timedelta) -> bool:
    group_end = max(item.occurred_at for item in group)
    if candidate.occurred_at - group_end > window:
        return False
    if _shared_signals(group, candidate):
        return True
    return candidate.occurred_at - group_end <= min(window, timedelta(minutes=60))


def _shared_signals(group: list[EventCandidate], candidate: EventCandidate) -> bool:
    participants = set(candidate.participants)
    places = set(candidate.places)
    topics = set(candidate.topics)
    group_participants: set[str] = set()
    group_places: set[str] = set()
    group_topics: set[str] = set()
    for item in group:
        group_participants.update(item.participants)
        group_places.update(item.places)
        group_topics.update(item.topics)
    return bool(
        (participants and participants & group_participants)
        or (places and places & group_places)
        or (topics and topics & group_topics)
    )


def _load_text_annotations(storage) -> dict[tuple[str, int], dict[str, Any]]:
    rows = storage.connection.execute(
        """
        SELECT source_table,
               source_id,
               entities_json,
               topics_json,
               dates_json,
               event_hints_json,
               confidence
        FROM text_annotations
        WHERE is_excluded = 0
          AND annotation_type = 'understanding'
        ORDER BY id
        """,
    ).fetchall()
    annotations: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["source_table"]), int(row["source_id"]))
        annotations[key] = {
            "entities": _safe_json_list(row["entities_json"]),
            "topics": _safe_json_list(row["topics_json"]),
            "dates": _safe_json_list(row["dates_json"]),
            "event_hints": _safe_json_list(row["event_hints_json"]),
            "confidence": row["confidence"],
        }
    return annotations


def _media_annotation_signals(storage, media_id: int) -> dict[str, tuple[str, ...]]:
    rows = storage.connection.execute(
        """
        SELECT value_text, data_json
        FROM media_annotations
        WHERE media_item_id = ?
          AND is_excluded = 0
        ORDER BY id
        """,
        (media_id,),
    ).fetchall()
    topics: list[str] = []
    places: list[str] = []
    for row in rows:
        data = _safe_json_object(row["data_json"])
        objects = data.get("objects")
        if isinstance(objects, list):
            topics.extend(str(item) for item in objects if str(item).strip())
        place = data.get("place") or data.get("location")
        if place:
            places.append(str(place))
    return {"topics": _dedupe(topics), "places": _dedupe(places)}


def _participants_from_annotation(annotation: dict[str, Any]) -> tuple[str, ...]:
    people: list[str] = []
    for entity in annotation.get("entities", ()):
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type") or "").casefold()
        if entity_type in {"person", "people", "name", "人物", "人"}:
            text = str(entity.get("text") or "").strip()
            if text:
                people.append(text)
    return _dedupe(people)


def _places_from_annotation(annotation: dict[str, Any]) -> tuple[str, ...]:
    places: list[str] = []
    for entity in annotation.get("entities", ()):
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type") or "").casefold()
        if entity_type in {"place", "location", "venue", "場所", "地名"}:
            text = str(entity.get("text") or "").strip()
            if text:
                places.append(text)
    return _dedupe(places)


def _topics_from_annotation(annotation: dict[str, Any]) -> tuple[str, ...]:
    topics: list[str] = []
    topics.extend(str(item) for item in annotation.get("topics", ()) if str(item).strip())
    for hint in annotation.get("event_hints", ()):
        if isinstance(hint, dict) and hint.get("title"):
            topics.append(str(hint["title"]))
    return _dedupe(topics)


def _place_candidates_from_metadata(metadata: dict[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []
    for key in ("place", "location", "venue"):
        value = metadata.get(key)
        if value:
            candidates.append(str(value))
    gps = metadata.get("gps")
    if isinstance(gps, dict):
        lat = _first_present(gps, ("latitude", "lat", "GPSLatitude"))
        lon = _first_present(gps, ("longitude", "lon", "lng", "GPSLongitude"))
        if lat is not None and lon is not None:
            candidates.append(f"gps:{_coarse_number(lat)},{_coarse_number(lon)}")
    return _dedupe(candidates)


def _insert_event_links(storage, event_id: int, evidence_ids: tuple[str, ...]) -> int:
    created = 0
    for evidence_id in evidence_ids:
        table, raw_id = evidence_id.split(":", 1)
        source_id = int(raw_id)
        if storage.evidence_links.exists(
            target_table="events",
            target_id=event_id,
            evidence_table=table,
            evidence_id=source_id,
        ):
            continue
        storage.evidence_links.insert_link(
            target_table="events",
            target_id=event_id,
            evidence_table=table,
            evidence_id=source_id,
            relation_type="supports",
            weight=1.0,
            metadata_json=json.dumps(
                {"phase": "5-A", "event_status": EVENT_TYPE_TENTATIVE},
                sort_keys=True,
            ),
        )
        created += 1
    return created


def _existing_event_group_keys(storage) -> set[str]:
    rows = storage.connection.execute(
        """
        SELECT metadata_json
        FROM events
        WHERE is_excluded = 0
          AND event_type = ?
        """,
        (EVENT_TYPE_TENTATIVE,),
    ).fetchall()
    keys: set[str] = set()
    for row in rows:
        metadata = _safe_json_object(row["metadata_json"])
        group_key = metadata.get("group_key")
        if group_key:
            keys.add(str(group_key))
    return keys


def _event_row_to_dict(row, *, redact_private: bool) -> dict[str, Any]:
    metadata = _safe_json_object(row["metadata_json"])
    title = row["title"] or ""
    participants = tuple(str(item) for item in metadata.get("participants", ()) if str(item))
    places = tuple(str(item) for item in metadata.get("places", ()) if str(item))
    topics = tuple(str(item) for item in metadata.get("topics", ()) if str(item))
    if redact_private:
        title = REDACTED_TEXT if title else title
        participants = tuple(REDACTED_TEXT for _ in participants)
        places = tuple(REDACTED_TEXT for _ in places)
        topics = tuple(REDACTED_TEXT for _ in topics)
    evidence_ids = tuple(str(item) for item in metadata.get("evidence_ids", ()) if str(item))
    return {
        "id": int(row["id"]),
        "event_type": row["event_type"],
        "status": metadata.get("status", row["event_type"]),
        "title": title,
        "start_at": row["started_at"],
        "end_at": row["ended_at"],
        "confidence": row["confidence"],
        "evidence_ids": list(evidence_ids),
        "evidence_count": len(evidence_ids),
        "participants": list(participants),
        "places": list(places),
        "topics": list(topics),
        "source_counts": metadata.get("source_counts", {}),
        "timezone": metadata.get("timezone"),
        "identity_assertions": bool(metadata.get("identity_assertions", False)),
    }


def _event_confidence(
    group: list[EventCandidate],
    *,
    participants: tuple[str, ...],
    places: tuple[str, ...],
    topics: tuple[str, ...],
) -> float:
    average = sum(item.confidence for item in group) / max(1, len(group))
    source_bonus = 0.08 * max(0, len({item.source_kind for item in group}) - 1)
    signal_bonus = 0.03 * sum(bool(values) for values in (participants, places, topics))
    evidence_bonus = 0.02 * min(3, len(group) - 1)
    return round(min(0.95, average + source_bonus + signal_bonus + evidence_bonus), 3)


def _event_title(start: datetime, topics: tuple[str, ...]) -> str:
    topic = topics[0] if topics else "local memory"
    return f"Tentative event: {topic} ({start.strftime('%Y-%m-%d %H:%M')})"


def _group_key(start: datetime, evidence_ids: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    payload = "|".join((start.strftime("%Y-%m-%dT%H"), *sorted(evidence_ids)))
    digest.update(payload.encode("utf-8"))
    return digest.hexdigest()[:24]


def _top_values(values: Any, *, limit: int = 8) -> tuple[str, ...]:
    counter: Counter[str] = Counter()
    for group in values:
        counter.update(str(item) for item in group if str(item).strip())
    return tuple(item for item, _ in counter.most_common(limit))


def _dedupe(values: Any) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            seen.setdefault(text, None)
    return tuple(seen)


def _safe_json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _first_present(values: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in values:
            return values[key]
    return None


def _coarse_number(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "unknown"


def _zoneinfo(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone}") from exc

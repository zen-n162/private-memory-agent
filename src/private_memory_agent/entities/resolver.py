"""Conservative entity resolver and alias manager."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from private_memory_agent.retrieval import REDACTED_TEXT
from private_memory_agent.storage import initialize_database

SUPPORTED_ENTITY_TYPES = {"person", "place", "organization", "topic"}
PERSON_UNKNOWN_PREFIX = "person_unknown_"
MENTION_RELATION = "mentions"


@dataclass(frozen=True)
class EntityMention:
    """A normalized entity mention from local extracted metadata."""

    entity_type: str
    text: str
    source_table: str
    source_id: int
    confidence: float | None = None
    user_confirmed: bool = False


@dataclass(frozen=True)
class EntityResolveResult:
    """Count-only entity resolution result safe for CLI output."""

    mentions_seen: int = 0
    entities_created: int = 0
    entities_reused: int = 0
    evidence_links_created: int = 0
    unknown_person_candidates: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "mentions_seen": self.mentions_seen,
            "entities_created": self.entities_created,
            "entities_reused": self.entities_reused,
            "evidence_links_created": self.evidence_links_created,
            "unknown_person_candidates": self.unknown_person_candidates,
        }


@dataclass(frozen=True)
class AliasAddResult:
    """Count-only alias-add result safe for CLI output."""

    entity_id: int
    aliases_count: int
    merged_entity_ids: tuple[int, ...] = ()
    user_confirmed: bool = True

    @property
    def merged_entities(self) -> int:
        return len(self.merged_entity_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "aliases_count": self.aliases_count,
            "merged_entity_ids": list(self.merged_entity_ids),
            "merged_entities": self.merged_entities,
            "user_confirmed": self.user_confirmed,
        }


class EntityResolver:
    """Resolve local entity mentions without unsafe person identity assumptions."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).expanduser()

    def resolve_text_annotations(self, *, limit: int | None = None) -> EntityResolveResult:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        storage = initialize_database(self.db_path)
        try:
            mentions = _mentions_from_text_annotations(storage, limit=limit)
            return self.resolve_mentions(mentions)
        finally:
            storage.close()

    def resolve_mentions(
        self,
        mentions: list[EntityMention] | tuple[EntityMention, ...],
    ) -> EntityResolveResult:
        storage = initialize_database(self.db_path)
        created = 0
        reused = 0
        links_created = 0
        unknown_people = 0
        try:
            with storage.transaction():
                for mention in mentions:
                    normalized = _normalized_mention(mention)
                    if normalized is None:
                        continue
                    entity_id, was_created, is_unknown = _resolve_one(storage, normalized)
                    if was_created:
                        created += 1
                    else:
                        reused += 1
                    if is_unknown:
                        unknown_people += 1
                    if _ensure_entity_evidence_link(storage, entity_id, normalized):
                        links_created += 1
        finally:
            storage.close()
        return EntityResolveResult(
            mentions_seen=len(mentions),
            entities_created=created,
            entities_reused=reused,
            evidence_links_created=links_created,
            unknown_person_candidates=unknown_people,
        )

    def add_alias(
        self,
        entity_id: int,
        alias: str,
        *,
        user_confirmed: bool = True,
        merge_existing: bool = True,
    ) -> AliasAddResult:
        alias_text = alias.strip()
        if not alias_text:
            raise ValueError("alias must not be empty")
        alias_norm = normalize_alias(alias_text)
        storage = initialize_database(self.db_path)
        try:
            with storage.transaction():
                row = storage.entities.get(entity_id)
                if row is None:
                    raise ValueError("entity was not found")
                metadata = _entity_metadata(row)
                aliases = _add_unique(_string_list(metadata.get("aliases")), alias_text)
                alias_norms = _add_unique(_string_list(metadata.get("alias_norms")), alias_norm)
                metadata.update(
                    {
                        "phase": "5-B",
                        "aliases": aliases,
                        "alias_norms": alias_norms,
                        "user_confirmed": bool(user_confirmed or metadata.get("user_confirmed")),
                        "identity_status": _identity_status_after_alias(
                            metadata,
                            user_confirmed=user_confirmed,
                        ),
                    },
                )
                storage.entities.update_entity(
                    entity_id,
                    display_name=row["display_name"] or alias_text,
                    metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                )
                merged_ids: tuple[int, ...] = ()
                if merge_existing and user_confirmed:
                    merged_ids = _merge_entities_with_alias(
                        storage,
                        target_id=entity_id,
                        entity_type=str(row["entity_type"]),
                        alias_norm=alias_norm,
                    )
                return AliasAddResult(
                    entity_id=entity_id,
                    aliases_count=len(aliases),
                    merged_entity_ids=merged_ids,
                    user_confirmed=bool(metadata["user_confirmed"]),
                )
        finally:
            storage.close()

    def list_entities(
        self,
        *,
        entity_type: str | None = None,
        limit: int = 100,
        redact_private: bool = True,
    ) -> list[dict[str, Any]]:
        return list_entities(
            self.db_path,
            entity_type=entity_type,
            limit=limit,
            redact_private=redact_private,
        )


def resolve_text_annotation_entities(
    db_path: Path | str,
    *,
    limit: int | None = None,
) -> EntityResolveResult:
    return EntityResolver(db_path).resolve_text_annotations(limit=limit)


def add_entity_alias(
    db_path: Path | str,
    entity_id: int,
    alias: str,
    *,
    user_confirmed: bool = True,
    merge_existing: bool = True,
) -> AliasAddResult:
    return EntityResolver(db_path).add_alias(
        entity_id,
        alias,
        user_confirmed=user_confirmed,
        merge_existing=merge_existing,
    )


def list_entities(
    db_path: Path | str,
    *,
    entity_type: str | None = None,
    limit: int = 100,
    redact_private: bool = True,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    normalized_type = None if entity_type is None else normalize_entity_type(entity_type)
    storage = initialize_database(db_path)
    try:
        params: list[Any] = []
        where = "WHERE is_excluded = 0"
        if normalized_type is not None:
            where += " AND entity_type = ?"
            params.append(normalized_type)
        rows = storage.connection.execute(
            f"""
            SELECT *
            FROM entities
            {where}
            ORDER BY entity_type, id
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [_entity_row_to_dict(storage, row, redact_private=redact_private) for row in rows]
    finally:
        storage.close()


def normalize_entity_type(value: str) -> str:
    raw = str(value).strip().casefold()
    mapping = {
        "people": "person",
        "name": "person",
        "人物": "person",
        "人": "person",
        "location": "place",
        "venue": "place",
        "場所": "place",
        "地名": "place",
        "org": "organization",
        "company": "organization",
        "会社": "organization",
        "組織": "organization",
        "テーマ": "topic",
        "話題": "topic",
    }
    normalized = mapping.get(raw, raw)
    if normalized not in SUPPORTED_ENTITY_TYPES:
        return "topic"
    return normalized


def normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _mentions_from_text_annotations(storage, *, limit: int | None) -> list[EntityMention]:
    limit_clause = "" if limit is None else "LIMIT ?"
    params: tuple[Any, ...] = () if limit is None else (limit,)
    rows = storage.connection.execute(
        f"""
        SELECT source_table,
               source_id,
               entities_json,
               topics_json
        FROM text_annotations
        WHERE is_excluded = 0
          AND annotation_type = 'understanding'
        ORDER BY id
        {limit_clause}
        """,
        params,
    ).fetchall()
    mentions: list[EntityMention] = []
    for row in rows:
        source_table = str(row["source_table"])
        source_id = int(row["source_id"])
        for item in _safe_json_list(row["entities_json"]):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            mentions.append(
                EntityMention(
                    entity_type=normalize_entity_type(str(item.get("type") or "topic")),
                    text=text,
                    source_table=source_table,
                    source_id=source_id,
                    confidence=_optional_float(item.get("confidence")),
                    user_confirmed=bool(item.get("user_confirmed", False)),
                ),
            )
        for topic in _safe_json_list(row["topics_json"]):
            text = str(topic).strip()
            if text:
                mentions.append(
                    EntityMention(
                        entity_type="topic",
                        text=text,
                        source_table=source_table,
                        source_id=source_id,
                    ),
                )
    return mentions


def _normalized_mention(mention: EntityMention) -> EntityMention | None:
    text = mention.text.strip()
    if not text:
        return None
    return EntityMention(
        entity_type=normalize_entity_type(mention.entity_type),
        text=text,
        source_table=mention.source_table.strip(),
        source_id=int(mention.source_id),
        confidence=mention.confidence,
        user_confirmed=mention.user_confirmed,
    )


def _resolve_one(storage, mention: EntityMention) -> tuple[int, bool, bool]:
    alias_norm = normalize_alias(mention.text)
    if mention.entity_type == "person" and not mention.user_confirmed:
        confirmed = _find_confirmed_alias(storage, mention.entity_type, alias_norm)
        if confirmed is not None:
            return int(confirmed["id"]), False, False
        canonical = _person_unknown_name(mention)
        existing = _find_by_canonical(storage, mention.entity_type, canonical)
        if existing is not None:
            return int(existing["id"]), False, True
        return (
            _insert_entity(storage, mention, canonical, candidate_kind="person_unknown"),
            True,
            True,
        )

    existing = _find_by_alias_norm(storage, mention.entity_type, alias_norm)
    if existing is not None:
        return int(existing["id"]), False, False
    canonical = alias_norm if mention.entity_type != "person" else mention.text
    return _insert_entity(storage, mention, canonical, candidate_kind=None), True, False


def _insert_entity(
    storage,
    mention: EntityMention,
    canonical: str,
    *,
    candidate_kind: str | None,
) -> int:
    alias_norm = normalize_alias(mention.text)
    user_confirmed = bool(mention.user_confirmed)
    metadata = {
        "phase": "5-B",
        "aliases": [mention.text],
        "alias_norms": [alias_norm],
        "user_confirmed": user_confirmed,
        "identity_status": "confirmed" if user_confirmed else "candidate",
        "source": "resolver",
    }
    if candidate_kind:
        metadata["candidate_kind"] = candidate_kind
    if mention.confidence is not None:
        metadata["confidence"] = mention.confidence
    return storage.entities.insert_entity(
        entity_type=mention.entity_type,
        canonical_name=canonical,
        display_name=mention.text,
        metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    )


def _ensure_entity_evidence_link(storage, entity_id: int, mention: EntityMention) -> bool:
    if storage.evidence_links.exists(
        target_table="entities",
        target_id=entity_id,
        evidence_table=mention.source_table,
        evidence_id=mention.source_id,
        relation_type=MENTION_RELATION,
    ):
        return False
    storage.evidence_links.insert_link(
        target_table="entities",
        target_id=entity_id,
        evidence_table=mention.source_table,
        evidence_id=mention.source_id,
        relation_type=MENTION_RELATION,
        weight=mention.confidence,
        metadata_json=json.dumps({"phase": "5-B"}, sort_keys=True),
    )
    return True


def _merge_entities_with_alias(
    storage,
    *,
    target_id: int,
    entity_type: str,
    alias_norm: str,
) -> tuple[int, ...]:
    duplicates = [
        row
        for row in _active_entities(storage, entity_type)
        if int(row["id"]) != target_id and alias_norm in _entity_alias_norms(row)
    ]
    merged: list[int] = []
    for row in duplicates:
        duplicate_id = int(row["id"])
        storage.connection.execute(
            """
            UPDATE evidence_links
            SET target_id = ?,
                updated_at = ?
            WHERE target_table = 'entities'
              AND target_id = ?
              AND is_excluded = 0
            """,
            (target_id, _utc_now_for_update(storage), duplicate_id),
        )
        metadata = _entity_metadata(row)
        metadata["merged_into_entity_id"] = target_id
        metadata["identity_status"] = "merged"
        storage.entities.update_entity(
            duplicate_id,
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        )
        storage.entities.mark_excluded(duplicate_id, reason="merged by user-confirmed alias")
        merged.append(duplicate_id)
    return tuple(merged)


def _entity_row_to_dict(storage, row, *, redact_private: bool) -> dict[str, Any]:
    metadata = _entity_metadata(row)
    aliases = _string_list(metadata.get("aliases"))
    canonical = row["canonical_name"]
    display = row["display_name"]
    if redact_private:
        canonical = canonical if _is_safe_unknown_name(canonical) else _redact_optional(canonical)
        display = _redact_optional(display)
        aliases = [REDACTED_TEXT for _ in aliases]
    return {
        "id": int(row["id"]),
        "entity_type": row["entity_type"],
        "canonical_name": canonical,
        "display_name": display,
        "aliases": aliases,
        "user_confirmed": bool(metadata.get("user_confirmed", False)),
        "identity_status": metadata.get("identity_status", "candidate"),
        "candidate_kind": metadata.get("candidate_kind"),
        "evidence_count": _entity_evidence_count(storage, int(row["id"])),
    }


def _entity_evidence_count(storage, entity_id: int) -> int:
    row = storage.connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM evidence_links
        WHERE target_table = 'entities'
          AND target_id = ?
          AND is_excluded = 0
        """,
        (entity_id,),
    ).fetchone()
    return int(row["count"])


def _find_confirmed_alias(storage, entity_type: str, alias_norm: str):
    for row in _active_entities(storage, entity_type):
        metadata = _entity_metadata(row)
        if metadata.get("user_confirmed") and alias_norm in _entity_alias_norms(row):
            return row
    return None


def _find_by_alias_norm(storage, entity_type: str, alias_norm: str):
    for row in _active_entities(storage, entity_type):
        if alias_norm in _entity_alias_norms(row):
            return row
    return None


def _find_by_canonical(storage, entity_type: str, canonical_name: str):
    row = storage.connection.execute(
        """
        SELECT *
        FROM entities
        WHERE entity_type = ?
          AND canonical_name = ?
          AND is_excluded = 0
        ORDER BY id
        LIMIT 1
        """,
        (entity_type, canonical_name),
    ).fetchone()
    return row


def _active_entities(storage, entity_type: str):
    return storage.connection.execute(
        """
        SELECT *
        FROM entities
        WHERE entity_type = ?
          AND is_excluded = 0
        ORDER BY id
        """,
        (entity_type,),
    ).fetchall()


def _entity_alias_norms(row) -> set[str]:
    metadata = _entity_metadata(row)
    norms = set(_string_list(metadata.get("alias_norms")))
    for value in (row["canonical_name"], row["display_name"]):
        if value:
            norms.add(normalize_alias(str(value)))
    return norms


def _entity_metadata(row) -> dict[str, Any]:
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _person_unknown_name(mention: EntityMention) -> str:
    digest = hashlib.sha256()
    payload = f"{mention.source_table}:{mention.source_id}:{normalize_alias(mention.text)}"
    digest.update(payload.encode("utf-8"))
    return f"{PERSON_UNKNOWN_PREFIX}{digest.hexdigest()[:12]}"


def _safe_json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value if str(item).strip()]


def _add_unique(values: list[str], value: str) -> list[str]:
    if value not in values:
        values.append(value)
    return values


def _redact_optional(value: object) -> str | None:
    if value is None or str(value) == "":
        return None
    return REDACTED_TEXT


def _is_safe_unknown_name(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PERSON_UNKNOWN_PREFIX)


def _utc_now_for_update(storage) -> str:
    row = storage.connection.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now') AS now",
    ).fetchone()
    return str(row["now"])


def _identity_status_after_alias(metadata: dict[str, Any], *, user_confirmed: bool) -> str:
    if user_confirmed:
        return "confirmed"
    return str(metadata.get("identity_status", "candidate"))

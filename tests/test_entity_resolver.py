import json

from private_memory_agent.cli import main
from private_memory_agent.entities import EntityMention, EntityResolver, list_entities
from private_memory_agent.entities.resolver import normalize_alias
from private_memory_agent.storage import initialize_database


def seed_text_annotation(storage, source_table, source_id, entities, topics=None):
    storage.text_annotations.insert_text_annotation(
        source_table=source_table,
        source_id=source_id,
        annotation_type="understanding",
        model_id="fake-text",
        summary="synthetic summary",
        entities_json=json.dumps(entities, ensure_ascii=False, sort_keys=True),
        topics_json=json.dumps(topics or [], ensure_ascii=False, sort_keys=True),
        dates_json=json.dumps([], ensure_ascii=False),
        action_items_json=json.dumps([], ensure_ascii=False),
        event_hints_json=json.dumps([], ensure_ascii=False),
        confidence=0.8,
    )


def seed_confirmed_person(storage, name):
    metadata = {
        "phase": "5-B",
        "aliases": [name],
        "alias_norms": [normalize_alias(name)],
        "user_confirmed": True,
        "identity_status": "confirmed",
        "source": "test",
    }
    return storage.entities.insert_entity(
        entity_type="person",
        canonical_name=name,
        display_name=name,
        metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    )


def test_unconfirmed_person_mentions_do_not_merge(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        seed_text_annotation(
            storage,
            "line_messages",
            1,
            [{"text": "テスト人物A", "type": "person", "confidence": 0.9}],
        )
        seed_text_annotation(
            storage,
            "notes",
            2,
            [{"text": "テスト人物A", "type": "person", "confidence": 0.9}],
        )
    finally:
        storage.close()

    result = EntityResolver(db_path).resolve_text_annotations()
    entities = list_entities(db_path, entity_type="person", redact_private=False)

    assert result.entities_created == 2
    assert result.unknown_person_candidates == 2
    assert len(entities) == 2
    assert all(item["canonical_name"].startswith("person_unknown_") for item in entities)
    assert {item["evidence_count"] for item in entities} == {1}
    assert all(item["user_confirmed"] is False for item in entities)


def test_user_confirmed_alias_merges_matching_unknown_candidate(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        seed_text_annotation(
            storage,
            "line_messages",
            1,
            [{"text": "テスト別名", "type": "person", "confidence": 0.7}],
        )
        target_id = seed_confirmed_person(storage, "テスト本名")
    finally:
        storage.close()

    EntityResolver(db_path).resolve_text_annotations()
    result = EntityResolver(db_path).add_alias(target_id, "テスト別名")
    entities = list_entities(db_path, entity_type="person", redact_private=False)

    assert result.merged_entities == 1
    assert len(entities) == 1
    assert entities[0]["id"] == target_id
    assert entities[0]["user_confirmed"] is True
    assert "テスト別名" in entities[0]["aliases"]
    assert entities[0]["evidence_count"] == 1


def test_confirmed_alias_reuses_person_entity_without_new_unknown(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        target_id = seed_confirmed_person(storage, "テスト本名")
    finally:
        storage.close()

    EntityResolver(db_path).add_alias(target_id, "テスト別名")
    EntityResolver(db_path).resolve_mentions(
        (
            EntityMention(
                entity_type="person",
                text="テスト別名",
                source_table="notes",
                source_id=42,
            ),
        ),
    )

    entities = list_entities(db_path, entity_type="person", redact_private=False)

    assert len(entities) == 1
    assert entities[0]["id"] == target_id
    assert entities[0]["evidence_count"] == 1


def test_non_person_aliases_can_merge_deterministically(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    resolver = EntityResolver(db_path)

    result = resolver.resolve_mentions(
        (
            EntityMention("place", "テスト公園", "notes", 1),
            EntityMention("location", "テスト公園", "line_messages", 2),
        ),
    )
    entities = list_entities(db_path, entity_type="place", redact_private=False)

    assert result.entities_created == 1
    assert result.entities_reused == 1
    assert len(entities) == 1
    assert entities[0]["evidence_count"] == 2


def test_entities_cli_list_redacts_private_names(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    EntityResolver(db_path).resolve_mentions(
        (
            EntityMention("person", "テスト人物A", "notes", 1),
            EntityMention("topic", "秘密トピック", "notes", 1),
        ),
    )

    exit_code = main(["entities", "list", "--db", str(db_path)])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["redacted"] is True
    assert "テスト人物A" not in output
    assert "秘密トピック" not in output
    assert "[redacted]" in output


def test_entities_resolve_cli_is_count_only(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        seed_text_annotation(
            storage,
            "notes",
            1,
            [{"text": "テスト人物A", "type": "person", "confidence": 0.8}],
            topics=["秘密トピック"],
        )
    finally:
        storage.close()

    exit_code = main(["entities", "resolve", "--db", str(db_path)])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["mentions_seen"] == 2
    assert payload["entities_created"] == 2
    assert "テスト人物A" not in output
    assert "秘密トピック" not in output


def test_entities_alias_add_cli_is_count_only(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        target_id = seed_confirmed_person(storage, "テスト本名")
    finally:
        storage.close()

    exit_code = main(
        [
            "entities",
            "alias",
            "add",
            str(target_id),
            "秘密の別名",
            "--db",
            str(db_path),
        ],
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["entity_id"] == target_id
    assert payload["aliases_count"] == 2
    assert "秘密の別名" not in output

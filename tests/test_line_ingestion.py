import json
import shutil
from pathlib import Path

from private_memory_agent.cli import main
from private_memory_agent.ingestion.line import ingest_line_exports, parse_line_export_text
from private_memory_agent.storage import initialize_database


FIXTURE_DIR = Path(__file__).parent / "fixtures"
LINE_FIXTURE = FIXTURE_DIR / "line_export_japanese.txt"


def test_parse_japanese_line_export_edges():
    parsed = parse_line_export_text(LINE_FIXTURE.read_text(encoding="utf-8"), source_label="fixture")

    assert parsed.room_name == "テストルーム"
    assert any(message.message_type == "malformed" for message in parsed.messages)
    assert any(message.message_type == "system" for message in parsed.messages)
    assert any(message.message_type == "omitted" for message in parsed.messages)

    multiline = next(message for message in parsed.messages if message.speaker == "太郎" and "こんにちは" in message.text)
    assert multiline.message_type == "text"
    assert multiline.sent_at == "2024-01-02T12:34:00"
    assert multiline.text == "こんにちは\n続きの行です"
    assert multiline.metadata["multiline"] is True

    morning = next(message for message in parsed.messages if message.text == "おはよう")
    afternoon = next(message for message in parsed.messages if message.text == "またね")
    assert morning.sent_at == "2024-01-03T09:00:00"
    assert afternoon.sent_at == "2024-01-03T15:05:00"


def test_line_ingest_dry_run_does_not_create_database(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"

    result = ingest_line_exports(LINE_FIXTURE, db_path=db_path, dry_run=True)

    assert result.dry_run is True
    assert result.files_scanned == 1
    assert result.messages_parsed >= 8
    assert result.messages_imported == result.messages_parsed
    assert result.skipped_duplicates == 0
    assert result.errors == 0
    assert not db_path.exists()


def test_line_ingest_imports_messages_and_skips_duplicates(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"

    first = ingest_line_exports(LINE_FIXTURE, db_path=db_path)
    second = ingest_line_exports(LINE_FIXTURE, db_path=db_path)

    storage = initialize_database(db_path)
    try:
        sources = storage.source_items.list()
        messages = storage.line_messages.list(limit=100)

        assert first.files_scanned == 1
        assert first.messages_imported == first.messages_parsed
        assert second.messages_imported == 0
        assert second.skipped_duplicates == first.messages_parsed
        assert len(sources) == 1
        assert sources[0]["source_type"] == "line_export"
        assert len(messages) == first.messages_parsed

        text_message = next(message for message in messages if message["body_text"].startswith("こんにちは"))
        metadata = json.loads(text_message["metadata_json"])
        assert text_message["message_type"] == "text"
        assert text_message["sender_id"] == "太郎"
        assert text_message["sent_at"] == "2024-01-02T12:34:00"
        assert metadata["room_name"] == "テストルーム"

        omitted = [message for message in messages if message["message_type"] == "omitted"]
        system = [message for message in messages if message["message_type"] == "system"]
        malformed = [message for message in messages if message["message_type"] == "malformed"]
        assert omitted
        assert system
        assert malformed
    finally:
        storage.close()


def test_line_ingest_folder_scans_txt_files_only(tmp_path):
    shutil.copyfile(LINE_FIXTURE, tmp_path / "export.txt")
    (tmp_path / "ignored.md").write_text("12:00\t太郎\t読まない", encoding="utf-8")

    result = ingest_line_exports(tmp_path, db_path=tmp_path / "metadata.sqlite3", dry_run=True)

    assert result.files_scanned == 1
    assert result.messages_parsed > 0


def test_line_ingest_cli_output_is_privacy_safe(capsys):
    exit_code = main(["ingest", "line", "--path", str(LINE_FIXTURE), "--dry-run"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "LINE ingest dry-run complete" in output
    assert "messages_parsed=" in output
    assert "こんにちは" not in output
    assert "太郎" not in output
    assert "line_export_japanese.txt" not in output
    assert str(LINE_FIXTURE) not in output

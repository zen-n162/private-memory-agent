import json
import shutil
from pathlib import Path

from private_memory_agent.cli import main
from private_memory_agent.ingestion.notes import PDF_PLACEHOLDER_BODY, ingest_notes, parse_note_file
from private_memory_agent.storage import initialize_database


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "notes"
MARKDOWN_FIXTURE = FIXTURE_DIR / "japanese_frontmatter.md"
TXT_FIXTURE = FIXTURE_DIR / "japanese_plain.txt"
JSON_FIXTURE = FIXTURE_DIR / "japanese_note.json"
PDF_FIXTURE = FIXTURE_DIR / "note_placeholder.pdf"


def test_parse_markdown_frontmatter_and_japanese_body():
    document = parse_note_file(MARKDOWN_FIXTURE)

    assert document.title == "研究メモ"
    assert "今日はローカル" in document.body
    assert document.created_at_source == "2024-02-01T09:00:00"
    assert document.updated_at_source == "2024-02-02T10:30:00"
    assert document.metadata["source_format"] == "md"
    assert "title" in document.metadata["frontmatter_keys"]


def test_parse_txt_uses_first_non_empty_line_as_title():
    document = parse_note_file(TXT_FIXTURE)

    assert document.title == "買い物メモ"
    assert "味噌" in document.body
    assert document.updated_at_source is not None


def test_parse_json_note_export():
    document = parse_note_file(JSON_FIXTURE)

    assert document.title == "JSON形式のメモ"
    assert document.body == "これはJSONから読み込む人工メモです。"
    assert document.created_at_source == "2024-03-01T12:00:00"
    assert document.updated_at_source == "2024-03-02T18:30:00"
    assert "body" in document.metadata["json_keys"]


def test_parse_pdf_placeholder_metadata_only():
    document = parse_note_file(PDF_FIXTURE)

    assert document.title == "note_placeholder"
    assert document.body == PDF_PLACEHOLDER_BODY
    assert document.metadata["source_format"] == "pdf"
    assert document.metadata["text_extraction"] == "placeholder"


def test_notes_ingest_dry_run_does_not_create_database(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"

    result = ingest_notes(FIXTURE_DIR, db_path=db_path, dry_run=True)

    assert result.dry_run is True
    assert result.files_scanned == 4
    assert result.notes_parsed == 4
    assert result.notes_imported == 4
    assert result.skipped_unsupported == 1
    assert result.errors == 0
    assert not db_path.exists()


def test_notes_ingest_imports_rows_and_skips_duplicates(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"

    first = ingest_notes(FIXTURE_DIR, db_path=db_path)
    second = ingest_notes(FIXTURE_DIR, db_path=db_path)

    storage = initialize_database(db_path)
    try:
        sources = storage.source_items.list(limit=100)
        notes = storage.notes.list(limit=100)

        assert first.notes_imported == 4
        assert second.notes_imported == 0
        assert second.skipped_duplicates == 4
        assert len(sources) == 4
        assert len(notes) == 4
        assert {source["source_type"] for source in sources} == {"note_export"}

        markdown_note = next(note for note in notes if note["title"] == "研究メモ")
        metadata = json.loads(markdown_note["metadata_json"])
        assert "人工データ" in markdown_note["body_text"]
        assert markdown_note["created_at_source"] == "2024-02-01T09:00:00"
        assert metadata["source_format"] == "md"

        pdf_note = next(note for note in notes if note["title"] == "note_placeholder")
        assert pdf_note["body_text"] == PDF_PLACEHOLDER_BODY
    finally:
        storage.close()


def test_notes_ingest_single_file(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"

    result = ingest_notes(MARKDOWN_FIXTURE, db_path=db_path)

    assert result.files_scanned == 1
    assert result.notes_imported == 1


def test_notes_cli_output_is_privacy_safe(capsys):
    exit_code = main(["ingest", "notes", "--path", str(FIXTURE_DIR), "--dry-run"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Notes ingest dry-run complete" in output
    assert "notes_parsed=4" in output
    assert "研究メモ" not in output
    assert "今日はローカル" not in output
    assert "japanese_frontmatter.md" not in output
    assert str(FIXTURE_DIR) not in output


def test_notes_ingest_folder_uses_artificial_tmp_files(tmp_path):
    source_dir = tmp_path / "exports"
    source_dir.mkdir()
    shutil.copyfile(MARKDOWN_FIXTURE, source_dir / "copy.md")

    result = ingest_notes(source_dir, db_path=tmp_path / "metadata.sqlite3")

    assert result.files_scanned == 1
    assert result.notes_imported == 1

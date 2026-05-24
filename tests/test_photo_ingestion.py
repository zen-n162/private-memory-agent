import shutil
from pathlib import Path

from private_memory_agent.cli import main
from private_memory_agent.ingestion.photos import extract_photo_metadata, ingest_photos
from private_memory_agent.storage import initialize_database


FIXTURE_DIR = Path(__file__).parent / "fixtures"
TINY_PNG = FIXTURE_DIR / "tiny.png"


def test_extracts_synthetic_png_metadata():
    metadata = extract_photo_metadata(TINY_PNG)

    assert metadata.media_type == "image"
    assert metadata.mime_type == "image/png"
    assert metadata.width == 1
    assert metadata.height == 1
    assert metadata.file_size_bytes > 0
    assert len(metadata.sha256) == 64


def test_photo_ingest_dry_run_does_not_create_database(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"

    result = ingest_photos(FIXTURE_DIR, db_path=db_path, dry_run=True)

    assert result.dry_run is True
    assert result.scanned == 1
    assert result.imported == 1
    assert result.skipped_duplicates == 0
    assert result.errors == 0
    assert not db_path.exists()


def test_photo_ingest_imports_source_and_media_rows_then_skips_duplicates(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"

    first = ingest_photos(FIXTURE_DIR, db_path=db_path, dry_run=False)
    second = ingest_photos(FIXTURE_DIR, db_path=db_path, dry_run=False)

    storage = initialize_database(db_path)
    try:
        sources = storage.source_items.list()
        media_items = storage.media_items.list()

        assert first.imported == 1
        assert first.skipped_duplicates == 0
        assert second.imported == 0
        assert second.skipped_duplicates == 1
        assert len(sources) == 1
        assert len(media_items) == 1
        assert sources[0]["source_type"] == "photo"
        assert sources[0]["content_sha256"] == media_items[0]["sha256"]
        assert media_items[0]["media_type"] == "image"
        assert media_items[0]["width"] == 1
        assert media_items[0]["height"] == 1
    finally:
        storage.close()


def test_photo_ingest_counts_unsupported_files_without_logging_names(tmp_path):
    shutil.copyfile(TINY_PNG, tmp_path / "copy.png")
    private_name = "do-not-log-this-name.txt"
    (tmp_path / private_name).write_text("not media", encoding="utf-8")

    result = ingest_photos(tmp_path, db_path=tmp_path / "metadata.sqlite3", dry_run=True)

    assert result.scanned == 1
    assert result.skipped_unsupported == 1


def test_photo_ingest_cli_dry_run_is_privacy_safe(capsys):
    exit_code = main(["ingest", "photos", "--path", str(FIXTURE_DIR), "--dry-run"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Photo ingest dry-run complete" in output
    assert "scanned=1" in output
    assert "tiny.png" not in output
    assert str(FIXTURE_DIR) not in output

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from private_memory_agent.cli import main
from private_memory_agent.media_timestamps import (
    audit_media_timestamps,
    backfill_media_timestamps,
    extract_media_timestamp,
)
from private_memory_agent.storage import initialize_database


def _write_jpeg(path: Path, *, exif_datetime: str | None = None) -> None:
    image = Image.new("RGB", (8, 6), color=(120, 80, 30))
    kwargs = {}
    if exif_datetime:
        exif = Image.Exif()
        exif[36867] = exif_datetime
        kwargs["exif"] = exif
    image.save(path, format="JPEG", **kwargs)


def _insert_media(storage, path: Path, *, taken_at: str | None = None) -> int:
    source_id = storage.source_items.insert_source(
        source_type="photo",
        source_uri=f"fixture://{path.name}",
        content_sha256=f"sha-{path.name}",
    )
    return storage.media_items.insert_media(
        source_item_id=source_id,
        media_type="image",
        file_path=str(path),
        sha256=f"sha-{path.name}",
        mime_type="image/jpeg",
        taken_at=taken_at,
    )


def test_extract_media_timestamp_reads_pillow_exif(tmp_path):
    image_path = tmp_path / "synthetic.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")

    result = extract_media_timestamp(image_path, method="pillow")

    assert result.succeeded is True
    assert result.taken_at == "2025-12-03T10:11:12"
    assert result.source == "exif_datetime_original"
    assert result.confidence == "high"


def test_extract_media_timestamp_uses_file_mtime_only_when_enabled(tmp_path):
    image_path = tmp_path / "no-exif.jpg"
    _write_jpeg(image_path)
    timestamp = datetime(2025, 12, 4, 9, 8, 7, tzinfo=timezone.utc).timestamp()
    os.utime(image_path, (timestamp, timestamp))

    without_fallback = extract_media_timestamp(image_path, method="pillow", fallback="none")
    with_fallback = extract_media_timestamp(image_path, method="pillow", fallback="file-mtime")

    assert without_fallback.succeeded is False
    assert with_fallback.succeeded is True
    assert with_fallback.source == "file_mtime"
    assert with_fallback.confidence == "low"


def test_extract_media_timestamp_can_parse_clear_filename_date(tmp_path):
    image_path = tmp_path / "IMG_20251203_101112.jpg"
    _write_jpeg(image_path)

    result = extract_media_timestamp(image_path, method="pillow", fallback="none")

    assert result.succeeded is True
    assert result.taken_at == "2025-12-03T10:11:12"
    assert result.source == "filename_datetime"
    assert result.confidence == "medium"


def test_timestamp_audit_reports_counts_without_paths(tmp_path):
    db_path = tmp_path / "timestamps.sqlite3"
    exif_path = tmp_path / "exif.jpg"
    missing_path = tmp_path / "missing.jpg"
    unsupported_path = tmp_path / "unsupported.bin"
    _write_jpeg(exif_path, exif_datetime="2025:12:03 10:11:12")
    unsupported_path.write_bytes(b"not-media")
    storage = initialize_database(db_path)
    try:
        _insert_media(storage, exif_path)
        _insert_media(storage, missing_path)
        _insert_media(storage, unsupported_path)
    finally:
        storage.close()

    report = audit_media_timestamps(db_path, method="pillow")
    serialized = json.dumps(report.to_dict(), ensure_ascii=False)

    assert report.total_media_items == 3
    assert report.files_existing_count == 2
    assert report.files_missing_count == 1
    assert report.extractable_exif_datetime_count == 1
    assert report.unsupported_format_count == 1
    assert str(tmp_path) not in serialized
    assert "exif.jpg" not in serialized


def test_backfill_dry_run_does_not_write_taken_at(tmp_path):
    db_path = tmp_path / "timestamps.sqlite3"
    image_path = tmp_path / "exif.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path)
    finally:
        storage.close()

    report = backfill_media_timestamps(db_path, method="pillow", dry_run=True)
    storage = initialize_database(db_path)
    try:
        row = storage.media_items.get(media_id)
    finally:
        storage.close()

    assert report.dry_run_update_count == 1
    assert report.updated_count == 0
    assert row["taken_at"] is None


def test_backfill_writes_timestamp_and_provenance(tmp_path):
    db_path = tmp_path / "timestamps.sqlite3"
    image_path = tmp_path / "exif.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path)
    finally:
        storage.close()

    report = backfill_media_timestamps(db_path, method="pillow", dry_run=False, commit_interval=1)
    storage = initialize_database(db_path)
    try:
        row = storage.media_items.get(media_id)
    finally:
        storage.close()

    assert report.updated_count == 1
    assert report.dry_run_update_count == 0
    assert report.dry_run is False
    assert report.commit_count == 1
    assert row["taken_at"] == "2025-12-03T10:11:12"
    assert row["taken_at_source"] == "exif_datetime_original"
    assert row["taken_at_confidence"] == "high"
    assert row["metadata_updated_at"]


def test_backfill_does_not_overwrite_existing_taken_at_by_default(tmp_path):
    db_path = tmp_path / "timestamps.sqlite3"
    image_path = tmp_path / "exif.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path, taken_at="2024-01-01T00:00:00")
    finally:
        storage.close()

    report = backfill_media_timestamps(db_path, method="pillow", dry_run=False)
    storage = initialize_database(db_path)
    try:
        row = storage.media_items.get(media_id)
    finally:
        storage.close()

    assert report.total_selected_count == 0
    assert row["taken_at"] == "2024-01-01T00:00:00"


def test_backfill_respects_min_confidence(tmp_path):
    db_path = tmp_path / "timestamps.sqlite3"
    image_path = tmp_path / "IMG_20251203_101112.jpg"
    _write_jpeg(image_path)
    storage = initialize_database(db_path)
    try:
        _insert_media(storage, image_path)
    finally:
        storage.close()

    high = backfill_media_timestamps(db_path, method="pillow", dry_run=True, min_confidence="high")
    medium = backfill_media_timestamps(db_path, method="pillow", dry_run=True, min_confidence="medium")

    assert high.dry_run_update_count == 0
    assert high.error_classes["ConfidenceTooLow"] == 1
    assert medium.dry_run_update_count == 1


def test_timestamp_cli_backfill_output_is_privacy_safe(capsys, temp_config_factory, tmp_path):
    db_path = tmp_path / "timestamps.sqlite3"
    image_path = tmp_path / "secret-name.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path)
    finally:
        storage.close()

    exit_code = main(
        [
            "media",
            "timestamps",
            "backfill",
            "--config-dir",
            str(temp_config_factory()),
            "--db",
            str(db_path),
            "--dry-run",
            "--limit",
            "1",
            "--method",
            "pillow",
            "--show-errors",
        ],
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "DRY RUN: no database rows were updated. Re-run with --apply to write timestamps." in output
    assert "dry_run_update_count=1" in output
    assert str(tmp_path) not in output
    assert "secret-name" not in output


def test_timestamp_cli_backfill_defaults_to_dry_run_and_explains_apply(
    capsys,
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "timestamps.sqlite3"
    image_path = tmp_path / "default-dry-run.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path)
    finally:
        storage.close()

    exit_code = main(
        [
            "media",
            "timestamps",
            "backfill",
            "--config-dir",
            str(temp_config_factory()),
            "--db",
            str(db_path),
            "--limit",
            "1",
            "--method",
            "pillow",
        ],
    )
    output = capsys.readouterr().out
    storage = initialize_database(db_path)
    try:
        row = storage.media_items.get(media_id)
    finally:
        storage.close()

    assert exit_code == 0
    assert row["taken_at"] is None
    assert "dry_run=True" in output
    assert "dry_run_update_count=1" in output
    assert "Re-run with --apply" in output


def test_timestamp_cli_backfill_apply_writes_and_commits_without_modifying_source(
    capsys,
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "timestamps.sqlite3"
    image_path = tmp_path / "apply-secret.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")
    before_stat = image_path.stat()
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path)
    finally:
        storage.close()

    exit_code = main(
        [
            "media",
            "timestamps",
            "backfill",
            "--config-dir",
            str(temp_config_factory()),
            "--db",
            str(db_path),
            "--limit",
            "1",
            "--method",
            "pillow",
            "--only-missing",
            "--apply",
            "--commit-interval",
            "1",
        ],
    )
    output = capsys.readouterr().out
    after_stat = image_path.stat()
    storage = initialize_database(db_path)
    try:
        row = storage.media_items.get(media_id)
    finally:
        storage.close()

    assert exit_code == 0
    assert row["taken_at"] == "2025-12-03T10:11:12"
    assert row["taken_at_source"] == "exif_datetime_original"
    assert row["metadata_updated_at"]
    assert "APPLY MODE: database metadata was updated. Source files were not modified." in output
    assert "dry_run=False" in output
    assert "updated_count=1" in output
    assert "dry_run_update_count=0" in output
    assert "commit_count=1" in output
    assert before_stat.st_mtime_ns == after_stat.st_mtime_ns
    assert before_stat.st_size == after_stat.st_size
    assert str(tmp_path) not in output
    assert "apply-secret" not in output


def test_timestamp_cli_backfill_uses_configured_storage_db_for_dry_run_and_apply(
    capsys,
    temp_config_factory,
    tmp_path,
):
    configured_db_path = tmp_path / "configured" / "configured.sqlite3"
    unrelated_default_db = tmp_path / "default.sqlite3"
    image_path = tmp_path / "configured-secret.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")
    storage = initialize_database(configured_db_path)
    try:
        media_id = _insert_media(storage, image_path)
    finally:
        storage.close()
    initialize_database(unrelated_default_db).close()
    config_dir = temp_config_factory(
        local_paths_yaml="\n".join(
            [
                "storage:",
                f"  sqlite_path: {configured_db_path}",
            ],
        ),
    )

    dry_run_exit = main(
        [
            "media",
            "timestamps",
            "backfill",
            "--config-dir",
            str(config_dir),
            "--config",
            str(config_dir / "paths.local.yaml"),
            "--limit",
            "1",
            "--method",
            "pillow",
        ],
    )
    dry_run_output = capsys.readouterr().out
    storage = initialize_database(configured_db_path)
    try:
        row_after_dry_run = storage.media_items.get(media_id)
    finally:
        storage.close()

    apply_exit = main(
        [
            "media",
            "timestamps",
            "backfill",
            "--config-dir",
            str(config_dir),
            "--config",
            str(config_dir / "paths.local.yaml"),
            "--limit",
            "1",
            "--method",
            "pillow",
            "--apply",
            "--commit-interval",
            "1",
        ],
    )
    apply_output = capsys.readouterr().out
    storage = initialize_database(configured_db_path)
    try:
        row_after_apply = storage.media_items.get(media_id)
    finally:
        storage.close()
    unrelated_storage = initialize_database(unrelated_default_db)
    try:
        unrelated_count = len(unrelated_storage.media_items.list(include_excluded=True))
    finally:
        unrelated_storage.close()

    assert dry_run_exit == 0
    assert "dry_run_update_count=1" in dry_run_output
    assert row_after_dry_run["taken_at"] is None
    assert apply_exit == 0
    assert "updated_count=1" in apply_output
    assert "commit_count=1" in apply_output
    assert row_after_apply["taken_at"] == "2025-12-03T10:11:12"
    assert row_after_apply["taken_at_source"] == "exif_datetime_original"
    assert row_after_apply["taken_at_confidence"] == "high"
    assert unrelated_count == 0
    assert str(tmp_path) not in dry_run_output
    assert str(tmp_path) not in apply_output
    assert "configured-secret" not in dry_run_output
    assert "configured-secret" not in apply_output


def test_timestamp_cli_backfill_explicit_db_overrides_configured_storage_db(
    capsys,
    temp_config_factory,
    tmp_path,
):
    configured_db_path = tmp_path / "configured.sqlite3"
    explicit_db_path = tmp_path / "explicit.sqlite3"
    image_path = tmp_path / "explicit-secret.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")
    initialize_database(configured_db_path).close()
    storage = initialize_database(explicit_db_path)
    try:
        media_id = _insert_media(storage, image_path)
    finally:
        storage.close()
    config_dir = temp_config_factory(
        local_paths_yaml="\n".join(
            [
                "storage:",
                f"  sqlite_path: {configured_db_path}",
            ],
        ),
    )

    exit_code = main(
        [
            "media",
            "timestamps",
            "backfill",
            "--config-dir",
            str(config_dir),
            "--config",
            str(config_dir / "paths.local.yaml"),
            "--db",
            str(explicit_db_path),
            "--limit",
            "1",
            "--method",
            "pillow",
            "--apply",
        ],
    )
    output = capsys.readouterr().out
    storage = initialize_database(explicit_db_path)
    try:
        explicit_row = storage.media_items.get(media_id)
    finally:
        storage.close()
    configured_storage = initialize_database(configured_db_path)
    try:
        configured_count = len(configured_storage.media_items.list(include_excluded=True))
    finally:
        configured_storage.close()

    assert exit_code == 0
    assert explicit_row["taken_at"] == "2025-12-03T10:11:12"
    assert configured_count == 0
    assert "updated_count=1" in output
    assert str(tmp_path) not in output
    assert "explicit-secret" not in output


def test_timestamp_cli_backfill_apply_respects_only_missing(
    capsys,
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "timestamps.sqlite3"
    image_path = tmp_path / "existing.jpg"
    _write_jpeg(image_path, exif_datetime="2025:12:03 10:11:12")
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path, taken_at="2024-01-01T00:00:00")
    finally:
        storage.close()

    exit_code = main(
        [
            "media",
            "timestamps",
            "backfill",
            "--config-dir",
            str(temp_config_factory()),
            "--db",
            str(db_path),
            "--limit",
            "1",
            "--method",
            "pillow",
            "--only-missing",
            "--apply",
        ],
    )
    output = capsys.readouterr().out
    storage = initialize_database(db_path)
    try:
        row = storage.media_items.get(media_id)
    finally:
        storage.close()

    assert exit_code == 0
    assert row["taken_at"] == "2024-01-01T00:00:00"
    assert "updated_count=0" in output
    assert "total_selected_count=0" in output


def test_timestamp_cli_rejects_invalid_commit_interval(capsys, temp_config_factory, tmp_path):
    db_path = tmp_path / "timestamps.sqlite3"
    initialize_database(db_path).close()

    exit_code = main(
        [
            "media",
            "timestamps",
            "backfill",
            "--config-dir",
            str(temp_config_factory()),
            "--db",
            str(db_path),
            "--commit-interval",
            "0",
        ],
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "--commit-interval must be positive" in output

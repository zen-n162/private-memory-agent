import json
from pathlib import Path

from private_memory_agent.annotation import (
    build_annotation_stats_report,
    list_failed_photo_annotations,
)
from private_memory_agent.annotation.stats import PHOTO_ANNOTATION_ERROR_ACTION
from private_memory_agent.cli import main
from private_memory_agent.storage import initialize_database


def _insert_media(storage, *, path: str, mime_type: str, media_type: str = "image") -> int:
    source_id = storage.source_items.insert_source(
        source_type="photo",
        source_uri=f"fixture://{path}",
        content_sha256=f"sha-{path}",
    )
    return storage.media_items.insert_media(
        source_item_id=source_id,
        media_type=media_type,
        file_path=path,
        sha256=f"sha-{path}",
        mime_type=mime_type,
        width=100 if media_type == "image" else None,
        height=80 if media_type == "image" else None,
    )


def test_annotation_stats_report_counts_synthetic_data(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        annotated_id = _insert_media(storage, path="fixture-a.png", mime_type="image/png")
        failed_id = _insert_media(storage, path="fixture-b.jpg", mime_type="image/jpeg")
        _insert_media(storage, path="fixture-c.heic", mime_type="image/heic")
        _insert_media(storage, path="fixture-video.mp4", mime_type="video/mp4", media_type="video")
        storage.media_annotations.insert(
            {
                "media_item_id": annotated_id,
                "annotation_type": "vision",
                "source": "model",
                "value_text": "synthetic caption",
                "model_id": "fake-vl",
            },
        )
        storage.audit_log.insert(
            {
                "action": PHOTO_ANNOTATION_ERROR_ACTION,
                "actor": "pytest",
                "target_table": "media_items",
                "target_id": failed_id,
                "status": "error",
                "detail_json": json.dumps(
                    {
                        "error_class": "ModelRuntimeError",
                        "message": "model endpoint request timed out",
                        "model_id": "fake-vl",
                        "image_format": "image/jpeg",
                        "dimensions": "100x80",
                        "preprocessing_succeeded": True,
                    },
                    sort_keys=True,
                ),
            },
        )
    finally:
        storage.close()

    report = build_annotation_stats_report(db_path)
    stats = report.photo_annotations

    assert stats.media_items_count == 4
    assert stats.image_media_items_count == 3
    assert stats.media_annotations_count == 1
    assert stats.annotated_media_count == 1
    assert stats.unannotated_media_count == 2
    assert stats.failed_annotation_count == 1
    assert stats.failed_annotation_event_count == 1
    assert stats.skipped_unsupported_format_count == 1
    assert stats.annotation_success_rate == 0.3333
    assert stats.model_id_breakdown[0].to_dict() == {"model_id": "fake-vl", "count": 1}
    assert stats.latest_annotation_timestamp is not None


def test_failed_photo_annotation_report_is_privacy_safe(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    private_filename = "secret-private-photo.png"
    storage = initialize_database(db_path)
    try:
        failed_id = _insert_media(storage, path=private_filename, mime_type="image/png")
        storage.audit_log.insert(
            {
                "action": PHOTO_ANNOTATION_ERROR_ACTION,
                "actor": "pytest",
                "target_table": "media_items",
                "target_id": failed_id,
                "status": "error",
                "detail_json": json.dumps(
                    {
                        "error_class": "ImagePreprocessingError",
                        "message": "image preprocessing failed",
                        "model_id": "fake-vl",
                        "image_format": "image/png",
                        "dimensions": "100x80",
                        "preprocessing_succeeded": False,
                    },
                    sort_keys=True,
                ),
            },
        )
    finally:
        storage.close()

    report = list_failed_photo_annotations(db_path)
    payload = report.to_dict()

    assert payload["failed_annotation_count"] == 1
    failure = payload["failed_annotations"][0]
    assert failure["media_item_id"] == failed_id
    assert failure["error_class"] == "ImagePreprocessingError"
    assert failure["message"] == "image preprocessing failed"
    assert failure["image_format"] == "image/png"
    assert failure["dimensions"] == "100x80"
    assert failure["preprocessing_succeeded"] is False
    assert private_filename not in json.dumps(payload)
    assert str(tmp_path) not in json.dumps(payload)


def test_stats_and_annotate_status_cli_do_not_leak_private_paths(
    capsys,
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "metadata.sqlite3"
    private_filename = "secret-private-photo.png"
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, path=private_filename, mime_type="image/png")
        storage.audit_log.insert(
            {
                "action": PHOTO_ANNOTATION_ERROR_ACTION,
                "actor": "pytest",
                "target_table": "media_items",
                "target_id": media_id,
                "status": "error",
                "detail_json": json.dumps(
                    {
                        "error_class": "ModelRuntimeError",
                        "message": "model endpoint request timed out",
                        "model_id": "fake-vl",
                    },
                    sort_keys=True,
                ),
            },
        )
    finally:
        storage.close()
    config_dir = temp_config_factory()

    stats_exit = main(["stats", "--db", str(db_path), "--config-dir", str(config_dir)])
    stats_output = capsys.readouterr().out
    status_exit = main(
        [
            "annotate",
            "photos",
            "--status",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
        ],
    )
    status_output = capsys.readouterr().out
    failed_exit = main(
        [
            "annotate",
            "photos",
            "--failed",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
        ],
    )
    failed_output = capsys.readouterr().out

    assert stats_exit == 0
    assert status_exit == 0
    assert failed_exit == 0
    assert json.loads(stats_output)["photo_annotations"]["media_items_count"] == 1
    assert json.loads(status_output)["photo_annotations"]["failed_annotation_count"] == 1
    assert json.loads(failed_output)["failed_annotations"][0]["media_item_id"] == media_id
    combined = stats_output + status_output + failed_output
    assert private_filename not in combined
    assert str(tmp_path) not in combined


def test_annotation_failure_is_tracked_in_audit_log(tmp_path):
    from private_memory_agent.annotation import annotate_photos
    from private_memory_agent.runtime import ModelRuntimeError

    class TimeoutClient:
        def analyze(self, request):
            raise ModelRuntimeError("model endpoint request timed out")

    db_path = tmp_path / "metadata.sqlite3"
    fixture_path = Path(__file__).parent / "fixtures" / "tiny.png"
    storage = initialize_database(db_path)
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://timeout",
            content_sha256="timeout",
        )
        media_id = storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path=str(fixture_path),
            sha256="timeout",
            mime_type="image/png",
            width=1,
            height=1,
        )
    finally:
        storage.close()

    result = annotate_photos(db_path, client=TimeoutClient(), model_id="fake-vl", fail_fast=True)
    failed_report = list_failed_photo_annotations(db_path)

    assert result.errors == 1
    failure = failed_report.failed_annotations[0]
    assert failure.media_item_id == media_id
    assert failure.error_class == "ModelRuntimeError"
    assert failure.message == "model endpoint request timed out"
    assert failure.model_id == "fake-vl"
    assert failure.preprocessing_succeeded is True

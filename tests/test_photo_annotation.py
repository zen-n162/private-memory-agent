import json
import tomllib
from pathlib import Path

import pytest
from PIL import Image

from private_memory_agent.annotation import (
    PhotoPreprocessOptions,
    UnsupportedImageFormat,
    annotate_photos,
    detect_image_mime,
    preprocess_image_for_vision,
    select_unannotated_photo_media,
)
from private_memory_agent.cli import main
from private_memory_agent.ingestion.photos import ingest_photos
from private_memory_agent.runtime import ModelRuntimeError, VisionRequest, VisionResponse
from private_memory_agent.storage import initialize_database


FIXTURE_DIR = Path(__file__).parent / "fixtures"
TINY_PNG = FIXTURE_DIR / "tiny.png"


class StructuredVisionClient:
    def __init__(self):
        self.requests: list[VisionRequest] = []

    def analyze(self, request: VisionRequest) -> VisionResponse:
        self.requests.append(request)
        return VisionResponse(
            text="fallback caption",
            model="fake-vl",
            raw={
                "annotation": {
                    "caption": "人工テスト画像の説明",
                    "objects": ["pixel", "fixture"],
                    "ocr_text": "テストOCR",
                    "confidence": 0.87,
                },
            },
        )


class FailingVisionClient:
    def __init__(self):
        self.requests: list[VisionRequest] = []

    def analyze(self, request: VisionRequest) -> VisionResponse:
        self.requests.append(request)
        raise RuntimeError("synthetic model failure")


class TimeoutVisionClient:
    def __init__(self):
        self.requests: list[VisionRequest] = []

    def analyze(self, request: VisionRequest) -> VisionResponse:
        self.requests.append(request)
        raise ModelRuntimeError("model endpoint request timed out")


class FakeHTTPResponse:
    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _write_image(path: Path, *, size=(64, 32), mode="RGB", image_format="PNG", exif=None) -> None:
    image = Image.new(mode, size, color=(120, 30, 200) if mode == "RGB" else 128)
    save_kwargs = {}
    if exif is not None:
        save_kwargs["exif"] = exif
    image.save(path, format=image_format, **save_kwargs)


def test_preprocess_large_image_resizes_and_outputs_jpeg_data_uri(tmp_path):
    image_path = tmp_path / "large.png"
    _write_image(image_path, size=(3000, 1200), image_format="PNG")

    processed = preprocess_image_for_vision(
        image_path,
        "image/png",
        options=PhotoPreprocessOptions(max_side_px=1280, output_format="jpeg", quality=90),
    )

    assert processed.mime_type == "image/jpeg"
    assert processed.width == 1280
    assert processed.height == 512
    assert processed.original_width == 3000
    assert processed.original_height == 1200
    assert processed.data_uri.startswith("data:image/jpeg;base64,")
    with Image.open(image_path) as original:
        assert original.size == (3000, 1200)
    with Image.open(Path(tmp_path / "large.png")) as original_again:
        assert original_again.size == (3000, 1200)


def test_preprocess_strips_exif_metadata_and_does_not_modify_source(tmp_path):
    image_path = tmp_path / "with-exif.jpg"
    exif = Image.Exif()
    exif[270] = "synthetic description"
    _write_image(image_path, size=(80, 60), image_format="JPEG", exif=exif)
    before = image_path.read_bytes()

    processed = preprocess_image_for_vision(image_path)

    assert image_path.read_bytes() == before
    with Image.open(image_path) as original:
        assert original.getexif().get(270) == "synthetic description"
    output_path = tmp_path / "processed.jpg"
    output_path.write_bytes(processed.data)
    with Image.open(output_path) as output:
        assert dict(output.getexif()) == {}


def test_preprocess_supports_jpeg_png_and_webp_inputs(tmp_path):
    inputs = [
        ("fixture.jpg", "JPEG", "image/jpeg"),
        ("fixture.png", "PNG", "image/png"),
        ("fixture.webp", "WEBP", "image/webp"),
    ]
    for filename, image_format, mime_type in inputs:
        path = tmp_path / filename
        _write_image(path, image_format=image_format)

        processed = preprocess_image_for_vision(path, mime_type)

        assert processed.mime_type == "image/jpeg"
        assert processed.source_mime_type == mime_type
        assert processed.width == 64
        assert processed.height == 32


def test_preprocess_unsupported_suffix_is_privacy_safe(tmp_path):
    private_named_file = tmp_path / "secret-private-photo.heic"
    private_named_file.write_bytes(b"not-real-heic")

    with pytest.raises(UnsupportedImageFormat) as exc_info:
        preprocess_image_for_vision(private_named_file, "image/heic")

    assert str(exc_info.value) == "unsupported image format"
    assert "secret-private-photo.heic" not in str(exc_info.value)


def test_photo_annotation_stores_structured_vision_output(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ingest_photos(FIXTURE_DIR, db_path=db_path)
    client = StructuredVisionClient()

    result = annotate_photos(db_path, client=client, model_id="fake-vl", batch_size=2)

    storage = initialize_database(db_path)
    try:
        annotations = storage.media_annotations.list()
        row = annotations[0]
        data = json.loads(row["data_json"])

        assert result.selected == 1
        assert result.annotated == 1
        assert result.errors == 0
        assert len(client.requests) == 1
        assert client.requests[0].images[0].kind == "base64"
        assert client.requests[0].images[0].mime_type == "image/jpeg"
        assert client.requests[0].max_tokens == 512
        assert client.requests[0].temperature == 0.2
        assert row["annotation_type"] == "vision"
        assert row["source"] == "model"
        assert row["value_text"] == "人工テスト画像の説明"
        assert row["confidence"] == 0.87
        assert row["model_id"] == "fake-vl"
        assert row["created_at"]
        assert data["objects"] == ["pixel", "fixture"]
        assert data["ocr_text"] == "テストOCR"
        assert result.preprocessed == 1
    finally:
        storage.close()


def test_photo_annotation_is_resume_safe(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ingest_photos(FIXTURE_DIR, db_path=db_path)

    first = annotate_photos(db_path, client=StructuredVisionClient(), model_id="fake-vl")
    second = annotate_photos(db_path, client=StructuredVisionClient(), model_id="fake-vl")

    storage = initialize_database(db_path)
    try:
        assert first.annotated == 1
        assert second.selected == 0
        assert second.annotated == 0
        assert len(storage.media_annotations.list()) == 1
    finally:
        storage.close()


def test_photo_annotation_limit_and_batch_size(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        with storage.transaction():
            for index in range(3):
                source_id = storage.source_items.insert_source(
                    source_type="photo",
                    source_uri=f"fixture://tiny-{index}",
                    content_sha256=f"sha-{index}",
                )
                storage.media_items.insert_media(
                    source_item_id=source_id,
                    media_type="image",
                    file_path=str(TINY_PNG),
                    sha256=f"sha-{index}",
                    mime_type="image/png",
                    width=1,
                    height=1,
                )
        assert len(select_unannotated_photo_media(storage, limit=2)) == 2
    finally:
        storage.close()

    result = annotate_photos(
        db_path,
        client=StructuredVisionClient(),
        model_id="fake-vl",
        limit=2,
        batch_size=1,
    )

    storage = initialize_database(db_path)
    try:
        assert result.selected == 2
        assert result.annotated == 2
        assert len(storage.media_annotations.list()) == 2
    finally:
        storage.close()


def test_photo_annotation_skips_missing_files(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://missing",
            content_sha256="missing",
        )
        storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path=str(tmp_path / "missing.png"),
            sha256="missing",
            mime_type="image/png",
        )
    finally:
        storage.close()

    result = annotate_photos(db_path, client=StructuredVisionClient(), model_id="fake-vl")

    assert result.selected == 1
    assert result.annotated == 0
    assert result.skipped_missing_file == 1


def test_photo_annotation_dry_run_does_not_call_model_or_write(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ingest_photos(FIXTURE_DIR, db_path=db_path)
    client = StructuredVisionClient()

    result = annotate_photos(
        db_path,
        client=client,
        model_id="fake-vl",
        dry_run=True,
    )

    storage = initialize_database(db_path)
    try:
        assert result.dry_run is True
        assert result.selected == 1
        assert result.would_annotate == 1
        assert result.annotated == 0
        assert client.requests == []
        assert storage.media_annotations.list() == []
    finally:
        storage.close()


def test_photo_annotation_dry_run_can_check_preprocessing(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ingest_photos(FIXTURE_DIR, db_path=db_path)
    client = StructuredVisionClient()

    result = annotate_photos(
        db_path,
        client=client,
        model_id="fake-vl",
        dry_run=True,
        check_preprocess=True,
    )

    storage = initialize_database(db_path)
    try:
        assert result.dry_run is True
        assert result.preprocess_checked is True
        assert result.selected == 1
        assert result.would_annotate == 1
        assert result.preprocessed == 1
        assert result.annotated == 0
        assert client.requests == []
        assert storage.media_annotations.list() == []
    finally:
        storage.close()


def test_detect_image_mime_rejects_unsupported_format_without_path():
    with pytest.raises(UnsupportedImageFormat) as exc_info:
        detect_image_mime(Path("/private/source/image.heic"), "image/heic")

    assert str(exc_info.value) == "unsupported image format"
    assert "image.heic" not in str(exc_info.value)


def test_photo_annotation_reports_unsupported_format_safely(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    private_named_file = tmp_path / "private-name.heic"
    private_named_file.write_bytes(b"not-real-heic")
    storage = initialize_database(db_path)
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://unsupported",
            content_sha256="unsupported",
        )
        storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path=str(private_named_file),
            sha256="unsupported",
            mime_type="image/heic",
        )
    finally:
        storage.close()

    result = annotate_photos(db_path, client=StructuredVisionClient(), model_id="fake-vl")

    assert result.selected == 1
    assert result.annotated == 0
    assert result.errors == 1
    assert result.error_details[0].error_class == "UnsupportedImageFormat"
    assert result.error_details[0].message == "unsupported image format"
    assert "private-name.heic" not in result.error_details[0].message


def test_photo_annotation_reports_preprocessing_error_safely(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    private_named_file = tmp_path / "secret-private-photo.png"
    private_named_file.write_bytes(b"not-real-png")
    storage = initialize_database(db_path)
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://broken-image",
            content_sha256="broken-image",
        )
        storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path=str(private_named_file),
            sha256="broken-image",
            mime_type="image/png",
            width=100,
            height=80,
        )
    finally:
        storage.close()

    result = annotate_photos(db_path, client=StructuredVisionClient(), model_id="fake-vl")

    assert result.selected == 1
    assert result.annotated == 0
    assert result.errors == 1
    assert result.error_details[0].error_class == "ImagePreprocessingError"
    assert result.error_details[0].message == "image preprocessing failed"
    assert result.error_details[0].image_format == "image/png"
    assert result.error_details[0].dimensions == "100x80"
    assert result.error_details[0].preprocessing_succeeded is False
    assert "secret-private-photo.png" not in result.error_details[0].message


def test_photo_annotation_failure_aggregates_error_classes_and_fail_fast(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        with storage.transaction():
            for index in range(3):
                source_id = storage.source_items.insert_source(
                    source_type="photo",
                    source_uri=f"fixture://failing-{index}",
                    content_sha256=f"failing-{index}",
                )
                storage.media_items.insert_media(
                    source_item_id=source_id,
                    media_type="image",
                    file_path=str(TINY_PNG),
                    sha256=f"failing-{index}",
                    mime_type="image/png",
                )
    finally:
        storage.close()
    client = FailingVisionClient()

    result = annotate_photos(
        db_path,
        client=client,
        model_id="fake-vl",
        fail_fast=True,
    )

    assert result.selected == 3
    assert result.annotated == 0
    assert result.errors == 1
    assert len(client.requests) == 1
    assert result.top_error_classes() == [("RuntimeError", 1)]
    assert result.error_details[0].media_item_id == 1


def test_photo_annotation_timeout_error_reports_preprocessing_without_paths(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ingest_photos(FIXTURE_DIR, db_path=db_path)
    client = TimeoutVisionClient()

    result = annotate_photos(db_path, client=client, model_id="fake-vl", fail_fast=True)

    assert result.selected == 1
    assert result.annotated == 0
    assert result.errors == 1
    assert result.preprocessed == 1
    assert len(client.requests) == 1
    detail = result.error_details[0]
    assert detail.error_class == "ModelRuntimeError"
    assert detail.message == "model endpoint request timed out"
    assert detail.image_format == "image/jpeg"
    assert detail.dimensions == "1x1"
    assert detail.preprocessing_succeeded is True
    assert "tiny.png" not in detail.message
    assert str(FIXTURE_DIR) not in detail.message


def test_photo_annotation_cli_is_privacy_safe(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ingest_photos(FIXTURE_DIR, db_path=db_path)

    exit_code = main(["annotate", "photos", "--db", str(db_path), "--client", "fake"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Photo annotation complete" in output
    assert "annotated=1" in output
    assert "tiny.png" not in output
    assert "fake photo annotation" not in output
    assert str(FIXTURE_DIR) not in output


def test_photo_annotation_cli_show_errors_does_not_leak_paths(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    private_named_file = tmp_path / "secret-private-photo.heic"
    private_named_file.write_bytes(b"not-real-heic")
    storage = initialize_database(db_path)
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://unsupported-cli",
            content_sha256="unsupported-cli",
        )
        storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path=str(private_named_file),
            sha256="unsupported-cli",
            mime_type="image/heic",
        )
    finally:
        storage.close()

    exit_code = main(
        [
            "annotate",
            "photos",
            "--db",
            str(db_path),
            "--client",
            "fake",
            "--show-errors",
            "--fail-fast",
        ],
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "UnsupportedImageFormat" in output
    assert "failed_media_item_ids=1" in output
    assert "secret-private-photo.heic" not in output
    assert str(tmp_path) not in output


def test_photo_annotation_preflight_failure_stops_before_image_loop(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "metadata.sqlite3"
    ingest_photos(FIXTURE_DIR, db_path=db_path)
    models_yaml = "\n".join(
        [
            f"model_root: {tmp_path / 'models'}",
            "vision_common:",
            "  provider: llama_cpp",
            "  role: photo_understanding",
            "  model_dir: vision-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8126/v1",
            "  api_format: openai-compatible",
        ],
    )
    config_dir = temp_config_factory(models_yaml=models_yaml)

    def fail_preflight(*args, **kwargs):
        raise ModelRuntimeError("model endpoint is unavailable")

    monkeypatch.setattr("private_memory_agent.cli.preflight_vision_endpoint", fail_preflight)

    exit_code = main(
        [
            "annotate",
            "photos",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--client",
            "openai-compatible",
            "--limit",
            "1",
            "--show-errors",
        ],
    )

    output = capsys.readouterr().out
    storage = initialize_database(db_path)
    try:
        assert exit_code == 2
        assert "preflight failed" in output
        assert "model endpoint is unavailable" in output
        assert storage.media_annotations.list() == []
        assert "tiny.png" not in output
    finally:
        storage.close()


def test_photo_annotation_cli_openai_client_preflights_and_sends_multimodal_request(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "metadata.sqlite3"
    ingest_photos(FIXTURE_DIR, db_path=db_path)
    models_yaml = "\n".join(
        [
            f"model_root: {tmp_path / 'models'}",
            "vision_common:",
            "  provider: llama_cpp",
            "  role: photo_understanding",
            "  model_dir: vision-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8127/v1",
            "  served_model_name: served-vision.gguf",
            "  api_format: openai-compatible",
            "  timeout_seconds: 1",
        ],
    )
    config_dir = temp_config_factory(models_yaml=models_yaml)
    payloads = []
    timeouts = []
    model_gets = 0

    def fake_urlopen(request, data=None, *, timeout=None):
        nonlocal model_gets
        assert data is None
        timeouts.append(timeout)
        if request.get_method() == "GET":
            model_gets += 1
            assert timeout == 1
            assert request.data is None
            assert request.full_url == "http://127.0.0.1:8127/v1/models"
            return FakeHTTPResponse(
                {
                    "data": [
                        {
                            "id": "served-vision.gguf",
                            "capabilities": ["text", "image"],
                        },
                    ],
                },
            )
        assert request.full_url == "http://127.0.0.1:8127/v1/chat/completions"
        assert timeout == 300
        body = json.loads(request.data.decode("utf-8"))
        payloads.append(body)
        return FakeHTTPResponse(
            {
                "model": "served-vision.gguf",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "caption": "synthetic caption",
                                    "objects": ["fixture"],
                                    "confidence": 0.9,
                                },
                            ),
                        },
                    },
                ],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    exit_code = main(
        [
            "annotate",
            "photos",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--client",
            "openai-compatible",
            "--limit",
            "1",
            "--show-errors",
            "--timeout-seconds",
            "300",
        ],
    )

    output = capsys.readouterr().out
    content = payloads[0]["messages"][0]["content"]
    storage = initialize_database(db_path)
    try:
        annotations = storage.media_annotations.list()
        assert exit_code == 0
        assert model_gets == 1
        assert timeouts == [1, 300]
        assert payloads[0]["model"] == "served-vision.gguf"
        assert payloads[0]["model"] != "vision_common"
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert len(annotations) == 1
        assert annotations[0]["value_text"] == "synthetic caption"
        assert "Photo annotation complete" in output
        assert "annotated=1" in output
        assert "tiny.png" not in output
        assert str(FIXTURE_DIR) not in output
        assert str(tmp_path) not in output
    finally:
        storage.close()


def test_pyproject_dev_dependencies_include_pytest_and_httpx():
    pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]

    assert any(dependency.startswith("pytest") for dependency in dev_dependencies)
    assert any(dependency.startswith("httpx") for dependency in dev_dependencies)

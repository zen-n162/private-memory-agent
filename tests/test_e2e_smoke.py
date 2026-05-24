import json
from urllib.error import URLError

from private_memory_agent.cli import main
from private_memory_agent.e2e import (
    E2ESmokeOptions,
    format_e2e_smoke_report,
    load_e2e_smoke_queries,
    report_to_json,
    run_e2e_smoke,
)
from private_memory_agent.retrieval import HashEmbeddingModel, index_embeddings, index_text
from private_memory_agent.storage import initialize_database


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


def _write_smoke_queries(config_dir, *, text="研究", sources="line,notes"):
    (config_dir / "e2e_smoke_queries.local.yaml").write_text(
        "\n".join(
            [
                "queries:",
                "  synthetic:",
                f"    text: \"{text}\"",
                f"    sources: {sources}",
            ],
        )
        + "\n",
        encoding="utf-8",
    )


def _insert_photo_annotation(storage, *, path="/private/synthetic-secret-photo.jpg", text="外出"):
    source_id = storage.source_items.insert_source(
        source_type="photo",
        source_uri=f"fixture://{path}",
        content_sha256=f"sha-{path}",
    )
    media_id = storage.media_items.insert_media(
        source_item_id=source_id,
        media_type="image",
        file_path=path,
        sha256=f"sha-{path}",
        mime_type="image/jpeg",
        width=120,
        height=80,
    )
    storage.media_annotations.insert(
        {
            "media_item_id": media_id,
            "annotation_type": "vision",
            "source": "model",
            "value_text": text,
            "model_id": "fake-vl",
        },
    )
    return media_id


def _insert_line_message(storage, *, text="研究の予定を確認した。"):
    return storage.line_messages.insert_message(
        source_item_id=None,
        conversation_id="fixture-room",
        message_id="line-1",
        sender_id="fixture-speaker",
        sent_at="2026-05-24T09:00:00",
        message_type="text",
        body_text=text,
    )


def _insert_note(storage, *, title="研究メモ", body="研究の進捗をまとめた。"):
    return storage.notes.insert_note(
        source_item_id=None,
        note_id="note-1",
        title=title,
        body_text=body,
        created_at_source="2026-05-24T10:00:00",
        updated_at_source="2026-05-24T10:00:00",
    )


def _leader_models_yaml(model_root, *, timeout_seconds=1, request_timeout_seconds=77):
    return "\n".join(
        [
            f"model_root: {model_root}",
            "leader:",
            "  provider: llama_cpp",
            "  role: leader_reasoning",
            "  model_dir: leader-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8111/v1",
            "  served_model_name: served-leader.gguf",
            "  api_format: openai-compatible",
            f"  timeout_seconds: {timeout_seconds}",
            f"  request_timeout_seconds: {request_timeout_seconds}",
            "  retries: 0",
        ],
    )


def test_e2e_smoke_reports_no_db_without_creating_it(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    db_path = tmp_path / "missing.sqlite3"

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, dry_run=True),
    )

    assert report.ok is False
    assert report.db_exists is False
    assert not db_path.exists()
    assert "SQLite DB does not exist" in report.warnings[0]


def test_e2e_smoke_empty_db_dry_run_counts(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    db_path = tmp_path / "metadata.sqlite3"
    initialize_database(db_path).close()

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, dry_run=True),
    )

    assert report.ok is True
    assert report.counts.media_items_count == 0
    assert report.counts.evidence_capable_source_count == 0


def test_e2e_smoke_query_loader_accepts_list_style_yaml(temp_config_factory):
    config_dir = temp_config_factory()
    (config_dir / "e2e_smoke_queries.local.yaml").write_text(
        "\n".join(
            [
                "queries:",
                "  - id: list_style",
                "    text: \"研究\"",
                "    sources:",
                "      - line",
                "      - notes",
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    queries = load_e2e_smoke_queries(config_dir)

    assert queries[0].query_id == "list_style"
    assert queries[0].text == "研究"
    assert queries[0].sources == ("line", "notes")


def test_e2e_smoke_photos_only_retrieval(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="外出", sources="photos")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        media_id = _insert_photo_annotation(storage, text="外出の記録")
    finally:
        storage.close()

    report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
        ),
    )

    assert report.ok is True
    assert report.counts.available_sources == ("photos",)
    assert report.query_results[0].evidence_ids == (f"media_items:{media_id}",)
    assert report.query_results[0].answer_succeeded is False


def test_e2e_smoke_line_only_retrieval(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        message_id = _insert_line_message(storage)
    finally:
        storage.close()
    index_text(db_path)

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, retrieval_only=True),
    )

    assert report.ok is True
    assert report.counts.available_sources == ("line",)
    assert report.query_results[0].evidence_ids == (f"line_messages:{message_id}",)


def test_e2e_smoke_notes_only_retrieval(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="研究", sources="notes")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        note_id = _insert_note(storage)
    finally:
        storage.close()
    index_text(db_path)

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, retrieval_only=True),
    )

    assert report.ok is True
    assert report.counts.available_sources == ("notes",)
    assert report.query_results[0].evidence_ids == (f"notes:{note_id}",)


def test_e2e_smoke_mixed_sources_fake_model(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="研究", sources="photos,line,notes")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(storage, text="研究発表の写真")
        _insert_line_message(storage)
        _insert_note(storage)
    finally:
        storage.close()
    index_text(db_path)

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, fake_model=True),
    )

    assert report.ok is True
    assert set(report.counts.available_sources) == {"photos", "line", "notes"}
    assert report.query_results[0].retrieval_succeeded is True
    assert report.query_results[0].answer_succeeded is True
    assert report.query_results[0].answer_confidence == 0.5
    assert report.source_coverage.real_note_evidence_count >= 1


def test_e2e_smoke_line_notes_fake_model_accepts_weak_keyword_evidence(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_smoke_queries(
        config_dir,
        text="研究に関係するメモを探してください",
        sources="line,notes",
    )
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究の予定を確認した。")
        _insert_note(storage, body="研究に関係するメモ。")
    finally:
        storage.close()
    index_text(db_path)

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, fake_model=True),
    )

    assert report.ok is True
    assert report.query_results[0].answer_succeeded is True
    assert report.query_results[0].error_class is None
    assert report.query_results[0].answer_confidence == 0.4
    assert report.query_results[0].evidence_source_counts["notes"] >= 1
    assert set(report.query_results[0].used_sources) == {"line", "notes"}


def test_e2e_smoke_semantic_mode_reports_candidate_count(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="ProjectAlpha", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        message_id = _insert_line_message(storage, text="ProjectAlpha の準備")
    finally:
        storage.close()
    index_embeddings(db_path, HashEmbeddingModel())

    report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            semantic_model="hash",
            semantic_top_k=5,
        ),
    )
    result = report.query_results[0]

    assert report.ok is True
    assert result.semantic_enabled is True
    assert result.semantic_model == "hash"
    assert result.semantic_candidate_count >= 1
    assert result.evidence_ids == (f"line_messages:{message_id}",)


def test_e2e_real_model_preflights_and_uses_query_limit_timeout(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root, timeout_seconds=1, request_timeout_seconds=55),
    )
    (config_dir / "e2e_smoke_queries.local.yaml").write_text(
        "\n".join(
            [
                "queries:",
                "  first:",
                "    text: \"研究\"",
                "    sources: line",
                "  second:",
                "    text: \"予定\"",
                "    sources: line",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        message_id = _insert_line_message(storage)
    finally:
        storage.close()
    index_text(db_path)
    calls = []

    def fake_urlopen(request, data=None, *, timeout=None):
        calls.append((request.get_method(), request.full_url, timeout, request.data))
        if request.get_method() == "GET":
            assert request.data is None
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "served-leader.gguf"
        assert body["max_tokens"] == 33
        assert body["temperature"] == 0.2
        assert body["response_format"] == {"type": "json_object"}
        assert len(body["messages"]) == 2
        prompt = body["messages"][1]["content"]
        assert "Local evidence (compact" in prompt
        assert "synthetic private snippet" not in prompt
        payload = {
            "conclusion": "synthetic grounded answer",
            "evidence_references": [f"line_messages:{message_id}"],
            "confidence": 0.4,
            "unknowns": ["synthetic uncertainty"],
            "used_sources": ["line"],
        }
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [
                    {"message": {"content": "```json\n" + json.dumps(payload) + "\n```"}},
                ],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            real_model=True,
            query_limit=1,
            timeout_seconds=123,
            max_tokens=33,
            response_format_json=True,
        ),
    )

    assert report.ok is True
    assert len(report.query_results) == 1
    assert report.query_results[0].answer_succeeded is True
    assert report.query_results[0].answer_evidence_references == (f"line_messages:{message_id}",)
    assert report.query_results[0].used_sources == ("line",)
    assert report.query_results[0].model_id == "served-leader.gguf"
    assert report.query_results[0].endpoint_url == "http://127.0.0.1:8111/v1"
    assert report.query_results[0].timeout_seconds == 123
    assert report.query_results[0].max_tokens == 33
    assert report.query_results[0].prompt_chars > 0
    assert report.query_results[0].evidence_sent_count == 1
    assert report.query_results[0].json_extraction_succeeded is True
    assert report.query_results[0].json_extraction_strategy == "fenced_json"
    assert report.query_results[0].raw_response_chars > 0
    assert report.query_results[0].json_retry_used is False
    assert report.query_results[0].allowed_evidence_count == 1
    assert report.query_results[0].allowed_sources == ("line",)
    assert report.query_results[0].answer_conclusion is None
    assert report.query_results[0].answer_unknowns == ()
    assert report.query_results[0].answer_unknown_count == 1
    assert report.answer_audit.answer_succeeded_count == 1
    assert report.answer_audit.average_confidence == 0.4
    assert report.answer_audit.answer_source_counts == {"line": 1}
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert [call[2] for call in calls] == [1, 123]


def test_e2e_real_model_timeout_reports_safe_diagnostics(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    private_text = "研究 private timeout line"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text=private_text)
    finally:
        storage.close()
    index_text(db_path)

    def fake_urlopen(request, data=None, *, timeout=None):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        raise TimeoutError

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            real_model=True,
            query_limit=1,
            timeout_seconds=44,
            max_tokens=22,
        ),
    )
    output = json.dumps(report.to_dict(), ensure_ascii=False)
    result = report.query_results[0]

    assert report.ok is False
    assert result.error_class == "ModelRuntimeError"
    assert result.error_message == "model endpoint request timed out"
    assert result.timeout_seconds == 44
    assert result.max_tokens == 22
    assert result.prompt_chars > 0
    assert result.evidence_sent_count == 1
    assert private_text not in output
    assert str(tmp_path) not in output


def test_e2e_real_model_response_format_is_opt_in(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        message_id = _insert_line_message(storage)
    finally:
        storage.close()
    index_text(db_path)
    payloads = []

    def fake_urlopen(request, data=None, *, timeout=None):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        body = json.loads(request.data.decode("utf-8"))
        payloads.append(body)
        payload = {
            "conclusion": "synthetic grounded answer",
            "evidence_references": [f"line_messages:{message_id}"],
            "confidence": 0.4,
            "unknowns": ["synthetic uncertainty"],
            "used_sources": ["line"],
        }
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": json.dumps(payload)}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    no_format = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, real_model=True),
    )
    with_format = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            real_model=True,
            response_format_json=True,
        ),
    )

    assert no_format.ok is True
    assert with_format.ok is True
    assert "response_format" not in payloads[0]
    assert payloads[1]["response_format"] == {"type": "json_object"}


def test_e2e_real_model_unavailable_endpoint_returns_clean_report(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究 private snippet")
    finally:
        storage.close()
    index_text(db_path)

    def fake_urlopen(request, data=None, *, timeout=None):
        raise URLError("not ready")

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, real_model=True),
    )
    payload = json.dumps(report.to_dict(), ensure_ascii=False)

    assert report.ok is False
    assert report.query_results == ()
    assert any("leader endpoint preflight failed" in warning for warning in report.warnings)
    assert "private snippet" not in payload
    assert str(tmp_path) not in payload


def test_e2e_real_model_invalid_json_is_reported_without_private_leak(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    private_text = "研究 private line payload"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text=private_text)
    finally:
        storage.close()
    index_text(db_path)

    def fake_urlopen(request, data=None, *, timeout=None):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": "not json"}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, real_model=True),
    )
    output = json.dumps(report.to_dict(), ensure_ascii=False)

    assert report.ok is False
    assert report.query_results[0].error_class == "AnswerValidationError"
    assert "valid JSON object" in report.query_results[0].error_message
    assert report.query_results[0].json_extraction_succeeded is False
    assert report.query_results[0].json_extraction_strategy == "failed"
    assert report.query_results[0].raw_response_chars == len("not json")
    assert report.query_results[0].answer_validation_error_class == "AnswerValidationError"
    assert report.query_results[0].contains_json_like_object is False
    assert report.query_results[0].contains_think_tag is False
    assert report.query_results[0].contains_fenced_json is False
    assert report.query_results[0].extraction_attempts >= 1
    assert report.query_results[0].json_retry_used is True
    assert report.query_results[0].json_retry_succeeded is False
    assert report.query_results[0].allowed_evidence_count == 1
    assert report.query_results[0].allowed_sources == ("line",)
    assert report.query_results[0].raw_model_output_preview is None
    assert private_text not in output
    assert str(tmp_path) not in output


def test_e2e_real_model_unknown_evidence_id_is_rejected(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage)
    finally:
        storage.close()
    index_text(db_path)

    def fake_urlopen(request, data=None, *, timeout=None):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        payload = {
            "conclusion": "synthetic grounded answer",
            "evidence_references": ["notes:999"],
            "confidence": 0.4,
            "unknowns": ["synthetic uncertainty"],
            "used_sources": ["notes"],
        }
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": json.dumps(payload)}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, real_model=True),
    )

    assert report.ok is False
    assert report.query_results[0].error_class == "AnswerValidationError"
    assert "unknown_evidence_reference" in report.query_results[0].error_message
    assert report.answer_audit.answer_validation_error_count == 1
    assert report.answer_audit.unknown_evidence_reference_count == 1


def test_e2e_real_model_json_retry_success_is_reported(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        message_id = _insert_line_message(storage)
    finally:
        storage.close()
    index_text(db_path)
    post_count = 0

    def fake_urlopen(request, data=None, *, timeout=None):
        nonlocal post_count
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        post_count += 1
        if post_count == 1:
            return FakeHTTPResponse(
                {
                    "model": "served-leader.gguf",
                    "choices": [{"message": {"content": "not json"}}],
                },
            )
        payload = {
            "conclusion": "synthetic grounded answer",
            "evidence_references": [f"line_messages:{message_id}"],
            "confidence": 0.4,
            "unknowns": ["synthetic uncertainty"],
            "used_sources": ["line"],
        }
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": json.dumps(payload)}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, real_model=True, json_retry=1),
    )

    assert report.ok is True
    assert post_count == 2
    assert report.query_results[0].json_extraction_succeeded is True
    assert report.query_results[0].json_extraction_strategy == "retry_success"
    assert report.query_results[0].json_retry_used is True
    assert report.query_results[0].json_retry_succeeded is True
    assert report.query_results[0].answer_evidence_references == (f"line_messages:{message_id}",)
    assert report.answer_audit.retry_used_count == 1
    assert report.answer_audit.retry_success_count == 1
    assert report.answer_audit.answer_succeeded_count == 1


def test_e2e_show_model_output_requires_explicit_flag(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage)
    finally:
        storage.close()
    index_text(db_path)

    def fake_urlopen(request, data=None, *, timeout=None):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": "synthetic raw model output"}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    hidden = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, real_model=True),
    )
    shown = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            real_model=True,
            show_model_output=True,
        ),
    )

    assert hidden.query_results[0].raw_model_output_preview is None
    assert shown.query_results[0].raw_model_output_preview == "synthetic raw model output"
    assert any("raw model output preview requested" in warning for warning in shown.warnings)


def test_e2e_default_output_hides_answer_text(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究 private answer source")
    finally:
        storage.close()
    index_text(db_path)

    report = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, fake_model=True),
    )
    payload = report_to_json(report)
    human = format_e2e_smoke_report(report)

    assert report.ok is True
    assert report.query_results[0].answer_succeeded is True
    assert report.query_results[0].answer_conclusion is None
    assert "Retrieved local evidence is sufficient" not in payload
    assert "Retrieved local evidence is sufficient" not in human
    assert "private answer source" not in payload
    assert "private answer source" not in human


def test_e2e_show_answer_displays_answer_without_snippets(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究 raw evidence should stay hidden")
    finally:
        storage.close()
    index_text(db_path)

    report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            fake_model=True,
            show_answer=True,
        ),
    )
    output = format_e2e_smoke_report(report)

    assert report.ok is True
    assert report.query_results[0].answer_conclusion is not None
    assert "Retrieved local evidence is sufficient" in output
    assert "This answer was produced by a fake leader client." in output
    assert "raw evidence should stay hidden" not in output
    assert not report.query_results[0].safe_snippets
    assert any("answer display requested" in warning for warning in report.warnings)


def test_e2e_show_snippets_requires_explicit_flag_and_truncates(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    long_text = "研究 " + ("synthetic detail " * 30)
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text=long_text)
    finally:
        storage.close()
    index_text(db_path)

    hidden = run_e2e_smoke(
        E2ESmokeOptions(config_dir=config_dir, db_path=db_path, retrieval_only=True),
    )
    shown = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            show_snippets=True,
        ),
    )

    assert hidden.query_results[0].safe_snippets == ()
    assert shown.query_results[0].safe_snippets
    snippet = shown.query_results[0].safe_snippets[0]["snippet"]
    assert len(snippet) <= 160
    assert "synthetic detail" in snippet
    assert str(tmp_path) not in format_e2e_smoke_report(shown)
    assert any("snippet display requested" in warning for warning in shown.warnings)


def test_e2e_warns_when_photo_annotation_text_index_lags(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(storage, text="synthetic outdoor caption")
    finally:
        storage.close()

    report = run_e2e_smoke(E2ESmokeOptions(config_dir=config_dir, db_path=db_path, dry_run=True))

    assert any("photo annotation text index is behind" in warning for warning in report.warnings)


def test_e2e_smoke_query_id_selects_single_query(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    (config_dir / "e2e_smoke_queries.local.yaml").write_text(
        "\n".join(
            [
                "queries:",
                "  first:",
                "    text: \"該当なし\"",
                "    sources: line",
                "  second:",
                "    text: \"研究\"",
                "    sources: line",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        message_id = _insert_line_message(storage)
    finally:
        storage.close()
    index_text(db_path)

    report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="second",
        ),
    )

    assert report.ok is True
    assert len(report.query_results) == 1
    assert report.query_results[0].evidence_ids == (f"line_messages:{message_id}",)

    by_label = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="query_2",
        ),
    )
    assert by_label.ok is True
    assert by_label.query_results[0].evidence_ids == (f"line_messages:{message_id}",)


def test_e2e_smoke_require_source_notes_passes_when_note_evidence_found(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="研究に関係するメモを探してください", sources="line,notes")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        for index in range(20):
            _insert_line_message(storage, text=f"研究の予定を確認した {index}")
        note_id = _insert_note(storage, body="研究に関係するメモ。")
    finally:
        storage.close()
    index_text(db_path)

    report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            no_fallback=True,
            diagnose=True,
            require_sources=("notes",),
        ),
    )

    assert report.ok is True
    assert report.missing_required_sources == ()
    assert report.source_coverage.real_note_evidence_count >= 1
    assert f"notes:{note_id}" in report.query_results[0].evidence_ids
    assert report.query_results[0].source_stage_counts["notes"]["drop_reason"] is None


def test_e2e_smoke_require_source_notes_fails_when_missing(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="研究", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage)
    finally:
        storage.close()
    index_text(db_path)

    report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            no_fallback=True,
            require_sources=("notes",),
        ),
    )

    assert report.ok is False
    assert report.missing_required_sources == ("notes",)
    assert any("required sources" in warning for warning in report.warnings)


def test_e2e_smoke_cli_json_is_privacy_safe(capsys, temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="外出", sources="photos")
    db_path = tmp_path / "metadata.sqlite3"
    private_path = "/private/Alice-secret-vacation-photo.jpg"
    private_caption = "Alice secret vacation caption"
    storage = initialize_database(db_path)
    try:
        media_id = _insert_photo_annotation(
            storage,
            path=private_path,
            text=f"外出 {private_caption}",
        )
    finally:
        storage.close()

    exit_code = main(
        [
            "e2e",
            "smoke",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--retrieval-only",
            "--json",
        ],
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["query_results"][0]["evidence_ids"] == [f"media_items:{media_id}"]
    assert private_path not in output
    assert private_caption not in output
    assert str(tmp_path) not in output
    assert "外出" not in output


def test_e2e_smoke_cli_human_output_is_privacy_safe(capsys, temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_smoke_queries(config_dir, text="外出", sources="photos")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(
            storage,
            path="/private/birthday-secret-photo.jpg",
            text="外出 birthday secret caption",
        )
    finally:
        storage.close()

    exit_code = main(
        [
            "e2e",
            "smoke",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--retrieval-only",
        ],
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "E2E smoke passed" in output
    assert "birthday-secret-photo" not in output
    assert "birthday secret caption" not in output
    assert str(tmp_path) not in output


def test_e2e_smoke_retrieval_only_empty_db_exits_needs_attention(
    capsys,
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    db_path = tmp_path / "metadata.sqlite3"
    initialize_database(db_path).close()

    exit_code = main(
        [
            "e2e",
            "smoke",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--retrieval-only",
        ],
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "needs-attention" in output
    assert "No evidence-capable sources" in output

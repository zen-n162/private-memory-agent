import json

from private_memory_agent.cli import main
from private_memory_agent.evaluation import (
    GoldenEvalOptions,
    format_golden_eval_report,
    golden_report_to_json,
    load_golden_questions,
    run_golden_eval,
    write_golden_outputs,
)
from private_memory_agent.retrieval import index_text
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


def _write_golden_questions(config_dir, *, text="研究", sources="line,notes"):
    (config_dir / "golden_questions.local.yaml").write_text(
        "\n".join(
            [
                "questions:",
                "  - id: golden_research",
                "    category: research",
                f"    text: \"{text}\"",
                f"    sources: {sources}",
                "  - id: golden_missing",
                "    category: safety",
                "    text: \"該当なし\"",
                "    sources: notes",
            ],
        )
        + "\n",
        encoding="utf-8",
    )


def _insert_line_message(storage, *, text="研究の予定を確認した。"):
    return storage.line_messages.insert_message(
        source_item_id=None,
        conversation_id="golden-room",
        message_id="golden-line-1",
        sender_id="golden-speaker",
        sent_at="2026-05-24T09:00:00",
        message_type="text",
        body_text=text,
    )


def _insert_note(storage, *, title="研究メモ", body="研究の進捗をまとめた。"):
    return storage.notes.insert_note(
        source_item_id=None,
        note_id="golden-note-1",
        title=title,
        body_text=body,
        created_at_source="2026-05-24T10:00:00",
        updated_at_source="2026-05-24T10:00:00",
    )


def _leader_models_yaml(model_root):
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
            "  timeout_seconds: 1",
            "  request_timeout_seconds: 77",
            "  retries: 0",
        ],
    )


def test_golden_question_loader_uses_local_override(temp_config_factory):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir)

    questions = load_golden_questions(config_dir)

    assert [question.question_id for question in questions] == [
        "golden_research",
        "golden_missing",
    ]
    assert questions[0].sources == ("line", "notes")


def test_golden_local_questions_are_covered_by_gitignore():
    text = open(".gitignore", encoding="utf-8").read()

    assert "configs/*.local.yaml" in text


def test_golden_retrieval_only_is_privacy_safe(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    private_text = "研究 private golden evidence"
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        message_id = _insert_line_message(storage, text=private_text)
    finally:
        storage.close()
    index_text(db_path)

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
        ),
    )
    payload = golden_report_to_json(report)

    assert report.ok is True
    assert report.results[0].retrieval_succeeded is True
    assert report.results[0].evidence_ids == (f"line_messages:{message_id}",)
    assert report.results[0].answer_succeeded is False
    assert private_text not in payload
    assert str(tmp_path) not in payload


def test_golden_fake_model_hides_answer_by_default_and_shows_when_requested(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究 raw evidence stays hidden")
    finally:
        storage.close()
    index_text(db_path)

    hidden = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            fake_model=True,
            query_id="golden_research",
        ),
    )
    shown = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            fake_model=True,
            query_id="golden_research",
            show_answer=True,
        ),
    )
    hidden_payload = golden_report_to_json(hidden)
    shown_markdown = format_golden_eval_report(shown)

    assert hidden.ok is True
    assert hidden.results[0].answer_conclusion is None
    assert "Retrieved local evidence is sufficient" not in hidden_payload
    assert shown.results[0].answer_conclusion is not None
    assert "Retrieved local evidence is sufficient" in shown_markdown
    assert "raw evidence stays hidden" not in shown_markdown


def test_golden_show_snippets_requires_explicit_flag(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    long_text = "研究 " + ("synthetic detail " * 30)
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text=long_text)
    finally:
        storage.close()
    index_text(db_path)

    hidden = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
        ),
    )
    shown = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            show_snippets=True,
        ),
    )

    assert hidden.results[0].safe_snippets == ()
    assert shown.results[0].safe_snippets
    assert len(shown.results[0].safe_snippets[0]["snippet"]) <= 160


def test_golden_query_limit_and_query_id(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage)
        _insert_note(storage, body="該当なしのメモ。")
    finally:
        storage.close()
    index_text(db_path)

    limited = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_limit=1,
        ),
    )
    selected = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_missing",
        ),
    )

    assert len(limited.results) == 1
    assert limited.results[0].question_id == "golden_research"
    assert len(selected.results) == 1
    assert selected.results[0].question_id == "golden_missing"


def test_golden_markdown_and_jsonl_outputs_include_manual_placeholders(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage)
    finally:
        storage.close()
    index_text(db_path)
    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
        ),
    )
    markdown_path = tmp_path / "reports" / "golden.md"
    jsonl_path = tmp_path / "reports" / "golden.jsonl"

    write_golden_outputs(report, markdown_path=markdown_path, jsonl_path=jsonl_path)
    markdown = markdown_path.read_text(encoding="utf-8")
    jsonl_lines = jsonl_path.read_text(encoding="utf-8").splitlines()

    assert "answer_correctness" in markdown
    assert "evidence_relevance" in markdown
    assert json.loads(jsonl_lines[0])["record_type"] == "summary"
    assert json.loads(jsonl_lines[1])["record_type"] == "question"


def test_golden_real_model_uses_fake_http_client(monkeypatch, temp_config_factory, tmp_path):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
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
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "served-leader.gguf"
        assert body["max_tokens"] == 44
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

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            real_model=True,
            query_id="golden_research",
            timeout_seconds=123,
            max_tokens=44,
        ),
    )

    assert report.ok is True
    assert post_count == 1
    assert report.results[0].answer_succeeded is True
    assert report.results[0].used_sources == ("line",)
    assert report.results[0].evidence_reference_count == 1


def test_golden_eval_cli_json_and_markdown_are_privacy_safe(
    capsys,
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    private_text = "研究 private cli evidence"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text=private_text)
    finally:
        storage.close()
    index_text(db_path)
    output_path = tmp_path / "golden_eval.md"

    exit_code = main(
        [
            "eval",
            "golden",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--retrieval-only",
            "--query-limit",
            "1",
            "--json",
            "--output",
            str(output_path),
        ],
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["summary"]["question_count"] == 1
    assert output_path.exists()
    assert "answer_correctness" in output_path.read_text(encoding="utf-8")
    assert private_text not in output
    assert private_text not in output_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in output

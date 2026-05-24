import json

from private_memory_agent.cli import main
from private_memory_agent.agent import FakeRetrievalPlanner, RetrievalPlan
from private_memory_agent.evaluation import (
    GoldenEvalOptions,
    format_golden_eval_report,
    golden_report_to_json,
    load_golden_questions,
    run_golden_eval,
    write_golden_outputs,
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


def _insert_photo_annotation(storage, *, text="研究写真"):
    source_id = storage.source_items.insert_source(
        source_type="photo",
        source_uri="fixture://golden/photo.jpg",
        content_sha256="golden-photo-sha",
    )
    media_id = storage.media_items.insert_media(
        source_item_id=source_id,
        media_type="image",
        file_path="/private/golden-photo.jpg",
        sha256="golden-photo-sha",
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


def test_golden_question_loader_parses_source_constraints(temp_config_factory):
    config_dir = temp_config_factory()
    (config_dir / "golden_questions.local.yaml").write_text(
        "\n".join(
            [
                "questions:",
                "  - id: constrained",
                "    category: research",
                "    text: \"研究\"",
                "    expected_sources:",
                "      - line",
                "      - notes",
                "    required_sources:",
                "      - notes",
                "    preferred_sources:",
                "      - line",
                "    excluded_sources:",
                "      - photos",
                "    expected_keywords:",
                "      - 研究",
                "    optional_keywords:",
                "      - 準備",
                "    negative_keywords:",
                "      - unrelated",
                "    evaluation_focus:",
                "      - source_coverage",
                "    source_policy: strict",
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    question = load_golden_questions(config_dir)[0]

    assert question.expected_sources == ("line", "notes")
    assert question.required_sources == ("notes",)
    assert question.preferred_sources == ("line",)
    assert question.excluded_sources == ("photos",)
    assert question.expected_keywords == ("研究",)
    assert question.optional_keywords == ("準備",)
    assert question.negative_keywords == ("unrelated",)
    assert question.evaluation_focus == ("source_coverage",)
    assert question.source_policy == "strict"


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
            snippet_chars=40,
        ),
    )

    assert hidden.results[0].safe_snippets == ()
    assert shown.results[0].safe_snippets
    assert len(shown.results[0].safe_snippets[0]["snippet"]) <= 40


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


def test_golden_excluded_photos_are_not_returned_for_line_notes_question(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    (config_dir / "golden_questions.local.yaml").write_text(
        "\n".join(
            [
                "questions:",
                "  - id: line_notes_only",
                "    text: \"研究\"",
                "    expected_sources: [line, notes]",
                "    excluded_sources: [photos]",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(storage, text="研究写真")
        _insert_line_message(storage, text="研究のLINE")
        note_id = _insert_note(storage, body="研究のノート")
    finally:
        storage.close()
    index_text(db_path)

    report = run_golden_eval(
        GoldenEvalOptions(config_dir=config_dir, db_path=db_path, retrieval_only=True),
    )
    result = report.results[0]

    assert report.ok is True
    assert "photos" not in result.evidence_source_counts
    assert result.requested_sources == ("line", "notes")
    assert result.excluded_sources == ("photos",)
    assert result.excluded_source_violations == ()
    assert f"notes:{note_id}" in result.evidence_ids


def test_golden_source_balancing_returns_line_and_notes_when_available(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    (config_dir / "golden_questions.local.yaml").write_text(
        "\n".join(
            [
                "questions:",
                "  - id: balanced",
                "    text: \"研究\"",
                "    expected_sources: [line, notes]",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        for index in range(10):
            _insert_line_message(storage, text=f"研究のLINE {index}")
        _insert_note(storage, body="研究のノート")
    finally:
        storage.close()
    index_text(db_path)

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            limit=2,
        ),
    )

    assert report.ok is True
    assert report.results[0].evidence_source_counts["line"] >= 1
    assert report.results[0].evidence_source_counts["notes"] >= 1
    assert report.results[0].missing_expected_sources == ()


def test_golden_expected_keyword_boost_ranks_matching_evidence_first(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    (config_dir / "golden_questions.local.yaml").write_text(
        "\n".join(
            [
                "questions:",
                "  - id: keyword_rank",
                "    text: \"研究\"",
                "    sources: line",
                "    expected_keywords:",
                "      - QST",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        non_match_id = _insert_line_message(storage, text="研究の一般的な連絡")
        match_id = _insert_line_message(storage, text="研究 QST 面接 準備の連絡")
    finally:
        storage.close()
    index_text(db_path)

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            limit=2,
        ),
    )
    result = report.results[0]

    assert report.ok is True
    assert result.evidence_ids[0] == f"line_messages:{match_id}"
    assert f"line_messages:{non_match_id}" in result.evidence_ids
    assert result.expected_keywords_hit_count == 1
    assert result.expected_keyword_hit_evidence_count == 1
    assert result.evidence_keyword_hit_counts[f"line_messages:{match_id}"] == 1
    assert result.evidence_keyword_hit_counts[f"line_messages:{non_match_id}"] == 0
    assert result.relevance_score > 0.7


def test_golden_missing_expected_keywords_are_reported_and_strict_policy_fails(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究の一般的な連絡")
    finally:
        storage.close()
    index_text(db_path)

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            expected_keywords=("QST",),
            keyword_policy="strict",
        ),
    )

    assert report.ok is False
    assert report.results[0].retrieval_succeeded is True
    assert report.results[0].missing_expected_keywords == ("QST",)
    assert report.results[0].retrieval_passed_keyword_policy is False
    assert report.results[0].relevance_score < 0.8


def test_golden_negative_keyword_penalty_is_reported(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    (config_dir / "golden_questions.local.yaml").write_text(
        "\n".join(
            [
                "questions:",
                "  - id: negative_keyword",
                "    text: \"研究\"",
                "    sources: line",
                "    expected_keywords: [研究]",
                "    negative_keywords: [unrelated]",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究 unrelated diagnostic")
    finally:
        storage.close()
    index_text(db_path)

    soft = run_golden_eval(
        GoldenEvalOptions(config_dir=config_dir, db_path=db_path, retrieval_only=True),
    )
    strict = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            keyword_policy="strict",
        ),
    )

    assert soft.results[0].negative_keyword_hit_count == 1
    assert soft.results[0].retrieval_passed_keyword_policy is True
    assert strict.ok is False
    assert strict.results[0].retrieval_passed_keyword_policy is False


def test_golden_strict_source_policy_fails_when_required_source_missing(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    (config_dir / "golden_questions.local.yaml").write_text(
        "\n".join(
            [
                "questions:",
                "  - id: strict_missing",
                "    text: \"研究\"",
                "    expected_sources: [line]",
                "    required_sources: [notes]",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究のLINE")
    finally:
        storage.close()
    index_text(db_path)

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            source_policy="strict",
        ),
    )

    assert report.ok is False
    assert report.results[0].retrieval_succeeded is True
    assert report.results[0].missing_required_sources == ("notes",)
    assert report.results[0].retrieval_passed_source_policy is False


def test_golden_soft_source_policy_reports_missing_required_without_failing(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    (config_dir / "golden_questions.local.yaml").write_text(
        "\n".join(
            [
                "questions:",
                "  - id: soft_missing",
                "    text: \"研究\"",
                "    expected_sources: [line]",
                "    required_sources: [notes]",
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究のLINE")
    finally:
        storage.close()
    index_text(db_path)

    report = run_golden_eval(
        GoldenEvalOptions(config_dir=config_dir, db_path=db_path, retrieval_only=True),
    )

    assert report.ok is True
    assert report.results[0].missing_required_sources == ("notes",)
    assert report.results[0].source_policy == "soft"
    assert report.results[0].retrieval_passed_source_policy is True


def test_golden_cli_source_constraints_filter_and_report(capsys, temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(storage, text="研究写真")
        _insert_line_message(storage, text="研究のLINE")
        _insert_note(storage, body="研究のノート")
    finally:
        storage.close()
    index_text(db_path)

    exit_code = main(
        [
            "eval",
            "golden",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--retrieval-only",
            "--query-id",
            "golden_research",
            "--require-source",
            "line",
            "--require-source",
            "notes",
            "--exclude-source",
            "photos",
            "--expected-keyword",
            "研究",
            "--negative-keyword",
            "unrelated",
            "--json",
        ],
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    result = payload["results"][0]
    assert result["requested_sources"] == ["line", "notes"]
    assert result["required_sources"] == ["line", "notes"]
    assert result["excluded_sources"] == ["photos"]
    assert "photos" not in result["evidence_source_counts"]
    assert result["expected_keywords_count"] == 1
    assert result["expected_keywords_hit_count"] == 1
    assert result["negative_keywords_count"] == 1
    assert result["relevance_score"] > 0


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
    assert "source_policy_passed" in markdown
    assert "evidence_relevance_score" in markdown
    assert "expected_keywords_hit_count" in markdown
    assert "missing_expected_keywords" in markdown
    assert "source_mismatch_notes" in markdown
    assert "irrelevant_evidence_notes" in markdown
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


def test_golden_show_snippets_lists_matched_keywords_when_explicit(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究 QST の準備をした。")
    finally:
        storage.close()
    index_text(db_path)

    hidden = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            expected_keywords=("QST",),
        ),
    )
    shown = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            expected_keywords=("QST",),
            show_snippets=True,
            snippet_chars=32,
        ),
    )

    assert hidden.results[0].safe_snippets == ()
    assert shown.results[0].safe_snippets[0]["matched_keywords"] == "QST"
    assert len(shown.results[0].safe_snippets[0]["snippet"]) <= 32


def test_golden_leader_plan_uses_plan_derived_query_without_showing_plan(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="generic question", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        message_id = _insert_line_message(storage, text="ProjectAlpha specific evidence")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find project evidence",
            specific_concepts=("ProjectAlpha",),
            generic_concepts=("generic",),
            source_preferences=("line",),
            source_constraints=("line",),
            retrieval_queries=("ProjectAlpha",),
            evidence_acceptance_criteria=("contains ProjectAlpha",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
        ),
    )
    payload = golden_report_to_json(report)
    result = report.results[0]

    assert report.ok is True
    assert result.evidence_ids == (f"line_messages:{message_id}",)
    assert result.plan_metadata.plan_created is True
    assert result.plan_metadata.specific_concept_count == 1
    assert result.plan_metadata.plan is None
    assert "ProjectAlpha specific evidence" not in payload


def test_golden_show_plan_is_explicit(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="generic question", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="ProjectAlpha specific evidence")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find project evidence",
            specific_concepts=("ProjectAlpha",),
            retrieval_queries=("ProjectAlpha",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            show_plan=True,
        ),
    )

    assert report.results[0].plan_metadata.plan is not None
    assert report.results[0].plan_metadata.plan["specific_concepts"] == ["ProjectAlpha"]


def test_golden_leader_rerank_demotes_generic_evidence(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        generic_id = _insert_line_message(storage, text="研究の一般的な話")
        specific_id = _insert_line_message(storage, text="ProjectAlpha の準備")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find project evidence",
            specific_concepts=("ProjectAlpha",),
            generic_concepts=("研究",),
            retrieval_queries=("研究",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            leader_rerank=True,
            show_relevance=True,
            limit=2,
        ),
    )
    result = report.results[0]

    assert result.evidence_ids[0] == f"line_messages:{specific_id}"
    assert f"line_messages:{generic_id}" in result.evidence_ids
    assert result.leader_rerank_used is True
    assert result.relevance_judged is True
    assert result.plan_relevance_specificity_counts["specific"] == 1
    assert result.plan_relevance_specificity_counts["generic"] == 1
    assert result.relevance_scores


def test_golden_generic_only_candidates_are_not_usable_evidence(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究の一般的な予定")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find specific project preparation",
            main_entities=("ProjectAlpha",),
            specific_concepts=("ProjectAlpha",),
            generic_concepts=("研究", "予定"),
            retrieval_queries=("研究",),
            evidence_acceptance_criteria=("contains ProjectAlpha",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            leader_rerank=True,
            show_relevance=True,
            limit=1,
        ),
    )
    result = report.results[0]

    assert report.ok is True
    assert result.candidate_retrieval_succeeded is True
    assert result.usable_evidence_succeeded is False
    assert result.usable_evidence_count == 0
    assert result.should_use_evidence_count == 0
    assert result.relevance_policy_passed is True
    assert result.final_relevance_score < 0.6
    assert result.relevance_score == result.final_relevance_score
    assert result.insufficient_evidence_reason == (
        "candidate evidence was found, but relevance judge found no usable evidence"
    )


def test_golden_strict_relevance_policy_fails_when_no_usable_evidence(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究の一般的な予定")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find specific project preparation",
            specific_concepts=("ProjectAlpha",),
            generic_concepts=("研究",),
            retrieval_queries=("研究",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            leader_rerank=True,
            minimum_relevance_score=0.6,
            require_usable_evidence=True,
            relevance_policy="strict",
            limit=1,
        ),
    )
    result = report.results[0]

    assert report.ok is False
    assert result.candidate_retrieval_succeeded is True
    assert result.usable_evidence_succeeded is False
    assert result.relevance_policy_passed is False
    assert result.passed is False


def test_golden_retrieval_repair_reports_improvement_when_specific_evidence_found(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究の一般的な予定")
        specific_id = _insert_line_message(storage, text="ProjectAlpha の具体的な準備")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find specific project preparation",
            main_entities=("ProjectAlpha",),
            specific_concepts=(),
            generic_concepts=("研究",),
            retrieval_queries=("研究",),
            evidence_acceptance_criteria=("contains ProjectAlpha",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            leader_rerank=True,
            retrieval_repair=1,
            limit=1,
        ),
    )
    result = report.results[0]

    assert result.repair_attempted is True
    assert result.retrieval_repair_count == 1
    assert result.repair_improved is True
    assert result.pre_repair_usable_evidence_count == 0
    assert result.post_repair_usable_evidence_count == 1
    assert result.usable_evidence_succeeded is True
    assert result.evidence_ids == (f"line_messages:{specific_id}",)
    assert result.repair_queries_created_count >= 1


def test_golden_repair_query_diagnostics_prefer_specific_plan_terms(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究の一般的な予定")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find specific project preparation",
            main_entities=("ProjectAlpha",),
            specific_concepts=("SpecificBeta",),
            generic_concepts=("研究", "予定"),
            retrieval_queries=("研究",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            leader_rerank=True,
            retrieval_repair=1,
            limit=1,
        ),
    )
    result = report.results[0]

    assert result.repair_attempted is True
    assert result.repair_used_specific_concepts is True
    assert result.repair_used_main_entities is True
    assert result.repair_specific_query_count == 2
    assert result.repair_generic_query_count == 0


def test_golden_semantic_retrieval_reports_candidates_and_can_improve_repair(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究の一般的な予定")
        specific_id = _insert_line_message(storage, text="ProjectAlpha の具体的な準備")
    finally:
        storage.close()
    model = HashEmbeddingModel()
    index_embeddings(db_path, model)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find project evidence",
            main_entities=("ProjectAlpha",),
            generic_concepts=("研究",),
            retrieval_queries=("研究",),
            evidence_acceptance_criteria=("contains ProjectAlpha",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            leader_rerank=True,
            retrieval_repair=1,
            semantic_model="hash",
            semantic_top_k=5,
            semantic_weight=1.2,
            reranker="fake",
            rerank_top_k=2,
            limit=1,
        ),
    )
    result = report.results[0]

    assert result.semantic_enabled is True
    assert result.semantic_model == "hash"
    assert result.semantic_embedding_model_id == "hash-embedding-v1"
    assert result.semantic_candidate_count >= 1
    assert result.reranker == "fake"
    assert result.reranker_model_id == "fake-reranker-v1"
    assert result.reranked_candidate_count >= 1
    assert result.repair_improved is True
    assert result.usable_evidence_succeeded is True
    assert result.evidence_ids == (f"line_messages:{specific_id}",)


def test_golden_retrieval_repair_reports_no_improvement_for_generic_only(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究の一般的な予定")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find specific project preparation",
            main_entities=("ProjectAlpha",),
            specific_concepts=(),
            generic_concepts=("研究",),
            retrieval_queries=("研究",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            leader_rerank=True,
            retrieval_repair=1,
            limit=1,
        ),
    )
    result = report.results[0]

    assert result.repair_attempted is True
    assert result.repair_improved is False
    assert result.pre_repair_usable_evidence_count == 0
    assert result.post_repair_usable_evidence_count == 0
    assert result.usable_evidence_succeeded is False


def test_golden_plan_source_preferences_can_affect_ranking(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line,notes")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="研究の連絡")
        note_id = _insert_note(storage, body="研究のメモ")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="prefer notes",
            generic_concepts=("研究",),
            source_preferences=("notes",),
            source_constraints=("line", "notes"),
            retrieval_queries=("研究",),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            limit=1,
        ),
    )

    assert report.results[0].evidence_ids == (f"notes:{note_id}",)


def test_golden_retrieval_repair_runs_second_query_when_first_is_weak(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text="ProjectAlpha の準備")
    finally:
        storage.close()
    index_text(db_path)
    planner = FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find project evidence",
            main_entities=("ProjectAlpha",),
            generic_concepts=("研究",),
            retrieval_queries=("研究", "ProjectAlpha"),
        ),
    )

    report = run_golden_eval(
        GoldenEvalOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            query_id="golden_research",
            retrieval_planner=planner,
            leader_rerank=True,
            retrieval_repair=1,
        ),
    )
    result = report.results[0]

    assert result.retrieval_repair_count == 1
    assert result.retrieval_succeeded is True
    assert result.evidence_source_counts["line"] == 1


def test_golden_cli_leader_plan_flags_are_privacy_safe(capsys, temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    _write_golden_questions(config_dir, text="研究", sources="line")
    db_path = tmp_path / "golden.sqlite3"
    private_text = "研究 private planner evidence"
    storage = initialize_database(db_path)
    try:
        _insert_line_message(storage, text=private_text)
    finally:
        storage.close()
    index_text(db_path)

    exit_code = main(
        [
            "eval",
            "golden",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--retrieval-only",
            "--query-id",
            "golden_research",
            "--expected-keyword",
            "研究",
            "--leader-rerank",
            "--show-relevance",
            "--json",
        ],
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["results"][0]["leader_rerank_used"] is False
    assert payload["results"][0]["plan"]["plan_created"] is False
    assert private_text not in output

import json

from private_memory_agent.agent import FakeRetrievalPlanner, RetrievalPlan
from private_memory_agent.cli import main
from private_memory_agent.evaluation import (
    SemanticCompareOptions,
    semantic_compare_report_to_json,
    run_semantic_compare,
)
from private_memory_agent.retrieval import HashEmbeddingModel, index_embeddings, index_text
from private_memory_agent.storage import initialize_database


def _write_golden_question(config_dir, *, text="ProjectAlpha"):
    (config_dir / "golden_questions.local.yaml").write_text(
        "\n".join(
            [
                "questions:",
                "  - id: q_specific",
                "    category: research",
                f"    text: \"{text}\"",
                "    sources: line",
            ],
        )
        + "\n",
        encoding="utf-8",
    )


def _seed_compare_db(db_path):
    storage = initialize_database(db_path)
    try:
        generic_id = storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="compare-room",
            message_id="generic",
            sender_id="speaker",
            sent_at="2026-05-24T09:00:00",
            message_type="text",
            body_text="研究の一般的な予定を確認した。",
        )
        specific_id = storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="compare-room",
            message_id="specific",
            sender_id="speaker",
            sent_at="2026-05-24T10:00:00",
            message_type="text",
            body_text="ProjectAlpha の具体的な準備を確認した。",
        )
    finally:
        storage.close()
    index_text(db_path)
    index_embeddings(db_path, HashEmbeddingModel())
    return {"generic_id": generic_id, "specific_id": specific_id}


def _planner():
    return FakeRetrievalPlanner(
        RetrievalPlan(
            intent="find specific project evidence",
            main_entities=("ProjectAlpha",),
            specific_concepts=("ProjectAlpha",),
            generic_concepts=("研究", "予定"),
            retrieval_queries=("ProjectAlpha",),
            evidence_acceptance_criteria=("contains ProjectAlpha",),
        ),
    )


def test_semantic_compare_reports_all_configs_and_recommends_judged_quality(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_question(config_dir)
    db_path = tmp_path / "compare.sqlite3"
    _seed_compare_db(db_path)

    report = run_semantic_compare(
        SemanticCompareOptions(
            config_dir=config_dir,
            db_path=db_path,
            query_id="q_specific",
            real_semantic_model="hash",
            real_reranker="fake",
            embedding_device="cpu",
            retrieval_planner=_planner(),
            show_relevance=True,
        ),
    )

    config_ids = {result.config_id for result in report.config_results}
    assert {
        "text_only",
        "hash_semantic",
        "ruri_v3_310m",
        "ruri_v3_310m_plus_reranker",
        "leader_plan_ruri",
        "leader_plan_ruri_plus_reranker",
    } <= config_ids
    assert report.recommended_config_id in {
        "leader_plan_ruri",
        "leader_plan_ruri_plus_reranker",
    }
    assert report.embedding_device_status.selected_device == "cpu"
    recommended = next(
        item for item in report.config_results if item.config_id == report.recommended_config_id
    )
    assert recommended.quality_judged is True
    assert recommended.strict_passed_count == 1
    assert recommended.usable_evidence_count >= 1


def test_semantic_compare_warns_when_reranker_only_is_not_quality_judged(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_golden_question(config_dir)
    db_path = tmp_path / "compare.sqlite3"
    _seed_compare_db(db_path)

    report = run_semantic_compare(
        SemanticCompareOptions(
            config_dir=config_dir,
            db_path=db_path,
            query_id="q_specific",
            real_semantic_model="hash",
            real_reranker="fake",
            embedding_device="cpu",
            retrieval_planner=_planner(),
        ),
    )

    reranker_only = next(
        item for item in report.config_results if item.config_id == "ruri_v3_310m_plus_reranker"
    )
    assert reranker_only.quality_judged is False
    assert reranker_only.strict_passed_count == 0
    assert any("did not run relevance judging" in warning for warning in reranker_only.warnings)


def test_semantic_compare_json_is_privacy_safe(temp_config_factory, tmp_path):
    config_dir = temp_config_factory()
    private_text = "ProjectAlpha private evidence body"
    _write_golden_question(config_dir, text="ProjectAlpha")
    db_path = tmp_path / "compare.sqlite3"
    storage = initialize_database(db_path)
    try:
        storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="compare-room",
            message_id="private",
            sender_id="speaker",
            sent_at="2026-05-24T10:00:00",
            message_type="text",
            body_text=private_text,
        )
    finally:
        storage.close()
    index_embeddings(db_path, HashEmbeddingModel())

    report = run_semantic_compare(
        SemanticCompareOptions(
            config_dir=config_dir,
            db_path=db_path,
            query_id="q_specific",
            real_semantic_model="hash",
            real_reranker="fake",
            embedding_device="cpu",
            retrieval_planner=_planner(),
        ),
    )
    payload = semantic_compare_report_to_json(report)

    assert private_text not in payload
    assert str(tmp_path) not in payload
    assert "quality_judged" in payload


def test_cli_semantic_compare_outputs_json_without_private_text(
    temp_config_factory,
    capsys,
    tmp_path,
):
    config_dir = temp_config_factory()
    private_text = "ProjectAlpha private CLI evidence"
    _write_golden_question(config_dir, text="ProjectAlpha")
    db_path = tmp_path / "compare.sqlite3"
    storage = initialize_database(db_path)
    try:
        storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="compare-room",
            message_id="private-cli",
            sender_id="speaker",
            sent_at="2026-05-24T10:00:00",
            message_type="text",
            body_text=private_text,
        )
    finally:
        storage.close()
    index_embeddings(db_path, HashEmbeddingModel())

    exit_code = main(
        [
            "eval",
            "semantic-compare",
            "--config-dir",
            str(config_dir),
            "--db",
            str(db_path),
            "--query-id",
            "q_specific",
            "--real-semantic-model",
            "hash",
            "--real-reranker",
            "fake",
            "--embedding-device",
            "cpu",
            "--json",
        ],
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code in {0, 1}
    assert payload["config_results"]
    assert private_text not in output
    assert str(tmp_path) not in output

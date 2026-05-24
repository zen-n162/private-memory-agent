import json

from private_memory_agent.cli import main
from private_memory_agent.evaluation import (
    EVAL_METRIC_NAMES,
    create_synthetic_eval_data,
    default_eval_cases,
    run_synthetic_eval,
)


def test_synthetic_eval_data_generator_creates_required_cases(tmp_path):
    db_path = tmp_path / "eval.sqlite3"
    data = create_synthetic_eval_data(db_path, run_id="unit")
    cases = default_eval_cases(data)

    assert db_path.exists()
    assert len(cases) == 7
    assert {case.category for case in cases} == {
        "date_questions",
        "person_questions_with_uncertainty",
        "place_questions",
        "insufficient_evidence",
        "prompt_injection_in_notes",
        "line_joke_vs_fact",
        "privacy_redaction",
    }
    assert data.evidence_ids_by_key["date"].startswith("line_messages:")
    assert data.evidence_ids_by_key["place"].startswith("media_items:")


def test_synthetic_eval_passes_with_fake_client_and_redacted_output(tmp_path):
    result = run_synthetic_eval(db_path=tmp_path / "eval.sqlite3", run_id="unit")
    payload = result.to_dict()
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert result.passed is True
    assert set(result.metrics) == set(EVAL_METRIC_NAMES)
    assert all(score == 1.0 for score in result.metrics.values())
    assert payload["case_count"] == 7
    assert all(case["passed"] for case in payload["cases"])
    assert "Synthetic Private Name" not in serialized
    assert "Hidden synthetic diary detail" not in serialized
    assert "EXFILTRATE_SYNTHETIC_SECRET" not in serialized


def test_synthetic_eval_includes_expected_safety_cases(tmp_path):
    result = run_synthetic_eval(db_path=tmp_path / "eval.sqlite3", run_id="unit")
    cases = {case.case_id: case for case in result.cases}

    insufficient = cases["insufficient_evidence"]
    injection = cases["prompt_injection_note"]
    joke_vs_fact = cases["line_joke_vs_fact"]
    privacy = cases["privacy_redaction"]

    assert insufficient.metrics["insufficient_evidence_handling"] is True
    assert insufficient.retrieved_evidence_ids == ()
    assert injection.metrics["groundedness_check"] is True
    assert injection.metrics["privacy_leak_check"] is True
    assert joke_vs_fact.metrics["evidence_recall_proxy"] is True
    assert set(joke_vs_fact.forbidden_evidence_ids).isdisjoint(
        joke_vs_fact.retrieved_evidence_ids,
    )
    assert privacy.privacy_leak_count == 0


def test_eval_run_cli_outputs_metrics_without_private_markers(capsys, tmp_path):
    db_path = tmp_path / "eval.sqlite3"

    exit_code = main(["eval", "run", "--db", str(db_path), "--summary"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["case_count"] == 7
    assert payload["failed_cases"] == []
    assert set(payload["metrics"]) == set(EVAL_METRIC_NAMES)
    assert "Synthetic Private Name" not in output
    assert "Hidden synthetic diary detail" not in output

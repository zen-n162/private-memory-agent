"""Synthetic local evaluation harness."""

from private_memory_agent.evaluation.harness import (
    EVAL_METRIC_NAMES,
    EvalCase,
    EvalCaseResult,
    EvalRunResult,
    SyntheticEvalData,
    create_synthetic_eval_data,
    default_eval_cases,
    run_synthetic_eval,
)
from private_memory_agent.evaluation.golden import (
    GoldenEvalOptions,
    GoldenEvalReport,
    GoldenQuestion,
    format_golden_eval_report,
    load_golden_questions,
    report_to_json as golden_report_to_json,
    report_to_jsonl as golden_report_to_jsonl,
    run_golden_eval,
    write_golden_outputs,
)

__all__ = [
    "EVAL_METRIC_NAMES",
    "EvalCase",
    "EvalCaseResult",
    "EvalRunResult",
    "SyntheticEvalData",
    "create_synthetic_eval_data",
    "default_eval_cases",
    "format_golden_eval_report",
    "GoldenEvalOptions",
    "GoldenEvalReport",
    "GoldenQuestion",
    "golden_report_to_json",
    "golden_report_to_jsonl",
    "load_golden_questions",
    "run_golden_eval",
    "run_synthetic_eval",
    "write_golden_outputs",
]

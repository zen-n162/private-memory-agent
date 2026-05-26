"""Command line interface for Private Memory Agent."""

from __future__ import annotations

import argparse
import ipaddress
import json
from collections.abc import Sequence
from pathlib import Path

from private_memory_agent import __version__
from private_memory_agent.agent import (
    AnswerValidationError,
    FakeLeaderChatModelClient,
    LeaderAgent,
    PrivacyGuard,
    PrivacyGuardPolicy,
    run_query_flow,
)
from private_memory_agent.annotation import (
    DEFAULT_PHOTO_ANNOTATION_PROMPT,
    PhotoPreprocessOptions,
    annotate_photos,
    annotate_text,
    build_annotation_stats_report,
    list_failed_photo_annotations,
)
from private_memory_agent.config import load_config
from private_memory_agent.db_diagnostics import (
    format_database_schema_report,
    inspect_database_schema,
    report_to_json as diagnostics_report_to_json,
    run_retrieval_audit,
)
from private_memory_agent.doctor import format_doctor_result, run_doctor
from private_memory_agent.e2e import (
    DEFAULT_E2E_DB_PATH,
    DEFAULT_E2E_LEADER_MODEL_KEY,
    DEFAULT_E2E_MAX_EVIDENCE_CHARS,
    DEFAULT_E2E_MAX_EVIDENCE_ITEMS,
    DEFAULT_E2E_QUERY_LIMIT,
    DEFAULT_E2E_REAL_MODEL_MAX_TOKENS,
    E2ESmokeOptions,
    format_e2e_smoke_report,
    load_e2e_smoke_queries,
    report_to_json as e2e_report_to_json,
    run_e2e_smoke,
)
from private_memory_agent.entities import (
    add_entity_alias,
    list_entities,
    resolve_text_annotation_entities,
)
from private_memory_agent.evaluation import (
    GoldenEvalOptions,
    SemanticCompareOptions,
    format_golden_eval_report,
    format_semantic_compare_report,
    golden_report_to_json,
    run_golden_eval,
    run_semantic_compare,
    run_synthetic_eval,
    semantic_compare_report_to_json,
    write_golden_outputs,
)
from private_memory_agent.ingestion import ingest_line_exports, ingest_notes, ingest_photos
from private_memory_agent.media_timestamps import (
    audit_media_timestamps,
    backfill_media_timestamps,
    format_timestamp_audit,
    format_timestamp_backfill,
)
from private_memory_agent.models import ModelRegistry
from private_memory_agent.retrieval import (
    FakeEmbeddingModel,
    HashEmbeddingModel,
    QdrantVectorStore,
    RERANKER_MODEL_CHOICES,
    RetrievalFilters,
    RetrievalService,
    SEMANTIC_MODEL_CHOICES,
    SentenceTransformersEmbeddingModel,
    build_evidence_reranker,
    build_semantic_embedding_model,
    index_embeddings,
    index_text,
    normalize_semantic_model_name,
    search_text,
    semantic_search,
)
from private_memory_agent.runtime import (
    ChatTextUnderstandingClient,
    FakeTextUnderstandingClient,
    FakeVisionModelClient,
    GPUInfo,
    ModelPingResult,
    ModelRuntimeError,
    OpenAICompatibleHTTPClient,
    RUNTIME_PROFILES,
    build_runtime_plan,
    endpoint_from_model_spec,
    ping_configured_model_endpoints,
    ping_model_endpoint,
    preflight_vision_endpoint,
    run_chat_smoke_test,
    run_json_smoke_test,
    run_vision_smoke_test,
)
from private_memory_agent.temporal import answer_temporal_event_query
from private_memory_agent.timeline import build_events, list_events

DEFAULT_VISION_ANNOTATION_TIMEOUT_SECONDS = 300.0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level `pma` argument parser."""

    parser = argparse.ArgumentParser(
        prog="pma",
        description="Private Memory Agent local-first command line.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"private-memory-agent {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser("config", help="Inspect resolved configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_show_parser = config_subparsers.add_parser(
        "show",
        help="Show resolved non-secret configuration.",
    )
    _add_config_dir_argument(config_show_parser)
    config_show_parser.set_defaults(func=_config_show_command)

    stats_parser = subparsers.add_parser(
        "stats",
        help="Show privacy-safe local processing statistics.",
    )
    _add_config_dir_argument(stats_parser)
    stats_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_E2E_DB_PATH,
        help="SQLite database path to inspect.",
    )
    stats_parser.set_defaults(func=_stats_command)

    media_parser = subparsers.add_parser(
        "media",
        help="Inspect and repair privacy-safe local media metadata.",
    )
    media_subparsers = media_parser.add_subparsers(dest="media_command")
    media_timestamps_parser = media_subparsers.add_parser(
        "timestamps",
        help="Audit or backfill media capture timestamps.",
    )
    media_timestamps_subparsers = media_timestamps_parser.add_subparsers(
        dest="media_timestamps_command",
    )
    media_timestamps_audit_parser = media_timestamps_subparsers.add_parser(
        "audit",
        help="Audit media timestamp coverage without printing paths.",
    )
    _add_config_dir_argument(media_timestamps_audit_parser)
    media_timestamps_audit_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_E2E_DB_PATH,
        help="SQLite database path to inspect.",
    )
    media_timestamps_audit_parser.add_argument(
        "--method",
        choices=("auto", "exiftool", "pillow"),
        default="auto",
        help="Timestamp extraction method used for extractability counts.",
    )
    media_timestamps_audit_parser.add_argument(
        "--fallback",
        choices=("none", "file-mtime"),
        default="none",
        help="Optional low-confidence fallback used for extractability counts.",
    )
    media_timestamps_audit_parser.add_argument(
        "--month-histogram",
        action="store_true",
        help="Include month counts from stored taken_at values.",
    )
    media_timestamps_audit_parser.add_argument(
        "--extract-limit",
        type=int,
        default=200,
        help=(
            "Maximum existing supported files to inspect for extractable timestamp "
            "counts. Use 0 for all files."
        ),
    )
    media_timestamps_audit_parser.add_argument(
        "--json",
        action="store_true",
        help="Print audit as JSON.",
    )
    media_timestamps_audit_parser.set_defaults(func=_media_timestamps_audit_command)

    media_timestamps_backfill_parser = media_timestamps_subparsers.add_parser(
        "backfill",
        help="Backfill missing media capture timestamps from local files.",
    )
    _add_config_dir_argument(media_timestamps_backfill_parser)
    media_timestamps_backfill_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_E2E_DB_PATH,
        help="SQLite database path to update.",
    )
    dry_run_group = media_timestamps_backfill_parser.add_mutually_exclusive_group()
    dry_run_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Preview updates without writing. This is the default.",
    )
    dry_run_group.add_argument(
        "--write",
        dest="dry_run",
        action="store_false",
        help="Write backfilled timestamps to SQLite. Source files are still read-only.",
    )
    media_timestamps_backfill_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum media rows to process.",
    )
    media_timestamps_backfill_parser.add_argument(
        "--source",
        choices=("photos",),
        default="photos",
        help="Media source to backfill.",
    )
    media_timestamps_backfill_parser.add_argument(
        "--method",
        choices=("auto", "exiftool", "pillow"),
        default="auto",
        help="Timestamp extraction method.",
    )
    media_timestamps_backfill_parser.add_argument(
        "--fallback",
        choices=("none", "file-mtime"),
        default="none",
        help="Optional low-confidence fallback. Use file-mtime only when acceptable.",
    )
    media_timestamps_backfill_parser.add_argument(
        "--min-confidence",
        choices=("high", "medium", "low"),
        default="high",
        help="Minimum timestamp confidence to accept.",
    )
    media_timestamps_backfill_parser.add_argument(
        "--only-missing",
        action="store_true",
        default=True,
        help="Only fill rows where taken_at is missing. This is the default.",
    )
    media_timestamps_backfill_parser.add_argument(
        "--overwrite-existing",
        dest="only_missing",
        action="store_false",
        help="Allow updating rows that already have taken_at.",
    )
    media_timestamps_backfill_parser.add_argument(
        "--show-errors",
        action="store_true",
        help="Show privacy-safe error examples by media_item_id.",
    )
    media_timestamps_backfill_parser.add_argument(
        "--json",
        action="store_true",
        help="Print backfill report as JSON.",
    )
    media_timestamps_backfill_parser.set_defaults(func=_media_timestamps_backfill_command)

    db_parser = subparsers.add_parser(
        "db",
        help="Inspect privacy-safe SQLite database metadata.",
    )
    db_subparsers = db_parser.add_subparsers(dest="db_command")
    db_schema_parser = db_subparsers.add_parser(
        "schema",
        help="Show SQLite schema metadata without row payloads.",
    )
    _add_config_dir_argument(db_schema_parser)
    db_schema_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_E2E_DB_PATH,
        help="SQLite database path to inspect.",
    )
    db_schema_parser.add_argument(
        "--json",
        action="store_true",
        help="Print schema metadata as JSON.",
    )
    db_schema_parser.set_defaults(func=_db_schema_command)

    e2e_parser = subparsers.add_parser(
        "e2e",
        help="Run privacy-safe end-to-end smoke workflows.",
    )
    e2e_subparsers = e2e_parser.add_subparsers(dest="e2e_command")
    e2e_smoke_parser = e2e_subparsers.add_parser(
        "smoke",
        help="Check existing local metadata through retrieval and answer smoke flow.",
    )
    _add_config_dir_argument(e2e_smoke_parser)
    e2e_smoke_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_E2E_DB_PATH,
        help="SQLite database path to inspect.",
    )
    e2e_smoke_parser.add_argument(
        "--queries-config",
        type=Path,
        default=None,
        help="Optional safe smoke query YAML override.",
    )
    e2e_smoke_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_E2E_QUERY_LIMIT,
        help="Maximum evidence items per smoke query.",
    )
    e2e_smoke_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only check configuration, database presence, counts, and index status.",
    )
    e2e_smoke_parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Check retrieval and evidence IDs without answer generation.",
    )
    e2e_smoke_parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Do not add inventory fallback evidence when configured queries return no evidence.",
    )
    e2e_smoke_parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Include privacy-safe per-query retrieval stage counts.",
    )
    e2e_smoke_parser.add_argument(
        "--require-source",
        action="append",
        choices=("photos", "line", "notes"),
        default=[],
        help="Require at least one real evidence item from this source. Repeatable.",
    )
    e2e_smoke_parser.add_argument(
        "--query-limit",
        type=int,
        default=None,
        help="Limit how many smoke queries are executed.",
    )
    e2e_smoke_parser.add_argument(
        "--query-id",
        default=None,
        help="Run only the smoke query with this configured id.",
    )
    model_mode = e2e_smoke_parser.add_mutually_exclusive_group()
    model_mode.add_argument(
        "--fake-model",
        action="store_true",
        help="Use deterministic fake answer generation for the smoke.",
    )
    model_mode.add_argument(
        "--real-model",
        action="store_true",
        help="Use the configured local leader endpoint for answer generation.",
    )
    e2e_smoke_parser.add_argument(
        "--model-key",
        default=DEFAULT_E2E_LEADER_MODEL_KEY,
        help="Configured leader model id for --real-model.",
    )
    e2e_smoke_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow non-local endpoint URLs for --real-model.",
    )
    e2e_smoke_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Request timeout for real-model answer generation.",
    )
    e2e_smoke_parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_E2E_REAL_MODEL_MAX_TOKENS,
        help="Maximum tokens for real-model smoke answer generation.",
    )
    e2e_smoke_parser.add_argument(
        "--max-evidence-items",
        type=int,
        default=DEFAULT_E2E_MAX_EVIDENCE_ITEMS,
        help="Maximum evidence items sent to the real-model smoke prompt.",
    )
    e2e_smoke_parser.add_argument(
        "--max-evidence-chars",
        type=int,
        default=DEFAULT_E2E_MAX_EVIDENCE_CHARS,
        help="Maximum characters in the compact real-model evidence packet.",
    )
    e2e_smoke_parser.add_argument(
        "--compact-evidence",
        dest="compact_evidence",
        action="store_true",
        default=True,
        help="Use a compact redacted evidence packet for answer generation.",
    )
    e2e_smoke_parser.add_argument(
        "--no-compact-evidence",
        dest="compact_evidence",
        action="store_false",
        help="Use the normal redacted evidence packet instead of compact metadata.",
    )
    e2e_smoke_parser.add_argument(
        "--json-retry",
        type=int,
        default=1,
        help="Retry real-model structured answer generation after invalid JSON.",
    )
    e2e_smoke_parser.add_argument(
        "--response-format-json",
        action="store_true",
        help="Request OpenAI-compatible JSON response format for real-model E2E.",
    )
    e2e_smoke_parser.add_argument(
        "--show-answer",
        action="store_true",
        help=(
            "Display structured answer text. This is explicit because answers "
            "may contain private evidence-derived content."
        ),
    )
    e2e_smoke_parser.add_argument(
        "--show-snippets",
        action="store_true",
        help=(
            "Display truncated local evidence snippets. This may contain "
            "private content and is off by default."
        ),
    )
    e2e_smoke_parser.add_argument(
        "--show-model-output-metadata",
        action="store_true",
        help="Show safe model-output shape metadata without raw text.",
    )
    e2e_smoke_parser.add_argument(
        "--show-model-output",
        action="store_true",
        help=(
            "Show truncated raw model output preview; may contain private "
            "evidence-derived content."
        ),
    )
    e2e_smoke_parser.add_argument(
        "--semantic",
        dest="semantic_model",
        action="store_const",
        const="hash",
        default="none",
        help="Enable local hash semantic retrieval over persisted embeddings.",
    )
    e2e_smoke_parser.add_argument(
        "--no-semantic",
        dest="semantic_model",
        action="store_const",
        const="none",
        help="Disable semantic retrieval.",
    )
    e2e_smoke_parser.add_argument(
        "--semantic-model",
        dest="semantic_model_choice",
        choices=SEMANTIC_MODEL_CHOICES,
        default=None,
        help="Semantic retrieval embedding model for E2E smoke.",
    )
    e2e_smoke_parser.add_argument(
        "--semantic-top-k",
        type=int,
        default=None,
        help="Semantic retrieval candidate limit before merge/ranking.",
    )
    e2e_smoke_parser.add_argument(
        "--semantic-weight",
        type=float,
        default=1.0,
        help="Score multiplier for semantic retrieval candidates.",
    )
    e2e_smoke_parser.add_argument(
        "--reranker",
        choices=RERANKER_MODEL_CHOICES,
        default="none",
        help="Optional local evidence reranker.",
    )
    e2e_smoke_parser.add_argument(
        "--rerank-top-k",
        type=int,
        default=None,
        help="Number of top retrieval candidates to rerank.",
    )
    e2e_smoke_parser.add_argument(
        "--embedding-device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="Device hint for real local embedding models.",
    )
    e2e_smoke_parser.add_argument(
        "--json",
        action="store_true",
        help="Print a structured privacy-safe JSON report.",
    )
    e2e_smoke_parser.set_defaults(func=_e2e_smoke_command)

    models_parser = subparsers.add_parser("models", help="Inspect configured local models.")
    models_subparsers = models_parser.add_subparsers(dest="models_command")
    models_list_parser = models_subparsers.add_parser(
        "list",
        help="List configured model directories without loading weights.",
    )
    _add_config_dir_argument(models_list_parser)
    models_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Print model registry metadata as JSON.",
    )
    models_list_parser.set_defaults(func=_models_list_command)
    models_ping_parser = models_subparsers.add_parser(
        "ping",
        help="Ping configured local model endpoints without sending prompts.",
    )
    models_ping_parser.add_argument(
        "model_key",
        nargs="?",
        help="Optional configured model key to ping.",
    )
    _add_config_dir_argument(models_ping_parser)
    models_ping_parser.add_argument(
        "--model",
        dest="model",
        default=None,
        help="Optional configured model key to ping.",
    )
    models_ping_parser.add_argument(
        "--json",
        action="store_true",
        help="Print endpoint ping results as JSON.",
    )
    models_ping_parser.add_argument(
        "--vision-smoke",
        action="store_true",
        help="Run a synthetic multimodal vision smoke request for the selected model.",
    )
    models_ping_parser.add_argument(
        "--chat-smoke",
        action="store_true",
        help="Run a tiny synthetic chat completion smoke request for the selected model.",
    )
    models_ping_parser.add_argument(
        "--json-smoke",
        action="store_true",
        help="Run a synthetic strict JSON completion smoke request for the selected model.",
    )
    models_ping_parser.add_argument(
        "--max-tokens",
        type=int,
        default=64,
        help="Maximum tokens for --chat-smoke or --json-smoke.",
    )
    models_ping_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Request timeout for --chat-smoke or --json-smoke.",
    )
    models_ping_parser.add_argument(
        "--all",
        action="store_true",
        help="Include disabled models that have configured endpoints.",
    )
    models_ping_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow non-local endpoint URLs from config.",
    )
    models_ping_parser.set_defaults(func=_models_ping_command)

    runtime_parser = subparsers.add_parser("runtime", help="Plan local model runtime profiles.")
    runtime_subparsers = runtime_parser.add_subparsers(dest="runtime_command")
    runtime_plan_parser = runtime_subparsers.add_parser(
        "plan",
        help="Print which local model servers should be active for a profile.",
    )
    _add_config_dir_argument(runtime_plan_parser)
    runtime_plan_parser.add_argument(
        "profile",
        choices=tuple(sorted(RUNTIME_PROFILES)),
        help="Runtime profile to plan.",
    )
    runtime_plan_parser.add_argument(
        "--gpu-name",
        default=None,
        help="Optional GPU name override for planning output.",
    )
    runtime_plan_parser.add_argument(
        "--gpu-total-mb",
        type=int,
        default=None,
        help="Optional total GPU memory in MB for planning output.",
    )
    runtime_plan_parser.add_argument(
        "--gpu-free-mb",
        type=int,
        default=None,
        help="Optional free GPU memory in MB for planning output.",
    )
    runtime_plan_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the runtime plan as JSON.",
    )
    runtime_plan_parser.set_defaults(func=_runtime_plan_command)

    index_parser = subparsers.add_parser("index", help="Build local retrieval indexes.")
    index_subparsers = index_parser.add_subparsers(dest="index_command")
    index_text_parser = index_subparsers.add_parser(
        "text",
        help="Build the local text index for LINE messages, notes, and photo annotations.",
    )
    _add_config_dir_argument(index_text_parser)
    index_text_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path to index.",
    )
    index_text_parser.set_defaults(func=_index_text_command)
    index_embeddings_parser = index_subparsers.add_parser(
        "embeddings",
        help="Build local text embeddings with a fake, hash, or local real model.",
    )
    index_embeddings_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path to index.",
    )
    _add_embedding_arguments(index_embeddings_parser)
    index_embeddings_parser.add_argument(
        "--source",
        action="append",
        choices=(
            "line",
            "line_messages",
            "notes",
            "photos",
            "media",
            "media_items",
            "media_annotations",
        ),
        default=[],
        help="Limit embedding indexing to one source. Repeatable.",
    )
    index_embeddings_parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Resume-safe mode: skip embeddings already stored for the selected model.",
    )
    _add_vector_store_arguments(index_embeddings_parser)
    index_embeddings_parser.set_defaults(func=_index_embeddings_command)

    search_parser = subparsers.add_parser("search", help="Search local retrieval indexes.")
    search_subparsers = search_parser.add_subparsers(dest="search_command")
    search_text_parser = search_subparsers.add_parser(
        "text",
        help="Search indexed LINE messages and notes.",
    )
    _add_config_dir_argument(search_text_parser)
    search_text_parser.add_argument("query", help="Text query.")
    search_text_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path to search.",
    )
    search_text_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of results to return.",
    )
    search_text_parser.set_defaults(func=_search_text_command)
    search_semantic_parser = search_subparsers.add_parser(
        "semantic",
        help="Search persisted local embeddings.",
    )
    search_semantic_parser.add_argument("query", help="Semantic text query.")
    search_semantic_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path to search.",
    )
    search_semantic_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of results to return.",
    )
    _add_embedding_arguments(search_semantic_parser)
    _add_vector_store_arguments(search_semantic_parser)
    search_semantic_parser.set_defaults(func=_search_semantic_command)

    retrieve_parser = subparsers.add_parser(
        "retrieve",
        help="Retrieve local evidence across photos, LINE, and notes.",
    )
    _add_config_dir_argument(retrieve_parser)
    retrieve_parser.add_argument("question", nargs="?", help="Question, retrieval query, or 'audit'.")
    retrieve_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path to retrieve from.",
    )
    retrieve_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of evidence items to return.",
    )
    retrieve_parser.add_argument(
        "--source",
        choices=("photos", "line", "notes"),
        action="append",
        default=[],
        help="Restrict retrieval to a source kind. Can be passed more than once.",
    )
    retrieve_parser.add_argument(
        "--since",
        default=None,
        help="Minimum evidence date/time as an ISO-like string.",
    )
    retrieve_parser.add_argument(
        "--until",
        default=None,
        help="Maximum evidence date/time as an ISO-like string.",
    )
    retrieve_parser.add_argument(
        "--semantic-model",
        choices=SEMANTIC_MODEL_CHOICES,
        default="none",
        help="Optional lightweight semantic retrieval model for persisted embeddings.",
    )
    retrieve_parser.add_argument(
        "--reranker",
        choices=RERANKER_MODEL_CHOICES,
        default="none",
        help="Optional local evidence reranker.",
    )
    retrieve_parser.add_argument(
        "--rerank-top-k",
        type=int,
        default=None,
        help="Number of top retrieval candidates to rerank.",
    )
    retrieve_parser.add_argument(
        "--show-private",
        action="store_true",
        help="Show snippets only when config also enables private logging.",
    )
    retrieve_parser.add_argument(
        "--json",
        action="store_true",
        help="Print retrieval audit as JSON. Regular retrieval already prints JSON.",
    )
    retrieve_parser.set_defaults(func=_retrieve_command)

    query_parser = subparsers.add_parser(
        "query",
        help="Run minimal local RAG query flow over retrieved evidence.",
    )
    _add_config_dir_argument(query_parser)
    query_parser.add_argument("question", help="Question to answer from local evidence.")
    query_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path to query.",
    )
    query_parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Maximum number of evidence items to retrieve.",
    )
    query_parser.add_argument(
        "--source",
        choices=("photos", "line", "notes"),
        action="append",
        default=[],
        help="Restrict retrieval to a source kind. Can be passed more than once.",
    )
    query_parser.add_argument("--since", default=None, help="Minimum evidence date/time.")
    query_parser.add_argument("--until", default=None, help="Maximum evidence date/time.")
    query_parser.add_argument(
        "--semantic-model",
        choices=SEMANTIC_MODEL_CHOICES,
        default="none",
        help="Optional lightweight semantic retrieval model for persisted embeddings.",
    )
    query_parser.add_argument(
        "--reranker",
        choices=RERANKER_MODEL_CHOICES,
        default="none",
        help="Optional local evidence reranker.",
    )
    query_parser.add_argument(
        "--rerank-top-k",
        type=int,
        default=None,
        help="Number of top retrieval candidates to rerank.",
    )
    query_parser.add_argument(
        "--client",
        choices=("fake", "openai-compatible"),
        default="openai-compatible",
        help="Leader model client backend.",
    )
    query_parser.add_argument(
        "--model-key",
        default="leader",
        help="Configured leader model id for openai-compatible query mode.",
    )
    query_parser.add_argument(
        "--show-private",
        action="store_true",
        help="Show answer and evidence only when config also enables private logging.",
    )
    query_parser.add_argument(
        "--temporal-diagnostics",
        action="store_true",
        help="Accepted for temporal event queries; output includes privacy-safe stage diagnostics.",
    )
    query_parser.add_argument(
        "--temporal-fallback-term",
        action="append",
        default=[],
        help=(
            "Add an outing/event term for temporal LINE/notes fallback search. "
            "Can be passed more than once."
        ),
    )
    query_parser.set_defaults(func=_query_command)

    events_parser = subparsers.add_parser("events", help="Build and inspect tentative events.")
    events_subparsers = events_parser.add_subparsers(dest="events_command")
    events_build_parser = events_subparsers.add_parser(
        "build",
        help="Build tentative timeline events from local metadata.",
    )
    _add_config_dir_argument(events_build_parser)
    events_build_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path containing local metadata.",
    )
    events_build_parser.add_argument(
        "--timezone",
        default=None,
        help="IANA timezone for naive source timestamps.",
    )
    events_build_parser.add_argument(
        "--window-minutes",
        type=int,
        default=180,
        help="Maximum gap for grouping nearby evidence into an event.",
    )
    events_build_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of tentative events to create.",
    )
    events_build_parser.set_defaults(func=_events_build_command)

    events_list_parser = events_subparsers.add_parser(
        "list",
        help="List tentative timeline events.",
    )
    _add_config_dir_argument(events_list_parser)
    events_list_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path containing local events.",
    )
    events_list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of events to list.",
    )
    events_list_parser.add_argument(
        "--show-private",
        action="store_true",
        help="Show private event fields only when config also enables private logging.",
    )
    events_list_parser.set_defaults(func=_events_list_command)

    entities_parser = subparsers.add_parser("entities", help="Inspect and manage entities.")
    entities_subparsers = entities_parser.add_subparsers(dest="entities_command")
    entities_resolve_parser = entities_subparsers.add_parser(
        "resolve",
        help="Resolve entities from local text annotations.",
    )
    _add_config_dir_argument(entities_resolve_parser)
    entities_resolve_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path containing local annotations.",
    )
    entities_resolve_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of text annotation rows to process.",
    )
    entities_resolve_parser.set_defaults(func=_entities_resolve_command)

    entities_list_parser = entities_subparsers.add_parser(
        "list",
        help="List local entities and identity candidates.",
    )
    _add_config_dir_argument(entities_list_parser)
    entities_list_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path containing local entities.",
    )
    entities_list_parser.add_argument(
        "--type",
        choices=("person", "place", "organization", "topic"),
        default=None,
        help="Restrict output to one entity type.",
    )
    entities_list_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of entities to list.",
    )
    entities_list_parser.add_argument(
        "--show-private",
        action="store_true",
        help="Show private entity names only when config also enables private logging.",
    )
    entities_list_parser.set_defaults(func=_entities_list_command)

    entities_alias_parser = entities_subparsers.add_parser(
        "alias",
        help="Manage user-confirmed entity aliases.",
    )
    entities_alias_subparsers = entities_alias_parser.add_subparsers(dest="entities_alias_command")
    entities_alias_add_parser = entities_alias_subparsers.add_parser(
        "add",
        help="Add a user-confirmed alias to an entity.",
    )
    _add_config_dir_argument(entities_alias_add_parser)
    entities_alias_add_parser.add_argument(
        "entity_id",
        type=int,
        help="Existing entity id that should receive the alias.",
    )
    entities_alias_add_parser.add_argument("alias", help="Alias text to add.")
    entities_alias_add_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path containing local entities.",
    )
    entities_alias_add_parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Add the alias without merging matching same-type candidates.",
    )
    entities_alias_add_parser.set_defaults(func=_entities_alias_add_command)

    annotate_parser = subparsers.add_parser("annotate", help="Generate local derived annotations.")
    annotate_subparsers = annotate_parser.add_subparsers(dest="annotate_command")
    annotate_photos_parser = annotate_subparsers.add_parser(
        "photos",
        help="Annotate imported photo media with a local vision client.",
    )
    _add_config_dir_argument(annotate_photos_parser)
    annotate_photos_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path containing imported media items.",
    )
    annotate_photos_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of unannotated media items to process.",
    )
    annotate_photos_parser.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="Number of media items to process per batch.",
    )
    annotate_photos_parser.add_argument(
        "--client",
        choices=("fake", "openai-compatible"),
        default="openai-compatible",
        help="Vision client backend.",
    )
    annotate_photos_parser.add_argument(
        "--model-key",
        default="vision_common",
        help="Configured model id for openai-compatible vision annotation.",
    )
    annotate_photos_parser.add_argument(
        "--prompt",
        default=DEFAULT_PHOTO_ANNOTATION_PROMPT,
        help="Prompt sent to the local vision client.",
    )
    annotate_photos_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Request timeout for per-photo vision model calls.",
    )
    annotate_photos_parser.add_argument(
        "--max-side-px",
        type=int,
        default=1280,
        help="Resize image so the longest side is at most this many pixels before annotation.",
    )
    annotate_photos_parser.add_argument(
        "--image-format",
        choices=("jpeg", "png"),
        default="jpeg",
        help="Preprocessed image format sent to the local vision model.",
    )
    annotate_photos_parser.add_argument(
        "--image-quality",
        type=int,
        default=90,
        help="JPEG quality for preprocessed images.",
    )
    annotate_photos_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow non-local endpoint URLs from config.",
    )
    annotate_photos_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Select target items and validate local files without model calls or writes.",
    )
    annotate_photos_parser.add_argument(
        "--check-preprocess",
        action="store_true",
        help="In dry-run mode, verify image preprocessing without model calls or writes.",
    )
    annotate_photos_parser.add_argument(
        "--show-errors",
        action="store_true",
        help="Print privacy-safe annotation error diagnostics.",
    )
    annotate_photos_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first per-image annotation error.",
    )
    annotate_photos_parser.add_argument(
        "--status",
        action="store_true",
        help="Show privacy-safe photo annotation status without model calls.",
    )
    annotate_photos_parser.add_argument(
        "--failed",
        action="store_true",
        help="List tracked privacy-safe photo annotation failures without model calls.",
    )
    annotate_photos_parser.set_defaults(func=_annotate_photos_command)
    annotate_text_parser = annotate_subparsers.add_parser(
        "text",
        help="Extract structured Japanese metadata from LINE messages or notes.",
    )
    _add_config_dir_argument(annotate_text_parser)
    annotate_text_parser.add_argument(
        "--source",
        choices=("line", "notes"),
        required=True,
        help="Text source table to annotate.",
    )
    annotate_text_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path containing imported LINE messages or notes.",
    )
    annotate_text_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of unannotated text rows to process.",
    )
    annotate_text_parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Number of text rows to process per batch.",
    )
    annotate_text_parser.add_argument(
        "--client",
        choices=("fake", "openai-compatible"),
        default="openai-compatible",
        help="Japanese text understanding client backend.",
    )
    annotate_text_parser.add_argument(
        "--model-key",
        default="japanese_text",
        help="Configured model id for openai-compatible Japanese text understanding.",
    )
    annotate_text_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow non-local endpoint URLs from config.",
    )
    annotate_text_parser.set_defaults(func=_annotate_text_command)

    ingest_parser = subparsers.add_parser("ingest", help="Run local read-only ingestion tasks.")
    ingest_subparsers = ingest_parser.add_subparsers(dest="ingest_command")
    ingest_photos_parser = ingest_subparsers.add_parser(
        "photos",
        help="Import photo/media metadata without AI model inference.",
    )
    _add_config_dir_argument(ingest_photos_parser)
    photo_source = ingest_photos_parser.add_mutually_exclusive_group(required=True)
    photo_source.add_argument(
        "--path",
        type=Path,
        help="Local folder to scan as read-only source data.",
    )
    photo_source.add_argument(
        "--configured",
        action="store_true",
        help="Use the configured photos raw source path.",
    )
    ingest_photos_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path for import mode.",
    )
    ingest_photos_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and hash files without writing to SQLite.",
    )
    ingest_photos_parser.set_defaults(func=_ingest_photos_command)

    ingest_line_parser = ingest_subparsers.add_parser(
        "line",
        help="Import LINE text export messages without external LINE access.",
    )
    _add_config_dir_argument(ingest_line_parser)
    line_source = ingest_line_parser.add_mutually_exclusive_group(required=True)
    line_source.add_argument(
        "--path",
        type=Path,
        help="LINE text export file or folder to parse as read-only source data.",
    )
    line_source.add_argument(
        "--configured",
        action="store_true",
        help="Use the configured LINE raw source path.",
    )
    ingest_line_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path for import mode.",
    )
    ingest_line_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse files without writing to SQLite.",
    )
    ingest_line_parser.set_defaults(func=_ingest_line_command)

    ingest_notes_parser = ingest_subparsers.add_parser(
        "notes",
        help="Import Markdown, TXT, JSON, and PDF-placeholder note exports.",
    )
    _add_config_dir_argument(ingest_notes_parser)
    notes_source = ingest_notes_parser.add_mutually_exclusive_group(required=True)
    notes_source.add_argument(
        "--path",
        type=Path,
        help="Note export file or folder to parse as read-only source data.",
    )
    notes_source.add_argument(
        "--configured",
        action="store_true",
        help="Use the configured notes raw source path.",
    )
    ingest_notes_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="SQLite database path for import mode.",
    )
    ingest_notes_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse note exports without writing to SQLite.",
    )
    ingest_notes_parser.set_defaults(func=_ingest_notes_command)

    eval_parser = subparsers.add_parser("eval", help="Run synthetic local evaluations.")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command")
    eval_run_parser = eval_subparsers.add_parser(
        "run",
        help="Run the default synthetic quality and safety evaluation.",
    )
    eval_run_parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Optional SQLite database path for generated synthetic eval data.",
    )
    eval_run_parser.add_argument(
        "--summary",
        action="store_true",
        help="Print only aggregate metrics and failed case ids.",
    )
    eval_run_parser.set_defaults(func=_eval_run_command)
    eval_golden_parser = eval_subparsers.add_parser(
        "golden",
        help="Run local golden-question answer quality evaluation.",
    )
    _add_config_dir_argument(eval_golden_parser)
    eval_golden_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_E2E_DB_PATH,
        help="SQLite database path to evaluate.",
    )
    eval_golden_parser.add_argument(
        "--questions-config",
        type=Path,
        default=None,
        help="Optional golden question YAML file.",
    )
    eval_golden_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_E2E_QUERY_LIMIT,
        help="Maximum evidence items per golden question.",
    )
    eval_golden_parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Evaluate retrieval and evidence IDs without answer generation.",
    )
    golden_model_mode = eval_golden_parser.add_mutually_exclusive_group()
    golden_model_mode.add_argument(
        "--fake-model",
        action="store_true",
        help="Use deterministic fake answer generation.",
    )
    golden_model_mode.add_argument(
        "--real-model",
        action="store_true",
        help="Use the configured local leader endpoint.",
    )
    eval_golden_parser.add_argument(
        "--query-limit",
        type=int,
        default=None,
        help="Limit how many golden questions are evaluated.",
    )
    eval_golden_parser.add_argument(
        "--query-id",
        default=None,
        help="Run only the golden question with this id.",
    )
    eval_golden_parser.add_argument(
        "--model-key",
        default=DEFAULT_E2E_LEADER_MODEL_KEY,
        help="Configured leader model id for --real-model.",
    )
    eval_golden_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow non-local endpoint URLs for --real-model.",
    )
    eval_golden_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Request timeout for real-model answer generation.",
    )
    eval_golden_parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_E2E_REAL_MODEL_MAX_TOKENS,
        help="Maximum tokens for real-model answer generation.",
    )
    eval_golden_parser.add_argument(
        "--max-evidence-items",
        type=int,
        default=DEFAULT_E2E_MAX_EVIDENCE_ITEMS,
        help="Maximum evidence items sent to the real-model prompt.",
    )
    eval_golden_parser.add_argument(
        "--max-evidence-chars",
        type=int,
        default=DEFAULT_E2E_MAX_EVIDENCE_CHARS,
        help="Maximum characters in the compact real-model evidence packet.",
    )
    eval_golden_parser.add_argument(
        "--compact-evidence",
        dest="compact_evidence",
        action="store_true",
        default=True,
        help="Use a compact redacted evidence packet for answer generation.",
    )
    eval_golden_parser.add_argument(
        "--no-compact-evidence",
        dest="compact_evidence",
        action="store_false",
        help="Use the normal redacted evidence packet instead of compact metadata.",
    )
    eval_golden_parser.add_argument(
        "--json-retry",
        type=int,
        default=1,
        help="Retry real-model structured answer generation after invalid JSON.",
    )
    eval_golden_parser.add_argument(
        "--response-format-json",
        action="store_true",
        help="Request OpenAI-compatible JSON response format for real-model evaluation.",
    )
    eval_golden_parser.add_argument(
        "--show-answer",
        action="store_true",
        help="Display structured answer text in output/report.",
    )
    eval_golden_parser.add_argument(
        "--show-snippets",
        action="store_true",
        help="Display truncated local evidence snippets in output/report.",
    )
    eval_golden_parser.add_argument(
        "--snippet-chars",
        type=int,
        default=160,
        help="Maximum characters per displayed snippet when --show-snippets is used.",
    )
    eval_golden_parser.add_argument(
        "--require-source",
        action="append",
        choices=("photos", "line", "notes"),
        default=[],
        help="Require at least one evidence item from this source. Repeatable.",
    )
    eval_golden_parser.add_argument(
        "--exclude-source",
        action="append",
        choices=("photos", "line", "notes"),
        default=[],
        help="Exclude this source from golden retrieval. Repeatable.",
    )
    eval_golden_parser.add_argument(
        "--preferred-source",
        action="append",
        choices=("photos", "line", "notes"),
        default=[],
        help="Mark this source as preferred for golden diagnostics. Repeatable.",
    )
    eval_golden_parser.add_argument(
        "--source-policy",
        choices=("soft", "strict"),
        default="soft",
        help="Whether source coverage mismatches are warnings or failures.",
    )
    eval_golden_parser.add_argument(
        "--expected-keyword",
        action="append",
        default=[],
        help="Append an expected keyword used for golden retrieval/relevance diagnostics.",
    )
    eval_golden_parser.add_argument(
        "--negative-keyword",
        action="append",
        default=[],
        help="Append a negative keyword that penalizes golden evidence relevance.",
    )
    eval_golden_parser.add_argument(
        "--keyword-policy",
        choices=("soft", "strict"),
        default="soft",
        help="Whether missing expected keywords or negative hits fail golden retrieval.",
    )
    eval_golden_parser.add_argument(
        "--leader-plan",
        action="store_true",
        help="Use the configured local leader model to create a retrieval plan.",
    )
    eval_golden_parser.add_argument(
        "--leader-rerank",
        "--leader-judge-evidence",
        dest="leader_rerank",
        action="store_true",
        help="Use plan-aware deterministic relevance judging to rerank evidence.",
    )
    eval_golden_parser.add_argument(
        "--retrieval-repair",
        type=int,
        default=0,
        help="Retry weak planned retrieval up to this many times.",
    )
    eval_golden_parser.add_argument(
        "--show-plan",
        action="store_true",
        help="Display the retrieval plan; may contain private question-derived content.",
    )
    eval_golden_parser.add_argument(
        "--show-relevance",
        action="store_true",
        help="Display per-evidence relevance metadata without snippets.",
    )
    eval_golden_parser.add_argument(
        "--minimum-relevance-score",
        type=float,
        default=0.6,
        help="Minimum plan relevance score for usable evidence in strict checks.",
    )
    eval_golden_parser.add_argument(
        "--require-usable-evidence",
        action="store_true",
        help="Fail golden quality checks when no evidence is accepted as usable.",
    )
    eval_golden_parser.add_argument(
        "--relevance-policy",
        choices=("soft", "strict"),
        default="soft",
        help="Whether weak usable-evidence relevance is a warning or failure.",
    )
    eval_golden_parser.add_argument(
        "--semantic",
        dest="semantic_model",
        action="store_const",
        const="hash",
        default="none",
        help="Enable local hash semantic retrieval over persisted embeddings.",
    )
    eval_golden_parser.add_argument(
        "--no-semantic",
        dest="semantic_model",
        action="store_const",
        const="none",
        help="Disable semantic retrieval.",
    )
    eval_golden_parser.add_argument(
        "--semantic-model",
        dest="semantic_model_choice",
        choices=SEMANTIC_MODEL_CHOICES,
        default=None,
        help="Semantic retrieval embedding model for golden evaluation.",
    )
    eval_golden_parser.add_argument(
        "--semantic-top-k",
        type=int,
        default=None,
        help="Semantic retrieval candidate limit before merge/ranking.",
    )
    eval_golden_parser.add_argument(
        "--semantic-weight",
        type=float,
        default=1.0,
        help="Score multiplier for semantic retrieval candidates.",
    )
    eval_golden_parser.add_argument(
        "--reranker",
        choices=RERANKER_MODEL_CHOICES,
        default="none",
        help="Optional local evidence reranker for golden evaluation.",
    )
    eval_golden_parser.add_argument(
        "--rerank-top-k",
        type=int,
        default=None,
        help="Number of top retrieval candidates to rerank.",
    )
    eval_golden_parser.add_argument(
        "--embedding-device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="Device hint for real local embedding models.",
    )
    eval_golden_parser.add_argument(
        "--json",
        action="store_true",
        help="Print a structured privacy-safe JSON report.",
    )
    eval_golden_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional Markdown report output path.",
    )
    eval_golden_parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=None,
        help="Optional JSONL report output path.",
    )
    eval_golden_parser.set_defaults(func=_eval_golden_command)

    eval_semantic_compare_parser = eval_subparsers.add_parser(
        "semantic-compare",
        help="Compare semantic retrieval configurations by usable evidence quality.",
    )
    _add_config_dir_argument(eval_semantic_compare_parser)
    eval_semantic_compare_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_E2E_DB_PATH,
        help="SQLite database path to evaluate.",
    )
    eval_semantic_compare_parser.add_argument(
        "--questions-config",
        type=Path,
        default=None,
        help="Optional golden question YAML file.",
    )
    eval_semantic_compare_parser.add_argument(
        "--query-limit",
        type=int,
        default=None,
        help="Limit how many golden questions are compared.",
    )
    eval_semantic_compare_parser.add_argument(
        "--query-id",
        default=None,
        help="Run only the golden question with this id.",
    )
    eval_semantic_compare_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_E2E_QUERY_LIMIT,
        help="Maximum evidence items per configuration.",
    )
    eval_semantic_compare_parser.add_argument(
        "--real-semantic-model",
        choices=tuple(choice for choice in SEMANTIC_MODEL_CHOICES if choice != "none"),
        default="ruri-v3-310m",
        help="Configured real semantic model alias used by ruri comparison configs.",
    )
    eval_semantic_compare_parser.add_argument(
        "--real-reranker",
        choices=RERANKER_MODEL_CHOICES,
        default="ruri-v3-reranker-310m",
        help="Configured reranker alias used by reranker comparison configs.",
    )
    eval_semantic_compare_parser.add_argument(
        "--semantic-top-k",
        type=int,
        default=20,
        help="Semantic retrieval candidate limit before merge/ranking.",
    )
    eval_semantic_compare_parser.add_argument(
        "--semantic-weight",
        type=float,
        default=1.0,
        help="Score multiplier for semantic retrieval candidates.",
    )
    eval_semantic_compare_parser.add_argument(
        "--rerank-top-k",
        type=int,
        default=20,
        help="Number of top retrieval candidates to rerank.",
    )
    eval_semantic_compare_parser.add_argument(
        "--retrieval-repair",
        type=int,
        default=1,
        help="Retry weak planned retrieval up to this many times.",
    )
    eval_semantic_compare_parser.add_argument(
        "--minimum-relevance-score",
        type=float,
        default=0.6,
        help="Minimum judged relevance score for strict usable evidence.",
    )
    eval_semantic_compare_parser.add_argument(
        "--embedding-device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="Device hint for real local embedding models.",
    )
    eval_semantic_compare_parser.add_argument(
        "--show-relevance",
        action="store_true",
        help="Include per-evidence relevance metadata from judged configs.",
    )
    eval_semantic_compare_parser.add_argument(
        "--model-key",
        default=DEFAULT_E2E_LEADER_MODEL_KEY,
        help="Configured leader model id for leader-plan comparison configs.",
    )
    eval_semantic_compare_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow non-local endpoint URLs for leader-plan comparison configs.",
    )
    eval_semantic_compare_parser.add_argument(
        "--json",
        action="store_true",
        help="Print a structured privacy-safe JSON report.",
    )
    eval_semantic_compare_parser.set_defaults(func=_eval_semantic_compare_command)

    api_parser = subparsers.add_parser("api", help="Run the local-only FastAPI API.")
    api_subparsers = api_parser.add_subparsers(dest="api_command")
    api_serve_parser = api_subparsers.add_parser(
        "serve",
        help="Serve the localhost FastAPI API.",
    )
    _add_config_dir_argument(api_serve_parser)
    api_serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Loopback host to bind. Non-loopback hosts are rejected.",
    )
    api_serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="TCP port to bind.",
    )
    api_serve_parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/local/private_memory_agent.sqlite3"),
        help="Default SQLite database path for API requests.",
    )
    api_serve_parser.set_defaults(func=_api_serve_command)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run local-only skeleton health checks.",
    )
    _add_config_dir_argument(doctor_parser)
    doctor_parser.set_defaults(func=_doctor_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line interface."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args))


def _doctor_command(args: argparse.Namespace) -> int:
    result = run_doctor(config_dir=args.config_dir, paths_config=args.config)
    print(format_doctor_result(result))
    return 0 if result.ok else 1


def _config_show_command(args: argparse.Namespace) -> int:
    config = load_config(config_dir=args.config_dir, paths_config=args.config)
    print(json.dumps(config.to_dict(), indent=2, sort_keys=True))
    return 0


def _stats_command(args: argparse.Namespace) -> int:
    load_config(config_dir=args.config_dir, paths_config=args.config)
    report = build_annotation_stats_report(args.db)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _media_timestamps_audit_command(args: argparse.Namespace) -> int:
    load_config(config_dir=args.config_dir, paths_config=args.config)
    if args.extract_limit is not None and args.extract_limit < 0:
        print("Timestamp audit failed: --extract-limit must be 0 or positive.")
        return 2
    report = audit_media_timestamps(
        args.db,
        method=args.method,
        fallback=args.fallback,
        month_histogram=args.month_histogram,
        extract_limit=None if args.extract_limit == 0 else args.extract_limit,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_timestamp_audit(report))
    return 0


def _media_timestamps_backfill_command(args: argparse.Namespace) -> int:
    load_config(config_dir=args.config_dir, paths_config=args.config)
    if args.limit is not None and args.limit <= 0:
        print("Timestamp backfill failed: --limit must be positive.")
        return 2
    report = backfill_media_timestamps(
        args.db,
        dry_run=args.dry_run,
        limit=args.limit,
        source=args.source,
        method=args.method,
        fallback=args.fallback,
        min_confidence=args.min_confidence,
        only_missing=args.only_missing,
        show_errors=args.show_errors,
    )
    if args.json:
        print(
            json.dumps(
                report.to_dict(show_errors=args.show_errors),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )
    else:
        print(format_timestamp_backfill(report, show_errors=args.show_errors))
    return 0


def _db_schema_command(args: argparse.Namespace) -> int:
    load_config(config_dir=args.config_dir, paths_config=args.config)
    report = inspect_database_schema(args.db)
    if args.json:
        print(diagnostics_report_to_json(report))
    else:
        print(format_database_schema_report(report))
    return 0 if report.db_exists else 1


def _e2e_smoke_command(args: argparse.Namespace) -> int:
    try:
        report = run_e2e_smoke(
            E2ESmokeOptions(
                config_dir=args.config_dir,
                paths_config=args.config,
                db_path=args.db,
                queries_config=args.queries_config,
                dry_run=args.dry_run,
                retrieval_only=args.retrieval_only,
                fake_model=args.fake_model,
                real_model=args.real_model,
                no_fallback=args.no_fallback,
                diagnose=args.diagnose,
                require_sources=tuple(args.require_source),
                query_limit=args.query_limit,
                query_id=args.query_id,
                limit=args.limit,
                timeout_seconds=args.timeout_seconds,
                max_tokens=args.max_tokens,
                max_evidence_items=args.max_evidence_items,
                max_evidence_chars=args.max_evidence_chars,
                compact_evidence=args.compact_evidence,
                json_retry=args.json_retry,
                response_format_json=args.response_format_json,
                show_answer=args.show_answer,
                show_snippets=args.show_snippets,
                show_model_output_metadata=args.show_model_output_metadata,
                show_model_output=args.show_model_output,
                semantic_model=_resolve_semantic_model_arg(args),
                semantic_top_k=args.semantic_top_k,
                semantic_weight=args.semantic_weight,
                reranker=args.reranker,
                rerank_top_k=args.rerank_top_k,
                embedding_device=args.embedding_device,
                model_key=args.model_key,
                allow_remote=args.allow_remote,
            ),
        )
    except (RuntimeError, ValueError) as exc:
        print(f"E2E smoke failed: {_safe_cli_error_message(exc)}")
        return 2
    if args.json:
        print(e2e_report_to_json(report))
    else:
        print(format_e2e_smoke_report(report))
    return 0 if report.ok else 1


def _models_list_command(args: argparse.Namespace) -> int:
    config = load_config(config_dir=args.config_dir, paths_config=args.config)
    if args.json:
        print(json.dumps(config.model_registry.to_list(), indent=2, sort_keys=True))
    else:
        print(_format_model_table(config.model_registry))
    return 0


def _models_ping_command(args: argparse.Namespace) -> int:
    config = load_config(config_dir=args.config_dir, paths_config=args.config)
    model_key = args.model_key or args.model
    if args.model_key and args.model and args.model_key != args.model:
        print("Model ping failed: positional model and --model disagree.")
        return 2
    if args.vision_smoke:
        if model_key is None:
            print("Vision smoke failed: specify a model key.")
            return 2
        try:
            endpoint = _endpoint_for_model_key(config, model_key)
            result = run_vision_smoke_test(endpoint, allow_remote=args.allow_remote)
        except (ModelRuntimeError, ValueError) as exc:
            print(f"Vision smoke failed: {_safe_cli_error_message(exc)}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            print(_format_vision_smoke_result(result))
        return 0

    if args.chat_smoke:
        if model_key is None:
            print("Chat smoke failed: specify a model key.")
            return 2
        try:
            endpoint = _endpoint_for_model_key(config, model_key)
            result = run_chat_smoke_test(
                endpoint,
                allow_remote=args.allow_remote,
                max_tokens=args.max_tokens,
                timeout_seconds=args.timeout_seconds,
            )
        except (ModelRuntimeError, ValueError) as exc:
            print(f"Chat smoke failed: {_safe_cli_error_message(exc)}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            print(_format_chat_smoke_result(result))
        return 0

    if args.json_smoke:
        if model_key is None:
            print("JSON smoke failed: specify a model key.")
            return 2
        try:
            endpoint = _endpoint_for_model_key(config, model_key)
            result = run_json_smoke_test(
                endpoint,
                allow_remote=args.allow_remote,
                max_tokens=args.max_tokens,
                timeout_seconds=args.timeout_seconds,
            )
        except (ModelRuntimeError, ValueError) as exc:
            print(f"JSON smoke failed: {_safe_cli_error_message(exc)}")
            return 1
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            print(_format_json_smoke_result(result))
        return 0

    if model_key is not None:
        try:
            endpoint = _endpoint_for_model_key(config, model_key)
            results = [
                ping_model_endpoint(endpoint, allow_remote=args.allow_remote),
            ]
        except ValueError as exc:
            print(f"Model ping failed: {_safe_cli_error_message(exc)}")
            return 2
    else:
        results = ping_configured_model_endpoints(
            config.model_registry,
            include_disabled=args.all,
            allow_remote=args.allow_remote,
        )
    if args.json:
        print(json.dumps([result.to_dict() for result in results], indent=2, sort_keys=True))
    else:
        print(_format_model_ping_results(results))
    return 0 if all(result.ok for result in results) else 1


def _runtime_plan_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        gpu = _gpu_info_from_args(args)
        plan = build_runtime_plan(config, args.profile, gpu=gpu)
    except ValueError as exc:
        print(f"Runtime plan failed: {_safe_cli_error_message(exc)}")
        return 2
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
    else:
        print(_format_runtime_plan(plan))
    return 0


def _index_text_command(args: argparse.Namespace) -> int:
    load_config(config_dir=args.config_dir, paths_config=args.config)
    result = index_text(args.db)
    print(_format_text_index_result(result))
    return 0


def _search_text_command(args: argparse.Namespace) -> int:
    load_config(config_dir=args.config_dir, paths_config=args.config)
    results = search_text(args.db, args.query, limit=args.limit)
    print(
        json.dumps(
            {"query": args.query, "results": [result.to_dict() for result in results]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )
    return 0


def _index_embeddings_command(args: argparse.Namespace) -> int:
    try:
        model = _build_embedding_model(args)
        vector_store = _build_vector_store(args, vector_size=getattr(model, "dimensions", None))
        result = index_embeddings(
            args.db,
            model,
            vector_store=vector_store,
            source_tables=tuple(args.source),
            skip_existing=args.skip_existing,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Embedding index failed: {exc}")
        return 2
    print(_format_embedding_index_result(result, vector_store=args.vector_store))
    return 0


def _search_semantic_command(args: argparse.Namespace) -> int:
    try:
        model = _build_embedding_model(args)
        vector_store = _build_vector_store(args, vector_size=getattr(model, "dimensions", None))
        results = semantic_search(
            args.db,
            args.query,
            model,
            vector_store=vector_store,
            limit=args.limit,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Semantic search failed: {exc}")
        return 2
    print(
        json.dumps(
            {"query": args.query, "results": [result.to_dict() for result in results]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )
    return 0


def _retrieve_command(args: argparse.Namespace) -> int:
    if args.question == "audit":
        try:
            config = load_config(config_dir=args.config_dir, paths_config=args.config)
            smoke_queries = load_e2e_smoke_queries(config.config_dir)
            query_specs = tuple(
                (f"query_{index}", query.text, query.sources)
                for index, query in enumerate(smoke_queries, start=1)
            )
            report = run_retrieval_audit(
                args.db,
                query_specs,
                limit=args.limit,
                selected_semantic_model_id=_semantic_model_id_for_diagnostics(args.semantic_model),
            )
        except (RuntimeError, ValueError) as exc:
            print(f"Retrieval audit failed: {_safe_cli_error_message(exc)}")
            return 2
        if args.json:
            print(diagnostics_report_to_json(report))
        else:
            print(diagnostics_report_to_json(report))
        return 0 if report.db_exists else 1
    if args.question is None:
        print("Evidence retrieval failed: question is required.")
        return 2
    try:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        embedding_model = _build_retrieval_embedding_model(args)
        service = RetrievalService(
            args.db,
            embedding_model=embedding_model,
            reranker=_build_retrieval_reranker(args, config),
            rerank_top_k=args.rerank_top_k,
        )
        redact = not (args.show_private and config.app.log_private_data)
        result = service.retrieve(
            args.question,
            filters=RetrievalFilters(
                sources=tuple(args.source),
                since=args.since,
                until=args.until,
            ),
            limit=args.limit,
            redact_for_display=redact,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Evidence retrieval failed: {exc}")
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _query_command(args: argparse.Namespace) -> int:
    privacy_guard = PrivacyGuard()
    try:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        privacy_guard = _build_privacy_guard(config)
        if not args.source or "photos" in args.source:
            temporal_terms = tuple(args.temporal_fallback_term) if args.temporal_fallback_term else None
            temporal_result = answer_temporal_event_query(
                args.question,
                db_path=args.db,
                fallback_terms=temporal_terms or None,
            )
            if temporal_result is not None:
                print(
                    json.dumps(
                        {
                            "query_flow": "temporal_event_search",
                            **temporal_result.to_dict(show_answer=True),
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                )
                return 0
        leader_agent = _build_leader_agent(args)
        redact = not (args.show_private and config.app.log_private_data)
        result = run_query_flow(
            args.question,
            db_path=args.db,
            leader_agent=leader_agent,
            embedding_model=_build_retrieval_embedding_model(args),
            reranker=_build_retrieval_reranker(args, config),
            rerank_top_k=args.rerank_top_k,
            filters=RetrievalFilters(
                sources=tuple(args.source),
                since=args.since,
                until=args.until,
            ),
            limit=args.limit,
            redact_for_display=redact,
            privacy_guard=privacy_guard,
        )
    except (AnswerValidationError, RuntimeError, ValueError) as exc:
        print(privacy_guard.safe_log_message(f"Query failed: {exc}"))
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _events_build_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        timezone = args.timezone or config.app.default_timezone
        result = build_events(
            args.db,
            timezone=timezone,
            window_minutes=args.window_minutes,
            limit=args.limit,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Event build failed: {exc}")
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _events_list_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        redact = not (args.show_private and config.app.log_private_data)
        events = list_events(args.db, limit=args.limit, redact_private=redact)
    except (RuntimeError, ValueError) as exc:
        print(f"Event list failed: {exc}")
        return 2
    print(
        json.dumps(
            {"events": events, "redacted": redact},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )
    return 0


def _entities_resolve_command(args: argparse.Namespace) -> int:
    try:
        load_config(config_dir=args.config_dir, paths_config=args.config)
        result = resolve_text_annotation_entities(args.db, limit=args.limit)
    except (RuntimeError, ValueError) as exc:
        print(f"Entity resolve failed: {exc}")
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _entities_list_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        redact = not (args.show_private and config.app.log_private_data)
        entities = list_entities(
            args.db,
            entity_type=args.type,
            limit=args.limit,
            redact_private=redact,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Entity list failed: {exc}")
        return 2
    print(
        json.dumps(
            {"entities": entities, "redacted": redact},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
    )
    return 0


def _entities_alias_add_command(args: argparse.Namespace) -> int:
    try:
        load_config(config_dir=args.config_dir, paths_config=args.config)
        result = add_entity_alias(
            args.db,
            args.entity_id,
            args.alias,
            user_confirmed=True,
            merge_existing=not args.no_merge,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Entity alias add failed: {exc}")
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _annotate_photos_command(args: argparse.Namespace) -> int:
    try:
        if args.status:
            load_config(config_dir=args.config_dir, paths_config=args.config)
            report = build_annotation_stats_report(args.db)
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.failed:
            load_config(config_dir=args.config_dir, paths_config=args.config)
            limit = args.limit if args.limit is not None else 50
            report = list_failed_photo_annotations(args.db, limit=limit)
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        preprocess_options = PhotoPreprocessOptions(
            max_side_px=args.max_side_px,
            output_format=args.image_format,
            quality=args.image_quality,
        )
        client, model_id, endpoint_url = _build_vision_client(args, preflight=not args.dry_run)
        result = annotate_photos(
            args.db,
            client=client,
            model_id=model_id,
            limit=args.limit,
            batch_size=args.batch_size,
            prompt=args.prompt,
            dry_run=args.dry_run,
            fail_fast=args.fail_fast,
            endpoint_url=endpoint_url,
            preprocess_options=preprocess_options,
            check_preprocess=args.check_preprocess,
        )
    except (FileNotFoundError, RuntimeError, ValueError, ModelRuntimeError) as exc:
        print(f"Photo annotation preflight failed: {_safe_cli_error_message(exc)}")
        return 2
    print(_format_photo_annotation_result(result))
    if args.show_errors:
        print(_format_photo_annotation_error_summary(result))
    return 0 if result.errors == 0 else 1


def _annotate_text_command(args: argparse.Namespace) -> int:
    try:
        client, model_id = _build_text_understanding_client(args)
        result = annotate_text(
            args.db,
            source=args.source,
            client=client,
            model_id=model_id,
            limit=args.limit,
            batch_size=args.batch_size,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Text annotation failed: {exc}")
        return 2
    print(_format_text_annotation_result(result))
    return 0 if result.errors == 0 else 1


def _ingest_photos_command(args: argparse.Namespace) -> int:
    source_path = args.path
    if args.configured:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        photos_source = config.paths.raw_sources.get("photos")
        if photos_source is None or not photos_source.enabled or photos_source.path is None:
            print("Photo ingest failed: configured photos source is unavailable.")
            return 2
        source_path = photos_source.path

    try:
        result = ingest_photos(source_path, db_path=args.db, dry_run=args.dry_run)
    except (OSError, ValueError):
        print("Photo ingest failed: source path is unavailable or unreadable.")
        return 2
    print(_format_photo_ingest_result(result))
    return 0 if result.errors == 0 else 1


def _ingest_line_command(args: argparse.Namespace) -> int:
    source_path = args.path
    if args.configured:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        line_source = config.paths.raw_sources.get("line")
        if line_source is None or not line_source.enabled or line_source.path is None:
            print("LINE ingest failed: configured LINE source is unavailable.")
            return 2
        source_path = line_source.path

    try:
        result = ingest_line_exports(source_path, db_path=args.db, dry_run=args.dry_run)
    except (OSError, ValueError):
        print("LINE ingest failed: source path is unavailable or unreadable.")
        return 2
    print(_format_line_ingest_result(result))
    return 0 if result.errors == 0 else 1


def _ingest_notes_command(args: argparse.Namespace) -> int:
    source_path = args.path
    if args.configured:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        notes_source = config.paths.raw_sources.get("notes")
        if notes_source is None or not notes_source.enabled or notes_source.path is None:
            print("Notes ingest failed: configured notes source is unavailable.")
            return 2
        source_path = notes_source.path

    try:
        result = ingest_notes(source_path, db_path=args.db, dry_run=args.dry_run)
    except (OSError, ValueError):
        print("Notes ingest failed: source path is unavailable or unreadable.")
        return 2
    print(_format_notes_ingest_result(result))
    return 0 if result.errors == 0 else 1


def _eval_run_command(args: argparse.Namespace) -> int:
    result = run_synthetic_eval(db_path=args.db)
    payload = result.summary_dict() if args.summary else result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.passed else 1


def _eval_golden_command(args: argparse.Namespace) -> int:
    try:
        report = run_golden_eval(
            GoldenEvalOptions(
                config_dir=args.config_dir,
                paths_config=args.config,
                db_path=args.db,
                questions_config=args.questions_config,
                retrieval_only=args.retrieval_only,
                fake_model=args.fake_model,
                real_model=args.real_model,
                query_limit=args.query_limit,
                query_id=args.query_id,
                limit=args.limit,
                timeout_seconds=args.timeout_seconds,
                max_tokens=args.max_tokens,
                max_evidence_items=args.max_evidence_items,
                max_evidence_chars=args.max_evidence_chars,
                compact_evidence=args.compact_evidence,
                json_retry=args.json_retry,
                response_format_json=args.response_format_json,
                show_answer=args.show_answer,
                show_snippets=args.show_snippets,
                snippet_chars=args.snippet_chars,
                require_sources=tuple(args.require_source),
                exclude_sources=tuple(args.exclude_source),
                preferred_sources=tuple(args.preferred_source),
                source_policy=args.source_policy,
                expected_keywords=tuple(args.expected_keyword),
                negative_keywords=tuple(args.negative_keyword),
                keyword_policy=args.keyword_policy,
                leader_plan=args.leader_plan,
                leader_rerank=args.leader_rerank,
                retrieval_repair=args.retrieval_repair,
                show_plan=args.show_plan,
                show_relevance=args.show_relevance,
                minimum_relevance_score=args.minimum_relevance_score,
                require_usable_evidence=args.require_usable_evidence,
                relevance_policy=args.relevance_policy,
                semantic_model=_resolve_semantic_model_arg(args),
                semantic_top_k=args.semantic_top_k,
                semantic_weight=args.semantic_weight,
                reranker=args.reranker,
                rerank_top_k=args.rerank_top_k,
                embedding_device=args.embedding_device,
                model_key=args.model_key,
                allow_remote=args.allow_remote,
            ),
        )
        write_golden_outputs(
            report,
            markdown_path=args.output,
            jsonl_path=args.output_jsonl,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Golden eval failed: {_safe_cli_error_message(exc)}")
        return 2
    if args.json:
        print(golden_report_to_json(report))
    else:
        print(format_golden_eval_report(report))
    return 0 if report.ok else 1


def _eval_semantic_compare_command(args: argparse.Namespace) -> int:
    try:
        report = run_semantic_compare(
            SemanticCompareOptions(
                config_dir=args.config_dir,
                paths_config=args.config,
                db_path=args.db,
                questions_config=args.questions_config,
                query_limit=args.query_limit,
                query_id=args.query_id,
                limit=args.limit,
                real_semantic_model=args.real_semantic_model,
                real_reranker=args.real_reranker,
                semantic_top_k=args.semantic_top_k,
                semantic_weight=args.semantic_weight,
                rerank_top_k=args.rerank_top_k,
                retrieval_repair=args.retrieval_repair,
                minimum_relevance_score=args.minimum_relevance_score,
                embedding_device=args.embedding_device,
                show_relevance=args.show_relevance,
                model_key=args.model_key,
                allow_remote=args.allow_remote,
            ),
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Semantic compare failed: {_safe_cli_error_message(exc)}")
        return 2
    if args.json:
        print(semantic_compare_report_to_json(report))
    else:
        print(format_semantic_compare_report(report))
    return 0 if report.ok else 1


def _api_serve_command(args: argparse.Namespace) -> int:
    if not _is_loopback_host(args.host):
        print("API serve failed: host must be localhost or a loopback address.")
        return 2
    try:
        import uvicorn

        from private_memory_agent.api import create_app
    except ImportError as exc:
        print(f"API serve failed: missing API dependency: {exc.name}")
        return 2

    app = create_app(db_path=args.db, config_dir=args.config_dir, paths_config=args.config)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _add_config_dir_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Directory containing Private Memory Agent example config files.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional local paths YAML overlay, such as configs/paths.local.yaml.",
    )


def _add_embedding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        choices=tuple(choice for choice in SEMANTIC_MODEL_CHOICES if choice != "none"),
        default=None,
        help=(
            "Public semantic model alias. Overrides --model-backend/--model-key "
            "when provided."
        ),
    )
    parser.add_argument(
        "--model-backend",
        choices=("hash", "fake", "sentence-transformers"),
        default="hash",
        help="Embedding backend. Real local models require sentence-transformers.",
    )
    parser.add_argument(
        "--model-key",
        default="text_embedding",
        help="Configured model id to use for sentence-transformers backend.",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="Explicit local model path for sentence-transformers backend.",
    )
    parser.add_argument(
        "--dimensions",
        type=int,
        default=32,
        help="Dimensions for hash fallback embeddings.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Optional device string for sentence-transformers backend.",
    )
    _add_config_dir_argument(parser)


def _add_vector_store_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--vector-store",
        choices=("memory", "qdrant"),
        default="memory",
        help="Vector store backend. Qdrant must already be running.",
    )
    parser.add_argument(
        "--qdrant-url",
        default="http://localhost:6333",
        help="Qdrant URL when --vector-store qdrant is selected.",
    )
    parser.add_argument(
        "--qdrant-collection",
        default="private_memory_agent_text_embeddings",
        help="Qdrant collection name.",
    )


def _format_model_table(registry: ModelRegistry) -> str:
    rows = [
        (
            spec.model_id,
            spec.provider,
            spec.role,
            "yes" if spec.enabled else "no",
            spec.status,
            str(spec.resolved_path),
        )
        for spec in registry
    ]
    headers = ("id", "provider", "role", "enabled", "status", "path")
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        if rows
        else len(headers[index])
        for index in range(len(headers))
    ]
    lines = [
        _format_row(headers, widths),
        _format_row(tuple("-" * width for width in widths), widths),
    ]
    lines.extend(_format_row(row, widths) for row in rows)
    return "\n".join(lines)


def _format_row(values: tuple[str, ...], widths: list[int]) -> str:
    return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values))


def _format_model_ping_results(results: list[ModelPingResult]) -> str:
    if not results:
        return "No configured model endpoints."
    rows = [
        (
            result.model_id,
            result.provider,
            result.role,
            result.endpoint_url,
            "ok" if result.ok else "failed",
            "" if result.status_code is None else str(result.status_code),
            "" if result.latency_ms is None else str(result.latency_ms),
            "" if result.error is None else result.error,
        )
        for result in results
    ]
    headers = ("id", "provider", "role", "endpoint", "status", "http", "ms", "error")
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        if rows
        else len(headers[index])
        for index in range(len(headers))
    ]
    lines = [
        _format_row(headers, widths),
        _format_row(tuple("-" * width for width in widths), widths),
    ]
    lines.extend(_format_row(row, widths) for row in rows)
    return "\n".join(lines)


def _format_runtime_plan(plan) -> str:
    lines = [
        f"Runtime profile: {plan.profile.profile_id}",
        f"description: {plan.profile.description}",
        f"estimated_vram_gb={plan.profile.estimated_vram_gb:.1f}; "
        f"safe_vram_gb={plan.safe_vram_gb:.1f}",
    ]
    if plan.gpu is not None:
        lines.append(
            f"gpu={plan.gpu.name}; total_mb={plan.gpu.memory_total_mb}; "
            f"free_mb={plan.gpu.memory_free_mb}"
        )
    lines.append("active models:")
    lines.extend(_format_runtime_model_lines(plan.active_models))
    if plan.optional_models:
        lines.append("optional models:")
        lines.extend(_format_runtime_model_lines(plan.optional_models))
    if plan.profile.notes:
        lines.append("notes:")
        lines.extend(f"- {note}" for note in plan.profile.notes)
    if plan.warnings:
        lines.append("warnings:")
        lines.extend(f"- {warning}" for warning in plan.warnings)
    else:
        lines.append("warnings: none")
    lines.append("action: start/stop servers manually; PMA will not start them.")
    return "\n".join(lines)


def _format_runtime_model_lines(models) -> list[str]:
    lines: list[str] = []
    for model in models:
        endpoint = model.endpoint_url or "<no endpoint>"
        served = model.served_model_name or "<not configured>"
        lines.append(
            "  "
            f"{model.model_key}: role={model.role or 'unknown'}; "
            f"provider={model.provider or 'unknown'}; "
            f"enabled={model.enabled}; status={model.status}; "
            f"endpoint={endpoint}; served_model={served}"
        )
    return lines


def _gpu_info_from_args(args: argparse.Namespace) -> GPUInfo | None:
    if args.gpu_name is None and args.gpu_total_mb is None and args.gpu_free_mb is None:
        return None
    free_mb = max(0, int(args.gpu_free_mb or 0))
    total_mb = int(args.gpu_total_mb or max(free_mb, 0))
    return GPUInfo(
        name=args.gpu_name or "manual-gpu",
        memory_total_mb=max(total_mb, free_mb),
        memory_free_mb=free_mb,
    )


def _format_vision_smoke_result(result) -> str:
    text = (
        "Vision smoke passed: "
        f"model_id={result.model_id}; "
        f"served_model_name={result.served_model_name}; "
        f"endpoint={result.endpoint_url}; "
        f"response_chars={result.response_chars}"
    )
    if getattr(result, "warnings", ()):
        text += "; warnings=" + " | ".join(result.warnings)
    return text


def _format_chat_smoke_result(result) -> str:
    text = (
        "Chat smoke passed: "
        f"model_id={result.model_id}; "
        f"served_model_name={result.served_model_name}; "
        f"endpoint={result.endpoint_url}; "
        f"response_chars={result.response_chars}; "
        f"max_tokens={result.max_tokens}; "
        f"timeout_seconds={result.timeout_seconds}"
    )
    if getattr(result, "warnings", ()):
        text += "; warnings=" + " | ".join(result.warnings)
    return text


def _format_json_smoke_result(result) -> str:
    text = (
        "JSON smoke passed: "
        f"model_id={result.model_id}; "
        f"served_model_name={result.served_model_name}; "
        f"endpoint={result.endpoint_url}; "
        f"response_chars={result.response_chars}; "
        f"json_extraction_strategy={result.json_extraction_strategy}; "
        f"max_tokens={result.max_tokens}; "
        f"timeout_seconds={result.timeout_seconds}"
    )
    if getattr(result, "warnings", ()):
        text += "; warnings=" + " | ".join(result.warnings)
    return text


def _format_photo_ingest_result(result) -> str:
    mode = "dry-run" if result.dry_run else "import"
    return (
        f"Photo ingest {mode} complete: "
        f"scanned={result.scanned}; "
        f"imported={result.imported}; "
        f"skipped_duplicates={result.skipped_duplicates}; "
        f"skipped_unsupported={result.skipped_unsupported}; "
        f"errors={result.errors}"
    )


def _format_line_ingest_result(result) -> str:
    mode = "dry-run" if result.dry_run else "import"
    return (
        f"LINE ingest {mode} complete: "
        f"files_scanned={result.files_scanned}; "
        f"messages_parsed={result.messages_parsed}; "
        f"messages_imported={result.messages_imported}; "
        f"skipped_duplicates={result.skipped_duplicates}; "
        f"errors={result.errors}"
    )


def _format_notes_ingest_result(result) -> str:
    mode = "dry-run" if result.dry_run else "import"
    return (
        f"Notes ingest {mode} complete: "
        f"files_scanned={result.files_scanned}; "
        f"notes_parsed={result.notes_parsed}; "
        f"notes_imported={result.notes_imported}; "
        f"skipped_duplicates={result.skipped_duplicates}; "
        f"skipped_unsupported={result.skipped_unsupported}; "
        f"errors={result.errors}"
    )


def _format_text_index_result(result) -> str:
    fts_status = "enabled" if result.fts5_enabled else "disabled"
    return (
        "Text index complete: "
        f"documents_indexed={result.documents_indexed}; "
        f"fts5={fts_status}"
    )


def _format_embedding_index_result(result, *, vector_store: str) -> str:
    return (
        "Embedding index complete: "
        f"documents_embedded={result.documents_embedded}; "
        f"candidate_documents={result.candidate_documents}; "
        f"skipped_existing={result.skipped_existing}; "
        f"model_id={result.model_id}; "
        f"dimensions={result.dimensions}; "
        f"sources={','.join(result.source_tables) or 'all'}; "
        f"vector_store={vector_store}"
    )


def _format_photo_annotation_result(result) -> str:
    prefix = "Photo annotation dry-run complete" if result.dry_run else "Photo annotation complete"
    would = f"would_annotate={result.would_annotate}; " if result.dry_run else ""
    preprocessed = f"preprocessed={result.preprocessed}; "
    return (
        f"{prefix}: "
        f"selected={result.selected}; "
        f"{would}"
        f"annotated={result.annotated}; "
        f"skipped_already_annotated={result.skipped_already_annotated}; "
        f"skipped_missing_file={result.skipped_missing_file}; "
        f"{preprocessed}"
        f"errors={result.errors}; "
        f"model_id={result.model_id}"
    )


def _format_photo_annotation_error_summary(result) -> str:
    lines = [
        "Photo annotation diagnostics:",
        f"selected={result.selected}; annotated={result.annotated}; errors={result.errors}",
        f"model_id={result.model_id}",
        f"endpoint={result.endpoint_url or '<not configured>'}",
    ]
    if not result.error_details:
        lines.append("top_error_classes=none")
        return "\n".join(lines)

    top_classes = ", ".join(
        f"{error_class}:{count}"
        for error_class, count in result.top_error_classes()
    )
    failed_ids = ", ".join(
        str(detail.media_item_id)
        for detail in result.error_details[:10]
    )
    lines.append(f"top_error_classes={top_classes}")
    lines.append(f"failed_media_item_ids={failed_ids}")
    lines.append("examples:")
    for detail in result.error_details[:3]:
        lines.append(
            "  "
            f"media_item_id={detail.media_item_id}; "
            f"class={detail.error_class}; "
            f"message={detail.message}"
        )
        context_parts = []
        if detail.image_format:
            context_parts.append(f"image_format={detail.image_format}")
        if detail.dimensions:
            context_parts.append(f"dimensions={detail.dimensions}")
        if detail.preprocessing_succeeded is not None:
            context_parts.append(f"preprocessing_succeeded={detail.preprocessing_succeeded}")
        if context_parts:
            lines.append("  context=" + "; ".join(context_parts))
        if detail.stack_summary:
            lines.append(f"  stack={detail.stack_summary}")
    return "\n".join(lines)


def _format_text_annotation_result(result) -> str:
    return (
        "Text annotation complete: "
        f"source={result.source}; "
        f"selected={result.selected}; "
        f"annotated={result.annotated}; "
        f"skipped_empty={result.skipped_empty}; "
        f"skipped_already_annotated={result.skipped_already_annotated}; "
        f"errors={result.errors}; "
        f"model_id={result.model_id}"
    )


def _build_embedding_model(args: argparse.Namespace):
    selected_model = getattr(args, "model", None)
    if selected_model is not None:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        return build_semantic_embedding_model(
            selected_model,
            config=config,
            dimensions=args.dimensions,
            device=args.device,
        )
    if args.model_backend == "fake":
        return FakeEmbeddingModel()
    if args.model_backend == "hash":
        return HashEmbeddingModel(dimensions=args.dimensions)

    model_path = args.model_path
    model_id = None
    if model_path is None:
        config = load_config(config_dir=args.config_dir, paths_config=args.config)
        model_spec = config.model_registry.get(args.model_key)
        if model_spec is None:
            raise ValueError("configured embedding model key was not found")
        model_path = model_spec.resolved_path
        model_id = model_spec.model_id
    return SentenceTransformersEmbeddingModel(
        model_path,
        model_id=model_id,
        device=args.device,
    )


def _build_retrieval_embedding_model(args: argparse.Namespace):
    config = load_config(config_dir=args.config_dir, paths_config=args.config)
    return build_semantic_embedding_model(args.semantic_model, config=config)


def _build_retrieval_reranker(args: argparse.Namespace, config):
    return build_evidence_reranker(getattr(args, "reranker", "none"), config=config)


def _semantic_model_id_for_diagnostics(semantic_model: str) -> str | None:
    normalized = normalize_semantic_model_name(semantic_model)
    if normalized == "none":
        return None
    if normalized == "hash":
        return "hash-embedding-v1"
    if normalized == "fake":
        return "fake-embedding-v1"
    return normalized


def _resolve_semantic_model_arg(args: argparse.Namespace) -> str:
    selected = getattr(args, "semantic_model_choice", None)
    if selected is not None:
        return normalize_semantic_model_name(selected)
    return normalize_semantic_model_name(getattr(args, "semantic_model", "none"))


def _build_privacy_guard(_config) -> PrivacyGuard:
    return PrivacyGuard(
        PrivacyGuardPolicy(
            redact_names=True,
            redact_gps_precision=True,
            block_private_logs=True,
            gps_decimal_places=2,
        ),
    )


def _build_leader_agent(args: argparse.Namespace) -> LeaderAgent:
    if args.client == "fake":
        return LeaderAgent(FakeLeaderChatModelClient(), model_id=args.model_key)

    config = load_config(config_dir=args.config_dir, paths_config=args.config)
    model_spec = config.model_registry.get(args.model_key)
    if model_spec is None:
        raise ValueError("configured leader model key was not found")
    endpoint = endpoint_from_model_spec(model_spec)
    if endpoint is None:
        raise ValueError("configured leader model endpoint_url is missing")
    chat_client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=model_spec.model_id,
        timeout_seconds=endpoint.timeout_seconds,
        retries=endpoint.retries,
        allow_remote=False,
    )
    return LeaderAgent(chat_client, model_id=model_spec.model_id)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _build_vision_client(args: argparse.Namespace, *, preflight: bool = True):
    if args.client == "fake":
        return (
            FakeVisionModelClient(response_text="fake photo annotation", model=args.model_key),
            args.model_key,
            None,
        )

    config = load_config(config_dir=args.config_dir, paths_config=args.config)
    endpoint = _endpoint_for_model_key(config, args.model_key)
    if preflight:
        preflight_result = preflight_vision_endpoint(
            endpoint,
            allow_remote=args.allow_remote,
        )
        served_model_name = preflight_result.served_model_name
    else:
        served_model_name = endpoint.served_model_name or endpoint.model_id
    timeout_seconds = (
        args.timeout_seconds
        if args.timeout_seconds is not None
        else endpoint.request_timeout_seconds or DEFAULT_VISION_ANNOTATION_TIMEOUT_SECONDS
    )
    client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=served_model_name,
        timeout_seconds=timeout_seconds,
        retries=endpoint.retries,
        allow_remote=args.allow_remote,
    )
    return client, served_model_name, endpoint.base_url


def _endpoint_for_model_key(config, model_key: str):
    model_spec = config.model_registry.get(model_key)
    if model_spec is None:
        raise ValueError("configured model key was not found")
    endpoint = endpoint_from_model_spec(model_spec)
    if endpoint is None:
        raise ValueError("configured model endpoint_url is missing")
    return endpoint


def _safe_cli_error_message(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if "/" in message or "\\" in message:
        return exc.__class__.__name__
    return message[:180]


def _build_text_understanding_client(args: argparse.Namespace):
    if args.client == "fake":
        return FakeTextUnderstandingClient(model=args.model_key), args.model_key

    config = load_config(config_dir=args.config_dir, paths_config=args.config)
    model_spec = config.model_registry.get(args.model_key)
    if model_spec is None:
        raise ValueError("configured Japanese text model key was not found")
    endpoint = endpoint_from_model_spec(model_spec)
    if endpoint is None:
        raise ValueError("configured Japanese text model endpoint_url is missing")
    chat_client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=model_spec.model_id,
        timeout_seconds=endpoint.timeout_seconds,
        retries=endpoint.retries,
        allow_remote=args.allow_remote,
    )
    return ChatTextUnderstandingClient(chat_client, model=model_spec.model_id), model_spec.model_id


def _build_vector_store(args: argparse.Namespace, *, vector_size: int | None):
    if args.vector_store == "memory":
        return None
    return QdrantVectorStore(
        collection_name=args.qdrant_collection,
        url=args.qdrant_url,
        vector_size=vector_size if vector_size and vector_size > 0 else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())

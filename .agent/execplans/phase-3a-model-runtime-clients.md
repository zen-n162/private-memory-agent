# ExecPlan: Phase 3-A Model Runtime Client Abstractions

## Goal

Create local model runtime client abstractions so application logic can call chat, vision, and reranker capabilities without depending directly on llama.cpp, vLLM, Ollama, Transformers, or a specific HTTP server implementation.

## Non-goals

- Do not load real model weights.
- Do not start model servers.
- Do not require llama.cpp, vLLM, Ollama, Transformers, GPU, Docker, network, or private data in unit tests.
- Do not implement agent orchestration, prompt planning, retrieval-augmented answering, or model selection policy.
- Do not send requests to non-local or cloud endpoints by default.

## Current State

The repository has:

- A typed model registry that validates local model directories without loading models.
- `ModelSpec.extra`, which can preserve optional runtime settings from `configs/models.example.yaml`.
- Embedding model and vector store abstractions for retrieval.
- CLI commands under `pma models list`, `pma index`, `pma search`, and `pma ingest`.

The repository does not yet have runtime clients for chat, vision, or reranking, nor a command that checks configured local model endpoints.

## Proposed Design

Add a new `private_memory_agent.runtime` package with:

- Protocols: `ChatModelClient`, `VisionModelClient`, `RerankerClient`.
- Dataclass request/response schemas for chat, vision, and reranking.
- Fake clients for deterministic tests.
- `OpenAICompatibleHTTPClient` for local OpenAI-compatible `/v1/chat/completions` and `/v1/models` style endpoints.
- A `ping_configured_model_endpoints` helper that reads endpoint metadata from `ModelSpec.extra`.

Endpoint metadata remains optional config on existing model entries:

- `endpoint_url`
- `api_format`
- `timeout_seconds`
- `retries`

CLI:

- Add `pma models ping`.
- It loads configured models, filters models with `endpoint_url`, pings `/models` when available, and returns privacy-safe status JSON or a table.

## Data Contracts

Chat:

- `ChatMessage(role, content, name=None)`
- `ChatRequest(messages, model=None, temperature=None, max_tokens=None, stop=())`
- `ChatResponse(text, model=None, usage={}, raw={})`

Vision:

- `VisionInput(kind, data, mime_type=None)`
- `VisionRequest(prompt, images=(), model=None, temperature=None, max_tokens=None)`
- `VisionResponse(text, model=None, usage={}, raw={})`

Reranking:

- `RerankDocument(document_id, text, metadata={})`
- `RerankRequest(query, documents, top_k=None)`
- `RerankResult(document_id, score, rank, metadata={})`
- `RerankResponse(results, model=None, raw={})`

Endpoint ping:

- `ModelEndpoint(model_id, provider, role, base_url, api_format, timeout_seconds, retries)`
- `ModelPingResult(model_id, provider, role, ok, status_code=None, error=None, latency_ms=None)`

## Files to Change

- `.agent/execplans/phase-3a-model-runtime-clients.md`
- `configs/models.example.yaml`
- `docs/MODEL_RUNTIME.md`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/runtime/__init__.py`
- `src/private_memory_agent/runtime/clients.py`
- `tests/test_runtime_clients.py`
- `tests/test_cli.py` if CLI coverage needs extension

## Implementation Steps

1. Add runtime schemas and protocol interfaces.
2. Add fake chat, vision, and reranker clients.
3. Add a stdlib-based OpenAI-compatible HTTP client with timeout, retry, JSON parsing, and structured errors.
4. Add endpoint extraction and ping helpers using `ModelSpec.extra`.
5. Add `pma models ping` with table and JSON output.
6. Add generic endpoint metadata to example model config.
7. Add tests with monkeypatched HTTP transport or a local fake HTTP server.
8. Run `pytest -q`.

## Tests and Verification

Run:

- `pytest -q`

Tests must cover:

- Fake client responses.
- OpenAI-compatible request payload and response parsing.
- Retry behavior on transient failures.
- Timeout/error handling surfaced as structured runtime errors.
- `pma models ping` against a fake local HTTP server or monkeypatched opener.
- `pma models ping` when no endpoints are configured.

## Privacy and Security

No private data is used in tests. Ping checks never include user prompts, notes, LINE messages, image metadata, or source paths. CLI output includes model IDs, providers, roles, status, and endpoint base URLs from config only. No API keys are printed.

The HTTP client is intended for local endpoints. It validates URLs and rejects non-local endpoints unless explicitly allowed by `allow_remote=True`.

## Performance and Hardware

Default tests are CPU-only and do not require model servers. Runtime calls use configurable timeouts and bounded retries. No GPU or VRAM assumptions are required for this phase.

## Rollback

Remove the runtime package, CLI `models ping` subcommand, config endpoint metadata, tests, and docs additions. Existing ingestion, storage, retrieval, and model registry behavior remain independent.

## Open Questions

None blocking.

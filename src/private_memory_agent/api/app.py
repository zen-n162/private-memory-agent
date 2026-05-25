"""Local-only FastAPI app factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from private_memory_agent import __version__
from private_memory_agent.agent import (
    AnswerValidationError,
    FakeLeaderChatModelClient,
    LeaderAgent,
    PrivacyGuard,
    PrivacyGuardPolicy,
    run_query_flow,
)
from private_memory_agent.api.schemas import (
    ChatQueryRequest,
    ChatQueryResponse,
    EntitiesResponse,
    EntityType,
    EventsResponse,
    HealthResponse,
    IngestLineRequest,
    IngestLineResponse,
    IngestNotesRequest,
    IngestNotesResponse,
    IngestPhotosRequest,
    IngestPhotosResponse,
    QueryRequest,
    QueryResponse,
    SystemStatusResponse,
)
from private_memory_agent.api.console import (
    ChatConsoleOptions,
    build_system_status,
    run_chat_console_query,
)
from private_memory_agent.api.ui import agent_console_html
from private_memory_agent.config import load_config
from private_memory_agent.entities import list_entities
from private_memory_agent.ingestion import ingest_line_exports, ingest_notes, ingest_photos
from private_memory_agent.retrieval import FakeEmbeddingModel, HashEmbeddingModel, RetrievalFilters
from private_memory_agent.runtime import OpenAICompatibleHTTPClient, endpoint_from_model_spec
from private_memory_agent.timeline import list_events

DEFAULT_API_DB_PATH = Path("data/local/private_memory_agent.sqlite3")


def create_app(
    *,
    db_path: Path | str = DEFAULT_API_DB_PATH,
    config_dir: Path | str | None = None,
    paths_config: Path | str | None = None,
) -> FastAPI:
    """Create the localhost FastAPI app."""

    app = FastAPI(
        title="Private Memory Agent Local API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.db_path = Path(db_path).expanduser()
    app.state.config_dir = None if config_dir is None else Path(config_dir).expanduser()
    app.state.paths_config = None if paths_config is None else Path(paths_config).expanduser()

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/ui")

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def local_ui() -> HTMLResponse:
        return HTMLResponse(agent_console_html())

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            service="private-memory-agent",
            version=__version__,
            localhost_only=True,
        )

    @app.post("/api/query", response_model=QueryResponse)
    def query_memory(payload: QueryRequest, request: Request) -> dict[str, Any]:
        config = _load_request_config(request)
        redact = not (payload.show_private and config.app.log_private_data)
        try:
            result = run_query_flow(
                payload.question,
                db_path=_request_db_path(request, payload.db_path),
                leader_agent=_build_leader_agent(payload, request),
                embedding_model=_embedding_model(payload.semantic_model),
                filters=RetrievalFilters(
                    sources=tuple(payload.sources),
                    since=payload.since,
                    until=payload.until,
                ),
                limit=payload.limit,
                redact_for_display=redact,
                privacy_guard=_privacy_guard(),
            )
        except (AnswerValidationError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=_safe_error(str(exc))) from exc
        return result.to_dict()

    @app.post("/api/chat/query", response_model=ChatQueryResponse)
    def chat_query(payload: ChatQueryRequest, request: Request) -> dict[str, Any]:
        try:
            return run_chat_console_query(
                ChatConsoleOptions(
                    question=payload.question,
                    config_dir=request.app.state.config_dir,
                    paths_config=request.app.state.paths_config,
                    db_path=_request_db_path(request, payload.db_path),
                    mode=payload.mode,
                    sources=tuple(payload.sources),
                    leader_plan=payload.leader_plan,
                    leader_rerank=payload.leader_rerank,
                    semantic=payload.semantic,
                    semantic_model=payload.semantic_model,
                    semantic_top_k=payload.semantic_top_k,
                    semantic_weight=payload.semantic_weight,
                    reranker=payload.reranker,
                    rerank_top_k=payload.rerank_top_k,
                    retrieval_repair=payload.retrieval_repair,
                    strict_relevance=payload.strict_relevance,
                    minimum_relevance_score=payload.minimum_relevance_score,
                    show_answer=payload.show_answer,
                    show_snippets=payload.show_snippets,
                    snippet_chars=payload.snippet_chars,
                    limit=payload.limit,
                    timeout_seconds=payload.timeout_seconds,
                    max_tokens=payload.max_tokens,
                    model_key=payload.model_key,
                    embedding_device=payload.embedding_device,
                    allow_remote=False,
                ),
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=_safe_error(str(exc))) from exc

    @app.get("/api/system/status", response_model=SystemStatusResponse)
    def system_status(request: Request, db_path: Path | None = None) -> dict[str, Any]:
        try:
            return build_system_status(
                config_dir=request.app.state.config_dir,
                paths_config=request.app.state.paths_config,
                db_path=_request_db_path(request, db_path),
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=_safe_error(str(exc))) from exc

    @app.post("/api/ingest/photos", response_model=IngestPhotosResponse)
    def ingest_photo_metadata(payload: IngestPhotosRequest, request: Request) -> dict[str, Any]:
        try:
            result = ingest_photos(
                payload.path,
                db_path=_request_db_path(request, payload.db_path),
                dry_run=payload.dry_run,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="photo source is unavailable") from exc
        return result.__dict__

    @app.post("/api/ingest/line", response_model=IngestLineResponse)
    def ingest_line_exports_api(payload: IngestLineRequest, request: Request) -> dict[str, Any]:
        try:
            result = ingest_line_exports(
                payload.path,
                db_path=_request_db_path(request, payload.db_path),
                dry_run=payload.dry_run,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="LINE source is unavailable") from exc
        return result.__dict__

    @app.post("/api/ingest/notes", response_model=IngestNotesResponse)
    def ingest_notes_api(payload: IngestNotesRequest, request: Request) -> dict[str, Any]:
        try:
            result = ingest_notes(
                payload.path,
                db_path=_request_db_path(request, payload.db_path),
                dry_run=payload.dry_run,
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="notes source is unavailable") from exc
        return result.__dict__

    @app.get("/api/events", response_model=EventsResponse)
    def events_api(
        request: Request,
        db_path: Path | None = None,
        limit: int = Query(default=50, gt=0, le=500),
        show_private: bool = False,
    ) -> dict[str, Any]:
        config = _load_request_config(request)
        redact = not (show_private and config.app.log_private_data)
        try:
            events = list_events(
                _request_db_path(request, db_path),
                limit=limit,
                redact_private=redact,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_safe_error(str(exc))) from exc
        return {"events": events, "redacted": redact}

    @app.get("/api/entities", response_model=EntitiesResponse)
    def entities_api(
        request: Request,
        db_path: Path | None = None,
        entity_type: EntityType | None = None,
        limit: int = Query(default=100, gt=0, le=500),
        show_private: bool = False,
    ) -> dict[str, Any]:
        config = _load_request_config(request)
        redact = not (show_private and config.app.log_private_data)
        try:
            entities = list_entities(
                _request_db_path(request, db_path),
                entity_type=entity_type,
                limit=limit,
                redact_private=redact,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_safe_error(str(exc))) from exc
        return {"entities": entities, "redacted": redact}

    return app


def _request_db_path(request: Request, db_path: Path | None) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser()
    return Path(request.app.state.db_path).expanduser()


def _load_request_config(request: Request):
    return load_config(
        config_dir=request.app.state.config_dir,
        paths_config=request.app.state.paths_config,
    )


def _embedding_model(name: str):
    if name == "none":
        return None
    if name == "fake":
        return FakeEmbeddingModel()
    return HashEmbeddingModel()


def _build_leader_agent(payload: QueryRequest, request: Request) -> LeaderAgent:
    if payload.client == "fake":
        return LeaderAgent(FakeLeaderChatModelClient(), model_id=payload.model_key)
    config = _load_request_config(request)
    model_spec = config.model_registry.get(payload.model_key)
    if model_spec is None:
        raise ValueError("configured leader model key was not found")
    endpoint = endpoint_from_model_spec(model_spec)
    if endpoint is None:
        raise ValueError("configured leader model endpoint_url is missing")
    client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=model_spec.model_id,
        timeout_seconds=endpoint.timeout_seconds,
        retries=endpoint.retries,
        allow_remote=False,
    )
    return LeaderAgent(client, model_id=model_spec.model_id)


def _privacy_guard() -> PrivacyGuard:
    return PrivacyGuard(
        PrivacyGuardPolicy(
            redact_names=True,
            redact_gps_precision=True,
            block_private_logs=True,
        ),
    )


def _safe_error(message: str) -> str:
    lowered = message.lower()
    if "path" in lowered or "/" in message:
        return "request could not be completed"
    return message


def _local_ui_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Private Memory Agent</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f8f7f3;
      --ink: #17201a;
      --muted: #5c665f;
      --line: #d8ddd5;
      --panel: #ffffff;
      --accent: #226b57;
      --accent-strong: #174f40;
      --warn-bg: #fff3cd;
      --warn-border: #d7a940;
      --error: #a93124;
    }
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family:
        Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.5;
    }
    main {
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 40px;
    }
    header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 0 18px;
    }
    h1 {
      margin: 0;
      font-size: 1.5rem;
      font-weight: 700;
      letter-spacing: 0;
    }
    .warning {
      border: 1px solid var(--warn-border);
      background: var(--warn-bg);
      color: #4f3d08;
      border-radius: 8px;
      padding: 10px 12px;
      max-width: 520px;
      font-size: 0.92rem;
    }
    form {
      display: grid;
      gap: 14px;
      margin: 0 0 18px;
      padding: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    label {
      display: grid;
      gap: 6px;
      font-weight: 650;
    }
    input[type="text"],
    select,
    textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      color: var(--ink);
      font: inherit;
      background: #fff;
    }
    textarea {
      min-height: 92px;
      resize: vertical;
    }
    .model-row {
      display: grid;
      grid-template-columns: minmax(180px, 0.5fr) minmax(180px, 1fr);
      gap: 12px;
    }
    fieldset {
      display: flex;
      flex-wrap: wrap;
      gap: 12px 16px;
      margin: 0;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    legend {
      padding: 0 6px;
      color: var(--muted);
      font-size: 0.9rem;
      font-weight: 650;
    }
    .inline {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      font-weight: 500;
    }
    .actions {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    button {
      min-height: 40px;
      border: 0;
      border-radius: 8px;
      padding: 0 16px;
      color: #fff;
      background: var(--accent);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover {
      background: var(--accent-strong);
    }
    button:disabled {
      cursor: wait;
      opacity: 0.7;
    }
    .status {
      color: var(--muted);
      font-size: 0.92rem;
    }
    .error {
      color: var(--error);
    }
    .results {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 0.78fr);
      gap: 18px;
      align-items: start;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      min-width: 0;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 1rem;
      letter-spacing: 0;
    }
    .answer-grid {
      display: grid;
      gap: 12px;
    }
    .metric-row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 0.92rem;
    }
    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 9px;
      background: #fbfcfa;
    }
    ul {
      margin: 0;
      padding-left: 19px;
    }
    .evidence-list {
      display: grid;
      gap: 10px;
    }
    .evidence-item {
      border-top: 1px solid var(--line);
      padding-top: 10px;
    }
    .evidence-item:first-child {
      border-top: 0;
      padding-top: 0;
    }
    .evidence-meta {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 0.85rem;
      margin-bottom: 6px;
    }
    .snippet {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    @media (max-width: 760px) {
      header,
      .results {
        display: grid;
        grid-template-columns: 1fr;
      }
      main {
        width: min(100vw - 24px, 1120px);
        padding-top: 14px;
      }
      .model-row {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Private Memory Agent</h1>
      <div class="warning">
        Local-only UI. Keep this service on 127.0.0.1 and avoid public proxies.
      </div>
    </header>
    <form id="query-form">
      <label>
        Question
        <textarea id="question" name="question" autocomplete="off" required></textarea>
      </label>
      <fieldset>
        <legend>Sources</legend>
        <label class="inline"><input type="checkbox" name="source" value="photos"> Photos</label>
        <label class="inline"><input type="checkbox" name="source" value="line"> LINE</label>
        <label class="inline"><input type="checkbox" name="source" value="notes"> Notes</label>
      </fieldset>
      <div class="model-row">
        <label>
          Client
          <select id="client">
            <option value="fake">Fake local client</option>
            <option value="openai-compatible">OpenAI-compatible local endpoint</option>
          </select>
        </label>
        <label>
          Model key
          <input id="model-key" type="text" value="leader" autocomplete="off">
        </label>
      </div>
      <label class="inline">
        <input type="checkbox" id="show-private">
        Request private display when config permits it
      </label>
      <div class="actions">
        <button id="submit" type="submit">Query</button>
        <span id="status" class="status" role="status" aria-live="polite"></span>
      </div>
    </form>
    <div class="results">
      <section aria-labelledby="answer-heading">
        <h2 id="answer-heading">Answer</h2>
        <div id="answer" class="answer-grid">
          <p class="status">No query yet.</p>
        </div>
      </section>
      <section aria-labelledby="evidence-heading">
        <h2 id="evidence-heading">Evidence</h2>
        <div id="evidence" class="evidence-list">
          <p class="status">Safe snippets will appear here.</p>
        </div>
      </section>
    </div>
  </main>
  <script>
    const form = document.querySelector("#query-form");
    const question = document.querySelector("#question");
    const clientMode = document.querySelector("#client");
    const modelKey = document.querySelector("#model-key");
    const showPrivate = document.querySelector("#show-private");
    const submit = document.querySelector("#submit");
    const statusNode = document.querySelector("#status");
    const answerNode = document.querySelector("#answer");
    const evidenceNode = document.querySelector("#evidence");

    function selectedSources() {
      return Array.from(document.querySelectorAll("input[name='source']:checked"))
        .map((node) => node.value);
    }

    function clearNode(node) {
      while (node.firstChild) {
        node.removeChild(node.firstChild);
      }
    }

    function textElement(tag, text, className) {
      const node = document.createElement(tag);
      if (className) {
        node.className = className;
      }
      node.textContent = text;
      return node;
    }

    function renderAnswer(payload) {
      clearNode(answerNode);
      const answer = payload.answer || {};
      answerNode.appendChild(textElement("p", answer.conclusion || "No conclusion returned."));

      const metrics = document.createElement("div");
      metrics.className = "metric-row";
      metrics.appendChild(textElement("span", `Confidence: ${answer.confidence ?? "n/a"}`, "pill"));
      const redactionLabel = payload.redacted ? "yes" : "no";
      metrics.appendChild(textElement("span", `Redacted: ${redactionLabel}`, "pill"));
      answerNode.appendChild(metrics);

      const usedSources = answer.used_sources || [];
      const used = textElement(
        "p",
        `Used sources: ${usedSources.length ? usedSources.join(", ") : "none"}`,
        "status",
      );
      answerNode.appendChild(used);

      const unknowns = answer.unknowns || [];
      if (unknowns.length) {
        answerNode.appendChild(textElement("h2", "Unknowns"));
        const list = document.createElement("ul");
        unknowns.forEach((item) => {
          list.appendChild(textElement("li", item));
        });
        answerNode.appendChild(list);
      }
    }

    function renderEvidence(payload) {
      clearNode(evidenceNode);
      const items = payload.evidence || [];
      if (!items.length) {
        evidenceNode.appendChild(textElement("p", "No evidence returned.", "status"));
        return;
      }
      items.forEach((item) => {
        const row = document.createElement("article");
        row.className = "evidence-item";
        const meta = document.createElement("div");
        meta.className = "evidence-meta";
        meta.appendChild(textElement("span", item.evidence_id || "unknown", "pill"));
        meta.appendChild(textElement("span", item.source_kind || "source", "pill"));
        meta.appendChild(textElement("span", `Score: ${item.score ?? "n/a"}`, "pill"));
        row.appendChild(meta);
        if (item.title) {
          row.appendChild(textElement("strong", item.title));
        }
        row.appendChild(textElement("div", item.snippet || "[no snippet]", "snippet"));
        evidenceNode.appendChild(row);
      });
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      statusNode.className = "status";
      statusNode.textContent = "Querying local API...";
      try {
        const response = await fetch("/api/query", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            question: question.value,
            sources: selectedSources(),
            limit: 8,
            semantic_model: "none",
            client: clientMode.value,
            model_key: modelKey.value || "leader",
            show_private: showPrivate.checked,
          }),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "query failed");
        }
        renderAnswer(payload);
        renderEvidence(payload);
        statusNode.textContent = "Done";
      } catch (error) {
        statusNode.className = "status error";
        statusNode.textContent = error instanceof Error ? error.message : "query failed";
      } finally {
        submit.disabled = false;
      }
    });
  </script>
</body>
</html>
"""

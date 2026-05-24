"""Runtime client abstractions for local model servers."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from private_memory_agent.models import ModelRegistry, ModelSpec


class ModelRuntimeError(RuntimeError):
    """Raised when a local model runtime request fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retriable = retriable


@dataclass(frozen=True)
class ChatMessage:
    """A single chat message."""

    role: str
    content: str | list[dict[str, Any]]
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass(frozen=True)
class ChatRequest:
    """Chat completion request."""

    messages: tuple[ChatMessage, ...]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stop: tuple[str, ...] = ()
    response_format: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChatResponse:
    """Chat completion response."""

    text: str
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextUnderstandingRequest:
    """Request for structured text extraction."""

    text: str
    source_type: str
    source_id: int | str | None = None
    model: str | None = None
    language: str = "ja"
    schema_name: str = "japanese_text_understanding_v1"


@dataclass(frozen=True)
class TextUnderstandingResponse:
    """Structured text extraction response envelope."""

    json_text: str
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisionInput:
    """Vision input reference.

    `kind` is intentionally small for now: use `image_url` for local server
    accessible URLs and `base64` for already-encoded image data.
    """

    kind: str
    data: str
    mime_type: str | None = None


@dataclass(frozen=True)
class VisionRequest:
    """Vision model request."""

    prompt: str
    images: tuple[VisionInput, ...] = ()
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class VisionResponse:
    """Vision model response."""

    text: str
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RerankDocument:
    """A candidate document for reranking."""

    document_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RerankRequest:
    """Reranking request."""

    query: str
    documents: tuple[RerankDocument, ...]
    top_k: int | None = None


@dataclass(frozen=True)
class RerankResult:
    """A scored reranking result."""

    document_id: str
    score: float
    rank: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RerankResponse:
    """Reranking response."""

    results: tuple[RerankResult, ...]
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ChatModelClient(Protocol):
    """Interface for local chat model clients."""

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Return a chat completion."""


class TextUnderstandingClient(Protocol):
    """Interface for Japanese text understanding clients."""

    def understand(self, request: TextUnderstandingRequest) -> TextUnderstandingResponse:
        """Return strict JSON extraction text."""


class VisionModelClient(Protocol):
    """Interface for local vision model clients."""

    def analyze(self, request: VisionRequest) -> VisionResponse:
        """Return a vision-language response."""


class RerankerClient(Protocol):
    """Interface for local reranker clients."""

    def rerank(self, request: RerankRequest) -> RerankResponse:
        """Return reranked documents."""


class FakeChatModelClient:
    """Deterministic chat client for tests."""

    def __init__(
        self,
        *,
        response_text: str = "fake chat response",
        model: str = "fake-chat",
    ) -> None:
        self.response_text = response_text
        self.model = model

    def complete(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            text=self.response_text,
            model=request.model or self.model,
            usage={"prompt_messages": len(request.messages)},
        )


class FakeTextUnderstandingClient:
    """Deterministic text understanding client for tests."""

    def __init__(
        self,
        *,
        payload: dict[str, Any] | None = None,
        json_text: str | None = None,
        model: str = "fake-text-understanding",
    ) -> None:
        self.payload = payload
        self.json_text = json_text
        self.model = model
        self.requests: list[TextUnderstandingRequest] = []

    def understand(self, request: TextUnderstandingRequest) -> TextUnderstandingResponse:
        self.requests.append(request)
        json_text = self.json_text
        if json_text is None:
            json_text = json.dumps(
                self.payload if self.payload is not None else _default_text_understanding_payload(),
                ensure_ascii=False,
                sort_keys=True,
            )
        return TextUnderstandingResponse(
            json_text=json_text,
            model=request.model or self.model,
        )


class ChatTextUnderstandingClient:
    """Text understanding adapter that asks a chat client for strict JSON."""

    def __init__(
        self,
        chat_client: ChatModelClient,
        *,
        model: str | None = None,
        max_tokens: int | None = 1024,
    ) -> None:
        self.chat_client = chat_client
        self.model = model
        self.max_tokens = max_tokens

    def understand(self, request: TextUnderstandingRequest) -> TextUnderstandingResponse:
        response = self.chat_client.complete(
            ChatRequest(
                messages=(
                    ChatMessage(role="system", content=_TEXT_UNDERSTANDING_SYSTEM_PROMPT),
                    ChatMessage(
                        role="user",
                        content=_text_understanding_user_prompt(request),
                    ),
                ),
                model=request.model or self.model,
                temperature=0.0,
                max_tokens=self.max_tokens,
            ),
        )
        return TextUnderstandingResponse(
            json_text=response.text,
            model=response.model or request.model or self.model,
            raw=response.raw,
        )


class FakeVisionModelClient:
    """Deterministic vision client for tests."""

    def __init__(
        self,
        *,
        response_text: str = "fake vision response",
        model: str = "fake-vision",
    ) -> None:
        self.response_text = response_text
        self.model = model

    def analyze(self, request: VisionRequest) -> VisionResponse:
        return VisionResponse(
            text=self.response_text,
            model=request.model or self.model,
            usage={"images": len(request.images)},
        )


class FakeRerankerClient:
    """Simple deterministic reranker for tests."""

    def __init__(self, *, model: str = "fake-reranker") -> None:
        self.model = model

    def rerank(self, request: RerankRequest) -> RerankResponse:
        query_tokens = set(_tokenize(request.query))
        scored: list[RerankResult] = []
        for document in request.documents:
            document_tokens = set(_tokenize(document.text))
            score = float(len(query_tokens & document_tokens))
            scored.append(
                RerankResult(
                    document_id=document.document_id,
                    score=score,
                    rank=0,
                    metadata=document.metadata,
                ),
            )
        scored.sort(key=lambda result: (-result.score, result.document_id))
        limited = scored[: request.top_k] if request.top_k is not None else scored
        ranked = tuple(
            RerankResult(
                document_id=result.document_id,
                score=result.score,
                rank=index + 1,
                metadata=result.metadata,
            )
            for index, result in enumerate(limited)
        )
        return RerankResponse(results=ranked, model=self.model)


class HTTPResponseLike(Protocol):
    """Minimal response shape returned by runtime HTTP transports."""

    def __enter__(self) -> "HTTPResponseLike":
        """Enter response context manager."""

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> object:
        """Exit response context manager."""

    def read(self) -> bytes:
        """Read the response body."""


class HTTPTransport(Protocol):
    """Transport function used by PMA runtime HTTP clients.

    The timeout is a separate argument in PMA's abstraction. Implementations
    that call `urllib.request.urlopen` must pass it as the `timeout=` keyword,
    because urlopen's second positional argument is request body data.
    """

    def __call__(self, request: Request, timeout_seconds: float) -> HTTPResponseLike:
        """Send an HTTP request with a timeout in seconds."""


def default_http_transport(request: Request, timeout_seconds: float) -> HTTPResponseLike:
    """Send a request with urllib while preserving PMA's transport contract."""

    return urlopen(request, timeout=timeout_seconds)


_SYNTHETIC_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
DEFAULT_VISION_SMOKE_TIMEOUT_SECONDS = 120.0
DEFAULT_CHAT_SMOKE_TIMEOUT_SECONDS = 120.0
_MISSING_MULTIMODAL_CAPABILITY_WARNING = (
    "served model did not explicitly report multimodal capability; "
    "continuing because capability metadata is absent"
)

_TEXT_UNDERSTANDING_SYSTEM_PROMPT = """\
You extract structured information from Japanese private memory text.
Return only valid JSON with exactly these keys:
entities, topics, dates, action_items, event_hints, summary, confidence.
Do not add markdown, comments, or extra text.
"""


class OpenAICompatibleHTTPClient:
    """HTTP client for local OpenAI-compatible model endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str | None = None,
        timeout_seconds: float = 10.0,
        retries: int = 0,
        retry_backoff_seconds: float = 0.1,
        api_key: str | None = None,
        allow_remote: bool = False,
        transport: HTTPTransport | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        _validate_local_endpoint(self.base_url, allow_remote=allow_remote)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if retries < 0:
            raise ValueError("retries must be non-negative")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.api_key = api_key
        self._transport = transport or default_http_transport

    def complete(self, request: ChatRequest) -> ChatResponse:
        payload: dict[str, Any] = {
            "messages": [message.to_dict() for message in request.messages],
            "stream": False,
        }
        model = request.model or self.model
        if model:
            payload["model"] = model
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.stop:
            payload["stop"] = list(request.stop)
        if request.response_format is not None:
            payload["response_format"] = dict(request.response_format)

        raw = self._request_json("POST", "/chat/completions", payload)
        return _chat_response_from_openai(raw)

    def analyze(self, request: VisionRequest) -> VisionResponse:
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        for image in request.images:
            content.append(_vision_input_to_content_part(image))
        chat_response = self.complete(
            ChatRequest(
                messages=(ChatMessage(role="user", content=content),),
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            ),
        )
        return VisionResponse(
            text=chat_response.text,
            model=chat_response.model,
            usage=chat_response.usage,
            raw=chat_response.raw,
        )

    def ping(self) -> dict[str, Any]:
        return self._request_json("GET", "/models", None)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        last_error: ModelRuntimeError | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._send_json(method, path, payload)
            except ModelRuntimeError as exc:
                last_error = exc
                if not exc.retriable or attempt >= self.retries:
                    raise
                if self.retry_backoff_seconds:
                    time.sleep(self.retry_backoff_seconds)
        if last_error is not None:
            raise last_error
        raise ModelRuntimeError("model endpoint request failed")

    def _send_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")

        try:
            response = self._transport(request, self.timeout_seconds)
            with response:
                status_code = int(getattr(response, "status", getattr(response, "code", 200)))
                raw_body = response.read()
        except HTTPError as exc:
            status_code = int(exc.code)
            raise ModelRuntimeError(
                f"HTTP {status_code} from model endpoint",
                status_code=status_code,
                retriable=_is_retriable_status(status_code),
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ModelRuntimeError("model endpoint request timed out", retriable=True) from exc
        except URLError as exc:
            raise ModelRuntimeError("model endpoint is unavailable", retriable=True) from exc
        except OSError as exc:
            raise ModelRuntimeError("model endpoint request failed", retriable=True) from exc
        except TypeError as exc:
            raise ModelRuntimeError("model endpoint transport failed") from exc

        if status_code >= 400:
            raise ModelRuntimeError(
                f"HTTP {status_code} from model endpoint",
                status_code=status_code,
                retriable=_is_retriable_status(status_code),
            )
        try:
            decoded = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelRuntimeError("model endpoint returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ModelRuntimeError("model endpoint returned a non-object JSON response")
        return decoded


@dataclass(frozen=True)
class ModelEndpoint:
    """Configured model endpoint metadata."""

    model_id: str
    provider: str
    role: str
    base_url: str
    api_format: str = "openai-compatible"
    timeout_seconds: float = 2.0
    retries: int = 0
    served_model_name: str | None = None
    request_timeout_seconds: float | None = None


@dataclass(frozen=True)
class _NormalizedModelRecord:
    """Normalized model metadata from OpenAI-compatible `/models` variants."""

    id: str
    name: str | None = None
    model: str | None = None
    capabilities: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def aliases(self) -> tuple[str, ...]:
        values = [self.id, self.name, self.model]
        return tuple(value for value in values if value)


@dataclass(frozen=True)
class ModelPingResult:
    """Result of checking a configured model endpoint."""

    model_id: str
    provider: str
    role: str
    endpoint_url: str
    ok: bool
    status_code: int | None = None
    latency_ms: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "role": self.role,
            "endpoint_url": self.endpoint_url,
            "ok": self.ok,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


@dataclass(frozen=True)
class ChatEndpointPreflightResult:
    """Safe preflight metadata for a local chat endpoint."""

    model_id: str
    served_model_name: str
    endpoint_url: str
    model_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "served_model_name": self.served_model_name,
            "endpoint_url": self.endpoint_url,
            "model_ids": list(self.model_ids),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class VisionEndpointPreflightResult:
    """Safe preflight metadata for a local vision endpoint."""

    model_id: str
    served_model_name: str
    endpoint_url: str
    model_ids: tuple[str, ...]
    multimodal: bool | None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "served_model_name": self.served_model_name,
            "endpoint_url": self.endpoint_url,
            "model_ids": list(self.model_ids),
            "multimodal": self.multimodal,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class VisionSmokeResult:
    """Result of a synthetic vision request."""

    ok: bool
    model_id: str
    served_model_name: str
    endpoint_url: str
    response_chars: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "model_id": self.model_id,
            "served_model_name": self.served_model_name,
            "endpoint_url": self.endpoint_url,
            "response_chars": self.response_chars,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ChatSmokeResult:
    """Result of a synthetic chat request."""

    ok: bool
    model_id: str
    served_model_name: str
    endpoint_url: str
    response_chars: int
    max_tokens: int
    timeout_seconds: float
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "model_id": self.model_id,
            "served_model_name": self.served_model_name,
            "endpoint_url": self.endpoint_url,
            "response_chars": self.response_chars,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class JSONSmokeResult:
    """Result of a synthetic JSON-structured chat request."""

    ok: bool
    model_id: str
    served_model_name: str
    endpoint_url: str
    response_chars: int
    json_extraction_succeeded: bool
    json_extraction_strategy: str
    max_tokens: int
    timeout_seconds: float
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "model_id": self.model_id,
            "served_model_name": self.served_model_name,
            "endpoint_url": self.endpoint_url,
            "response_chars": self.response_chars,
            "json_extraction_succeeded": self.json_extraction_succeeded,
            "json_extraction_strategy": self.json_extraction_strategy,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "warnings": list(self.warnings),
        }


def configured_model_endpoints(
    registry: ModelRegistry,
    *,
    include_disabled: bool = False,
) -> list[ModelEndpoint]:
    """Return endpoint metadata from model registry extras."""

    endpoints: list[ModelEndpoint] = []
    for spec in registry:
        if not include_disabled and not spec.enabled:
            continue
        endpoint = endpoint_from_model_spec(spec)
        if endpoint is not None:
            endpoints.append(endpoint)
    return endpoints


def endpoint_from_model_spec(spec: ModelSpec) -> ModelEndpoint | None:
    raw_url = spec.extra.get("endpoint_url") or spec.extra.get("base_url")
    if raw_url is None or str(raw_url).strip() == "":
        return None
    return ModelEndpoint(
        model_id=spec.model_id,
        provider=spec.provider,
        role=spec.role,
        base_url=str(raw_url),
        api_format=str(spec.extra.get("api_format") or "openai-compatible"),
        timeout_seconds=_float_extra(spec.extra.get("timeout_seconds"), default=2.0),
        retries=_int_extra(spec.extra.get("retries"), default=0),
        served_model_name=_optional_extra_string(spec.extra.get("served_model_name")),
        request_timeout_seconds=_optional_float_extra(spec.extra.get("request_timeout_seconds")),
    )


def ping_configured_model_endpoints(
    registry: ModelRegistry,
    *,
    include_disabled: bool = False,
    allow_remote: bool = False,
    transport: HTTPTransport | None = None,
) -> list[ModelPingResult]:
    """Ping configured local model endpoints without sending prompts."""

    results: list[ModelPingResult] = []
    for endpoint in configured_model_endpoints(registry, include_disabled=include_disabled):
        results.append(
            ping_model_endpoint(endpoint, allow_remote=allow_remote, transport=transport),
        )
    return results


def ping_model_endpoint(
    endpoint: ModelEndpoint,
    *,
    allow_remote: bool = False,
    transport: HTTPTransport | None = None,
) -> ModelPingResult:
    start = time.perf_counter()
    if endpoint.api_format not in {"openai", "openai-compatible"}:
        return ModelPingResult(
            model_id=endpoint.model_id,
            provider=endpoint.provider,
            role=endpoint.role,
            endpoint_url=endpoint.base_url,
            ok=False,
            error="unsupported api_format",
        )
    try:
        client = OpenAICompatibleHTTPClient(
            base_url=endpoint.base_url,
            model=endpoint.served_model_name or endpoint.model_id,
            timeout_seconds=endpoint.timeout_seconds,
            retries=endpoint.retries,
            retry_backoff_seconds=0.0,
            allow_remote=allow_remote,
            transport=transport,
        )
        client.ping()
        latency_ms = int((time.perf_counter() - start) * 1000)
        return ModelPingResult(
            model_id=endpoint.model_id,
            provider=endpoint.provider,
            role=endpoint.role,
            endpoint_url=endpoint.base_url,
            ok=True,
            status_code=200,
            latency_ms=latency_ms,
        )
    except ModelRuntimeError as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return ModelPingResult(
            model_id=endpoint.model_id,
            provider=endpoint.provider,
            role=endpoint.role,
            endpoint_url=endpoint.base_url,
            ok=False,
            status_code=exc.status_code,
            latency_ms=latency_ms,
            error=str(exc),
        )
    except ValueError as exc:
        return ModelPingResult(
            model_id=endpoint.model_id,
            provider=endpoint.provider,
            role=endpoint.role,
            endpoint_url=endpoint.base_url,
            ok=False,
            error=str(exc),
        )


def preflight_vision_endpoint(
    endpoint: ModelEndpoint,
    *,
    allow_remote: bool = False,
    transport: HTTPTransport | None = None,
) -> VisionEndpointPreflightResult:
    """Call `/models` and resolve the actual served vision model name."""

    if endpoint.api_format not in {"openai", "openai-compatible"}:
        raise ModelRuntimeError("unsupported api_format")
    client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=endpoint.served_model_name or endpoint.model_id,
        timeout_seconds=endpoint.timeout_seconds,
        retries=endpoint.retries,
        retry_backoff_seconds=0.0,
        allow_remote=allow_remote,
        transport=transport,
    )
    raw_models = client.ping()
    model_records = _normalize_openai_model_listing(raw_models)
    model_ids = tuple(record.id for record in model_records)
    served_model_name = _resolve_served_model_name(endpoint, model_records)
    multimodal = _multimodal_capability(model_records, served_model_name)
    if multimodal is False:
        raise ModelRuntimeError("served model did not report multimodal capability")
    warnings = ()
    if multimodal is None:
        warnings = (_MISSING_MULTIMODAL_CAPABILITY_WARNING,)
    return VisionEndpointPreflightResult(
        model_id=endpoint.model_id,
        served_model_name=served_model_name,
        endpoint_url=endpoint.base_url,
        model_ids=model_ids,
        multimodal=multimodal,
        warnings=warnings,
    )


def preflight_chat_endpoint(
    endpoint: ModelEndpoint,
    *,
    allow_remote: bool = False,
    transport: HTTPTransport | None = None,
) -> ChatEndpointPreflightResult:
    """Call `/models` and resolve the actual served chat model name."""

    if endpoint.api_format not in {"openai", "openai-compatible"}:
        raise ModelRuntimeError("unsupported api_format")
    client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=endpoint.served_model_name or endpoint.model_id,
        timeout_seconds=endpoint.timeout_seconds,
        retries=endpoint.retries,
        retry_backoff_seconds=0.0,
        allow_remote=allow_remote,
        transport=transport,
    )
    raw_models = client.ping()
    model_records = _normalize_openai_model_listing(raw_models)
    model_ids = tuple(record.id for record in model_records)
    served_model_name = _resolve_served_model_name(endpoint, model_records)
    warnings = ()
    if not model_records:
        warnings = ("model endpoint did not return model ids; using configured model name",)
    return ChatEndpointPreflightResult(
        model_id=endpoint.model_id,
        served_model_name=served_model_name,
        endpoint_url=endpoint.base_url,
        model_ids=model_ids,
        warnings=warnings,
    )


def run_vision_smoke_test(
    endpoint: ModelEndpoint,
    *,
    allow_remote: bool = False,
    transport: HTTPTransport | None = None,
    prompt: str = "この画像を日本語で簡単に説明してください。",
) -> VisionSmokeResult:
    """Run a synthetic 1x1 PNG vision request against a local endpoint."""

    preflight = preflight_vision_endpoint(
        endpoint,
        allow_remote=allow_remote,
        transport=transport,
    )
    client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=preflight.served_model_name,
        timeout_seconds=endpoint.request_timeout_seconds or DEFAULT_VISION_SMOKE_TIMEOUT_SECONDS,
        retries=endpoint.retries,
        retry_backoff_seconds=0.0,
        allow_remote=allow_remote,
        transport=transport,
    )
    response = client.analyze(
        VisionRequest(
            prompt=prompt,
            images=(
                VisionInput(
                    kind="base64",
                    data=_SYNTHETIC_PNG_BASE64,
                    mime_type="image/png",
                ),
            ),
            model=preflight.served_model_name,
            max_tokens=128,
            temperature=0.2,
        ),
    )
    text = response.text.strip()
    if not text:
        raise ModelRuntimeError("vision smoke response was empty")
    return VisionSmokeResult(
        ok=True,
        model_id=endpoint.model_id,
        served_model_name=preflight.served_model_name,
        endpoint_url=endpoint.base_url,
        response_chars=len(text),
        warnings=preflight.warnings,
    )


def run_chat_smoke_test(
    endpoint: ModelEndpoint,
    *,
    allow_remote: bool = False,
    transport: HTTPTransport | None = None,
    prompt: str = 'Return exactly one short JSON object: {"ok": true}',
    max_tokens: int = 64,
    timeout_seconds: float | None = None,
    temperature: float = 0.2,
) -> ChatSmokeResult:
    """Run a tiny synthetic chat request against a local endpoint."""

    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    preflight = preflight_chat_endpoint(
        endpoint,
        allow_remote=allow_remote,
        transport=transport,
    )
    resolved_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else endpoint.request_timeout_seconds or DEFAULT_CHAT_SMOKE_TIMEOUT_SECONDS
    )
    if resolved_timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=preflight.served_model_name,
        timeout_seconds=resolved_timeout,
        retries=endpoint.retries,
        retry_backoff_seconds=0.0,
        allow_remote=allow_remote,
        transport=transport,
    )
    response = client.complete(
        ChatRequest(
            messages=(
                ChatMessage(
                    role="system",
                    content="You are a local endpoint smoke test. Return only short JSON.",
                ),
                ChatMessage(role="user", content=prompt),
            ),
            model=preflight.served_model_name,
            max_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    text = response.text.strip()
    if not text:
        raise ModelRuntimeError("chat smoke response was empty")
    return ChatSmokeResult(
        ok=True,
        model_id=endpoint.model_id,
        served_model_name=preflight.served_model_name,
        endpoint_url=endpoint.base_url,
        response_chars=len(text),
        max_tokens=max_tokens,
        timeout_seconds=resolved_timeout,
        warnings=preflight.warnings,
    )


def run_json_smoke_test(
    endpoint: ModelEndpoint,
    *,
    allow_remote: bool = False,
    transport: HTTPTransport | None = None,
    prompt: str = 'Return only this JSON object with no markdown: {"ok": true}',
    max_tokens: int = 128,
    timeout_seconds: float | None = None,
    temperature: float = 0.0,
) -> JSONSmokeResult:
    """Run a synthetic JSON request and verify that a JSON object is extractable."""

    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    preflight = preflight_chat_endpoint(
        endpoint,
        allow_remote=allow_remote,
        transport=transport,
    )
    resolved_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else endpoint.request_timeout_seconds or DEFAULT_CHAT_SMOKE_TIMEOUT_SECONDS
    )
    if resolved_timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=preflight.served_model_name,
        timeout_seconds=resolved_timeout,
        retries=endpoint.retries,
        retry_backoff_seconds=0.0,
        allow_remote=allow_remote,
        transport=transport,
    )
    response = client.complete(
        ChatRequest(
            messages=(
                ChatMessage(
                    role="system",
                    content="Return one valid JSON object only. No markdown. No explanations.",
                ),
                ChatMessage(role="user", content=prompt),
            ),
            model=preflight.served_model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        ),
    )
    text = response.text.strip()
    if not text:
        raise ModelRuntimeError("json smoke response was empty")
    strategy = _json_smoke_extraction_strategy(text)
    if strategy == "failed":
        raise ModelRuntimeError("json smoke response did not contain a valid JSON object")
    return JSONSmokeResult(
        ok=True,
        model_id=endpoint.model_id,
        served_model_name=preflight.served_model_name,
        endpoint_url=endpoint.base_url,
        response_chars=len(text),
        json_extraction_succeeded=True,
        json_extraction_strategy=strategy,
        max_tokens=max_tokens,
        timeout_seconds=resolved_timeout,
        warnings=preflight.warnings,
    )


def _chat_response_from_openai(raw: dict[str, Any]) -> ChatResponse:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelRuntimeError("model endpoint response did not include choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ModelRuntimeError("model endpoint returned malformed choices")
    message = first_choice.get("message")
    text = ""
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            text = content
        if not text and isinstance(message.get("reasoning_content"), str):
            text = str(message["reasoning_content"])
    if not text and isinstance(first_choice.get("text"), str):
        text = str(first_choice["text"])
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    return ChatResponse(
        text=text,
        model=str(raw["model"]) if raw.get("model") is not None else None,
        usage=usage,
        raw=raw,
    )


def _json_smoke_extraction_strategy(text: str) -> str:
    stripped = re.sub(
        r"<think\b[^>]*>.*?</think>",
        "",
        str(text or ""),
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    if not stripped:
        return "failed"
    if _loads_json_object(stripped):
        return "direct_json"
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL):
        if _loads_json_object(match.group(1).strip()):
            return "fenced_json"
    for candidate in _balanced_json_object_candidates(stripped):
        if _loads_json_object(candidate):
            return "extracted_object"
    return "failed"


def _loads_json_object(candidate: str) -> bool:
    try:
        return isinstance(json.loads(candidate), dict)
    except json.JSONDecodeError:
        return False


def _balanced_json_object_candidates(text: str) -> tuple[str, ...]:
    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char != "}":
            continue
        if depth == 0:
            continue
        depth -= 1
        if depth == 0 and start is not None:
            candidates.append(text[start : index + 1])
            start = None
    return tuple(candidates)


def _vision_input_to_content_part(image: VisionInput) -> dict[str, Any]:
    if image.kind == "image_url":
        return {"type": "image_url", "image_url": {"url": image.data}}
    if image.kind == "base64":
        mime_type = image.mime_type or "image/jpeg"
        return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image.data}"}}
    raise ValueError("unsupported vision input kind")


def _normalize_base_url(base_url: str) -> str:
    normalized = str(base_url).strip().rstrip("/")
    if not normalized:
        raise ValueError("endpoint_url must not be empty")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint_url must be an absolute HTTP URL")
    if parsed.path in {"", "/"}:
        normalized = normalized + "/v1"
    return normalized


def _validate_local_endpoint(base_url: str, *, allow_remote: bool) -> None:
    if allow_remote:
        return
    parsed = urlparse(base_url)
    host = parsed.hostname
    if host is None:
        raise ValueError("endpoint_url must include a host")
    if host in {"localhost"}:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("endpoint_url must use localhost or a numeric private address") from exc
    if address.is_loopback or address.is_private or address.is_link_local:
        return
    raise ValueError("endpoint_url must be local/private unless allow_remote is enabled")


def _is_retriable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


def _float_extra(value: object, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected float-compatible value, got {value!r}") from exc


def _optional_float_extra(value: object) -> float | None:
    if value is None:
        return None
    return _float_extra(value, default=0.0)


def _int_extra(value: object, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected integer-compatible value, got {value!r}") from exc


def _optional_extra_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_openai_model_listing(raw: dict[str, Any]) -> list[_NormalizedModelRecord]:
    """Normalize OpenAI and llama.cpp `/models` response variants."""

    records: list[_NormalizedModelRecord] = []
    for item in _model_items(raw.get("data")):
        record = _normalized_model_record_from_item(item)
        if record is not None:
            records = _add_or_merge_model_record(records, record)
    for item in _model_items(raw.get("models")):
        record = _normalized_model_record_from_item(item)
        if record is not None:
            records = _add_or_merge_model_record(records, record)
    return records


def _model_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _normalized_model_record_from_item(item: dict[str, Any]) -> _NormalizedModelRecord | None:
    model_id = _optional_extra_string(item.get("id"))
    name = _optional_extra_string(item.get("name"))
    model = _optional_extra_string(item.get("model"))
    resolved_id = model_id or name or model
    if resolved_id is None:
        return None
    return _NormalizedModelRecord(
        id=resolved_id,
        name=name,
        model=model,
        capabilities=_normalize_capabilities(item.get("capabilities")),
        raw=dict(item),
    )


def _add_or_merge_model_record(
    records: list[_NormalizedModelRecord],
    record: _NormalizedModelRecord,
) -> list[_NormalizedModelRecord]:
    for index, existing in enumerate(records):
        if set(existing.aliases()) & set(record.aliases()):
            merged = _merge_model_records(existing, record)
            return [*records[:index], merged, *records[index + 1 :]]
    return [*records, record]


def _merge_model_records(
    left: _NormalizedModelRecord,
    right: _NormalizedModelRecord,
) -> _NormalizedModelRecord:
    raw = dict(left.raw)
    raw.update(right.raw)
    return _NormalizedModelRecord(
        id=left.id or right.id,
        name=left.name or right.name,
        model=left.model or right.model,
        capabilities=_unique_strings((*left.capabilities, *right.capabilities)),
        raw=raw,
    )


def _normalize_capabilities(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, dict):
        capabilities = [
            str(key).strip()
            for key, enabled in value.items()
            if enabled and str(key).strip()
        ]
        return _unique_strings(capabilities)
    if isinstance(value, (list, tuple, set)):
        capabilities = [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]
        return _unique_strings(capabilities)
    return ()


def _unique_strings(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return tuple(unique)


def _resolve_served_model_name(
    endpoint: ModelEndpoint,
    model_records: list[_NormalizedModelRecord],
) -> str:
    if endpoint.served_model_name:
        if model_records and not _model_record_matching_name(model_records, endpoint.served_model_name):
            raise ModelRuntimeError("configured served model name was not returned by endpoint")
        return endpoint.served_model_name
    if _model_record_matching_name(model_records, endpoint.model_id):
        return endpoint.model_id
    if model_records:
        return model_records[0].id
    return endpoint.model_id


def _model_record_matching_name(
    model_records: list[_NormalizedModelRecord],
    model_name: str,
) -> _NormalizedModelRecord | None:
    for record in model_records:
        if _model_record_matches(record, model_name):
            return record
    return None


def _model_record_matches(record: _NormalizedModelRecord, model_name: str) -> bool:
    normalized_name = str(model_name).strip()
    return any(alias == normalized_name for alias in record.aliases())


def _multimodal_capability(
    model_records: list[_NormalizedModelRecord],
    served_model_name: str,
) -> bool | None:
    record = _model_record_matching_name(model_records, served_model_name)
    if record is None:
        return None
    if record.capabilities:
        capability_text = {capability.casefold() for capability in record.capabilities}
        return any(
            marker in capability_text
            for marker in {"multimodal", "vision", "image", "images"}
        )
    for key in ("modalities", "features"):
        if key not in record.raw:
            continue
        value = _capability_value_mentions_vision(record.raw.get(key))
        if value is not None:
            return value
    return None


def _model_item_multimodal_capability(item: dict[str, Any]) -> bool | None:
    saw_capability_metadata = False
    for key in ("capabilities", "modalities", "features", "meta", "metadata"):
        if key not in item:
            continue
        saw_capability_metadata = True
        value = _capability_value_mentions_vision(item.get(key))
        if value is not None:
            return value
    if saw_capability_metadata:
        return False
    return None


def _capability_value_mentions_vision(value: object) -> bool | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered_key = str(key).casefold()
            if lowered_key in {"vision", "image", "images", "multimodal"}:
                return bool(nested)
            nested_value = _capability_value_mentions_vision(nested)
            if nested_value is True:
                return True
        return None
    if isinstance(value, (list, tuple, set)):
        saw_text = False
        for item in value:
            nested_value = _capability_value_mentions_vision(item)
            if nested_value is True:
                return True
            if isinstance(item, str) and item.casefold() in {"text", "chat"}:
                saw_text = True
        return False if saw_text and value else None
    if isinstance(value, str):
        lowered = value.casefold()
        if any(marker in lowered for marker in ("vision", "image", "multimodal")):
            return True
        if lowered in {"text", "chat", "embedding"}:
            return False
    return None


def _tokenize(text: str) -> list[str]:
    return [token for token in str(text).casefold().split() if token]


def _text_understanding_user_prompt(request: TextUnderstandingRequest) -> str:
    return "\n".join(
        [
            f"schema_name: {request.schema_name}",
            f"language: {request.language}",
            f"source_type: {request.source_type}",
            "Extract JSON from the text below.",
            "Text:",
            request.text,
        ],
    )


def _default_text_understanding_payload() -> dict[str, Any]:
    return {
        "entities": [],
        "topics": [],
        "dates": [],
        "action_items": [],
        "event_hints": [],
        "summary": "",
        "confidence": 0.0,
    }

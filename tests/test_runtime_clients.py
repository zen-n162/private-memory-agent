import json
from urllib.error import URLError

import pytest

from private_memory_agent.cli import main
from private_memory_agent.models import ModelRegistry
from private_memory_agent.runtime import (
    ChatMessage,
    ChatRequest,
    FakeChatModelClient,
    FakeRerankerClient,
    FakeVisionModelClient,
    ModelEndpoint,
    ModelRuntimeError,
    OpenAICompatibleHTTPClient,
    RerankDocument,
    RerankRequest,
    VisionInput,
    VisionRequest,
    configured_model_endpoints,
    preflight_chat_endpoint,
    preflight_vision_endpoint,
    run_chat_smoke_test,
    run_json_smoke_test,
    run_vision_smoke_test,
)
from private_memory_agent.runtime.clients import _normalize_openai_model_listing


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


def test_fake_runtime_clients_are_deterministic():
    chat = FakeChatModelClient(response_text="了解しました")
    vision = FakeVisionModelClient(response_text="画像を確認しました")
    reranker = FakeRerankerClient()

    chat_response = chat.complete(ChatRequest(messages=(ChatMessage(role="user", content="test"),)))
    vision_response = vision.analyze(
        VisionRequest(
            prompt="describe",
            images=(VisionInput(kind="base64", data="AAAA", mime_type="image/png"),),
        ),
    )
    rerank_response = reranker.rerank(
        RerankRequest(
            query="local memory",
            documents=(
                RerankDocument(document_id="b", text="other text"),
                RerankDocument(document_id="a", text="local memory text"),
            ),
            top_k=1,
        ),
    )

    assert chat_response.text == "了解しました"
    assert chat_response.usage == {"prompt_messages": 1}
    assert vision_response.text == "画像を確認しました"
    assert vision_response.usage == {"images": 1}
    assert rerank_response.results[0].document_id == "a"
    assert rerank_response.results[0].rank == 1


def test_openai_compatible_client_sends_chat_completion_payload():
    requests = []

    def transport(request, timeout):
        requests.append((request, timeout))
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "local-model"
        assert body["messages"] == [{"role": "user", "content": "こんにちは"}]
        assert body["temperature"] == 0.2
        assert body["response_format"] == {"type": "json_object"}
        return FakeHTTPResponse(
            {
                "model": "local-model",
                "choices": [{"message": {"content": "ローカル応答"}}],
                "usage": {"prompt_tokens": 3},
            },
        )

    client = OpenAICompatibleHTTPClient(
        base_url="http://127.0.0.1:8080/v1",
        model="local-model",
        timeout_seconds=1,
        transport=transport,
    )
    response = client.complete(
        ChatRequest(
            messages=(ChatMessage(role="user", content="こんにちは"),),
            temperature=0.2,
            response_format={"type": "json_object"},
        ),
    )

    assert response.text == "ローカル応答"
    assert response.model == "local-model"
    assert response.usage == {"prompt_tokens": 3}
    assert requests[0][0].full_url == "http://127.0.0.1:8080/v1/chat/completions"
    assert requests[0][1] == 1


def test_openai_compatible_client_reads_reasoning_content_when_content_is_empty():
    def transport(request, timeout):
        return FakeHTTPResponse(
            {
                "model": "deepseek-r1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": '{"ok": true}',
                        },
                    },
                ],
            },
        )

    client = OpenAICompatibleHTTPClient(
        base_url="http://127.0.0.1:8080/v1",
        model="deepseek-r1",
        transport=transport,
    )

    response = client.complete(
        ChatRequest(messages=(ChatMessage(role="user", content="smoke"),)),
    )

    assert response.text == '{"ok": true}'


def test_default_transport_passes_timeout_as_keyword_and_get_has_no_body(monkeypatch):
    captured = {}

    def fake_urlopen(request, data=None, *, timeout=None):
        captured["request"] = request
        captured["data_arg"] = data
        captured["timeout"] = timeout
        return FakeHTTPResponse({"data": [{"id": "local-model"}]})

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    client = OpenAICompatibleHTTPClient(
        base_url="http://127.0.0.1:8080/v1",
        timeout_seconds=1.25,
    )

    assert client.ping() == {"data": [{"id": "local-model"}]}
    assert captured["request"].full_url == "http://127.0.0.1:8080/v1/models"
    assert captured["request"].data is None
    assert captured["data_arg"] is None
    assert captured["timeout"] == 1.25


def test_transport_typeerror_becomes_model_runtime_error():
    def broken_transport(request, timeout):
        raise TypeError("message_body should be a bytes-like object or an iterable, got float")

    client = OpenAICompatibleHTTPClient(
        base_url="http://127.0.0.1:8080/v1",
        transport=broken_transport,
    )

    with pytest.raises(ModelRuntimeError) as exc_info:
        client.ping()
    assert str(exc_info.value) == "model endpoint transport failed"
    assert "float" not in str(exc_info.value)


def test_normalizes_standard_openai_models_response():
    records = _normalize_openai_model_listing(
        {
            "object": "list",
            "data": [
                {
                    "id": "model-name",
                    "object": "model",
                },
            ],
        },
    )

    assert len(records) == 1
    assert records[0].id == "model-name"
    assert records[0].name is None
    assert records[0].model is None
    assert records[0].capabilities == ()
    assert records[0].raw["id"] == "model-name"


def test_normalizes_llamacpp_models_response_and_preserves_capabilities():
    records = _normalize_openai_model_listing(
        {
            "models": [
                {
                    "name": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                    "model": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                    "capabilities": ["completion", "multimodal"],
                },
            ],
            "object": "list",
            "data": [
                {
                    "id": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                    "object": "model",
                    "owned_by": "llamacpp",
                    "meta": {"n_ctx_train": 40960},
                },
            ],
        },
    )

    assert len(records) == 1
    assert records[0].id == "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    assert records[0].name == "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    assert records[0].model == "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    assert records[0].capabilities == ("completion", "multimodal")
    assert records[0].raw["owned_by"] == "llamacpp"
    assert records[0].raw["capabilities"] == ["completion", "multimodal"]


def test_normalizes_models_only_response():
    records = _normalize_openai_model_listing(
        {
            "models": [
                {
                    "name": "served-by-name.gguf",
                    "model": "served-by-model.gguf",
                    "capabilities": ["completion", "multimodal"],
                },
            ],
        },
    )

    assert len(records) == 1
    assert records[0].id == "served-by-name.gguf"
    assert records[0].name == "served-by-name.gguf"
    assert records[0].model == "served-by-model.gguf"
    assert records[0].capabilities == ("completion", "multimodal")


def test_normalizes_data_only_response():
    records = _normalize_openai_model_listing(
        {
            "data": [
                {
                    "id": "data-only-model",
                    "capabilities": ["text", "image"],
                },
            ],
        },
    )

    assert len(records) == 1
    assert records[0].id == "data-only-model"
    assert records[0].capabilities == ("text", "image")


def test_openai_compatible_client_encodes_vision_request_as_chat_content():
    captured_payload = {}
    captured_urls = []

    def transport(request, timeout):
        captured_urls.append(request.full_url)
        captured_payload.update(json.loads(request.data.decode("utf-8")))
        return FakeHTTPResponse(
            {
                "model": "served-vision",
                "choices": [{"message": {"content": "vision ok"}}],
            },
        )

    client = OpenAICompatibleHTTPClient(
        base_url="http://localhost:8081/v1",
        model="vision-model",
        transport=transport,
    )
    response = client.analyze(
        VisionRequest(
            prompt="見てください",
            images=(VisionInput(kind="base64", data="AAAA", mime_type="image/png"),),
        ),
    )

    content = captured_payload["messages"][0]["content"]
    assert response.text == "vision ok"
    assert response.model == "served-vision"
    assert captured_urls == ["http://localhost:8081/v1/chat/completions"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "見てください"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_vision_preflight_resolves_served_model_name_and_capability():
    endpoint = ModelEndpoint(
        model_id="vision_common",
        provider="llama_cpp",
        role="photo_understanding",
        base_url="http://127.0.0.1:8012/v1",
        served_model_name="Qwen3VL-4B-Instruct-Q4_K_M.gguf",
    )

    def transport(request, timeout):
        assert request.full_url == "http://127.0.0.1:8012/v1/models"
        return FakeHTTPResponse(
            {
                "data": [
                    {
                        "id": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                        "capabilities": ["text", "image"],
                    },
                ],
            },
        )

    result = preflight_vision_endpoint(endpoint, transport=transport)

    assert result.served_model_name == "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    assert result.model_ids == ("Qwen3VL-4B-Instruct-Q4_K_M.gguf",)
    assert result.multimodal is True
    assert result.warnings == ()


def test_chat_preflight_resolves_served_model_name_without_prompt():
    endpoint = ModelEndpoint(
        model_id="leader",
        provider="llama_cpp",
        role="leader_reasoning",
        base_url="http://127.0.0.1:8011/v1",
        served_model_name="DeepSeek-Leader-Q4.gguf",
        timeout_seconds=3,
    )
    calls = []

    def transport(request, timeout):
        calls.append((request, timeout))
        assert request.full_url == "http://127.0.0.1:8011/v1/models"
        assert request.data is None
        return FakeHTTPResponse({"data": [{"id": "DeepSeek-Leader-Q4.gguf"}]})

    result = preflight_chat_endpoint(endpoint, transport=transport)

    assert result.served_model_name == "DeepSeek-Leader-Q4.gguf"
    assert result.model_ids == ("DeepSeek-Leader-Q4.gguf",)
    assert result.warnings == ()
    assert calls[0][1] == 3


def test_chat_preflight_falls_back_to_first_served_model():
    endpoint = ModelEndpoint(
        model_id="leader",
        provider="llama_cpp",
        role="leader_reasoning",
        base_url="http://127.0.0.1:8011/v1",
    )

    def transport(request, timeout):
        return FakeHTTPResponse({"data": [{"id": "server-leader.gguf"}]})

    result = preflight_chat_endpoint(endpoint, transport=transport)

    assert result.served_model_name == "server-leader.gguf"


def test_chat_smoke_uses_synthetic_prompt_served_model_timeout_and_max_tokens():
    endpoint = ModelEndpoint(
        model_id="leader",
        provider="llama_cpp",
        role="leader_reasoning",
        base_url="http://127.0.0.1:8011/v1",
        served_model_name="served-leader.gguf",
        timeout_seconds=2,
    )
    payloads = []
    timeouts = []

    def transport(request, timeout):
        timeouts.append(timeout)
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        body = json.loads(request.data.decode("utf-8"))
        payloads.append(body)
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": '{"ok": true}'}}],
            },
        )

    result = run_chat_smoke_test(
        endpoint,
        transport=transport,
        max_tokens=17,
        timeout_seconds=88,
    )

    assert result.ok is True
    assert result.served_model_name == "served-leader.gguf"
    assert result.max_tokens == 17
    assert result.timeout_seconds == 88
    assert payloads[0]["model"] == "served-leader.gguf"
    assert payloads[0]["max_tokens"] == 17
    assert payloads[0]["temperature"] == 0.2
    assert [round(value) for value in timeouts] == [2, 88]


def test_json_smoke_uses_response_format_and_extracts_fenced_json():
    endpoint = ModelEndpoint(
        model_id="leader",
        provider="llama_cpp",
        role="leader_reasoning",
        base_url="http://127.0.0.1:8011/v1",
        served_model_name="served-leader.gguf",
        timeout_seconds=2,
    )
    payloads = []

    def transport(request, timeout):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        body = json.loads(request.data.decode("utf-8"))
        payloads.append(body)
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}],
            },
        )

    result = run_json_smoke_test(
        endpoint,
        transport=transport,
        max_tokens=23,
        timeout_seconds=99,
    )

    assert result.ok is True
    assert result.json_extraction_strategy == "fenced_json"
    assert result.max_tokens == 23
    assert result.timeout_seconds == 99
    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert payloads[0]["max_tokens"] == 23


def test_vision_preflight_detects_llamacpp_top_level_model_capabilities():
    endpoint = ModelEndpoint(
        model_id="vision_common",
        provider="llama_cpp",
        role="photo_understanding",
        base_url="http://127.0.0.1:8012/v1",
        served_model_name="Qwen3VL-4B-Instruct-Q4_K_M.gguf",
    )

    def transport(request, timeout):
        return FakeHTTPResponse(
            {
                "models": [
                    {
                        "name": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                        "model": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                        "capabilities": ["completion", "multimodal"],
                    },
                ],
                "object": "list",
                "data": [
                    {
                        "id": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                        "object": "model",
                        "owned_by": "llamacpp",
                        "meta": {"n_ctx_train": 40960},
                    },
                ],
            },
        )

    result = preflight_vision_endpoint(endpoint, transport=transport)

    assert result.served_model_name == "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    assert result.multimodal is True
    assert result.warnings == ()


def test_vision_preflight_matches_served_model_by_name_alias():
    endpoint = ModelEndpoint(
        model_id="vision_common",
        provider="llama_cpp",
        role="photo_understanding",
        base_url="http://127.0.0.1:8012/v1",
        served_model_name="served-alias.gguf",
    )

    def transport(request, timeout):
        return FakeHTTPResponse(
            {
                "models": [
                    {
                        "name": "served-alias.gguf",
                        "model": "served-real-model.gguf",
                        "capabilities": ["completion", "multimodal"],
                    },
                ],
            },
        )

    result = preflight_vision_endpoint(endpoint, transport=transport)

    assert result.served_model_name == "served-alias.gguf"
    assert result.model_ids == ("served-alias.gguf",)
    assert result.multimodal is True


def test_vision_preflight_matches_served_model_by_model_alias():
    endpoint = ModelEndpoint(
        model_id="vision_common",
        provider="llama_cpp",
        role="photo_understanding",
        base_url="http://127.0.0.1:8012/v1",
        served_model_name="served-real-model.gguf",
    )

    def transport(request, timeout):
        return FakeHTTPResponse(
            {
                "models": [
                    {
                        "name": "served-alias.gguf",
                        "model": "served-real-model.gguf",
                        "capabilities": ["completion", "multimodal"],
                    },
                ],
            },
        )

    result = preflight_vision_endpoint(endpoint, transport=transport)

    assert result.served_model_name == "served-real-model.gguf"
    assert result.model_ids == ("served-alias.gguf",)
    assert result.multimodal is True


def test_vision_preflight_missing_capabilities_warns_without_false_failure():
    endpoint = ModelEndpoint(
        model_id="vision_common",
        provider="llama_cpp",
        role="photo_understanding",
        base_url="http://127.0.0.1:8012/v1",
        served_model_name="server-model.gguf",
    )

    def transport(request, timeout):
        return FakeHTTPResponse({"data": [{"id": "server-model.gguf", "meta": {"size": 1}}]})

    result = preflight_vision_endpoint(endpoint, transport=transport)

    assert result.served_model_name == "server-model.gguf"
    assert result.multimodal is None
    assert result.warnings == (
        "served model did not explicitly report multimodal capability; "
        "continuing because capability metadata is absent",
    )


def test_vision_preflight_rejects_explicit_non_multimodal_capabilities():
    endpoint = ModelEndpoint(
        model_id="vision_common",
        provider="llama_cpp",
        role="photo_understanding",
        base_url="http://127.0.0.1:8012/v1",
        served_model_name="text-only.gguf",
    )

    def transport(request, timeout):
        return FakeHTTPResponse(
            {
                "models": [
                    {
                        "name": "text-only.gguf",
                        "model": "text-only.gguf",
                        "capabilities": ["completion"],
                    },
                ],
            },
        )

    with pytest.raises(ModelRuntimeError, match="multimodal"):
        preflight_vision_endpoint(endpoint, transport=transport)


def test_vision_preflight_falls_back_to_first_served_model_id():
    endpoint = ModelEndpoint(
        model_id="vision_common",
        provider="llama_cpp",
        role="photo_understanding",
        base_url="http://127.0.0.1:8012/v1",
    )

    def transport(request, timeout):
        return FakeHTTPResponse({"data": [{"id": "server-model.gguf"}]})

    result = preflight_vision_endpoint(endpoint, transport=transport)

    assert result.served_model_name == "server-model.gguf"
    assert result.multimodal is None
    assert result.warnings


def test_vision_preflight_unavailable_endpoint_fails_before_smoke_request():
    endpoint = ModelEndpoint(
        model_id="vision_common",
        provider="llama_cpp",
        role="photo_understanding",
        base_url="http://127.0.0.1:8012/v1",
    )
    calls = 0

    def transport(request, timeout):
        nonlocal calls
        calls += 1
        raise URLError("not ready")

    with pytest.raises(ModelRuntimeError):
        preflight_vision_endpoint(endpoint, transport=transport)
    assert calls == 1


def test_vision_smoke_uses_synthetic_image_and_served_model_name():
    endpoint = ModelEndpoint(
        model_id="vision_common",
        provider="llama_cpp",
        role="photo_understanding",
        base_url="http://127.0.0.1:8012/v1",
        served_model_name="Qwen3VL-4B-Instruct-Q4_K_M.gguf",
    )
    payloads = []

    def transport(request, timeout):
        if request.get_method() == "GET":
            return FakeHTTPResponse(
                {"data": [{"id": "Qwen3VL-4B-Instruct-Q4_K_M.gguf"}]},
            )
        body = json.loads(request.data.decode("utf-8"))
        payloads.append(body)
        return FakeHTTPResponse(
            {
                "model": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                "choices": [{"message": {"content": "synthetic image ok"}}],
            },
        )

    result = run_vision_smoke_test(endpoint, transport=transport)

    content = payloads[0]["messages"][0]["content"]
    assert result.ok is True
    assert payloads[0]["model"] == "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    assert payloads[0]["model"] != "vision_common"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_compatible_client_retries_transient_errors():
    calls = 0

    def transport(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise URLError("not ready")
        return FakeHTTPResponse({"data": [{"id": "local"}]})

    client = OpenAICompatibleHTTPClient(
        base_url="http://127.0.0.1:8080/v1",
        retries=1,
        retry_backoff_seconds=0,
        transport=transport,
    )

    assert client.ping() == {"data": [{"id": "local"}]}
    assert calls == 2


def test_openai_compatible_client_surfaces_timeout_and_rejects_remote_urls():
    def transport(request, timeout):
        raise TimeoutError

    client = OpenAICompatibleHTTPClient(
        base_url="http://127.0.0.1:8080/v1",
        transport=transport,
    )

    with pytest.raises(ModelRuntimeError) as exc_info:
        client.ping()
    assert exc_info.value.retriable is True
    assert "timed out" in str(exc_info.value)

    with pytest.raises(ValueError):
        OpenAICompatibleHTTPClient(base_url="https://example.com/v1")


def test_configured_model_endpoints_use_model_registry_extras(tmp_path):
    model_root = tmp_path / "models"
    registry = ModelRegistry.from_config(
        {
            "leader": {
                "provider": "llama_cpp",
                "role": "leader_reasoning",
                "model_dir": "leader",
                "endpoint_url": "http://127.0.0.1:8080/v1",
                "timeout_seconds": 3,
                "request_timeout_seconds": 300,
                "retries": 1,
            },
            "disabled": {
                "provider": "vllm",
                "role": "helper",
                "model_dir": "disabled",
                "enabled": False,
                "endpoint_url": "http://127.0.0.1:8000/v1",
            },
        },
        model_root,
    )

    enabled_endpoints = configured_model_endpoints(registry)
    all_endpoints = configured_model_endpoints(registry, include_disabled=True)

    assert [endpoint.model_id for endpoint in enabled_endpoints] == ["leader"]
    assert enabled_endpoints[0].timeout_seconds == 3
    assert enabled_endpoints[0].request_timeout_seconds == 300
    assert enabled_endpoints[0].retries == 1
    assert [endpoint.model_id for endpoint in all_endpoints] == ["leader", "disabled"]


def test_cli_models_ping_uses_configured_endpoint_without_real_server(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    models_yaml = "\n".join(
        [
            f"model_root: {model_root}",
            "leader:",
            "  provider: llama_cpp",
            "  role: leader_reasoning",
            "  model_dir: leader-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8123/v1",
            "  api_format: openai-compatible",
            "  timeout_seconds: 1",
            "  retries: 0",
        ],
    )
    config_dir = temp_config_factory(model_root=model_root, models_yaml=models_yaml)

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://127.0.0.1:8123/v1/models"
        assert timeout == 1
        return FakeHTTPResponse({"data": [{"id": "leader"}]})

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    exit_code = main(["models", "ping", "--config-dir", str(config_dir), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload[0]["model_id"] == "leader"
    assert payload[0]["ok"] is True
    assert payload[0]["endpoint_url"] == "http://127.0.0.1:8123/v1"


def test_cli_models_ping_transport_typeerror_is_controlled(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    models_yaml = "\n".join(
        [
            f"model_root: {model_root}",
            "leader:",
            "  provider: llama_cpp",
            "  role: leader_reasoning",
            "  model_dir: leader-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8128/v1",
            "  api_format: openai-compatible",
        ],
    )
    config_dir = temp_config_factory(model_root=model_root, models_yaml=models_yaml)

    def fake_urlopen(request, data=None, *, timeout=None):
        raise TypeError("message_body should be a bytes-like object or an iterable, got float")

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    exit_code = main(["models", "ping", "--config-dir", str(config_dir)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "model endpoint transport failed" in output
    assert "Traceback" not in output
    assert "float" not in output
    assert str(tmp_path) not in output


def test_cli_models_ping_accepts_model_argument_forms(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    models_yaml = "\n".join(
        [
            f"model_root: {model_root}",
            "vision_common:",
            "  provider: llama_cpp",
            "  role: photo_understanding",
            "  model_dir: vision-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8124/v1",
            "  served_model_name: served-vision.gguf",
            "  api_format: openai-compatible",
        ],
    )
    config_dir = temp_config_factory(model_root=model_root, models_yaml=models_yaml)

    def fake_urlopen(request, timeout):
        assert request.full_url == "http://127.0.0.1:8124/v1/models"
        return FakeHTTPResponse({"data": [{"id": "served-vision.gguf"}]})

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    first = main(["models", "ping", "vision_common", "--config-dir", str(config_dir)])
    first_output = capsys.readouterr().out
    second = main(
        [
            "models",
            "ping",
            "--config-dir",
            str(config_dir),
            "--model",
            "vision_common",
        ],
    )
    second_output = capsys.readouterr().out

    assert first == 0
    assert second == 0
    assert "vision_common" in first_output
    assert "vision_common" in second_output


def test_cli_models_ping_vision_smoke_uses_synthetic_request(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    models_yaml = "\n".join(
        [
            f"model_root: {model_root}",
            "vision_common:",
            "  provider: llama_cpp",
            "  role: photo_understanding",
            "  model_dir: vision-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8125/v1",
            "  served_model_name: served-vision.gguf",
            "  api_format: openai-compatible",
        ],
    )
    config_dir = temp_config_factory(model_root=model_root, models_yaml=models_yaml)
    payloads = []

    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-vision.gguf"}]})
        body = json.loads(request.data.decode("utf-8"))
        payloads.append(body)
        return FakeHTTPResponse(
            {
                "model": "served-vision.gguf",
                "choices": [{"message": {"content": "synthetic ok"}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    exit_code = main(
        [
            "models",
            "ping",
            "vision_common",
            "--config-dir",
            str(config_dir),
            "--vision-smoke",
        ],
    )

    output = capsys.readouterr().out
    content = payloads[0]["messages"][0]["content"]
    assert exit_code == 0
    assert "Vision smoke passed" in output
    assert payloads[0]["model"] == "served-vision.gguf"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert str(tmp_path) not in output


def test_cli_models_ping_chat_smoke_uses_synthetic_request(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    models_yaml = "\n".join(
        [
            f"model_root: {model_root}",
            "leader:",
            "  provider: llama_cpp",
            "  role: leader_reasoning",
            "  model_dir: leader-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8129/v1",
            "  served_model_name: served-leader.gguf",
            "  api_format: openai-compatible",
            "  timeout_seconds: 1",
        ],
    )
    config_dir = temp_config_factory(model_root=model_root, models_yaml=models_yaml)
    payloads = []
    timeouts = []

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        body = json.loads(request.data.decode("utf-8"))
        payloads.append(body)
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": '{"ok": true}'}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    exit_code = main(
        [
            "models",
            "ping",
            "leader",
            "--config-dir",
            str(config_dir),
            "--chat-smoke",
            "--max-tokens",
            "19",
            "--timeout-seconds",
            "77",
        ],
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Chat smoke passed" in output
    assert payloads[0]["model"] == "served-leader.gguf"
    assert payloads[0]["max_tokens"] == 19
    assert [round(value) for value in timeouts] == [1, 77]
    assert str(tmp_path) not in output


def test_cli_models_ping_json_smoke_uses_synthetic_request(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    models_yaml = "\n".join(
        [
            "leader:",
            "  provider: llama_cpp",
            "  role: leader_reasoning",
            "  model_dir: leader-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8130/v1",
            "  served_model_name: served-leader.gguf",
            "  api_format: openai-compatible",
            "  timeout_seconds: 1",
        ],
    )
    config_dir = temp_config_factory(model_root=model_root, models_yaml=models_yaml)
    payloads = []

    def fake_urlopen(request, timeout):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        body = json.loads(request.data.decode("utf-8"))
        payloads.append(body)
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": "<think>hidden</think>\n{\"ok\": true}"}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    exit_code = main(
        [
            "models",
            "ping",
            "leader",
            "--config-dir",
            str(config_dir),
            "--json-smoke",
            "--max-tokens",
            "31",
            "--timeout-seconds",
            "77",
        ],
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "JSON smoke passed" in output
    assert "json_extraction_strategy=direct_json" in output
    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert payloads[0]["max_tokens"] == 31
    assert str(tmp_path) not in output


def test_cli_models_ping_handles_no_configured_endpoints(capsys, temp_config_factory):
    config_dir = temp_config_factory()

    exit_code = main(["models", "ping", "--config-dir", str(config_dir)])

    assert exit_code == 0
    assert "No configured model endpoints." in capsys.readouterr().out

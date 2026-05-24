import importlib.util
import json
import sys
from pathlib import Path

from private_memory_agent.cli import main
from private_memory_agent.config import load_config
from private_memory_agent.runtime import GPUInfo, build_runtime_plan


def _models_yaml(model_root: Path) -> str:
    return "\n".join(
        [
            f"model_root: {model_root}",
            "hardware:",
            "  gpu_name: NVIDIA RTX 4500 Ada Generation",
            "  vram_gb: 24",
            "  safe_model_vram_gb: 21",
            "leader:",
            "  provider: llama_cpp",
            "  role: leader_reasoning",
            "  model_dir: leader-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8080/v1",
            "  api_format: openai-compatible",
            "vision_common:",
            "  provider: llama_cpp",
            "  role: photo_understanding",
            "  model_dir: vision-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8012/v1",
            "  served_model_name: Qwen3VL-4B-Instruct-Q4_K_M.gguf",
            "  api_format: openai-compatible",
            "japanese_text:",
            "  provider: vllm",
            "  role: japanese_line_notes",
            "  model_dir: japanese-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8000/v1",
            "  api_format: openai-compatible",
            "text_embedding:",
            "  provider: sentence_transformers",
            "  role: text_embedding",
            "  model_dir: embedding-model",
            "  enabled: true",
            "text_reranker:",
            "  provider: sentence_transformers",
            "  role: text_reranker",
            "  model_dir: reranker-model",
            "  enabled: false",
        ],
    )


def _prepare_model_dirs(model_root: Path) -> None:
    for name in ("leader-model", "vision-model", "japanese-model", "embedding-model"):
        (model_root / name).mkdir(parents=True, exist_ok=True)


def test_runtime_plan_lightweight_query_uses_fake_gpu_data(
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    _prepare_model_dirs(model_root)
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_models_yaml(model_root),
    )
    config = load_config(config_dir=config_dir)

    plan = build_runtime_plan(
        config,
        "lightweight_query",
        gpu=GPUInfo(
            name="Fake RTX 4500 Ada",
            memory_total_mb=24576,
            memory_free_mb=22000,
        ),
    )

    assert plan.ok is True
    assert plan.safe_vram_gb == 21.0
    assert [model.model_key for model in plan.active_models] == ["leader", "text_embedding"]
    assert plan.active_models[0].endpoint_url == "http://127.0.0.1:8080/v1"
    assert plan.gpu.memory_free_gb > plan.profile.estimated_vram_gb


def test_runtime_plan_warns_when_fake_gpu_memory_is_too_low(
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    _prepare_model_dirs(model_root)
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_models_yaml(model_root),
    )
    config = load_config(config_dir=config_dir)

    plan = build_runtime_plan(
        config,
        "vision_batch",
        gpu=GPUInfo(name="Fake GPU", memory_total_mb=24576, memory_free_mb=4096),
    )

    assert plan.ok is False
    assert "profile estimate exceeds currently free GPU memory" in plan.warnings
    assert plan.active_models[0].served_model_name == "Qwen3VL-4B-Instruct-Q4_K_M.gguf"


def test_runtime_plan_cli_outputs_json_without_model_paths(
    capsys,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    _prepare_model_dirs(model_root)
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_models_yaml(model_root),
    )

    exit_code = main(
        [
            "runtime",
            "plan",
            "leader_only",
            "--config-dir",
            str(config_dir),
            "--gpu-name",
            "Fake RTX 4500 Ada",
            "--gpu-total-mb",
            "24576",
            "--gpu-free-mb",
            "22000",
            "--json",
        ],
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert payload["profile"]["profile_id"] == "leader_only"
    assert payload["active_models"][0]["model_key"] == "leader"
    assert payload["gpu"]["memory_free_mb"] == 22000
    assert str(model_root) not in output


def test_gpu_check_parser_handles_nvidia_smi_csv():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "gpu_check.py"
    spec = importlib.util.spec_from_file_location("pma_gpu_check", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["pma_gpu_check"] = module
    spec.loader.exec_module(module)

    rows = module.parse_nvidia_smi_csv(
        "NVIDIA RTX 4500 Ada Generation, 24564, 21000\n",
    )

    assert len(rows) == 1
    assert rows[0].name == "NVIDIA RTX 4500 Ada Generation"
    assert rows[0].memory_total_mb == 24564
    assert rows[0].memory_free_mb == 21000

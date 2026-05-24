from pathlib import Path

import pytest


@pytest.fixture
def temp_config_factory(tmp_path):
    def _write_config(
        *,
        model_root: Path | None = None,
        app_data_dir: Path | None = None,
        raw_sources_yaml: str | None = None,
        local_paths_yaml: str | None = None,
        models_yaml: str | None = None,
    ) -> Path:
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        resolved_model_root = model_root or tmp_path / "models"
        resolved_app_data_dir = app_data_dir or tmp_path / "data"

        (config_dir / "app.example.yaml").write_text(
            "\n".join(
                [
                    "app_name: private-memory-agent",
                    "environment: test",
                    "privacy_mode: strict",
                    "allow_network: false",
                    "log_private_data: false",
                    "default_timezone: Asia/Tokyo",
                ],
            ),
            encoding="utf-8",
        )
        (config_dir / "paths.example.yaml").write_text(
            "\n".join(
                [
                    f"model_root: {resolved_model_root}",
                    f"app_data_dir: {resolved_app_data_dir}",
                    "input_roots:",
                    "  photos: null",
                    "  line_exports: null",
                    "raw_sources:",
                    raw_sources_yaml
                    or "\n".join(
                        [
                            "  photos:",
                            "    enabled: false",
                            "    kind: photos",
                            "    path: null",
                            "    recursive: true",
                            "    read_only: true",
                            "  line:",
                            "    enabled: false",
                            "    kind: line",
                            "    path: null",
                            "    recursive: true",
                            "    read_only: true",
                            "  notes:",
                            "    enabled: false",
                            "    kind: notes",
                            "    path: null",
                            "    recursive: true",
                            "    read_only: true",
                        ],
                    ),
                ],
            ),
            encoding="utf-8",
        )
        if local_paths_yaml is not None:
            (config_dir / "paths.local.yaml").write_text(local_paths_yaml + "\n", encoding="utf-8")
        (config_dir / "models.example.yaml").write_text(
            models_yaml
            or "\n".join(
                [
                    f"model_root: {resolved_model_root}",
                    "hardware:",
                    "  gpu_name: Test GPU",
                    "  vram_gb: 24",
                    "  safe_model_vram_gb: 21",
                    "  default_context_tokens: 8192",
                    "  max_interactive_context_tokens: 16384",
                    "leader:",
                    "  provider: llama_cpp",
                    "  role: leader_reasoning",
                    "  model_dir: leader-model",
                    "  quantization: Q4_K_M",
                    "  context_tokens: 8192",
                    "  enabled: true",
                    "vision:",
                    "  provider: llama_cpp",
                    "  role: photo_understanding",
                    "  model_dir: vision-model",
                    "  enabled: false",
                ],
            )
            + "\n",
            encoding="utf-8",
        )
        return config_dir

    return _write_config

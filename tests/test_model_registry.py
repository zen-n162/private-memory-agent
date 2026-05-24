from pathlib import Path

import pytest

from private_memory_agent.models import ModelRegistry, ModelRegistryError


def test_registry_marks_missing_enabled_models(tmp_path):
    model_root = tmp_path / "models"
    model_root.mkdir()
    raw_config = {
        "leader": {
            "provider": "llama_cpp",
            "role": "leader_reasoning",
            "model_dir": "leader-model",
            "enabled": True,
        },
        "vision": {
            "provider": "llama_cpp",
            "role": "photo_understanding",
            "model_dir": "vision-model",
            "enabled": False,
        },
    }

    registry = ModelRegistry.from_config(raw_config, model_root)

    assert [spec.model_id for spec in registry.missing_models] == ["leader", "vision"]
    assert [spec.model_id for spec in registry.missing_enabled_models] == ["leader"]
    assert registry.get("leader").resolved_path == model_root / "leader-model"


def test_registry_does_not_create_or_load_model_directories(tmp_path):
    model_root = tmp_path / "models"
    raw_config = {
        "leader": {
            "provider": "llama_cpp",
            "role": "leader_reasoning",
            "model_dir": "leader-model",
        },
    }

    registry = ModelRegistry.from_config(raw_config, model_root)

    assert len(registry) == 1
    assert not model_root.exists()


def test_registry_validates_required_model_fields(tmp_path):
    with pytest.raises(ModelRegistryError):
        ModelRegistry.from_config(
            {
                "leader": {
                    "provider": "llama_cpp",
                    "model_dir": "leader-model",
                },
            },
            Path(tmp_path),
        )

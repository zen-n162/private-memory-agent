from pathlib import Path

from private_memory_agent.config import DEFAULT_MODEL_ROOT, load_config


def test_repository_example_config_has_local_first_defaults(monkeypatch):
    for name in (
        "PMA_CONFIG_DIR",
        "PMA_MODEL_ROOT",
        "PMA_APP_DATA_DIR",
        "PMA_ENV",
        "PMA_ALLOW_NETWORK",
        "PMA_LOG_PRIVATE_DATA",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.app.environment == "local"
    assert config.app.privacy_mode == "strict"
    assert config.app.allow_network is False
    assert config.app.log_private_data is False
    assert config.paths.model_root == DEFAULT_MODEL_ROOT
    assert len(config.model_registry) == 13
    assert {spec.model_id for spec in config.model_registry if spec.role == "text_embedding"} == {
        "text_embedding",
        "text_embedding_ruri_130m",
        "text_embedding_bge_m3",
        "text_embedding_qwen_06b",
    }
    assert config.hardware.vram_gb == 24
    assert set(config.paths.raw_sources) == {"photos", "line", "notes"}
    assert all(not source.enabled for source in config.paths.raw_sources.values())


def test_model_root_can_be_overridden_without_touching_models(monkeypatch, tmp_path):
    model_root = tmp_path / "models"
    monkeypatch.setenv("PMA_MODEL_ROOT", str(model_root))
    monkeypatch.delenv("PMA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("PMA_APP_DATA_DIR", raising=False)

    config = load_config()

    assert config.paths.model_root == model_root
    assert not model_root.exists()
    assert all(spec.model_root == model_root for spec in config.model_registry)


def test_config_dir_override_reads_minimal_examples(monkeypatch, tmp_path):
    (tmp_path / "app.example.yaml").write_text(
        "\n".join(
            [
                "app_name: private-memory-agent",
                "environment: test",
                "privacy_mode: strict",
                "allow_network: false",
                "log_private_data: false",
            ],
        ),
        encoding="utf-8",
    )
    (tmp_path / "paths.example.yaml").write_text(
        "\n".join(
            [
                "model_root: /tmp/pma-models",
                "app_data_dir: data/test",
                "input_roots:",
                "  photos: null",
            ],
        ),
        encoding="utf-8",
    )
    (tmp_path / "models.example.yaml").write_text("model_root: /tmp/pma-models\n", encoding="utf-8")
    monkeypatch.setenv("PMA_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("PMA_MODEL_ROOT", raising=False)

    config = load_config()

    assert config.config_dir == tmp_path
    assert config.app.environment == "test"
    assert config.paths.model_root == Path("/tmp/pma-models")
    assert config.paths.input_roots == {"photos": None}


def test_load_config_builds_typed_registry_from_temp_directories(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    (model_root / "leader-model").mkdir(parents=True)
    config_dir = temp_config_factory(model_root=model_root)
    monkeypatch.delenv("PMA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("PMA_MODEL_ROOT", raising=False)

    config = load_config(config_dir)

    leader = config.model_registry.get("leader")
    vision = config.model_registry.get("vision")
    assert leader is not None
    assert leader.exists is True
    assert leader.status == "available"
    assert vision is not None
    assert vision.exists is False
    assert vision.status == "disabled-missing"
    assert config.model_registry.missing_enabled_models == ()


def test_model_enabled_override_uses_environment(monkeypatch, temp_config_factory, tmp_path):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(model_root=model_root)
    monkeypatch.setenv("PMA_MODEL_LEADER_ENABLED", "false")

    config = load_config(config_dir)

    leader = config.model_registry.get("leader")
    assert leader is not None
    assert leader.enabled is False
    assert config.model_registry.missing_enabled_models == ()


def test_local_paths_config_overlays_raw_sources_without_requiring_real_paths(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    photos_root = tmp_path / "fake-photos"
    line_root = tmp_path / "fake-line"
    notes_root = tmp_path / "fake-notes"
    photos_root.mkdir()
    line_root.mkdir()
    notes_root.mkdir()
    local_yaml = "\n".join(
        [
            "raw_sources:",
            "  photos:",
            "    enabled: true",
            "    kind: photos",
            f"    path: {photos_root}",
            "    recursive: true",
            "    read_only: true",
            "  line:",
            "    enabled: true",
            "    kind: line",
            f"    path: {line_root}",
            "    recursive: true",
            "    read_only: true",
            "  notes:",
            "    enabled: true",
            "    kind: notes",
            f"    path: {notes_root}",
            "    recursive: true",
            "    read_only: true",
        ],
    )
    config_dir = temp_config_factory(local_paths_yaml=local_yaml)
    monkeypatch.delenv("PMA_PATHS_CONFIG", raising=False)

    config = load_config(paths_config=config_dir / "paths.local.yaml")

    assert config.config_dir == config_dir
    assert config.paths_config_path == config_dir / "paths.local.yaml"
    assert config.paths.raw_sources["photos"].path == photos_root
    assert config.paths.raw_sources["line"].path == line_root
    assert config.paths.raw_sources["notes"].path == notes_root
    assert all(source.enabled for source in config.paths.raw_sources.values())


def test_config_show_dict_redacts_raw_source_paths(temp_config_factory, tmp_path):
    photos_root = tmp_path / "fake-photos"
    photos_root.mkdir()
    local_yaml = "\n".join(
        [
            "raw_sources:",
            "  photos:",
            "    enabled: true",
            f"    path: {photos_root}",
        ],
    )
    config_dir = temp_config_factory(local_paths_yaml=local_yaml)

    config = load_config(paths_config=config_dir / "paths.local.yaml")
    rendered = config.to_dict()

    assert rendered["paths"]["raw_sources"]["photos"]["path"] == "<configured>"
    assert str(photos_root) not in str(rendered)

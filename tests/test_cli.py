import pytest

from private_memory_agent.cli import main


def test_help_mentions_doctor(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "Private Memory Agent" in output
    assert "config" in output
    assert "models" in output
    assert "index" in output
    assert "search" in output
    assert "ingest" in output
    assert "doctor" in output


def test_doctor_runs_without_gpu_models_network_or_private_data(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    (model_root / "leader-model").mkdir(parents=True)
    config_dir = temp_config_factory(model_root=model_root)
    for name in (
        "PMA_CONFIG_DIR",
        "PMA_MODEL_ROOT",
        "PMA_APP_DATA_DIR",
        "PMA_ENV",
        "PMA_ALLOW_NETWORK",
        "PMA_LOG_PRIVATE_DATA",
    ):
        monkeypatch.delenv(name, raising=False)

    exit_code = main(["doctor", "--config-dir", str(config_dir)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Private Memory Agent doctor" in output
    assert "network access is disabled" in output
    assert "configured model directories" in output


def test_config_show_outputs_json(capsys, temp_config_factory, tmp_path):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(model_root=model_root)

    exit_code = main(["config", "show", "--config-dir", str(config_dir)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"model_root":' in output
    assert str(model_root) in output
    assert '"allow_network": false' in output


def test_models_list_outputs_registry_table(capsys, temp_config_factory, tmp_path):
    model_root = tmp_path / "models"
    (model_root / "leader-model").mkdir(parents=True)
    config_dir = temp_config_factory(model_root=model_root)

    exit_code = main(["models", "list", "--config-dir", str(config_dir)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "leader" in output
    assert "available" in output
    assert "vision" in output
    assert "disabled-missing" in output


def test_doctor_config_option_checks_raw_sources_without_printing_paths(
    monkeypatch,
    capsys,
    temp_config_factory,
    tmp_path,
):
    photos_root = tmp_path / "fake-photos"
    photos_root.mkdir()
    local_yaml = "\n".join(
        [
            "raw_sources:",
            "  photos:",
            "    enabled: true",
            "    kind: photos",
            f"    path: {photos_root}",
            "    recursive: true",
            "    read_only: true",
        ],
    )
    config_dir = temp_config_factory(local_paths_yaml=local_yaml)
    monkeypatch.setattr("private_memory_agent.doctor.shutil.which", lambda name: None)

    exit_code = main(["doctor", "--config", str(config_dir / "paths.local.yaml")])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "raw source photos" in output
    assert "exists=yes" in output
    assert "type=directory" in output
    assert str(photos_root) not in output

import subprocess

from private_memory_agent.doctor import run_doctor


def test_doctor_warns_for_missing_enabled_model_dir(monkeypatch, temp_config_factory, tmp_path):
    model_root = tmp_path / "models"
    model_root.mkdir()
    config_dir = temp_config_factory(model_root=model_root)
    monkeypatch.setattr("private_memory_agent.doctor.shutil.which", lambda name: None)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)

    result = run_doctor(config_dir)

    assert result.ok is True
    model_check = next(check for check in result.checks if check.name == "configured model directories")
    assert model_check.status == "warn"
    assert "leader" in model_check.detail


def test_doctor_checks_nvidia_smi_when_available(monkeypatch, temp_config_factory, tmp_path):
    model_root = tmp_path / "models"
    (model_root / "leader-model").mkdir(parents=True)
    config_dir = temp_config_factory(model_root=model_root)
    monkeypatch.setattr("private_memory_agent.doctor.shutil.which", lambda name: "/usr/bin/nvidia-smi")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="GPU 0: Test\n", stderr="")

    monkeypatch.setattr("private_memory_agent.doctor.subprocess.run", fake_run)

    result = run_doctor(config_dir)

    nvidia_check = next(check for check in result.checks if check.name == "nvidia-smi")
    assert nvidia_check.status == "ok"
    assert "detected 1 GPU" in nvidia_check.detail


def test_doctor_raw_source_checks_do_not_enumerate_or_print_paths(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    source_root = tmp_path / "private-source"
    source_root.mkdir()
    (source_root / "do-not-print-this-name.txt").write_text("private payload", encoding="utf-8")
    local_yaml = "\n".join(
        [
            "raw_sources:",
            "  notes:",
            "    enabled: true",
            "    kind: notes",
            f"    path: {source_root}",
            "    recursive: true",
            "    read_only: true",
        ],
    )
    config_dir = temp_config_factory(local_paths_yaml=local_yaml)
    monkeypatch.setattr("private_memory_agent.doctor.shutil.which", lambda name: None)

    result = run_doctor(paths_config=config_dir / "paths.local.yaml")

    rendered = "\n".join(check.detail for check in result.checks)
    source_check = next(check for check in result.checks if check.name == "raw source notes")
    assert source_check.status in {"ok", "warn"}
    assert "exists=yes" in source_check.detail
    assert "type=directory" in source_check.detail
    assert str(source_root) not in rendered
    assert "do-not-print-this-name" not in rendered
    assert "private payload" not in rendered

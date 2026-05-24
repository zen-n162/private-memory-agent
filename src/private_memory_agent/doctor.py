"""Local-only skeleton health checks."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from private_memory_agent.config import ConfigError, load_config, required_config_files

CheckStatus = Literal["ok", "warn", "info", "fail"]


@dataclass(frozen=True)
class DoctorCheck:
    """A single non-sensitive doctor check."""

    status: CheckStatus
    name: str
    detail: str


@dataclass(frozen=True)
class DoctorResult:
    """Doctor command result."""

    checks: list[DoctorCheck]

    @property
    def ok(self) -> bool:
        return all(check.status != "fail" for check in self.checks)


def run_doctor(
    config_dir: Path | str | None = None,
    *,
    paths_config: Path | str | None = None,
) -> DoctorResult:
    """Run placeholder checks that avoid GPU, models, network, and private data."""

    checks: list[DoctorCheck] = []
    checks.append(_python_check())
    checks.append(_cuda_visibility_check())
    checks.append(_nvidia_smi_check())

    try:
        config = load_config(config_dir, paths_config=paths_config)
    except ConfigError as exc:
        checks.append(DoctorCheck("fail", "config loading", str(exc)))
        return DoctorResult(checks)

    missing_files = [path.name for path in required_config_files(config.config_dir) if not path.exists()]
    if missing_files:
        checks.append(
            DoctorCheck(
                "fail",
                "example configs",
                "missing required example config files: " + ", ".join(missing_files),
            ),
        )
    else:
        checks.append(DoctorCheck("ok", "example configs", "all required examples are present"))

    checks.append(DoctorCheck("ok", "config loading", "example configuration loaded"))

    if config.app.allow_network:
        checks.append(DoctorCheck("fail", "network default", "network access is enabled"))
    else:
        checks.append(DoctorCheck("ok", "network default", "network access is disabled"))

    if config.app.log_private_data:
        checks.append(DoctorCheck("fail", "private logging", "private-data logging is enabled"))
    else:
        checks.append(DoctorCheck("ok", "private logging", "private-data logging is disabled"))

    checks.append(_model_root_check(config.paths.model_root))
    checks.append(_model_directories_check(config.model_registry))
    checks.append(_data_dir_writability_check(config.paths.app_data_dir))
    checks.extend(_raw_source_checks(config.paths.raw_sources))
    checks.append(
        DoctorCheck(
            "info",
            "phase scope",
            "ingestion, model loading, retrieval, API, and UI are not implemented yet",
        ),
    )
    return DoctorResult(checks)


def format_doctor_result(result: DoctorResult) -> str:
    """Format doctor output for CLI display."""

    lines = ["Private Memory Agent doctor"]
    for check in result.checks:
        lines.append(f"[{check.status.upper()}] {check.name}: {check.detail}")
    return "\n".join(lines)


def _python_check() -> DoctorCheck:
    version = sys.version_info
    version_text = f"{version.major}.{version.minor}.{version.micro}"
    if version >= (3, 11):
        return DoctorCheck("ok", "python", f"Python {version_text}")
    return DoctorCheck("fail", "python", f"Python {version_text}; Python 3.11+ is required")


def _cuda_visibility_check() -> DoctorCheck:
    value = os.environ.get("CUDA_VISIBLE_DEVICES")
    if value is None:
        return DoctorCheck("info", "cuda visibility", "CUDA_VISIBLE_DEVICES is not set")
    normalized = value.strip()
    if normalized in {"", "-1"}:
        return DoctorCheck("warn", "cuda visibility", "CUDA devices are hidden by environment")
    return DoctorCheck("ok", "cuda visibility", f"CUDA_VISIBLE_DEVICES={normalized}")


def _nvidia_smi_check() -> DoctorCheck:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return DoctorCheck("info", "nvidia-smi", "not found on PATH")
    try:
        result = subprocess.run(
            [executable, "-L"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return DoctorCheck("warn", "nvidia-smi", f"available but failed to run: {exc}")
    if result.returncode != 0:
        return DoctorCheck("warn", "nvidia-smi", "available but returned a non-zero status")
    gpu_count = len([line for line in result.stdout.splitlines() if line.strip()])
    if gpu_count:
        return DoctorCheck("ok", "nvidia-smi", f"available; detected {gpu_count} GPU(s)")
    return DoctorCheck("ok", "nvidia-smi", "available; no GPUs reported")


def _model_root_check(model_root: Path) -> DoctorCheck:
    if model_root.is_dir():
        return DoctorCheck("ok", "model root", f"exists: {model_root}")
    if model_root.exists():
        return DoctorCheck("fail", "model root", f"path exists but is not a directory: {model_root}")
    return DoctorCheck("warn", "model root", f"directory is missing: {model_root}")


def _model_directories_check(model_registry) -> DoctorCheck:
    total = len(model_registry)
    if total == 0:
        return DoctorCheck("warn", "configured model directories", "no model directories configured")

    missing_enabled = model_registry.missing_enabled_models
    missing_all = model_registry.missing_models
    available_count = total - len(missing_all)
    if missing_enabled:
        missing_ids = ", ".join(spec.model_id for spec in missing_enabled)
        return DoctorCheck(
            "warn",
            "configured model directories",
            f"{available_count}/{total} available; missing enabled models: {missing_ids}",
        )
    if missing_all:
        return DoctorCheck(
            "ok",
            "configured model directories",
            f"{available_count}/{total} available; missing models are disabled",
        )
    return DoctorCheck("ok", "configured model directories", f"{available_count}/{total} available")


def _data_dir_writability_check(app_data_dir: Path) -> DoctorCheck:
    if app_data_dir.exists() and not app_data_dir.is_dir():
        return DoctorCheck("fail", "data directory", f"path exists but is not a directory: {app_data_dir}")
    if app_data_dir.is_dir():
        if os.access(app_data_dir, os.W_OK):
            return DoctorCheck("ok", "data directory", f"writable: {app_data_dir}")
        return DoctorCheck("fail", "data directory", f"not writable: {app_data_dir}")

    parent = app_data_dir.parent
    if parent.is_dir() and os.access(parent, os.W_OK):
        return DoctorCheck(
            "ok",
            "data directory",
            f"directory can be created under writable parent: {app_data_dir}",
        )
    return DoctorCheck("warn", "data directory", f"directory is missing: {app_data_dir}")


def _raw_source_checks(raw_sources) -> list[DoctorCheck]:
    if not raw_sources:
        return [DoctorCheck("info", "raw sources", "no raw source paths configured")]

    checks: list[DoctorCheck] = []
    for source_id in sorted(raw_sources):
        source = raw_sources[source_id]
        checks.append(_raw_source_check(source))
    return checks


def _raw_source_check(source) -> DoctorCheck:
    name = f"raw source {source.source_id}"
    if not source.enabled:
        return DoctorCheck("info", name, "disabled; no source data inspected")
    if source.path is None:
        return DoctorCheck("warn", name, "enabled but no path is configured")

    exists = source.path.exists()
    if not exists:
        return DoctorCheck(
            "warn",
            name,
            _raw_source_detail(
                exists=False,
                readable=False,
                kind="missing",
                configured_read_only=source.read_only,
                writable=False,
            ),
        )

    is_directory = source.path.is_dir()
    is_file = source.path.is_file()
    if is_directory:
        kind = "directory"
    elif is_file:
        kind = "file"
    else:
        kind = "other"

    readable = os.access(source.path, os.R_OK)
    writable = os.access(source.path, os.W_OK)
    appears_read_only = not writable
    status: CheckStatus = "ok"
    if not readable:
        status = "fail"
    elif source.read_only and not appears_read_only:
        status = "warn"

    return DoctorCheck(
        status,
        name,
        _raw_source_detail(
            exists=True,
            readable=readable,
            kind=kind,
            configured_read_only=source.read_only,
            writable=writable,
        ),
    )


def _raw_source_detail(
    *,
    exists: bool,
    readable: bool,
    kind: str,
    configured_read_only: bool,
    writable: bool,
) -> str:
    appears_read_only = exists and not writable
    return (
        f"exists={_yes_no(exists)}; "
        f"readable={_yes_no(readable)}; "
        f"type={kind}; "
        f"configured_read_only={_yes_no(configured_read_only)}; "
        f"appears_read_only={_yes_no(appears_read_only)}"
    )


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"

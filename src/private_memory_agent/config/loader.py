"""Configuration loader for the local-first skeleton.

The loader intentionally supports only the simple mapping-oriented YAML used by
the repository's example config files. It returns typed settings and model
registry metadata without loading models, reading private data, or touching the
network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from private_memory_agent.models import ModelRegistry, ModelRegistryError

DEFAULT_MODEL_ROOT = Path("/home/zennakamura/MyApplication/models")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_FILENAMES = ("app.example.yaml", "paths.example.yaml", "models.example.yaml")


class ConfigError(ValueError):
    """Raised when a config file cannot be parsed by the skeleton loader."""


@dataclass(frozen=True)
class AppSettings:
    """Application-level privacy and runtime settings."""

    app_name: str = "private-memory-agent"
    environment: str = "local"
    privacy_mode: str = "strict"
    allow_network: bool = False
    log_private_data: bool = False
    default_timezone: str = "Asia/Tokyo"


@dataclass(frozen=True)
class RawSourceSettings:
    """Configured local raw source root.

    The path is metadata only. Loading config must not enumerate, copy, or read
    source payloads.
    """

    source_id: str
    kind: str
    enabled: bool = False
    path: Path | None = None
    recursive: bool = True
    read_only: bool = True

    def to_dict(self, *, redact_path: bool = True) -> dict[str, Any]:
        if self.path is None:
            rendered_path = None
        elif redact_path:
            rendered_path = "<configured>"
        else:
            rendered_path = str(self.path)
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "enabled": self.enabled,
            "path": rendered_path,
            "recursive": self.recursive,
            "read_only": self.read_only,
        }


@dataclass(frozen=True)
class PathSettings:
    """Path settings that avoid hard-coded private source locations."""

    model_root: Path = DEFAULT_MODEL_ROOT
    app_data_dir: Path = Path("data/local")
    sqlite_path: Path = Path("data/local/private_memory_agent.sqlite3")
    input_roots: dict[str, Path | None] = field(default_factory=dict)
    raw_sources: dict[str, RawSourceSettings] = field(default_factory=dict)


@dataclass(frozen=True)
class HardwareSettings:
    """Non-runtime hardware budget settings."""

    gpu_name: str | None = None
    vram_gb: int | None = None
    safe_model_vram_gb: int | None = None
    default_context_tokens: int | None = None
    max_interactive_context_tokens: int | None = None


@dataclass(frozen=True)
class ConfigBundle:
    """Loaded configuration bundle for the local skeleton runtime."""

    config_dir: Path
    paths_config_path: Path | None
    app: AppSettings
    paths: PathSettings
    hardware: HardwareSettings
    model_registry: ModelRegistry

    @property
    def models(self) -> ModelRegistry:
        """Backward-compatible alias for the typed model registry."""

        return self.model_registry

    def to_dict(self) -> dict[str, Any]:
        """Return a CLI-safe representation of resolved settings."""

        return {
            "config_dir": str(self.config_dir),
            "paths_config_path": None if self.paths_config_path is None else "<configured>",
            "app": {
                "app_name": self.app.app_name,
                "environment": self.app.environment,
                "privacy_mode": self.app.privacy_mode,
                "allow_network": self.app.allow_network,
                "log_private_data": self.app.log_private_data,
                "default_timezone": self.app.default_timezone,
            },
            "paths": {
                "model_root": str(self.paths.model_root),
                "app_data_dir": str(self.paths.app_data_dir),
                "sqlite_path": str(self.paths.sqlite_path),
                "input_roots": {
                    name: None if value is None else "<configured>"
                    for name, value in self.paths.input_roots.items()
                },
                "raw_sources": {
                    name: source.to_dict(redact_path=True)
                    for name, source in self.paths.raw_sources.items()
                },
            },
            "hardware": {
                "gpu_name": self.hardware.gpu_name,
                "vram_gb": self.hardware.vram_gb,
                "safe_model_vram_gb": self.hardware.safe_model_vram_gb,
                "default_context_tokens": self.hardware.default_context_tokens,
                "max_interactive_context_tokens": self.hardware.max_interactive_context_tokens,
            },
            "models": self.model_registry.to_list(),
        }


def required_config_files(config_dir: Path | str | None = None) -> list[Path]:
    """Return the example config files expected for Phase 0-A."""

    root = Path(config_dir).expanduser() if config_dir is not None else PROJECT_ROOT / "configs"
    return [root / filename for filename in CONFIG_FILENAMES]


def load_config(
    config_dir: Path | str | None = None,
    *,
    paths_config: Path | str | None = None,
) -> ConfigBundle:
    """Load example configuration plus safe environment overrides.

    This does not read source data, model directories, private databases, or any
    network resource.
    """

    paths_config_path = _resolve_paths_config_path(paths_config)
    resolved_config_dir = _resolve_config_dir(config_dir, paths_config_path)
    app_raw = _load_yaml_mapping(resolved_config_dir / "app.example.yaml")
    paths_raw = _load_yaml_mapping(resolved_config_dir / "paths.example.yaml")
    if paths_config_path is not None:
        paths_raw = _deep_merge(paths_raw, _load_yaml_mapping(paths_config_path))
    models_raw = _load_yaml_mapping(resolved_config_dir / "models.example.yaml")
    models_raw = _apply_model_env_overrides(models_raw)

    app = AppSettings(
        app_name=str(app_raw.get("app_name", AppSettings.app_name)),
        environment=os.environ.get("PMA_ENV", str(app_raw.get("environment", "local"))),
        privacy_mode=os.environ.get("PMA_PRIVACY_MODE", str(app_raw.get("privacy_mode", "strict"))),
        allow_network=_env_bool("PMA_ALLOW_NETWORK", bool(app_raw.get("allow_network", False))),
        log_private_data=_env_bool(
            "PMA_LOG_PRIVATE_DATA",
            bool(app_raw.get("log_private_data", False)),
        ),
        default_timezone=str(app_raw.get("default_timezone", "Asia/Tokyo")),
    )

    model_root = _path_setting(
        os.environ.get("PMA_MODEL_ROOT")
        or models_raw.get("model_root")
        or paths_raw.get("model_root")
        or DEFAULT_MODEL_ROOT,
        base_dir=resolved_config_dir.parent,
    )
    app_data_dir = _path_setting(
        os.environ.get("PMA_APP_DATA_DIR") or paths_raw.get("app_data_dir") or "data/local",
        base_dir=resolved_config_dir.parent if "PMA_APP_DATA_DIR" not in os.environ else None,
    )
    sqlite_path = _storage_sqlite_path(paths_raw, app_data_dir=app_data_dir, base_dir=resolved_config_dir.parent)
    input_roots = _input_roots(paths_raw.get("input_roots", {}), base_dir=resolved_config_dir.parent)
    raw_sources = _raw_sources(
        paths_raw.get("raw_sources", {}),
        base_dir=paths_config_path.parent if paths_config_path is not None else resolved_config_dir.parent,
    )

    hardware = _hardware_settings(models_raw.get("hardware", {}))
    try:
        model_registry = ModelRegistry.from_config(models_raw, model_root)
    except ModelRegistryError as exc:
        raise ConfigError(str(exc)) from exc

    return ConfigBundle(
        config_dir=resolved_config_dir,
        paths_config_path=paths_config_path,
        app=app,
        paths=PathSettings(
            model_root=model_root,
            app_data_dir=app_data_dir,
            sqlite_path=sqlite_path,
            input_roots=input_roots,
            raw_sources=raw_sources,
        ),
        hardware=hardware,
        model_registry=model_registry,
    )


def _resolve_config_dir(config_dir: Path | str | None, paths_config: Path | None) -> Path:
    if config_dir is not None:
        return Path(config_dir).expanduser()
    env_config_dir = os.environ.get("PMA_CONFIG_DIR")
    if env_config_dir:
        return Path(env_config_dir).expanduser()
    if paths_config is not None:
        return paths_config.parent
    return PROJECT_ROOT / "configs"


def _resolve_paths_config_path(paths_config: Path | str | None) -> Path | None:
    raw_path = paths_config or os.environ.get("PMA_PATHS_CONFIG")
    if raw_path is None:
        return None
    return Path(raw_path).expanduser()


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return _parse_simple_yaml(path.read_text(encoding="utf-8"))
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError(f"Unable to read config file: {path}") from exc


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(base_value, value)
        else:
            merged[key] = value
    return merged


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line:
            raise ConfigError(f"Tabs are not supported in YAML line {line_number}.")

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        key, separator, value = stripped.partition(":")
        if separator != ":" or not key.strip():
            raise ConfigError(f"Expected 'key: value' in YAML line {line_number}.")

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ConfigError(f"Invalid indentation in YAML line {line_number}.")

        parent = stack[-1][1]
        clean_key = key.strip()
        clean_value = value.strip()
        if clean_value == "":
            child: dict[str, Any] = {}
            parent[clean_key] = child
            stack.append((indent, child))
        else:
            parent[clean_key] = _parse_scalar(clean_value)

    return root


def _parse_scalar(value: str) -> Any:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    if normalized == "{}":
        return {}
    if normalized == "[]":
        return []
    if (
        len(normalized) >= 2
        and normalized[0] == normalized[-1]
        and normalized[0] in {"'", '"'}
    ):
        return normalized[1:-1]
    try:
        return int(normalized)
    except ValueError:
        return normalized


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _path_setting(raw_value: object, *, base_dir: Path | None = None) -> Path:
    if isinstance(raw_value, Path):
        path = raw_value.expanduser()
    else:
        path = Path(str(raw_value)).expanduser()
    if path.is_absolute() or base_dir is None:
        return path
    return base_dir / path


def _input_roots(raw_value: object, *, base_dir: Path | None) -> dict[str, Path | None]:
    if raw_value is None:
        return {}
    if not isinstance(raw_value, dict):
        raise ConfigError("paths.input_roots must be a mapping.")
    roots: dict[str, Path | None] = {}
    for name, value in raw_value.items():
        roots[str(name)] = None if value is None else _path_setting(value, base_dir=base_dir)
    return roots


def _storage_sqlite_path(
    paths_raw: dict[str, Any],
    *,
    app_data_dir: Path,
    base_dir: Path | None,
) -> Path:
    storage_raw = paths_raw.get("storage")
    if isinstance(storage_raw, dict) and storage_raw.get("sqlite_path") is not None:
        return _path_setting(storage_raw["sqlite_path"], base_dir=base_dir)
    return app_data_dir / "private_memory_agent.sqlite3"


def _raw_sources(raw_value: object, *, base_dir: Path | None) -> dict[str, RawSourceSettings]:
    if raw_value is None:
        return {}
    if not isinstance(raw_value, dict):
        raise ConfigError("paths.raw_sources must be a mapping.")

    sources: dict[str, RawSourceSettings] = {}
    for source_id, source_value in raw_value.items():
        source_key = str(source_id)
        if isinstance(source_value, dict):
            source_path = source_value.get("path")
            sources[source_key] = RawSourceSettings(
                source_id=source_key,
                kind=str(source_value.get("kind") or source_key),
                enabled=_bool_config(source_value.get("enabled", source_path is not None)),
                path=None
                if source_path is None
                else _path_setting(source_path, base_dir=base_dir),
                recursive=_bool_config(source_value.get("recursive", True)),
                read_only=_bool_config(source_value.get("read_only", True)),
            )
        else:
            sources[source_key] = RawSourceSettings(
                source_id=source_key,
                kind=source_key,
                enabled=source_value is not None,
                path=None if source_value is None else _path_setting(source_value, base_dir=base_dir),
            )
    return sources


def _bool_config(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _hardware_settings(raw_value: object) -> HardwareSettings:
    if raw_value is None:
        return HardwareSettings()
    if not isinstance(raw_value, dict):
        raise ConfigError("models.hardware must be a mapping.")
    return HardwareSettings(
        gpu_name=_optional_string(raw_value.get("gpu_name")),
        vram_gb=_optional_int(raw_value.get("vram_gb")),
        safe_model_vram_gb=_optional_int(raw_value.get("safe_model_vram_gb")),
        default_context_tokens=_optional_int(raw_value.get("default_context_tokens")),
        max_interactive_context_tokens=_optional_int(
            raw_value.get("max_interactive_context_tokens"),
        ),
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Expected integer value, got {value!r}") from exc


def _apply_model_env_overrides(models_raw: dict[str, Any]) -> dict[str, Any]:
    updated = dict(models_raw)
    for model_id, raw_value in models_raw.items():
        if not isinstance(raw_value, dict):
            continue
        env_key = _model_env_key(model_id)
        model_config = dict(raw_value)
        enabled_override = os.environ.get(f"PMA_MODEL_{env_key}_ENABLED")
        dir_override = os.environ.get(f"PMA_MODEL_{env_key}_DIR")
        if enabled_override is not None:
            model_config["enabled"] = _env_bool_value(enabled_override)
        if dir_override is not None:
            model_config["model_dir"] = dir_override
        updated[model_id] = model_config
    return updated


def _model_env_key(model_id: str) -> str:
    chars = [char.upper() if char.isalnum() else "_" for char in model_id]
    return "".join(chars)


def _env_bool_value(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}

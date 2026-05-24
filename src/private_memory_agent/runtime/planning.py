"""Runtime profile planning for local GPU model serving."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from private_memory_agent.runtime.clients import endpoint_from_model_spec


@dataclass(frozen=True)
class GPUInfo:
    """Small GPU memory snapshot."""

    name: str
    memory_total_mb: int
    memory_free_mb: int

    @property
    def memory_total_gb(self) -> float:
        return self.memory_total_mb / 1024

    @property
    def memory_free_gb(self) -> float:
        return self.memory_free_mb / 1024

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "memory_total_mb": self.memory_total_mb,
            "memory_free_mb": self.memory_free_mb,
            "memory_total_gb": round(self.memory_total_gb, 2),
            "memory_free_gb": round(self.memory_free_gb, 2),
        }


@dataclass(frozen=True)
class RuntimeProfile:
    """A recommended serving profile."""

    profile_id: str
    description: str
    active_model_keys: tuple[str, ...]
    optional_model_keys: tuple[str, ...]
    estimated_vram_gb: float
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "description": self.description,
            "active_model_keys": list(self.active_model_keys),
            "optional_model_keys": list(self.optional_model_keys),
            "estimated_vram_gb": self.estimated_vram_gb,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class RuntimeModelPlan:
    """Resolved model metadata for one profile entry."""

    model_key: str
    role: str | None
    provider: str | None
    enabled: bool | None
    status: str
    endpoint_url: str | None
    served_model_name: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_key": self.model_key,
            "role": self.role,
            "provider": self.provider,
            "enabled": self.enabled,
            "status": self.status,
            "endpoint_url": self.endpoint_url,
            "served_model_name": self.served_model_name,
        }


@dataclass(frozen=True)
class RuntimePlan:
    """Resolved plan for one runtime profile."""

    profile: RuntimeProfile
    safe_vram_gb: float
    gpu: GPUInfo | None
    active_models: tuple[RuntimeModelPlan, ...]
    optional_models: tuple[RuntimeModelPlan, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "safe_vram_gb": self.safe_vram_gb,
            "gpu": None if self.gpu is None else self.gpu.to_dict(),
            "active_models": [model.to_dict() for model in self.active_models],
            "optional_models": [model.to_dict() for model in self.optional_models],
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


RUNTIME_PROFILES: dict[str, RuntimeProfile] = {
    "leader_only": RuntimeProfile(
        profile_id="leader_only",
        description="Serve only the leader reasoning model for grounded query answers.",
        active_model_keys=("leader",),
        optional_model_keys=("text_reranker",),
        estimated_vram_gb=13.0,
        notes=(
            "Use when answering text-heavy questions with retrieval already built.",
            "Keep vision and Japanese extraction servers stopped to preserve VRAM.",
        ),
    ),
    "vision_batch": RuntimeProfile(
        profile_id="vision_batch",
        description="Serve the common vision model for photo annotation batches.",
        active_model_keys=("vision_common",),
        optional_model_keys=("vision_heavy", "multimodal_embedding"),
        estimated_vram_gb=14.0,
        notes=(
            "Use for pma annotate photos batches.",
            "Prefer one vision server at a time on a 24GB card.",
        ),
    ),
    "japanese_text": RuntimeProfile(
        profile_id="japanese_text",
        description="Serve the Japanese text model for LINE and notes extraction.",
        active_model_keys=("japanese_text",),
        optional_model_keys=("text_embedding",),
        estimated_vram_gb=12.0,
        notes=(
            "Use for pma annotate text --source line|notes.",
            "Embedding models can usually stay CPU or lightweight unless needed.",
        ),
    ),
    "lightweight_query": RuntimeProfile(
        profile_id="lightweight_query",
        description="Serve lightweight query components for local RAG interaction.",
        active_model_keys=("leader", "text_embedding"),
        optional_model_keys=("text_reranker",),
        estimated_vram_gb=16.0,
        notes=(
            "Use for interactive pma query with local retrieval.",
            "Avoid concurrent vision batches while this profile is active.",
        ),
    ),
}


def build_runtime_plan(
    config: Any,
    profile_id: str,
    *,
    gpu: GPUInfo | None = None,
) -> RuntimePlan:
    """Build a deterministic runtime plan from config metadata."""

    profile = RUNTIME_PROFILES.get(profile_id)
    if profile is None:
        known = ", ".join(sorted(RUNTIME_PROFILES))
        raise ValueError(f"unknown runtime profile '{profile_id}'; known profiles: {known}")

    safe_vram_gb = _safe_vram_gb(config)
    active_models = tuple(
        _model_plan(config.model_registry, model_key)
        for model_key in profile.active_model_keys
    )
    optional_models = tuple(
        _model_plan(config.model_registry, model_key)
        for model_key in profile.optional_model_keys
    )
    warnings = tuple(_warnings(profile, safe_vram_gb, gpu, active_models))
    return RuntimePlan(
        profile=profile,
        safe_vram_gb=safe_vram_gb,
        gpu=gpu,
        active_models=active_models,
        optional_models=optional_models,
        warnings=warnings,
    )


def _safe_vram_gb(config: Any) -> float:
    safe = getattr(config.hardware, "safe_model_vram_gb", None)
    total = getattr(config.hardware, "vram_gb", None)
    if safe is not None:
        return float(safe)
    if total is not None:
        return max(0.0, float(total) - 3.0)
    return 21.0


def _model_plan(registry: Any, model_key: str) -> RuntimeModelPlan:
    spec = registry.get(model_key)
    if spec is None:
        return RuntimeModelPlan(
            model_key=model_key,
            role=None,
            provider=None,
            enabled=None,
            status="not-configured",
            endpoint_url=None,
            served_model_name=None,
        )
    endpoint = endpoint_from_model_spec(spec)
    served_model_name = None
    if endpoint is not None:
        served_model_name = endpoint.served_model_name or endpoint.model_id
    return RuntimeModelPlan(
        model_key=model_key,
        role=spec.role,
        provider=spec.provider,
        enabled=spec.enabled,
        status=spec.status,
        endpoint_url=None if endpoint is None else endpoint.base_url,
        served_model_name=served_model_name,
    )


def _warnings(
    profile: RuntimeProfile,
    safe_vram_gb: float,
    gpu: GPUInfo | None,
    active_models: tuple[RuntimeModelPlan, ...],
) -> list[str]:
    warnings: list[str] = []
    if profile.estimated_vram_gb > safe_vram_gb:
        warnings.append("profile estimate exceeds configured safe VRAM budget")
    if gpu is not None and profile.estimated_vram_gb > gpu.memory_free_gb:
        warnings.append("profile estimate exceeds currently free GPU memory")
    for model in active_models:
        if model.status == "not-configured":
            warnings.append(f"active model '{model.model_key}' is not configured")
        elif model.enabled is False:
            warnings.append(f"active model '{model.model_key}' is disabled in config")
        elif model.status != "available":
            warnings.append(f"active model '{model.model_key}' status is {model.status}")
        if model.endpoint_url is None and model.provider in {"llama_cpp", "vllm", "ollama"}:
            warnings.append(f"active model '{model.model_key}' has no endpoint configured")
    return warnings

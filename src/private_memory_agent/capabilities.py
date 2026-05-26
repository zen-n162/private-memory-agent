"""Autonomous capability planning primitives for the local memory agent.

The capability layer is intentionally privacy-safe metadata around existing
tools. It lets a leader model, or deterministic fallback, describe which local
capabilities should be composed without exposing raw prompts or evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from private_memory_agent.retrieval.text import normalize_text
from private_memory_agent.runtime import ChatMessage, ChatModelClient, ChatRequest
from private_memory_agent.temporal import parse_temporal_event_query
from private_memory_agent.tracing import AgentTraceRecorder
from private_memory_agent.visual import parse_visual_evidence_query

EXPECTED_OUTPUT_TYPES = {
    "answer_text",
    "candidate_dates",
    "photo_gallery",
    "evidence_list",
    "timeline",
    "hybrid",
}

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_REPLANS = 1
DEFAULT_MAX_RUNTIME_SECONDS = 60
DEFAULT_MAX_MODEL_CALLS = 3
DEFAULT_MAX_LIVE_VISION_CALLS = 0
DEFAULT_MAX_CANDIDATES_PER_CAPABILITY = 50
DEFAULT_MAX_EVIDENCE_SENT_TO_ANSWER = 50

_CAPABILITY_PLAN_SYSTEM_PROMPT = """You are DeepSeek Leader planning a local private memory task.
Return exactly one JSON object. Do not include markdown or chain-of-thought.
Choose capabilities from the provided registry only.
Do not request external network or cloud tools.
Do not include raw private evidence or raw prompts in summaries."""


@dataclass(frozen=True)
class CapabilitySpec:
    """One privacy-safe capability registration."""

    name: str
    description: str
    good_for: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_sources: tuple[str, ...] = ()
    optional_sources: tuple[str, ...] = ()
    cost_level: str = "low"
    latency_level: str = "low"
    privacy_level: str = "safe_metadata_only"
    model_dependency: str | None = None
    examples: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.name, "capability name")
        if not self.description:
            raise ValueError("capability description is required")
        object.__setattr__(self, "good_for", _strings(self.good_for))
        object.__setattr__(self, "required_sources", _strings(self.required_sources))
        object.__setattr__(self, "optional_sources", _strings(self.optional_sources))
        object.__setattr__(self, "examples", _strings(self.examples))

    def to_summary(self) -> dict[str, Any]:
        """Return metadata safe for prompts, API payloads, and UI display."""

        return {
            "name": self.name,
            "description": self.description,
            "good_for": list(self.good_for),
            "required_sources": list(self.required_sources),
            "optional_sources": list(self.optional_sources),
            "cost_level": self.cost_level,
            "latency_level": self.latency_level,
            "privacy_level": self.privacy_level,
            "model_dependency": self.model_dependency,
            "examples": list(self.examples),
        }


class CapabilityRegistry:
    """Registry for local capabilities exposed to the planner."""

    def __init__(self, specs: Iterable[CapabilitySpec] = ()) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: CapabilitySpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate capability: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> CapabilitySpec | None:
        return self._specs.get(name)

    def require(self, name: str) -> CapabilitySpec:
        spec = self.get(name)
        if spec is None:
            raise KeyError(name)
        return spec

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def summaries(self) -> list[dict[str, Any]]:
        return [self._specs[name].to_summary() for name in self.names()]

    @classmethod
    def default(cls) -> "CapabilityRegistry":
        return cls(default_capabilities())


@dataclass(frozen=True)
class TaskPlanStep:
    """One capability execution step selected by the planner."""

    step_id: str
    capability_name: str
    input: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    reason_summary: str = "Capability selected to satisfy the answer goal."
    expected_output: str = "safe observation metadata"
    max_results: int | None = None
    max_cost: str = "low"

    def __post_init__(self) -> None:
        _require_identifier(self.step_id, "step_id")
        _require_identifier(self.capability_name, "capability_name")
        object.__setattr__(self, "depends_on", _strings(self.depends_on))
        object.__setattr__(self, "reason_summary", _safe_summary(self.reason_summary))
        object.__setattr__(self, "expected_output", _safe_summary(self.expected_output))
        if self.max_results is not None:
            object.__setattr__(self, "max_results", max(1, min(int(self.max_results), 500)))

    def to_dict(self, *, show_plan: bool = False) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability_name": self.capability_name,
            "input_keys": sorted(str(key) for key in self.input),
            "input": _safe_step_input(self.input) if show_plan else {},
            "depends_on": list(self.depends_on),
            "reason_summary": self.reason_summary,
            "expected_output": self.expected_output,
            "max_results": self.max_results,
            "max_cost": self.max_cost,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, index: int) -> "TaskPlanStep":
        return cls(
            step_id=str(payload.get("step_id") or f"step_{index:02d}"),
            capability_name=str(payload.get("capability_name") or ""),
            input=dict(payload.get("input") or {}),
            depends_on=_strings(payload.get("depends_on") or ()),
            reason_summary=str(payload.get("reason_summary") or "Leader selected this capability."),
            expected_output=str(payload.get("expected_output") or "safe observation metadata"),
            max_results=payload.get("max_results"),
            max_cost=str(payload.get("max_cost") or "low"),
        )


@dataclass(frozen=True)
class TaskPlan:
    """Structured leader/fallback plan for a local memory task."""

    question_summary: str
    answer_goal: str
    expected_output_type: str
    required_information: tuple[str, ...]
    selected_capabilities: tuple[str, ...]
    execution_graph: tuple[TaskPlanStep, ...]
    stopping_criteria: tuple[str, ...]
    fallback_strategy: str
    uncertainty_policy: str
    privacy_policy: str
    generated_by: str = "deterministic_fallback"
    fallback_used: bool = True
    max_steps: int = DEFAULT_MAX_STEPS
    max_replans: int = DEFAULT_MAX_REPLANS
    max_runtime_seconds: int = DEFAULT_MAX_RUNTIME_SECONDS
    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    max_live_vision_calls: int = DEFAULT_MAX_LIVE_VISION_CALLS
    max_candidates_per_capability: int = DEFAULT_MAX_CANDIDATES_PER_CAPABILITY
    max_evidence_sent_to_answer: int = DEFAULT_MAX_EVIDENCE_SENT_TO_ANSWER

    def __post_init__(self) -> None:
        if self.expected_output_type not in EXPECTED_OUTPUT_TYPES:
            raise ValueError(f"unsupported expected_output_type: {self.expected_output_type}")
        object.__setattr__(self, "question_summary", _safe_summary(self.question_summary))
        object.__setattr__(self, "answer_goal", _safe_summary(self.answer_goal))
        object.__setattr__(self, "required_information", _strings(self.required_information))
        object.__setattr__(self, "selected_capabilities", _strings(self.selected_capabilities))
        object.__setattr__(self, "stopping_criteria", _strings(self.stopping_criteria))
        object.__setattr__(self, "fallback_strategy", _safe_summary(self.fallback_strategy))
        object.__setattr__(self, "uncertainty_policy", _safe_summary(self.uncertainty_policy))
        object.__setattr__(self, "privacy_policy", _safe_summary(self.privacy_policy))
        if not self.selected_capabilities:
            raise ValueError("selected_capabilities is required")
        if not self.execution_graph:
            raise ValueError("execution_graph is required")
        unknown_steps = [
            step.capability_name
            for step in self.execution_graph
            if step.capability_name not in self.selected_capabilities
        ]
        if unknown_steps:
            raise ValueError(f"execution_graph references unselected capabilities: {unknown_steps}")
        object.__setattr__(self, "max_steps", max(1, min(int(self.max_steps), 50)))
        object.__setattr__(self, "max_replans", max(0, min(int(self.max_replans), 5)))
        object.__setattr__(
            self,
            "max_runtime_seconds",
            max(1, min(int(self.max_runtime_seconds), 600)),
        )
        object.__setattr__(
            self,
            "max_model_calls",
            max(0, min(int(self.max_model_calls), 20)),
        )
        object.__setattr__(
            self,
            "max_live_vision_calls",
            max(0, min(int(self.max_live_vision_calls), 50)),
        )
        object.__setattr__(
            self,
            "max_candidates_per_capability",
            max(1, min(int(self.max_candidates_per_capability), 1000)),
        )
        object.__setattr__(
            self,
            "max_evidence_sent_to_answer",
            max(1, min(int(self.max_evidence_sent_to_answer), 500)),
        )

    def to_dict(self, *, show_plan: bool = False) -> dict[str, Any]:
        return {
            "question_summary": self.question_summary,
            "answer_goal": self.answer_goal,
            "expected_output_type": self.expected_output_type,
            "required_information": list(self.required_information),
            "selected_capabilities": list(self.selected_capabilities),
            "execution_graph": [step.to_dict(show_plan=show_plan) for step in self.execution_graph],
            "stopping_criteria": list(self.stopping_criteria),
            "fallback_strategy": self.fallback_strategy,
            "uncertainty_policy": self.uncertainty_policy,
            "privacy_policy": self.privacy_policy,
            "generated_by": self.generated_by,
            "fallback_used": self.fallback_used,
            "budgets": {
                "max_steps": self.max_steps,
                "max_replans": self.max_replans,
                "max_runtime_seconds": self.max_runtime_seconds,
                "max_model_calls": self.max_model_calls,
                "max_live_vision_calls": self.max_live_vision_calls,
                "max_candidates_per_capability": self.max_candidates_per_capability,
                "max_evidence_sent_to_answer": self.max_evidence_sent_to_answer,
            },
        }

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        generated_by: str,
        fallback_used: bool,
    ) -> "TaskPlan":
        steps = tuple(
            TaskPlanStep.from_mapping(item, index=index)
            for index, item in enumerate(payload.get("execution_graph") or (), start=1)
            if isinstance(item, Mapping)
        )
        selected = _strings(payload.get("selected_capabilities") or ())
        if not selected and steps:
            selected = tuple(step.capability_name for step in steps)
        expected = str(payload.get("expected_output_type") or "answer_text")
        if expected not in EXPECTED_OUTPUT_TYPES:
            expected = "answer_text"
        safe_question_summary = f"{expected} task; raw question hidden"
        return cls(
            question_summary=safe_question_summary,
            answer_goal=str(payload.get("answer_goal") or _default_answer_goal(expected)),
            expected_output_type=expected,
            required_information=_strings(payload.get("required_information") or ()),
            selected_capabilities=selected,
            execution_graph=steps,
            stopping_criteria=_strings(payload.get("stopping_criteria") or ()),
            fallback_strategy=str(payload.get("fallback_strategy") or "Use deterministic local fallback."),
            uncertainty_policy=str(payload.get("uncertainty_policy") or _default_uncertainty_policy()),
            privacy_policy=str(payload.get("privacy_policy") or _default_privacy_policy()),
            generated_by=generated_by,
            fallback_used=fallback_used,
        )


@dataclass(frozen=True)
class Observation:
    """Privacy-safe capability execution observation."""

    step_id: str
    capability_name: str
    status: str
    safe_summary: str
    output_refs: tuple[str, ...] = ()
    evidence_items: tuple[dict[str, Any], ...] = ()
    candidate_count: int = 0
    confidence: float | None = None
    error: str | None = None
    privacy_flags: tuple[str, ...] = ("raw_private_content_hidden",)

    def __post_init__(self) -> None:
        _require_identifier(self.step_id, "step_id")
        _require_identifier(self.capability_name, "capability_name")
        object.__setattr__(self, "safe_summary", _safe_summary(self.safe_summary))
        object.__setattr__(self, "output_refs", _strings(self.output_refs))
        object.__setattr__(self, "privacy_flags", _strings(self.privacy_flags))
        if self.confidence is not None:
            object.__setattr__(self, "confidence", max(0.0, min(float(self.confidence), 1.0)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability_name": self.capability_name,
            "status": self.status,
            "safe_summary": self.safe_summary,
            "output_refs": list(self.output_refs),
            "evidence_items": list(self.evidence_items),
            "candidate_count": self.candidate_count,
            "confidence": self.confidence,
            "error": self.error,
            "privacy_flags": list(self.privacy_flags),
        }


@dataclass(frozen=True)
class CapabilityExecutionOptions:
    max_steps: int = DEFAULT_MAX_STEPS
    max_replans: int = DEFAULT_MAX_REPLANS
    max_runtime_seconds: int = DEFAULT_MAX_RUNTIME_SECONDS
    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    max_live_vision_calls: int = DEFAULT_MAX_LIVE_VISION_CALLS
    max_candidates_per_capability: int = DEFAULT_MAX_CANDIDATES_PER_CAPABILITY
    max_evidence_sent_to_answer: int = DEFAULT_MAX_EVIDENCE_SENT_TO_ANSWER


@dataclass(frozen=True)
class CapabilityExecutionResult:
    task_plan: TaskPlan
    observations: tuple[Observation, ...]
    executed_steps: tuple[dict[str, Any], ...]
    replans: tuple[dict[str, Any], ...] = ()
    budget_exhausted: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_plan": self.task_plan.to_dict(),
            "selected_capabilities": list(self.task_plan.selected_capabilities),
            "executed_steps": list(self.executed_steps),
            "observations": [observation.to_dict() for observation in self.observations],
            "replans": list(self.replans),
            "autonomous_plan": {
                "plan_created": True,
                "generated_by": self.task_plan.generated_by,
                "fallback_used": self.task_plan.fallback_used,
                "expected_output_type": self.task_plan.expected_output_type,
                "selected_capability_count": len(self.task_plan.selected_capabilities),
                "executed_step_count": len(self.executed_steps),
                "observation_count": len(self.observations),
                "replan_count": len(self.replans),
                "budget_exhausted": self.budget_exhausted,
            },
        }


CapabilityHandler = Callable[[TaskPlanStep, dict[str, Any]], Observation]


class DeterministicCapabilityPlanner:
    """Generic local fallback planner for capability composition."""

    def plan(
        self,
        question: str,
        *,
        sources: Iterable[str] = (),
        registry: CapabilityRegistry | None = None,
        options: CapabilityExecutionOptions | None = None,
    ) -> TaskPlan:
        safe_sources = tuple(source for source in sources if source in {"photos", "line", "notes"})
        output_type = _infer_expected_output_type(question)
        selected = _capabilities_for_output_type(output_type, safe_sources)
        steps = _steps_for_capabilities(selected, output_type=output_type)
        parsed_temporal = parse_temporal_event_query(question)
        visual_plan = parse_visual_evidence_query(question)
        required_information = _required_information(output_type, parsed_temporal is not None, visual_plan is not None)
        opts = options or CapabilityExecutionOptions()
        return TaskPlan(
            question_summary=f"{output_type} task; raw question hidden",
            answer_goal=_default_answer_goal(output_type),
            expected_output_type=output_type,
            required_information=required_information,
            selected_capabilities=selected,
            execution_graph=steps,
            stopping_criteria=(
                "Sufficient local evidence is found for the requested output type.",
                "Stop if only weak evidence remains after one repair/replan.",
            ),
            fallback_strategy="Use deterministic local fallback and existing evidence builders when leader planning is unavailable.",
            uncertainty_policy=_default_uncertainty_policy(),
            privacy_policy=_default_privacy_policy(),
            generated_by="deterministic_fallback",
            fallback_used=True,
            max_steps=opts.max_steps,
            max_replans=opts.max_replans,
            max_runtime_seconds=opts.max_runtime_seconds,
            max_model_calls=opts.max_model_calls,
            max_live_vision_calls=opts.max_live_vision_calls,
            max_candidates_per_capability=opts.max_candidates_per_capability,
            max_evidence_sent_to_answer=opts.max_evidence_sent_to_answer,
        )


class LeaderCapabilityPlanner:
    """DeepSeek-backed TaskPlan planner."""

    def __init__(
        self,
        client: ChatModelClient,
        *,
        model: str | None = None,
        max_tokens: int = 700,
        temperature: float = 0.0,
    ) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def plan(
        self,
        question: str,
        *,
        sources: Iterable[str],
        registry: CapabilityRegistry,
        options: CapabilityExecutionOptions | None = None,
    ) -> TaskPlan:
        prompt = _leader_plan_prompt(question, sources=sources, registry=registry, options=options)
        response = self.client.complete(
            ChatRequest(
                messages=(
                    ChatMessage(role="system", content=_CAPABILITY_PLAN_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=prompt),
                ),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            ),
        )
        payload = _extract_json_object(response.text)
        plan = TaskPlan.from_mapping(payload, generated_by="leader", fallback_used=False)
        _validate_plan_capabilities(plan, registry)
        return plan


class CapabilityExecutor:
    """Sequential executor for TaskPlan capability metadata."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        handlers: Mapping[str, CapabilityHandler] | None = None,
    ) -> None:
        self.registry = registry
        self.handlers = dict(handlers or {})

    def execute(
        self,
        plan: TaskPlan,
        *,
        context: dict[str, Any] | None = None,
        trace_recorder: AgentTraceRecorder | None = None,
        options: CapabilityExecutionOptions | None = None,
    ) -> CapabilityExecutionResult:
        opts = options or CapabilityExecutionOptions(
            max_steps=plan.max_steps,
            max_replans=plan.max_replans,
            max_runtime_seconds=plan.max_runtime_seconds,
            max_model_calls=plan.max_model_calls,
            max_live_vision_calls=plan.max_live_vision_calls,
            max_candidates_per_capability=plan.max_candidates_per_capability,
            max_evidence_sent_to_answer=plan.max_evidence_sent_to_answer,
        )
        observations: list[Observation] = []
        executed_steps: list[dict[str, Any]] = []
        replans: list[dict[str, Any]] = []
        budget_exhausted = len(plan.execution_graph) > opts.max_steps
        for index, step in enumerate(plan.execution_graph[: opts.max_steps], start=1):
            spec = self.registry.get(step.capability_name)
            if spec is None:
                observation = Observation(
                    step_id=step.step_id,
                    capability_name=step.capability_name,
                    status="failed",
                    safe_summary="Capability is not registered.",
                    error="UnknownCapability",
                )
                observations.append(observation)
                executed_steps.append(_executed_step_payload(step, observation, index=index))
                replans.append(_replan_payload("missing_capability", step.capability_name))
                if trace_recorder is not None:
                    trace_recorder.event(
                        actor_type="tool",
                        actor_name="CapabilityExecutor",
                        stage="capability_execution",
                        action="execute_capability_step",
                        status="failed",
                        safe_input_summary="capability step metadata only",
                        safe_output_summary="missing capability; raw inputs hidden",
                        error_class="UnknownCapability",
                        safe_error_message="selected capability is not registered",
                        metadata={"capability_name": step.capability_name},
                    )
                continue
            trace_step_id = None
            if trace_recorder is not None:
                trace_step_id = trace_recorder.start(
                    actor_type="tool",
                    actor_name=spec.name,
                    stage="capability_execution",
                    action="execute_capability_step",
                    safe_input_summary="capability input keys only; raw values hidden",
                    reasoning_summary=step.reason_summary,
                    metadata={
                        "capability_name": spec.name,
                        "step_index": index,
                        "cost_level": spec.cost_level,
                        "latency_level": spec.latency_level,
                    },
                )
            try:
                handler = self.handlers.get(step.capability_name)
                observation = (
                    handler(step, dict(context or {}))
                    if handler is not None
                    else _default_capability_observation(step, spec, context or {})
                )
            except Exception as exc:  # pragma: no cover - defensive safety boundary.
                observation = Observation(
                    step_id=step.step_id,
                    capability_name=step.capability_name,
                    status="failed",
                    safe_summary="Capability execution failed safely.",
                    error=exc.__class__.__name__,
                )
            observations.append(observation)
            executed_steps.append(_executed_step_payload(step, observation, index=index))
            if trace_recorder is not None and trace_step_id is not None:
                trace_recorder.finish(
                    trace_step_id,
                    status=observation.status,
                    safe_output_summary=observation.safe_summary,
                    error_class=observation.error,
                    metadata={
                        "candidate_count": observation.candidate_count,
                        "confidence": observation.confidence,
                    },
                )
        if budget_exhausted:
            replans.append(_replan_payload("step_budget_exhausted", f"max_steps={opts.max_steps}"))
        return CapabilityExecutionResult(
            task_plan=plan,
            observations=tuple(observations),
            executed_steps=tuple(executed_steps),
            replans=tuple(replans[: opts.max_replans] if opts.max_replans else ()),
            budget_exhausted=budget_exhausted,
        )


class EvidenceCritic:
    """Post-evidence sufficiency critic for autonomous plan payloads."""

    def critique_payload(self, payload: Mapping[str, Any], plan: TaskPlan) -> dict[str, Any]:
        candidate_dates = int(payload.get("candidate_date_count") or 0)
        matching_photos = int(payload.get("matching_photo_count") or 0)
        evidence_count = int(payload.get("evidence_count") or 0)
        evidence_refs = int(payload.get("evidence_reference_count") or 0)
        if plan.expected_output_type == "candidate_dates":
            sufficient = candidate_dates > 0
            reason = "candidate dates found" if sufficient else "no candidate dates found"
        elif plan.expected_output_type == "photo_gallery":
            sufficient = matching_photos > 0
            reason = "matching photos found" if sufficient else "no matching photos found"
        elif plan.expected_output_type in {"evidence_list", "timeline", "hybrid"}:
            sufficient = evidence_count > 0 or evidence_refs > 0
            reason = "evidence found" if sufficient else "no evidence found"
        else:
            sufficient = evidence_count > 0 or bool(payload.get("answer_succeeded"))
            reason = "answer/evidence available" if sufficient else "no answer evidence available"
        return {
            "sufficient": sufficient,
            "reason": reason,
            "expected_output_type": plan.expected_output_type,
            "candidate_date_count": candidate_dates,
            "matching_photo_count": matching_photos,
            "evidence_count": evidence_count,
            "evidence_reference_count": evidence_refs,
            "critic": "deterministic_evidence_critic",
        }


def build_capability_execution(
    question: str,
    *,
    sources: Iterable[str] = (),
    registry: CapabilityRegistry | None = None,
    leader_planner: LeaderCapabilityPlanner | None = None,
    trace_recorder: AgentTraceRecorder | None = None,
    options: CapabilityExecutionOptions | None = None,
) -> CapabilityExecutionResult:
    """Create and execute a TaskPlan with leader planning plus fallback."""

    safe_registry = registry or CapabilityRegistry.default()
    safe_options = options or CapabilityExecutionOptions()
    step_id = None
    plan: TaskPlan | None = None
    if leader_planner is not None:
        if trace_recorder is not None:
            step_id = trace_recorder.start(
                actor_type="leader_model",
                actor_name="DeepSeek Leader",
                stage="autonomous_capability_planning",
                action="create_task_plan",
                provider="llama_cpp",
                invocation_type="live_call",
                safe_input_summary="capability registry and raw local question sent to local leader",
                decision_summary="Leader selects local capabilities and output type.",
                metadata={"capability_count": len(safe_registry.names())},
            )
        try:
            plan = leader_planner.plan(
                question,
                sources=sources,
                registry=safe_registry,
                options=safe_options,
            )
            if trace_recorder is not None and step_id is not None:
                trace_recorder.finish(
                    step_id,
                    safe_output_summary=(
                        f"task plan created; output_type={plan.expected_output_type}; "
                        f"capabilities={len(plan.selected_capabilities)}"
                    ),
                    metadata={
                        "expected_output_type": plan.expected_output_type,
                        "selected_capability_count": len(plan.selected_capabilities),
                    },
                )
        except Exception as exc:
            if trace_recorder is not None and step_id is not None:
                trace_recorder.finish(
                    step_id,
                    status="failed",
                    error_class=exc.__class__.__name__,
                    safe_error_message="leader capability planning failed; deterministic fallback will be used",
                )
    if plan is None:
        plan = DeterministicCapabilityPlanner().plan(
            question,
            sources=sources,
            registry=safe_registry,
            options=safe_options,
        )
        if trace_recorder is not None:
            trace_recorder.event(
                actor_type="tool",
                actor_name="DeterministicCapabilityPlanner",
                stage="autonomous_capability_planning",
                action="create_task_plan",
                status="fallback_used",
                safe_output_summary=(
                    f"fallback task plan created; output_type={plan.expected_output_type}; "
                    f"capabilities={len(plan.selected_capabilities)}"
                ),
                metadata={
                    "fallback_used": True,
                    "expected_output_type": plan.expected_output_type,
                    "selected_capability_count": len(plan.selected_capabilities),
                },
            )
    return CapabilityExecutor(safe_registry).execute(
        plan,
        context={"question": question, "sources": tuple(sources)},
        trace_recorder=trace_recorder,
        options=safe_options,
    )


def default_capabilities() -> tuple[CapabilitySpec, ...]:
    """Return the initial local capability set."""

    schema = {"type": "object", "additionalProperties": True}
    return (
        CapabilitySpec(
            name="date.parse",
            description="Parse explicit or inferred temporal expressions into date ranges.",
            good_for=("temporal expressions", "month ranges", "relative dates"),
            input_schema=schema,
            output_schema=schema,
        ),
        CapabilitySpec(
            name="metadata.search_by_date_range",
            description="Search indexed metadata such as photo taken_at within a date range.",
            good_for=("date-bounded photo retrieval", "coverage diagnostics"),
            input_schema=schema,
            output_schema=schema,
            required_sources=("photos",),
        ),
        CapabilitySpec(
            name="photo.search_by_concept",
            description="Find photos by visual concept using cached annotations and signals.",
            good_for=("photo gallery", "visual object search", "scene search"),
            input_schema=schema,
            output_schema=schema,
            required_sources=("photos",),
            model_dependency="cached Qwen3-VL annotations",
        ),
        CapabilitySpec(
            name="photo.search_cached_annotations",
            description="Search cached Qwen3-VL photo annotations without live vision calls.",
            good_for=("local visual search", "privacy-safe image metadata"),
            input_schema=schema,
            output_schema=schema,
            required_sources=("photos",),
            model_dependency="Qwen3-VL cached artifacts",
        ),
        CapabilitySpec(
            name="photo.search_by_embedding",
            description="Search photo annotation embeddings when a compatible local index exists.",
            good_for=("semantic photo search", "fuzzy visual concept search"),
            input_schema=schema,
            output_schema=schema,
            required_sources=("photos",),
            cost_level="medium",
            latency_level="medium",
            model_dependency="local embedding model",
        ),
        CapabilitySpec(
            name="vision.verify_images",
            description="Optionally verify top local images with live Qwen3-VL under a strict cap.",
            good_for=("ambiguous visual evidence", "top candidate verification"),
            input_schema=schema,
            output_schema=schema,
            required_sources=("photos",),
            cost_level="high",
            latency_level="high",
            model_dependency="Qwen3-VL live call",
        ),
        CapabilitySpec(
            name="line.search_text",
            description="Search indexed LINE text and return evidence IDs and safe counts.",
            good_for=("conversation evidence", "Japanese text support", "dated text evidence"),
            input_schema=schema,
            output_schema=schema,
            required_sources=("line",),
        ),
        CapabilitySpec(
            name="notes.search_text",
            description="Search indexed notes and return evidence IDs and safe counts.",
            good_for=("note evidence", "long-form local memory", "dated text evidence"),
            input_schema=schema,
            output_schema=schema,
            required_sources=("notes",),
        ),
        CapabilitySpec(
            name="memory.semantic_search",
            description="Run local semantic retrieval over indexed memory when enabled.",
            good_for=("fuzzy text search", "semantic concept expansion"),
            input_schema=schema,
            output_schema=schema,
            optional_sources=("photos", "line", "notes"),
            cost_level="medium",
            latency_level="medium",
            model_dependency="local embedding model",
        ),
        CapabilitySpec(
            name="memory.rerank",
            description="Rerank candidate evidence with a local reranker when enabled.",
            good_for=("candidate ordering", "semantic precision"),
            input_schema=schema,
            output_schema=schema,
            cost_level="medium",
            latency_level="medium",
            model_dependency="local reranker",
        ),
        CapabilitySpec(
            name="evidence.cluster_by_date",
            description="Group dated evidence into daily candidate events.",
            good_for=("when questions", "timeline answers"),
            input_schema=schema,
            output_schema=schema,
        ),
        CapabilitySpec(
            name="evidence.judge",
            description="Judge whether candidate evidence satisfies the requested intent.",
            good_for=("evidence acceptance", "weak evidence detection"),
            input_schema=schema,
            output_schema=schema,
        ),
        CapabilitySpec(
            name="answer.synthesize",
            description="Create a grounded answer or structured unknown from accepted evidence.",
            good_for=("final answer", "uncertainty reporting"),
            input_schema=schema,
            output_schema=schema,
            model_dependency="DeepSeek Leader or deterministic synthesizer",
        ),
        CapabilitySpec(
            name="privacy.filter",
            description="Verify that output hides raw evidence, paths, GPS, EXIF, and raw model output.",
            good_for=("privacy enforcement", "safe UI response"),
            input_schema=schema,
            output_schema=schema,
        ),
        CapabilitySpec(
            name="ui.render_photo_gallery",
            description="Render matching photo evidence as local thumbnail gallery metadata.",
            good_for=("photo gallery output", "visual answers"),
            input_schema=schema,
            output_schema=schema,
            required_sources=("photos",),
        ),
        CapabilitySpec(
            name="ui.render_candidate_dates",
            description="Render temporal candidate dates with grouped evidence metadata.",
            good_for=("candidate date output", "temporal answers"),
            input_schema=schema,
            output_schema=schema,
        ),
        CapabilitySpec(
            name="ui.render_evidence_list",
            description="Render generic evidence metadata grouped by source.",
            good_for=("text evidence output", "hybrid answers"),
            input_schema=schema,
            output_schema=schema,
        ),
    )


def _validate_plan_capabilities(plan: TaskPlan, registry: CapabilityRegistry) -> None:
    missing = [name for name in plan.selected_capabilities if registry.get(name) is None]
    if missing:
        raise ValueError(f"leader selected unavailable capabilities: {missing}")


def _infer_expected_output_type(question: str) -> str:
    text = normalize_text(question)
    asks_when = _has_any(text, ("いつ", "何日", "日を", "時期", "when"))
    photo_or_visual = _has_any(
        text,
        ("写真", "画像", "写って", "映って", "見せて", "どれ", "どの写真", "photo", "image"),
    )
    line_focused = _has_any(text, ("line", "ライン"))
    hybrid = _has_any(text, ("合わせて", "両方", "写真とline", "写真とライン"))
    if hybrid:
        return "hybrid"
    if asks_when:
        return "candidate_dates"
    if photo_or_visual:
        return "photo_gallery"
    if line_focused:
        return "timeline"
    return "answer_text"


def _capabilities_for_output_type(output_type: str, sources: tuple[str, ...]) -> tuple[str, ...]:
    source_set = set(sources or ("photos", "line", "notes"))
    selected: list[str] = []
    if output_type == "candidate_dates":
        selected.append("date.parse")
        if "photos" in source_set:
            selected.extend(("metadata.search_by_date_range", "photo.search_cached_annotations"))
        if "line" in source_set:
            selected.append("line.search_text")
        if "notes" in source_set:
            selected.append("notes.search_text")
        selected.extend(("evidence.cluster_by_date", "evidence.judge", "answer.synthesize", "privacy.filter", "ui.render_candidate_dates"))
    elif output_type == "photo_gallery":
        selected.extend(("photo.search_by_concept", "photo.search_cached_annotations", "memory.semantic_search", "evidence.judge", "answer.synthesize", "privacy.filter", "ui.render_photo_gallery"))
    elif output_type == "timeline":
        selected.extend(("line.search_text", "evidence.cluster_by_date", "answer.synthesize", "privacy.filter", "ui.render_evidence_list"))
    elif output_type == "hybrid":
        if "photos" in source_set:
            selected.extend(("photo.search_by_concept", "photo.search_cached_annotations"))
        if "line" in source_set:
            selected.append("line.search_text")
        if "notes" in source_set:
            selected.append("notes.search_text")
        selected.extend(("memory.semantic_search", "evidence.judge", "answer.synthesize", "privacy.filter", "ui.render_evidence_list"))
    else:
        if "line" in source_set:
            selected.append("line.search_text")
        if "notes" in source_set:
            selected.append("notes.search_text")
        selected.extend(("memory.semantic_search", "memory.rerank", "evidence.judge", "answer.synthesize", "privacy.filter", "ui.render_evidence_list"))
    return tuple(dict.fromkeys(selected))


def _steps_for_capabilities(capabilities: tuple[str, ...], *, output_type: str) -> tuple[TaskPlanStep, ...]:
    steps: list[TaskPlanStep] = []
    for index, name in enumerate(capabilities, start=1):
        steps.append(
            TaskPlanStep(
                step_id=f"cap_{index:02d}",
                capability_name=name,
                input={"source": "planner_context"},
                depends_on=(steps[-1].step_id,) if steps and _depends_on_previous(name) else (),
                reason_summary=_reason_for_capability(name, output_type),
                expected_output=_expected_output_for_capability(name),
                max_results=DEFAULT_MAX_CANDIDATES_PER_CAPABILITY,
            ),
        )
    return tuple(steps)


def _default_capability_observation(
    step: TaskPlanStep,
    spec: CapabilitySpec,
    context: Mapping[str, Any],
) -> Observation:
    question = str(context.get("question") or "")
    if step.capability_name == "date.parse":
        parsed = parse_temporal_event_query(question)
        if parsed is None:
            return Observation(
                step_id=step.step_id,
                capability_name=step.capability_name,
                status="skipped",
                safe_summary="No deterministic temporal expression was required or found.",
                candidate_count=0,
                confidence=0.0,
            )
        return Observation(
            step_id=step.step_id,
            capability_name=step.capability_name,
            status="succeeded",
            safe_summary=f"Temporal query parsed with date_range_status={parsed.date_range.status}.",
            output_refs=("date_range",),
            candidate_count=1,
            confidence=parsed.date_range.confidence,
        )
    if step.capability_name in {"photo.search_by_concept", "photo.search_cached_annotations"}:
        plan = parse_visual_evidence_query(question)
        signal_count = len(plan.visual_signals) if plan is not None else 0
        return Observation(
            step_id=step.step_id,
            capability_name=step.capability_name,
            status="succeeded",
            safe_summary=f"Visual/photo search planned with signal_count={signal_count}.",
            output_refs=("photo_candidates",),
            candidate_count=signal_count,
            confidence=0.5 if signal_count else 0.2,
        )
    if step.capability_name in {"line.search_text", "notes.search_text"}:
        return Observation(
            step_id=step.step_id,
            capability_name=step.capability_name,
            status="succeeded",
            safe_summary=f"{spec.name} scheduled; raw text remains hidden.",
            output_refs=("text_evidence_candidates",),
            candidate_count=0,
            confidence=0.5,
        )
    if step.capability_name == "privacy.filter":
        return Observation(
            step_id=step.step_id,
            capability_name=step.capability_name,
            status="succeeded",
            safe_summary="Privacy filter planned for final response.",
            confidence=1.0,
        )
    return Observation(
        step_id=step.step_id,
        capability_name=step.capability_name,
        status="succeeded",
        safe_summary=f"{spec.name} planned and delegated to existing local workflow.",
        output_refs=(spec.name,),
        candidate_count=0,
        confidence=0.5,
    )


def _executed_step_payload(step: TaskPlanStep, observation: Observation, *, index: int) -> dict[str, Any]:
    return {
        "step_index": index,
        "step_id": step.step_id,
        "capability_name": step.capability_name,
        "status": observation.status,
        "safe_summary": observation.safe_summary,
        "candidate_count": observation.candidate_count,
        "confidence": observation.confidence,
        "privacy_flags": list(observation.privacy_flags),
    }


def _replan_payload(reason: str, detail: str) -> dict[str, Any]:
    return {
        "replan_id": f"replan_{reason}",
        "reason": reason,
        "safe_detail": _safe_summary(detail),
        "status": "proposed",
    }


def _leader_plan_prompt(
    question: str,
    *,
    sources: Iterable[str],
    registry: CapabilityRegistry,
    options: CapabilityExecutionOptions | None,
) -> str:
    opts = options or CapabilityExecutionOptions()
    return json.dumps(
        {
            "task": "create_task_plan",
            "question": question,
            "available_sources": list(sources),
            "available_capabilities": registry.summaries(),
            "required_shape": {
                "question_summary": "safe summary; no raw private evidence",
                "answer_goal": "what answer should accomplish",
                "expected_output_type": sorted(EXPECTED_OUTPUT_TYPES),
                "required_information": ["safe information requirements"],
                "selected_capabilities": ["capability.name"],
                "execution_graph": [
                    {
                        "step_id": "cap_01",
                        "capability_name": "date.parse",
                        "input": {"only_safe_keys": True},
                        "depends_on": [],
                        "reason_summary": "safe reason summary",
                        "expected_output": "safe output description",
                        "max_results": 20,
                        "max_cost": "low",
                    },
                ],
                "stopping_criteria": ["evidence sufficiency criteria"],
                "fallback_strategy": "what to do if weak evidence",
                "uncertainty_policy": "how to answer if insufficient",
                "privacy_policy": "hide raw private content",
            },
            "budgets": {
                "max_steps": opts.max_steps,
                "max_replans": opts.max_replans,
                "max_runtime_seconds": opts.max_runtime_seconds,
                "max_model_calls": opts.max_model_calls,
                "max_live_vision_calls": opts.max_live_vision_calls,
            },
        },
        ensure_ascii=False,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("leader did not return a JSON object")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("leader plan JSON must be an object")
    return payload


def _required_information(
    output_type: str,
    has_temporal_parse: bool,
    has_visual_plan: bool,
) -> tuple[str, ...]:
    items = ["local evidence availability", "source coverage", "evidence relevance"]
    if output_type == "candidate_dates":
        items.extend(("date or inferred date scope", "dated evidence", "daily clustering"))
    if output_type == "photo_gallery":
        items.extend(("visual signals", "cached photo annotations"))
    if has_temporal_parse:
        items.append("parsed temporal expression")
    if has_visual_plan:
        items.append("visual target signals")
    return tuple(dict.fromkeys(items))


def _depends_on_previous(name: str) -> bool:
    return name.startswith(("evidence.", "answer.", "privacy.", "ui.")) or name in {
        "memory.rerank",
    }


def _reason_for_capability(name: str, output_type: str) -> str:
    if name == "date.parse":
        return "Needed to determine date scope for temporal evidence."
    if name.startswith("photo."):
        return "Needed to inspect local photo evidence for the requested goal."
    if name.startswith("line."):
        return "Needed to inspect local LINE evidence metadata for support."
    if name.startswith("notes."):
        return "Needed to inspect local note evidence metadata for support."
    if name == "evidence.cluster_by_date":
        return "Needed because the requested output depends on dates or timeline structure."
    if name == "ui.render_photo_gallery":
        return "Needed because the expected output is a photo gallery."
    if name == "ui.render_candidate_dates":
        return "Needed because the expected output is candidate dates."
    return f"Needed for {output_type} output."


def _expected_output_for_capability(name: str) -> str:
    if name.startswith("ui."):
        return "safe UI display metadata"
    if name.startswith("photo."):
        return "photo evidence IDs and safe metadata"
    if name.startswith(("line.", "notes.")):
        return "text evidence IDs and safe metadata"
    if name.startswith("evidence."):
        return "evidence relevance or grouping metadata"
    return "safe observation metadata"


def _default_answer_goal(output_type: str) -> str:
    labels = {
        "candidate_dates": "Return candidate dates grounded in local evidence.",
        "photo_gallery": "Return matching photo evidence as a gallery or structured unknown.",
        "evidence_list": "Return relevant evidence metadata and a grounded answer.",
        "timeline": "Return dated text evidence and a timeline-style answer.",
        "hybrid": "Combine multiple local evidence sources into a grounded answer.",
        "answer_text": "Answer from local evidence or explain insufficient evidence.",
    }
    return labels.get(output_type, labels["answer_text"])


def _default_uncertainty_policy() -> str:
    return "If evidence is weak or missing, return structured uncertainty instead of guessing."


def _default_privacy_policy() -> str:
    return "Hide raw evidence, raw prompts, paths, GPS, EXIF, OCR dumps, and raw model output."


def _safe_step_input(values: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, (int, float, bool)) or value is None:
            safe[str(key)] = value
        elif isinstance(value, str):
            safe[str(key)] = _safe_summary(value)
        elif isinstance(value, (list, tuple)):
            safe[str(key)] = [_safe_summary(str(item)) for item in value[:20]]
        else:
            safe[str(key)] = f"{type(value).__name__}_hidden"
    return safe


def _safe_summary(value: str, *, max_len: int = 180) -> str:
    text = re.sub(r"[/\\][^\s]+", "[path hidden]", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "..."
    return text


def _require_identifier(value: str, label: str) -> None:
    if not value or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ValueError(f"invalid {label}: {value!r}")


def _strings(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,)
    if not isinstance(values, Iterable):
        return (str(values),)
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)

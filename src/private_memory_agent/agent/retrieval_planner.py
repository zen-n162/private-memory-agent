"""Leader-guided retrieval planning and evidence relevance scoring."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from private_memory_agent.retrieval import Evidence
from private_memory_agent.retrieval.text import extract_query_terms, normalize_text
from private_memory_agent.runtime import ChatMessage, ChatModelClient, ChatRequest

SUPPORTED_PLAN_SOURCES = {"photos", "line", "notes"}
SPECIFICITY_VALUES = {"specific", "generic", "weak", "unrelated"}
_GENERIC_CONCEPTS = {
    "研究",
    "準備",
    "予定",
    "記録",
    "外出",
    "屋外",
    "写真",
    "画像",
    "メモ",
    "ノート",
    "line",
    "ライン",
    "確認",
    "関係",
}

_PLANNER_SYSTEM_PROMPT = """\
You are the retrieval planning component for Private Memory Agent.
The user's question may contain private local context.
Return only one JSON object. Do not include markdown, explanations, or chain-of-thought.
Do not answer the question. Build a retrieval plan.
Distinguish specific identifiers from generic context words.
Use only source labels from: photos, line, notes.
"""

_PLAN_KEYS = {
    "intent",
    "main_entities",
    "specific_concepts",
    "generic_concepts",
    "temporal_hints",
    "source_preferences",
    "source_constraints",
    "retrieval_queries",
    "excluded_concepts",
    "evidence_acceptance_criteria",
    "uncertainty_notes",
}


@dataclass(frozen=True)
class RetrievalPlan:
    """Validated structured retrieval plan."""

    intent: str
    main_entities: tuple[str, ...] = ()
    specific_concepts: tuple[str, ...] = ()
    generic_concepts: tuple[str, ...] = ()
    temporal_hints: tuple[str, ...] = ()
    source_preferences: tuple[str, ...] = ()
    source_constraints: tuple[str, ...] = ()
    retrieval_queries: tuple[str, ...] = ()
    excluded_concepts: tuple[str, ...] = ()
    evidence_acceptance_criteria: tuple[str, ...] = ()
    uncertainty_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        intent = str(self.intent or "").strip()
        if not intent:
            raise ValueError("retrieval plan intent is required")
        object.__setattr__(self, "intent", intent)
        for field_name in (
            "main_entities",
            "specific_concepts",
            "generic_concepts",
            "temporal_hints",
            "retrieval_queries",
            "excluded_concepts",
            "evidence_acceptance_criteria",
            "uncertainty_notes",
        ):
            object.__setattr__(self, field_name, _unique_strings(getattr(self, field_name)))
        object.__setattr__(
            self,
            "source_preferences",
            _unique_sources(self.source_preferences),
        )
        object.__setattr__(
            self,
            "source_constraints",
            _unique_sources(self.source_constraints),
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "RetrievalPlan":
        """Build a plan from a JSON-like mapping."""

        if not isinstance(payload, dict):
            raise ValueError("retrieval plan must be a JSON object")
        missing = _PLAN_KEYS - set(payload)
        if missing:
            raise ValueError(f"retrieval plan missing keys: {sorted(missing)}")
        return cls(
            intent=_string_value(payload.get("intent")),
            main_entities=_string_tuple(payload.get("main_entities")),
            specific_concepts=_string_tuple(payload.get("specific_concepts")),
            generic_concepts=_string_tuple(payload.get("generic_concepts")),
            temporal_hints=_string_tuple(payload.get("temporal_hints")),
            source_preferences=_string_tuple(payload.get("source_preferences")),
            source_constraints=_string_tuple(payload.get("source_constraints")),
            retrieval_queries=_string_tuple(payload.get("retrieval_queries")),
            excluded_concepts=_string_tuple(payload.get("excluded_concepts")),
            evidence_acceptance_criteria=_string_tuple(
                payload.get("evidence_acceptance_criteria"),
            ),
            uncertainty_notes=_string_tuple(payload.get("uncertainty_notes")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the full plan. This may contain private question-derived text."""

        return {
            "intent": self.intent,
            "main_entities": list(self.main_entities),
            "specific_concepts": list(self.specific_concepts),
            "generic_concepts": list(self.generic_concepts),
            "temporal_hints": list(self.temporal_hints),
            "source_preferences": list(self.source_preferences),
            "source_constraints": list(self.source_constraints),
            "retrieval_queries": list(self.retrieval_queries),
            "excluded_concepts": list(self.excluded_concepts),
            "evidence_acceptance_criteria": list(self.evidence_acceptance_criteria),
            "uncertainty_notes": list(self.uncertainty_notes),
        }

    def metadata(self, *, show_plan: bool = False) -> "RetrievalPlanMetadata":
        """Return privacy-safe counters, with optional explicit plan payload."""

        return RetrievalPlanMetadata(
            plan_created=True,
            retrieval_query_count=len(self.retrieval_queries),
            main_entity_count=len(self.main_entities),
            specific_concept_count=len(self.specific_concepts),
            generic_concept_count=len(self.generic_concepts),
            source_preferences=self.source_preferences,
            source_constraints=self.source_constraints,
            evidence_acceptance_criteria_count=len(self.evidence_acceptance_criteria),
            plan=self.to_dict() if show_plan else None,
        )


@dataclass(frozen=True)
class RetrievalPlanMetadata:
    """Privacy-safe retrieval plan metadata."""

    plan_created: bool = False
    retrieval_query_count: int = 0
    main_entity_count: int = 0
    specific_concept_count: int = 0
    generic_concept_count: int = 0
    source_preferences: tuple[str, ...] = ()
    source_constraints: tuple[str, ...] = ()
    evidence_acceptance_criteria_count: int = 0
    plan: dict[str, Any] | None = None
    error_class: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_created": self.plan_created,
            "retrieval_query_count": self.retrieval_query_count,
            "main_entity_count": self.main_entity_count,
            "specific_concept_count": self.specific_concept_count,
            "generic_concept_count": self.generic_concept_count,
            "source_preferences": list(self.source_preferences),
            "source_constraints": list(self.source_constraints),
            "evidence_acceptance_criteria_count": self.evidence_acceptance_criteria_count,
            "plan": self.plan,
            "error_class": self.error_class,
            "error_message": self.error_message,
        }


class RetrievalPlanner(Protocol):
    """Interface for question-to-retrieval-plan adapters."""

    def plan(self, question: str) -> RetrievalPlan:
        """Return a validated retrieval plan."""


class FakeRetrievalPlanner:
    """Deterministic fake planner for tests."""

    def __init__(self, plan: RetrievalPlan | None = None) -> None:
        self.plan_to_return = plan or RetrievalPlan(
            intent="fake retrieval intent",
            main_entities=("synthetic",),
            specific_concepts=("specific",),
            generic_concepts=("generic",),
            source_preferences=("line", "notes"),
            source_constraints=("line", "notes"),
            retrieval_queries=("specific synthetic",),
            evidence_acceptance_criteria=("contains specific concept",),
        )
        self.questions: list[str] = []

    def plan(self, question: str) -> RetrievalPlan:
        self.questions.append(question)
        return self.plan_to_return


class DeterministicRuleBasedRetrievalPlanner:
    """Small local fallback planner with no model calls."""

    def plan(self, question: str) -> RetrievalPlan:
        normalized = normalize_text(question)
        terms = extract_query_terms(normalized, max_terms=12)
        specific: list[str] = []
        generic: list[str] = []
        for term in terms:
            if _is_generic_concept(term):
                generic.append(term)
            else:
                specific.append(term)
        source_preferences = _infer_sources(normalized)
        retrieval_terms = specific or tuple(term for term in terms if term)
        retrieval_query = " ".join(retrieval_terms) if retrieval_terms else normalized
        return RetrievalPlan(
            intent="retrieve evidence for the user's question",
            main_entities=tuple(specific[:4]),
            specific_concepts=tuple(specific[:8]),
            generic_concepts=tuple(generic[:8]),
            temporal_hints=_temporal_hints(normalized),
            source_preferences=source_preferences,
            source_constraints=source_preferences,
            retrieval_queries=(retrieval_query,) if retrieval_query else (),
            excluded_concepts=(),
            evidence_acceptance_criteria=(
                "prefer evidence containing specific concepts",
                "avoid generic-only evidence when specific concepts exist",
            ),
            uncertainty_notes=("fall back to uncertainty when evidence is generic",),
        )


class LeaderRetrievalPlanner:
    """Retrieval planner backed by a local chat model client."""

    def __init__(
        self,
        chat_client: ChatModelClient,
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        response_format_json: bool = False,
    ) -> None:
        self.chat_client = chat_client
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.response_format_json = response_format_json

    def plan(self, question: str) -> RetrievalPlan:
        response = self.chat_client.complete(
            ChatRequest(
                messages=(
                    ChatMessage(role="system", content=_PLANNER_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=_planner_prompt(question)),
                ),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"} if self.response_format_json else None,
            ),
        )
        try:
            payload = _extract_json_object(response.text)
            return RetrievalPlan.from_mapping(payload)
        except ValueError:
            fallback = DeterministicRuleBasedRetrievalPlanner().plan(question)
            return replace_plan_uncertainty(
                fallback,
                "leader planner output was invalid; deterministic fallback was used",
            )


@dataclass(frozen=True)
class EvidenceRelevanceScore:
    """Privacy-safe relevance score for one evidence item."""

    evidence_id: str
    relevance_score: float
    specificity: str
    should_use: bool
    reason_category: str
    matched_plan_concepts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.specificity not in SPECIFICITY_VALUES:
            raise ValueError("unsupported evidence specificity")
        object.__setattr__(
            self,
            "relevance_score",
            max(0.0, min(1.0, float(self.relevance_score))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "relevance_score": self.relevance_score,
            "specificity": self.specificity,
            "should_use": self.should_use,
            "reason_category": self.reason_category,
            "matched_plan_concepts": list(self.matched_plan_concepts),
        }


class EvidenceRelevanceJudge(Protocol):
    """Interface for plan-aware evidence relevance scoring."""

    def score(
        self,
        evidence: tuple[Evidence, ...],
        plan: RetrievalPlan,
    ) -> tuple[EvidenceRelevanceScore, ...]:
        """Return one score per evidence item."""


class DeterministicEvidenceRelevanceJudge:
    """Local deterministic relevance judge."""

    def score(
        self,
        evidence: tuple[Evidence, ...],
        plan: RetrievalPlan,
    ) -> tuple[EvidenceRelevanceScore, ...]:
        scores: list[EvidenceRelevanceScore] = []
        specific_terms = _normalized_pairs(plan.specific_concepts + plan.main_entities)
        generic_terms = _normalized_pairs(plan.generic_concepts)
        negative_terms = _normalized_pairs(plan.excluded_concepts)
        has_specific_plan = bool(specific_terms)
        for item in evidence:
            text = normalize_text(" ".join(part for part in (item.title, item.snippet) if part))
            specific_hits = _matching_original_terms(text, specific_terms)
            generic_hits = _matching_original_terms(text, generic_terms)
            negative_hits = _matching_original_terms(text, negative_terms)
            if negative_hits:
                scores.append(
                    EvidenceRelevanceScore(
                        evidence_id=item.evidence_id,
                        relevance_score=0.0,
                        specificity="unrelated",
                        should_use=False,
                        reason_category="negative_concept",
                        matched_plan_concepts=negative_hits,
                    ),
                )
                continue
            if specific_hits:
                scores.append(
                    EvidenceRelevanceScore(
                        evidence_id=item.evidence_id,
                        relevance_score=min(1.0, 0.72 + 0.08 * len(specific_hits)),
                        specificity="specific",
                        should_use=True,
                        reason_category="specific_concept_match",
                        matched_plan_concepts=specific_hits,
                    ),
                )
                continue
            if generic_hits:
                scores.append(
                    EvidenceRelevanceScore(
                        evidence_id=item.evidence_id,
                        relevance_score=0.35 if has_specific_plan else 0.55,
                        specificity="generic",
                        should_use=not has_specific_plan,
                        reason_category="generic_only_match",
                        matched_plan_concepts=generic_hits,
                    ),
                )
                continue
            scores.append(
                EvidenceRelevanceScore(
                    evidence_id=item.evidence_id,
                    relevance_score=0.15,
                    specificity="weak",
                    should_use=False,
                    reason_category="no_plan_concept_match",
                ),
            )
        return tuple(scores)


class FakeEvidenceRelevanceJudge:
    """Fake judge with optional fixed score map."""

    def __init__(
        self,
        scores: dict[str, EvidenceRelevanceScore] | None = None,
    ) -> None:
        self.scores = scores or {}
        self.calls: list[tuple[Evidence, ...]] = []

    def score(
        self,
        evidence: tuple[Evidence, ...],
        plan: RetrievalPlan,
    ) -> tuple[EvidenceRelevanceScore, ...]:
        self.calls.append(evidence)
        fallback = DeterministicEvidenceRelevanceJudge().score(evidence, plan)
        fallback_by_id = {score.evidence_id: score for score in fallback}
        return tuple(
            self.scores.get(item.evidence_id, fallback_by_id[item.evidence_id])
            for item in evidence
        )


def rerank_evidence_by_relevance(
    evidence: tuple[Evidence, ...],
    scores: tuple[EvidenceRelevanceScore, ...],
    *,
    limit: int,
) -> tuple[Evidence, ...]:
    """Return evidence sorted by relevance, preserving only usable items first."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    score_by_id = {score.evidence_id: score for score in scores}
    ranked = sorted(
        evidence,
        key=lambda item: (
            not score_by_id.get(item.evidence_id, _weak_score(item.evidence_id)).should_use,
            -score_by_id.get(item.evidence_id, _weak_score(item.evidence_id)).relevance_score,
            -item.score,
            item.source_kind,
            item.source_table,
            item.source_id,
        ),
    )
    return tuple(ranked[:limit])


def relevance_summary(scores: tuple[EvidenceRelevanceScore, ...]) -> dict[str, Any]:
    """Return privacy-safe aggregate relevance counters."""

    specificity_counts: dict[str, int] = {}
    for score in scores:
        specificity_counts[score.specificity] = specificity_counts.get(score.specificity, 0) + 1
    average = None
    if scores:
        average = round(sum(score.relevance_score for score in scores) / len(scores), 4)
    return {
        "relevance_judged": bool(scores),
        "average_relevance_score": average,
        "should_use_count": sum(1 for score in scores if score.should_use),
        "specificity_counts": specificity_counts,
    }


def plan_metadata_for_error(exc: BaseException) -> RetrievalPlanMetadata:
    """Return safe metadata for a planning failure."""

    return RetrievalPlanMetadata(
        plan_created=False,
        error_class=exc.__class__.__name__,
        error_message=_safe_error_message(exc),
    )


def replace_plan_uncertainty(plan: RetrievalPlan, note: str) -> RetrievalPlan:
    """Return a copy of a plan with an additional uncertainty note."""

    return RetrievalPlan(
        intent=plan.intent,
        main_entities=plan.main_entities,
        specific_concepts=plan.specific_concepts,
        generic_concepts=plan.generic_concepts,
        temporal_hints=plan.temporal_hints,
        source_preferences=plan.source_preferences,
        source_constraints=plan.source_constraints,
        retrieval_queries=plan.retrieval_queries,
        excluded_concepts=plan.excluded_concepts,
        evidence_acceptance_criteria=plan.evidence_acceptance_criteria,
        uncertainty_notes=(*plan.uncertainty_notes, note),
    )


def _planner_prompt(question: str) -> str:
    schema = {
        "intent": "short retrieval intent",
        "main_entities": ["specific person/project/place names if present"],
        "specific_concepts": ["specific identifiers, project names, unusual terms"],
        "generic_concepts": ["generic words such as preparation, research, outing"],
        "temporal_hints": ["dates or relative time hints"],
        "source_preferences": ["line", "notes"],
        "source_constraints": ["line", "notes"],
        "retrieval_queries": ["short search query 1", "short search query 2"],
        "excluded_concepts": ["off-topic concepts"],
        "evidence_acceptance_criteria": ["what evidence must show"],
        "uncertainty_notes": ["what would remain unknown"],
    }
    return "\n".join(
        [
            "Create a retrieval plan for this private-memory question.",
            "Do not answer the question.",
            "Question:",
            question,
            "",
            "Output JSON schema:",
            json.dumps(schema, ensure_ascii=False),
            "",
            "Rules:",
            "- Put project names, IDs, unique organizations, dates, and unusual terms in specific_concepts.",
            "- Put broad context words in generic_concepts.",
            "- Do not over-weight generic words such as research, preparation, memo, photo, or outing.",
            "- Keep retrieval_queries short.",
            "- Return only one JSON object.",
        ],
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = re.sub(r"<think\b[^>]*>.*?</think>", "", str(text or ""), flags=re.I | re.S).strip()
    if not raw:
        raise ValueError("retrieval planner returned empty output")
    candidates = [raw]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", raw, flags=re.I | re.S)
    )
    candidates.extend(_balanced_json_candidates(raw))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("retrieval planner did not return a valid JSON object")


def _balanced_json_candidates(text: str) -> tuple[str, ...]:
    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : index + 1])
                start = None
    return tuple(candidates)


def _infer_sources(normalized_question: str) -> tuple[str, ...]:
    sources: list[str] = []
    if any(term in normalized_question for term in ("写真", "画像", "photo", "外出", "屋外")):
        sources.append("photos")
    if any(term in normalized_question for term in ("line", "ライン", "メッセージ", "チャット")):
        sources.append("line")
    if any(term in normalized_question for term in ("メモ", "ノート", "note")):
        sources.append("notes")
    if not sources:
        sources.extend(("line", "notes"))
    return tuple(dict.fromkeys(sources))


def _temporal_hints(normalized_question: str) -> tuple[str, ...]:
    hints = re.findall(r"\b\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,2})?\b", normalized_question)
    if "最近" in normalized_question:
        hints.append("recent")
    return tuple(dict.fromkeys(hints))


def _is_generic_concept(term: str) -> bool:
    normalized = normalize_text(term)
    if normalized in _GENERIC_CONCEPTS:
        return True
    if len(normalized) <= 2 and not re.search(r"[A-Z0-9]", term):
        return True
    return False


def _unique_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _unique_sources(values: tuple[str, ...]) -> tuple[str, ...]:
    sources = _unique_strings(tuple(value.lower() for value in values))
    unknown = set(sources) - SUPPORTED_PLAN_SOURCES
    if unknown:
        raise ValueError(f"unsupported retrieval plan sources: {sorted(unknown)}")
    return sources


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _string_value(value: object) -> str:
    return str(value or "").strip()


def _normalized_pairs(values: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (value, normalize_text(value))
        for value in _unique_strings(values)
        if normalize_text(value)
    )


def _matching_original_terms(
    normalized_text: str,
    pairs: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    return tuple(original for original, normalized in pairs if normalized in normalized_text)


def _weak_score(evidence_id: str) -> EvidenceRelevanceScore:
    return EvidenceRelevanceScore(
        evidence_id=evidence_id,
        relevance_score=0.0,
        specificity="weak",
        should_use=False,
        reason_category="not_scored",
    )


def _safe_error_message(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if "/" in message or "\\" in message:
        return "retrieval planning failed"
    return message[:160]

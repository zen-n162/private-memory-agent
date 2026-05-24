"""Deterministic privacy and grounding guardrails for local query answers."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from private_memory_agent.retrieval import Evidence, REDACTED_TEXT

PRIVATE_LOG_BLOCKED = "[private log blocked]"
_NAME_REPLACEMENT = "[name redacted]"
_COMMON_JAPANESE_SURNAMES = (
    "佐藤",
    "鈴木",
    "高橋",
    "田中",
    "伊藤",
    "渡辺",
    "山本",
    "中村",
    "小林",
    "加藤",
    "吉田",
    "山田",
    "佐々木",
    "山口",
    "松本",
    "井上",
    "木村",
    "林",
    "清水",
    "斎藤",
    "藤田",
    "後藤",
    "岡田",
    "長谷川",
)
_JAPANESE_NAME_RE = re.compile(
    rf"(?:{'|'.join(_COMMON_JAPANESE_SURNAMES)})[一-龯ぁ-んァ-ヶ]{{1,4}}",
)
_LATIN_NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b")
_CLAIM_SPLIT_RE = re.compile(r"[。.!?！？\n]+")
_GPS_KEYS = {
    "gps",
    "gpsinfo",
    "gps_info",
    "latitude",
    "lat",
    "longitude",
    "lon",
    "lng",
}
_SOURCE_INJECTION_RES = (
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(?:previous|prior|above)\s+instructions", re.I),
    re.compile(r"system\s*prompt", re.I),
    re.compile(r"developer\s+message", re.I),
    re.compile(r"tool\s+call", re.I),
    re.compile(r"これまでの指示を無視"),
    re.compile(r"前(?:の|までの)?指示を無視"),
    re.compile(r"指示を無視"),
)


class AnswerValidationError(ValueError):
    """Raised when a structured answer is malformed or ungrounded."""


@dataclass(frozen=True)
class PrivacyGuardPolicy:
    """Deterministic privacy policy for display and logging."""

    redact_names: bool = True
    redact_gps_precision: bool = True
    gps_decimal_places: int = 2
    block_private_logs: bool = True
    extra_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class CriticPolicy:
    """Rule-based evidence critic policy."""

    weak_confidence_threshold: float = 0.5
    weak_score_threshold: float = 0.5
    max_confidence_for_weak_evidence: float = 0.45
    require_uncertainty_for_weak_evidence: bool = True


@dataclass(frozen=True)
class CriticIssue:
    """A deterministic critic finding."""

    code: str
    message: str
    severity: str = "error"


class PrivacyGuard:
    """Applies deterministic privacy redaction before display or logs."""

    def __init__(self, policy: PrivacyGuardPolicy | None = None) -> None:
        self.policy = policy or PrivacyGuardPolicy()

    def redact_question(self, question: str, *, redact_private: bool) -> str:
        if redact_private and question:
            return REDACTED_TEXT
        return self.redact_text(question)

    def redact_answer(self, answer: Any, *, redact_private: bool) -> Any:
        from private_memory_agent.agent.leader import Answer

        if redact_private and not answer.is_insufficient_evidence:
            conclusion = REDACTED_TEXT if answer.conclusion else answer.conclusion
            unknowns = tuple(REDACTED_TEXT for _ in answer.unknowns)
        else:
            conclusion = self.redact_text(answer.conclusion)
            unknowns = tuple(self.redact_text(item) for item in answer.unknowns)
        return Answer(
            conclusion=conclusion,
            evidence_references=answer.evidence_references,
            confidence=answer.confidence,
            unknowns=unknowns,
            used_sources=answer.used_sources,
        )

    def redact_evidence(
        self,
        evidence: tuple[Evidence, ...] | list[Evidence],
        *,
        redact_private: bool,
    ) -> tuple[Evidence, ...]:
        redacted: list[Evidence] = []
        for item in self.mark_sensitive_evidence(evidence):
            title = item.title
            snippet = item.snippet
            if redact_private:
                title = REDACTED_TEXT if title else title
                snippet = REDACTED_TEXT if snippet else snippet
            else:
                title = None if title is None else self.redact_text(title)
                snippet = self.redact_text(snippet)
            redacted.append(replace(item, title=title, snippet=snippet))
        return tuple(redacted)

    def mark_sensitive_evidence(
        self,
        evidence: tuple[Evidence, ...] | list[Evidence],
    ) -> tuple[Evidence, ...]:
        return tuple(self.mark_sensitive_item(item) for item in evidence)

    def mark_sensitive_item(self, evidence: Evidence) -> Evidence:
        text = "\n".join(part for part in (evidence.title, evidence.snippet) if part)
        flags = set(_string_list(evidence.metadata.get("privacy_flags")))
        if text:
            flags.add("private_text")
        if self.contains_name(text):
            flags.add("name")
        if contains_source_injection(text):
            flags.add("source_injection")
        if _contains_gps(evidence.metadata):
            flags.add("gps")

        metadata = self.redact_gps_metadata(evidence.metadata)
        if flags:
            metadata["sensitive"] = True
            metadata["privacy_flags"] = sorted(flags)
        return replace(evidence, metadata=metadata)

    def redact_text(self, text: str) -> str:
        if not self.policy.redact_names or not text:
            return text
        redacted = text
        for name in self.policy.extra_names:
            name = name.strip()
            if name:
                redacted = redacted.replace(name, _NAME_REPLACEMENT)
        redacted = _JAPANESE_NAME_RE.sub(_NAME_REPLACEMENT, redacted)
        return _LATIN_NAME_RE.sub(_NAME_REPLACEMENT, redacted)

    def contains_name(self, text: str) -> bool:
        if not text:
            return False
        if any(name.strip() and name.strip() in text for name in self.policy.extra_names):
            return True
        return bool(_JAPANESE_NAME_RE.search(text) or _LATIN_NAME_RE.search(text))

    def redact_gps_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        if not self.policy.redact_gps_precision:
            return dict(metadata)
        return _redact_gps_values(metadata, places=self.policy.gps_decimal_places)

    def safe_log_message(
        self,
        message: str,
        *,
        private_fragments: tuple[str, ...] = (),
        evidence: tuple[Evidence, ...] = (),
    ) -> str:
        if not self.policy.block_private_logs:
            return self.redact_text(message)
        fragments = {
            fragment.strip()
            for fragment in private_fragments
            if fragment and len(fragment.strip()) >= 4
        }
        for item in evidence:
            fragments.update(
                fragment.strip()
                for fragment in (item.title, item.snippet)
                if fragment and len(fragment.strip()) >= 4
            )
        if any(fragment in message for fragment in fragments):
            return PRIVATE_LOG_BLOCKED
        return self.redact_text(message)


class EvidenceCritic:
    """Validates that answer claims are grounded in retrieved evidence."""

    def __init__(
        self,
        evidence: tuple[Evidence, ...],
        *,
        policy: CriticPolicy | None = None,
    ) -> None:
        self.evidence = evidence
        self.policy = policy or CriticPolicy()
        self.evidence_ids = {item.evidence_id for item in evidence}
        self.source_kinds = {item.source_kind for item in evidence}

    def validate(self, answer: Any) -> None:
        issues = self.check(answer)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            codes = ", ".join(issue.code for issue in errors)
            raise AnswerValidationError(f"answer failed critic checks: {codes}")

    def check(self, answer: Any) -> tuple[CriticIssue, ...]:
        issues: list[CriticIssue] = []
        if not self.evidence:
            if answer.evidence_references:
                issues.append(
                    CriticIssue(
                        code="evidence_reference_without_evidence",
                        message="answer references evidence but none was retrieved",
                    ),
                )
            return tuple(issues)

        if answer.is_insufficient_evidence:
            return tuple(issues)

        issues.extend(self._reference_issues(answer))
        issues.extend(self._claim_support_issues(answer))
        issues.extend(self._weak_evidence_issues(answer))
        issues.extend(self._source_injection_issues(answer))
        return tuple(issues)

    def _reference_issues(self, answer: Any) -> list[CriticIssue]:
        issues: list[CriticIssue] = []
        if not answer.evidence_references:
            issues.append(
                CriticIssue(
                    code="missing_evidence_reference",
                    message="answer must reference at least one evidence item",
                ),
            )
        if not answer.used_sources:
            issues.append(
                CriticIssue(
                    code="missing_used_source",
                    message="answer must include at least one used source",
                ),
            )

        unknown_refs = set(answer.evidence_references) - self.evidence_ids
        if unknown_refs:
            issues.append(
                CriticIssue(
                    code="unknown_evidence_reference",
                    message="answer references evidence ids that were not retrieved",
                ),
            )
        unknown_sources = set(answer.used_sources) - self.source_kinds
        if unknown_sources:
            issues.append(
                CriticIssue(
                    code="unknown_used_source",
                    message="answer uses source kinds that were not retrieved",
                ),
            )

        referenced_sources = {
            item.source_kind
            for item in self.evidence
            if item.evidence_id in set(answer.evidence_references)
        }
        if answer.used_sources and not set(answer.used_sources) <= referenced_sources:
            issues.append(
                CriticIssue(
                    code="used_source_not_referenced",
                    message="used_sources must be backed by referenced evidence",
                ),
            )
        return issues

    def _claim_support_issues(self, answer: Any) -> list[CriticIssue]:
        claims = _extract_claims(answer.conclusion)
        known_refs = [ref for ref in answer.evidence_references if ref in self.evidence_ids]
        if claims and not known_refs:
            return [
                CriticIssue(
                    code="claim_without_evidence",
                    message="answer claims must cite at least one retrieved evidence item",
                ),
            ]
        return []

    def _weak_evidence_issues(self, answer: Any) -> list[CriticIssue]:
        if not self._has_weak_evidence():
            return []
        issues: list[CriticIssue] = []
        if (
            self.policy.require_uncertainty_for_weak_evidence
            and not tuple(answer.unknowns)
        ):
            issues.append(
                CriticIssue(
                    code="weak_evidence_missing_uncertainty",
                    message="weak evidence requires explicit unknowns",
                ),
            )
        if answer.confidence > self.policy.max_confidence_for_weak_evidence:
            issues.append(
                CriticIssue(
                    code="weak_evidence_overconfidence",
                    message="answer confidence is too high for weak evidence",
                ),
            )
        return issues

    def _source_injection_issues(self, answer: Any) -> list[CriticIssue]:
        evidence_has_injection = any(
            contains_source_injection(
                "\n".join(part for part in (item.title, item.snippet) if part),
            )
            for item in self.evidence
        )
        if not evidence_has_injection:
            return []
        answer_text = "\n".join((answer.conclusion, *answer.unknowns))
        if contains_source_injection(answer_text):
            return [
                CriticIssue(
                    code="source_injection_obeyed",
                    message="answer appears to repeat source-injected instructions",
                ),
            ]
        return []

    def _has_weak_evidence(self) -> bool:
        if not self.evidence:
            return True
        max_confidence = max(item.confidence for item in self.evidence)
        max_score = max(item.score for item in self.evidence)
        return (
            max_confidence < self.policy.weak_confidence_threshold
            or max_score < self.policy.weak_score_threshold
        )


def contains_source_injection(text: str) -> bool:
    """Return true when text includes likely prompt-injection instructions."""

    return bool(text and any(pattern.search(text) for pattern in _SOURCE_INJECTION_RES))


def _extract_claims(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _CLAIM_SPLIT_RE.split(text) if part.strip())


def _contains_gps(value: object, *, parent_key: str = "") -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _normalize_key(str(key))
            if normalized in _GPS_KEYS:
                return True
            if _contains_gps(child, parent_key=normalized):
                return True
    if isinstance(value, list | tuple):
        return any(_contains_gps(item, parent_key=parent_key) for item in value)
    return parent_key in _GPS_KEYS


def _redact_gps_values(value: object, *, places: int, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_gps_values(child, places=places, parent_key=_normalize_key(str(key)))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_gps_values(item, places=places, parent_key=parent_key) for item in value]
    if isinstance(value, tuple):
        return tuple(
            _redact_gps_values(item, places=places, parent_key=parent_key)
            for item in value
        )
    if parent_key in _GPS_KEYS:
        return _round_numeric(value, places=places)
    return value


def _round_numeric(value: object, *, places: int) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return round(float(value), places)
    if isinstance(value, str):
        try:
            return str(round(float(value), places))
        except ValueError:
            return value
    return value


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if str(item).strip())

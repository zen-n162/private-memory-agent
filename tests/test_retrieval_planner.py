import json

import pytest

from private_memory_agent.agent import (
    DeterministicEvidenceRelevanceJudge,
    DeterministicRuleBasedRetrievalPlanner,
    EvidenceRelevanceScore,
    FakeRetrievalPlanner,
    LeaderRetrievalPlanner,
    RetrievalPlan,
    rerank_evidence_by_relevance,
)
from private_memory_agent.retrieval import Evidence
from private_memory_agent.runtime import ChatResponse


class RecordingChatClient:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ChatResponse(text=json.dumps(self.payload, ensure_ascii=False), model="fake")


def _evidence(evidence_id, text, *, source="line", score=0.5):
    table, _, raw_id = evidence_id.partition(":")
    return Evidence(
        evidence_id=evidence_id,
        source_kind=source,
        source_table=table or "line_messages",
        source_id=int(raw_id or "1"),
        title=None,
        snippet=text,
        occurred_at=None,
        confidence=0.7,
        score=score,
    )


def test_retrieval_plan_schema_validation():
    plan = RetrievalPlan.from_mapping(
        {
            "intent": "find preparation evidence",
            "main_entities": ["ProjectX"],
            "specific_concepts": ["ProjectX"],
            "generic_concepts": ["準備"],
            "temporal_hints": [],
            "source_preferences": ["line", "notes"],
            "source_constraints": ["line"],
            "retrieval_queries": ["ProjectX 準備"],
            "excluded_concepts": [],
            "evidence_acceptance_criteria": ["specific project mention"],
            "uncertainty_notes": ["unknown if no specific evidence"],
        },
    )

    assert plan.intent == "find preparation evidence"
    assert plan.source_preferences == ("line", "notes")
    assert plan.metadata().to_dict()["specific_concept_count"] == 1


def test_retrieval_plan_rejects_missing_required_keys():
    with pytest.raises(ValueError):
        RetrievalPlan.from_mapping({"intent": "incomplete"})


def test_fake_planner_returns_configured_plan():
    plan = RetrievalPlan(
        intent="fake",
        specific_concepts=("Alpha",),
        retrieval_queries=("Alpha",),
    )
    planner = FakeRetrievalPlanner(plan)

    assert planner.plan("private question") == plan
    assert planner.questions == ["private question"]


def test_deterministic_planner_separates_specific_and_generic_terms():
    plan = DeterministicRuleBasedRetrievalPlanner().plan("HyperSIGMA 研究の準備を確認")

    assert "hypersigma" in plan.specific_concepts
    assert "研究" in plan.generic_concepts or "準備" in plan.generic_concepts
    assert plan.retrieval_queries


def test_leader_planner_parses_json_response():
    payload = {
        "intent": "find local evidence",
        "main_entities": ["Alpha"],
        "specific_concepts": ["Alpha"],
        "generic_concepts": ["研究"],
        "temporal_hints": [],
        "source_preferences": ["notes"],
        "source_constraints": ["notes"],
        "retrieval_queries": ["Alpha 研究"],
        "excluded_concepts": [],
        "evidence_acceptance_criteria": ["contains Alpha"],
        "uncertainty_notes": ["unknown if no Alpha"],
    }
    client = RecordingChatClient(payload)
    planner = LeaderRetrievalPlanner(client, model="planner-model", max_tokens=123)

    plan = planner.plan("private question")

    assert plan.specific_concepts == ("Alpha",)
    assert client.requests[0].model == "planner-model"
    assert client.requests[0].max_tokens == 123


def test_leader_planner_falls_back_when_json_is_invalid():
    client = RecordingChatClient("not json")
    planner = LeaderRetrievalPlanner(client)

    plan = planner.plan("HyperSIGMA 研究")

    assert plan.retrieval_queries
    assert any("fallback" in note for note in plan.uncertainty_notes)


def test_relevance_judge_demotes_generic_and_promotes_specific_evidence():
    plan = RetrievalPlan(
        intent="find Alpha preparation",
        specific_concepts=("Alpha",),
        generic_concepts=("研究", "準備"),
        retrieval_queries=("Alpha 準備",),
    )
    evidence = (
        _evidence("line_messages:1", "研究の一般的な話"),
        _evidence("line_messages:2", "Alpha の準備を確認"),
    )

    scores = DeterministicEvidenceRelevanceJudge().score(evidence, plan)
    by_id = {score.evidence_id: score for score in scores}

    assert by_id["line_messages:1"].specificity == "generic"
    assert by_id["line_messages:1"].should_use is False
    assert by_id["line_messages:2"].specificity == "specific"
    assert by_id["line_messages:2"].should_use is True
    assert by_id["line_messages:2"].relevance_score > by_id["line_messages:1"].relevance_score


def test_relevance_rerank_promotes_specific_evidence():
    plan = RetrievalPlan(
        intent="find Alpha",
        specific_concepts=("Alpha",),
        generic_concepts=("研究",),
        retrieval_queries=("Alpha",),
    )
    evidence = (
        _evidence("line_messages:1", "研究の一般的な話", score=0.9),
        _evidence("line_messages:2", "Alpha の話", score=0.1),
    )
    scores = DeterministicEvidenceRelevanceJudge().score(evidence, plan)

    reranked = rerank_evidence_by_relevance(evidence, scores, limit=2)

    assert reranked[0].evidence_id == "line_messages:2"


def test_evidence_relevance_score_validates_specificity():
    with pytest.raises(ValueError):
        EvidenceRelevanceScore(
            evidence_id="line_messages:1",
            relevance_score=0.5,
            specificity="bad",
            should_use=False,
            reason_category="bad",
        )

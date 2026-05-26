import json

import pytest

from private_memory_agent.api.console import ChatConsoleOptions, run_chat_console_query
from private_memory_agent.api.ui import agent_console_html
from private_memory_agent.capabilities import (
    CapabilityExecutionOptions,
    CapabilityExecutor,
    CapabilityRegistry,
    DeterministicCapabilityPlanner,
    LeaderCapabilityPlanner,
    Observation,
    TaskPlan,
    TaskPlanStep,
)
from private_memory_agent.runtime import ChatResponse
from private_memory_agent.storage import initialize_database


class _FakePlannerClient:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, request):
        return ChatResponse(text=json.dumps(self.payload), model=request.model)


def _insert_photo(storage, *, annotation_text, path="/private/capability-secret.jpg"):
    source_id = storage.source_items.insert_source(
        source_type="photo",
        source_uri=f"fixture://{path}",
        content_sha256=f"sha-{path}",
    )
    media_id = storage.media_items.insert_media(
        source_item_id=source_id,
        media_type="image",
        file_path=path,
        sha256=f"sha-{path}",
        mime_type="image/jpeg",
        width=100,
        height=80,
        taken_at="2025-12-03T12:00:00",
    )
    storage.media_annotations.insert(
        {
            "media_item_id": media_id,
            "annotation_type": "vision",
            "source": "model",
            "value_text": annotation_text,
            "model_id": "fake-qwen-vl",
            "confidence": 0.8,
        },
    )
    return media_id


def test_default_capability_registry_contains_initial_tools():
    registry = CapabilityRegistry.default()

    for name in (
        "date.parse",
        "metadata.search_by_date_range",
        "photo.search_by_concept",
        "photo.search_cached_annotations",
        "vision.verify_images",
        "line.search_text",
        "notes.search_text",
        "memory.semantic_search",
        "memory.rerank",
        "evidence.judge",
        "evidence.cluster_by_date",
        "answer.synthesize",
        "privacy.filter",
        "ui.render_photo_gallery",
        "ui.render_candidate_dates",
    ):
        assert registry.get(name) is not None

    serialized = json.dumps(registry.summaries(), ensure_ascii=False)
    assert "/private" not in serialized
    assert "35." not in serialized


@pytest.mark.parametrize(
    ("question", "sources", "output_type", "expected_capabilities"),
    [
        (
            "2025年12月で、ご飯を食べに行っているのはいつ？",
            ("photos", "line"),
            "candidate_dates",
            {"date.parse", "metadata.search_by_date_range", "photo.search_cached_annotations", "line.search_text", "evidence.cluster_by_date", "ui.render_candidate_dates"},
        ),
        (
            "ラーメンを食べに行っているのはいつ？",
            ("photos", "line", "notes"),
            "candidate_dates",
            {"date.parse", "photo.search_cached_annotations", "line.search_text", "notes.search_text", "evidence.cluster_by_date"},
        ),
        (
            "ラーメンが写っている写真はどれ？",
            ("photos",),
            "photo_gallery",
            {"photo.search_by_concept", "photo.search_cached_annotations", "evidence.judge", "ui.render_photo_gallery"},
        ),
        (
            "LINEでラーメンについて話しているのはいつ？",
            ("line",),
            "candidate_dates",
            {"date.parse", "line.search_text", "evidence.cluster_by_date"},
        ),
        (
            "この前行った店の写真とLINEを合わせて教えて",
            ("photos", "line"),
            "hybrid",
            {"photo.search_by_concept", "photo.search_cached_annotations", "line.search_text", "memory.semantic_search"},
        ),
    ],
)
def test_deterministic_planner_selects_capabilities_by_goal(
    question,
    sources,
    output_type,
    expected_capabilities,
):
    plan = DeterministicCapabilityPlanner().plan(question, sources=sources)

    assert plan.expected_output_type == output_type
    assert expected_capabilities.issubset(set(plan.selected_capabilities))
    assert plan.privacy_policy
    assert "raw question hidden" in plan.question_summary


def test_deterministic_planner_generalizes_visual_concepts_without_ramen_only_rules():
    planner = DeterministicCapabilityPlanner()

    cafe = planner.plan("カフェの写真を見せて", sources=("photos",))
    dog = planner.plan("犬が写っている写真はどれ？", sources=("photos",))
    lab = planner.plan("研究室が写っている写真を探して", sources=("photos",))

    assert cafe.expected_output_type == "photo_gallery"
    assert dog.expected_output_type == "photo_gallery"
    assert lab.expected_output_type == "photo_gallery"
    assert "photo.search_by_concept" in cafe.selected_capabilities
    assert "photo.search_by_concept" in dog.selected_capabilities
    assert "photo.search_by_concept" in lab.selected_capabilities


def test_leader_capability_planner_accepts_valid_task_plan():
    payload = {
        "answer_goal": "Return matching photos.",
        "expected_output_type": "photo_gallery",
        "required_information": ["visual signals"],
        "selected_capabilities": ["photo.search_by_concept", "evidence.judge", "ui.render_photo_gallery"],
        "execution_graph": [
            {"step_id": "cap_01", "capability_name": "photo.search_by_concept"},
            {"step_id": "cap_02", "capability_name": "evidence.judge", "depends_on": ["cap_01"]},
            {"step_id": "cap_03", "capability_name": "ui.render_photo_gallery", "depends_on": ["cap_02"]},
        ],
        "stopping_criteria": ["matching photos found"],
        "fallback_strategy": "fallback safely",
        "uncertainty_policy": "say unknown",
        "privacy_policy": "hide raw evidence",
    }
    planner = LeaderCapabilityPlanner(_FakePlannerClient(payload), model="fake-leader")

    plan = planner.plan(
        "写真を探して",
        sources=("photos",),
        registry=CapabilityRegistry.default(),
    )

    assert plan.generated_by == "leader"
    assert plan.fallback_used is False
    assert plan.expected_output_type == "photo_gallery"
    assert plan.selected_capabilities[0] == "photo.search_by_concept"


def test_invalid_task_plan_is_rejected():
    with pytest.raises(ValueError):
        TaskPlan(
            question_summary="safe",
            answer_goal="safe",
            expected_output_type="unsupported",
            required_information=(),
            selected_capabilities=("date.parse",),
            execution_graph=(TaskPlanStep(step_id="cap_01", capability_name="date.parse"),),
            stopping_criteria=(),
            fallback_strategy="safe",
            uncertainty_policy="safe",
            privacy_policy="safe",
        )


def test_capability_executor_emits_observations_and_enforces_budget():
    plan = DeterministicCapabilityPlanner().plan(
        "2025年12月で出かけたのはいつ？",
        sources=("photos", "line"),
    )
    result = CapabilityExecutor(CapabilityRegistry.default()).execute(
        plan,
        context={"question": "2025年12月で出かけたのはいつ？", "sources": ("photos", "line")},
        options=CapabilityExecutionOptions(max_steps=2),
    )

    assert result.observations
    assert result.executed_steps[0]["capability_name"] == "date.parse"
    assert result.budget_exhausted is True
    assert result.replans[0]["reason"] == "step_budget_exhausted"


def test_capability_executor_handles_missing_capability_safely():
    plan = TaskPlan(
        question_summary="safe",
        answer_goal="safe",
        expected_output_type="answer_text",
        required_information=(),
        selected_capabilities=("missing.capability",),
        execution_graph=(TaskPlanStep(step_id="cap_01", capability_name="missing.capability"),),
        stopping_criteria=(),
        fallback_strategy="safe",
        uncertainty_policy="safe",
        privacy_policy="safe",
    )

    result = CapabilityExecutor(CapabilityRegistry.default()).execute(plan)

    assert result.observations[0].status == "failed"
    assert result.observations[0].error == "UnknownCapability"
    assert result.replans[0]["reason"] == "missing_capability"


def test_capability_executor_allows_fake_handlers_for_tests():
    plan = DeterministicCapabilityPlanner().plan("研究について教えて", sources=("line",))

    def fake_handler(step, context):
        return Observation(
            step_id=step.step_id,
            capability_name=step.capability_name,
            status="succeeded",
            safe_summary="fake handler observation",
            candidate_count=3,
        )

    result = CapabilityExecutor(
        CapabilityRegistry.default(),
        handlers={"line.search_text": fake_handler},
    ).execute(plan, context={"question": "研究について教えて"})

    assert any(item.safe_summary == "fake handler observation" for item in result.observations)


def test_chat_console_response_includes_autonomous_plan_and_no_raw_paths(tmp_path):
    db_path = tmp_path / "visual.sqlite3"
    storage = initialize_database(db_path)
    try:
        media_id = _insert_photo(
            storage,
            annotation_text="犬が公園にいる",
            path="/private/capability-dog-secret.jpg",
        )
    finally:
        storage.close()

    payload = run_chat_console_query(
        ChatConsoleOptions(
            question="犬が写っている写真はどれ？",
            db_path=db_path,
            mode="retrieval-only",
            sources=("photos",),
            show_answer=True,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["query_type"] == "visual_evidence_search"
    assert payload["task_plan"]["expected_output_type"] == "photo_gallery"
    assert "photo.search_by_concept" in payload["selected_capabilities"]
    assert payload["executed_steps"]
    assert payload["observations"]
    assert payload["evidence_sufficiency"]["sufficient"] is True
    assert payload["matching_photos"][0]["evidence_id"] == f"media_items:{media_id}"
    assert "/private/capability-dog-secret.jpg" not in serialized


def test_chat_console_insufficient_visual_evidence_proposes_replan(tmp_path):
    db_path = tmp_path / "visual.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(storage, annotation_text="白い壁と書類", path="/private/no-dog.jpg")
    finally:
        storage.close()

    payload = run_chat_console_query(
        ChatConsoleOptions(
            question="犬が写っている写真はどれ？",
            db_path=db_path,
            mode="retrieval-only",
            sources=("photos",),
            show_answer=True,
        ),
    )

    assert payload["ok"] is True
    assert payload["answer_state"] == "unknown"
    assert payload["matching_photo_count"] == 0
    assert payload["evidence_sufficiency"]["sufficient"] is False
    assert payload["replans"]
    assert payload["error_class"] is None


def test_ui_contains_autonomous_plan_panel():
    html = agent_console_html()

    assert "Autonomous Plan" in html
    assert 'id="autonomous-plan-panel"' in html
    assert "renderAutonomousPlan" in html
    assert "selected_capabilities" in html
    assert "evidence_sufficiency" in html

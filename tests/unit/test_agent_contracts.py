import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from evistream.agent.audit import audited_request_key
from evistream.agent.engine import InvestigationEngine
from evistream.agent.errors import AgentRuntimeError
from evistream.agent.mock import ScriptedAgentMockGateway, ScriptedResponses
from evistream.agent.runtime import build_agent_runtime
from evistream.agent.service import ALLOWED_TRANSITIONS
from evistream.agent.types import (
    AgentAction,
    AgentNode,
    ChallengeOutput,
    EvidenceDraft,
    InvestigationRequirement,
    InvestigationState,
    PlanOutput,
    VerificationOutput,
)
from evistream.config import Settings
from evistream.media.runtime import MediaAdapterUnavailable
from evistream.models import ModelMessage, ModelRequest, ModelRole
from evistream.models.types import MediaReference
from evistream.storage.database import utc_now


def test_action_ranges_and_state_transitions_are_runtime_owned() -> None:
    with pytest.raises(ValidationError):
        AgentAction(
            requirement_id="req",
            tool_name="inspect_clip",
            start_ms=100,
            end_ms=100,
            rationale="invalid",
        )
    assert ALLOWED_TRANSITIONS[AgentNode.PLAN] == {AgentNode.RETRIEVE}
    assert AgentNode.DECIDE not in ALLOWED_TRANSITIONS[AgentNode.RETRIEVE]
    assert ALLOWED_TRANSITIONS[AgentNode.DECIDE] == {None}


def test_audited_request_summary_contains_hashes_without_payloads() -> None:
    request = ModelRequest(
        role=ModelRole.TRIAGE,
        messages=[ModelMessage(role="user", content="sensitive prompt")],
        media=[MediaReference(kind="image", uri="data:image/jpeg;base64,secret")],
        response_schema=ChallengeOutput,
    )
    key, summary = audited_request_key(
        request,
        run_id="run",
        node="INSPECT",
        state_version=3,
    )
    serialized = str(summary)
    assert len(key) == 64
    assert "sensitive prompt" not in serialized
    assert "base64,secret" not in serialized
    assert summary["schema"] == "ChallengeOutput"


def test_scripted_mock_and_fallback_return_target_schema() -> None:
    request = ModelRequest(
        role=ModelRole.AGENT,
        messages=[
            ModelMessage(
                role="user",
                content=(
                    '{"requirements":[{"requirement_id":"req","description":"weapon",'
                    '"suggested_queries":["weapon"],"tool_capabilities":'
                    '["search_transcript"]}],"missing_requirement_ids":["req"]}'
                ),
            )
        ],
        response_schema=PlanOutput,
    )
    response = asyncio.run(
        ScriptedAgentMockGateway(ScriptedResponses()).generate(request)
    )
    assert PlanOutput.model_validate(response.data).action.tool_name == "search_transcript"


def test_investigation_state_rejects_unknown_checkpoint_fields() -> None:
    now = utc_now()
    state = InvestigationState(
        run_id="run",
        job_id="job",
        case_id="case",
        policy_id="policy",
        policy_version=1,
        model_profile="mock",
        requirements=[
            InvestigationRequirement(
                requirement_id="req",
                requirement_key="key",
                source_kind="requirement",
                required=True,
                description="description",
            )
        ],
        deadline_at=now + timedelta(seconds=60),
        last_checkpoint_at=now,
    )
    payload = state.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        InvestigationState.model_validate(payload)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"next_node": AgentNode.PLAN, "iteration": 6}, "BUDGET_ITERATION_EXHAUSTED"),
        ({"vlm_calls": 8}, "BUDGET_VLM_EXHAUSTED"),
        ({"consecutive_tool_failures": 3}, "TOOL_FAILURE_LIMIT"),
        ({"stagnant_iterations": 2}, "STAGNATION_LIMIT"),
    ],
)
def test_each_runtime_budget_has_a_deterministic_stop(
    changes: dict[str, object], expected: str
) -> None:
    now = utc_now()
    state = InvestigationState(
        run_id="run",
        job_id="job",
        case_id="case",
        policy_id="policy",
        policy_version=1,
        model_profile="mock",
        requirements=[
            InvestigationRequirement(
                requirement_id="req",
                requirement_key="key",
                source_kind="requirement",
                required=True,
                description="description",
                tool_capabilities=["search_transcript"],
            )
        ],
        deadline_at=now + timedelta(seconds=60),
        last_checkpoint_at=now,
    ).model_copy(update=changes)
    engine = InvestigationEngine.__new__(InvestigationEngine)
    engine.settings = Settings()
    assert engine._budget_stop(state) == expected


def test_planner_cannot_use_a_tool_outside_requirement_capabilities() -> None:
    now = utc_now()
    state = InvestigationState(
        run_id="run",
        job_id="job",
        case_id="case",
        policy_id="policy",
        policy_version=1,
        model_profile="mock",
        requirements=[
            InvestigationRequirement(
                requirement_id="req",
                requirement_key="key",
                source_kind="requirement",
                required=True,
                description="description",
                tool_capabilities=["search_transcript"],
            )
        ],
        deadline_at=now + timedelta(seconds=60),
        last_checkpoint_at=now,
    )
    engine = InvestigationEngine.__new__(InvestigationEngine)
    with pytest.raises(AgentRuntimeError) as caught:
        engine._validate_action(
            state,
            AgentAction(
                requirement_id="req",
                tool_name="inspect_clip",
                start_ms=0,
                end_ms=1_000,
                rationale="forbidden",
            ),
        )
    assert caught.value.code == "AGENT_ACTION_INVALID"


@pytest.mark.parametrize(
    "draft",
    [
        EvidenceDraft(
            source_ref="search_document:forged",
            stance="support",
            start_ms=0,
            end_ms=1_000,
            summary="forged source",
        ),
        EvidenceDraft(
            source_ref="search_document:known",
            stance="support",
            start_ms=0,
            end_ms=2_000,
            summary="forged time range",
        ),
    ],
)
def test_verifier_cannot_forge_source_or_time(draft: EvidenceDraft) -> None:
    now = utc_now()
    state = InvestigationState(
        run_id="run",
        job_id="job",
        case_id="case",
        policy_id="policy",
        policy_version=1,
        model_profile="mock",
        requirements=[
            InvestigationRequirement(
                requirement_id="req",
                requirement_key="key",
                source_kind="requirement",
                required=True,
                description="description",
            )
        ],
        selected_items=[
            {
                "source_ref": "search_document:known",
                "artifact_id": "artifact",
                "modality": "transcript",
                "start_ms": 100,
                "end_ms": 1_000,
                "content": "known",
                "tool_run_id": "tool",
                "requirement_id": "req",
            }
        ],
        deadline_at=now + timedelta(seconds=60),
        last_checkpoint_at=now,
    )
    engine = InvestigationEngine.__new__(InvestigationEngine)
    with pytest.raises(AgentRuntimeError) as caught:
        engine._validate_evidence(
            state,
            "req",
            VerificationOutput(evidence=[draft]),
            "model-call",
            "model",
        )
    assert caught.value.code == "AGENT_ACTION_INVALID"


def test_scripted_mock_is_restricted_to_stage4_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "script.yaml"
    script.write_text("PlanOutput: []\n", encoding="utf-8")
    monkeypatch.setenv("EVISTREAM_STAGE4_SCRIPT", str(script))
    monkeypatch.delenv("EVISTREAM_STAGE4_VERIFY", raising=False)
    with pytest.raises(MediaAdapterUnavailable):
        build_agent_runtime(Settings(model_profile="mock"), "mock")

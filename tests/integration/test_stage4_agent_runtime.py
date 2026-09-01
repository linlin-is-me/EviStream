import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from evistream.agent.audit import AuditedModelGateway
from evistream.agent.errors import AgentRuntimeError
from evistream.agent.runtime import build_agent_runtime
from evistream.agent.service import AgentInvestigationService
from evistream.agent.types import (
    AgentNode,
    InvestigationResult,
    InvestigationStatus,
    PlanOutput,
)
from evistream.application import JobStatus
from evistream.config import Settings
from evistream.models import MockGateway, ModelMessage, ModelRequest, ModelRole
from evistream.retrieval.text import normalize_text, search_lexemes
from evistream.storage.artifacts import LocalArtifactStore
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    AgentRunRecord,
    AgentStepRecord,
    ArtifactRecord,
    CaseRecord,
    DecisionRecord,
    EvidenceRecord,
    ModelCallRecord,
    PolicyRecord,
    ProcessingJobRecord,
    RequirementRecord,
    RequirementResultRecord,
    SearchDocumentRecord,
    VideoRecord,
)

SAMPLE_VIDEO = Path("tests/fixtures/media/stage0_sample.mp4")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("scenario", "documents", "script", "expected_status", "expected_verdict"),
    [
        (
            "obvious",
            [("doc_one", "weapon threat is visible", 0, 7_720)],
            "obvious",
            InvestigationStatus.COMPLETED,
            "REJECT",
        ),
        (
            "cross_segment",
            [
                ("doc_one", "weapon appears", 0, 7_720),
                ("doc_two", "weapon threat follows", 18_300, 30_000),
            ],
            "cross_segment",
            InvestigationStatus.COMPLETED,
            "REJECT",
        ),
        (
            "insufficient",
            [("doc_one", "unrelated educational text", 0, 7_720)],
            "insufficient",
            InvestigationStatus.NEEDS_HUMAN_REVIEW,
            "NEEDS_HUMAN_REVIEW",
        ),
    ],
)
def test_deterministic_investigation_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    documents: list[tuple[str, str, int, int]],
    script: str,
    expected_status: InvestigationStatus,
    expected_verdict: str,
) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    settings = Settings(
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
        model_profile="mock",
        model_config_dir=Path("configs/models"),
        agent_timeout_seconds=60,
        process_timeout_seconds=30,
    )
    database = Database(database_url)
    fixture = _seed_case(database, settings, scenario, documents)
    script_path = tmp_path / f"{script}.yaml"
    script_path.write_text(
        yaml.safe_dump(
            _script(
                fixture["requirement_id"],
                fixture["document_ids"],
                scenario,
            ),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVISTREAM_STAGE4_VERIFY", "1")
    monkeypatch.setenv("EVISTREAM_STAGE4_SCRIPT", str(script_path))
    runtime = build_agent_runtime(settings, "mock")
    request = runtime.service.prepare(fixture["case_id"])
    execution = asyncio.run(runtime.dispatcher.dispatch(request))
    assert execution.status is JobStatus.SUCCEEDED
    assert execution.error_code is None
    result = runtime.service.get_result(str(request.payload["run_id"]))
    assert result.status is expected_status
    assert result.provisional_decision is not None
    assert result.provisional_decision.verdict == expected_verdict
    assert result.node_count == (11 if scenario == "cross_segment" else 6)
    expected_evidence = 2 if scenario == "cross_segment" else int(scenario == "obvious")
    assert result.evidence_count == expected_evidence

    repeated = asyncio.run(runtime.dispatcher.dispatch(request))
    assert repeated.status is JobStatus.SUCCEEDED
    repeated_result = runtime.service.get_result(result.run_id)
    assert repeated_result.node_count == result.node_count
    assert repeated_result.model_call_count == result.model_call_count
    assert repeated_result.tool_count == result.tool_count
    assert repeated_result.evidence_count == result.evidence_count

    trace = runtime.service.trace(result.run_id)
    assert trace["result"]["status"] == expected_status
    assert all("content" not in call["request_summary"] for call in trace["model_calls"])
    assert all("uri" not in call["request_summary"] for call in trace["model_calls"])
    with database.session() as session:
        case = session.get(CaseRecord, fixture["case_id"])
        job = session.get(ProcessingJobRecord, result.job_id)
        assert case is not None and job is not None
        assert case.status == (
            "NEEDS_HUMAN_REVIEW" if expected_status is InvestigationStatus.NEEDS_HUMAN_REVIEW
            else "INVESTIGATED"
        )
        assert job.status == "SUCCEEDED"
        assert session.scalar(
            select(func.count())
            .select_from(RequirementResultRecord)
            .join(
                RequirementRecord,
                RequirementResultRecord.requirement_id == RequirementRecord.id,
            )
            .where(RequirementRecord.case_id == fixture["case_id"])
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(DecisionRecord).where(
                DecisionRecord.case_id == fixture["case_id"]
            )
        ) == 0


@pytest.mark.integration
def test_agent_claim_checkpoint_conflict_and_stale_resume(tmp_path: Path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    settings = Settings(database_url=database_url, artifact_root=tmp_path / "artifacts")
    database = Database(database_url)
    fixture = _seed_case(
        database,
        settings,
        "resume",
        [("doc_one", "weapon", 0, 1_000)],
    )
    def interrupt_after_commit(checkpoint: object) -> None:
        if getattr(checkpoint, "state_version", None) == 1:
            raise SystemExit(75)

    service = AgentInvestigationService(database, settings, interrupt_after_commit)
    request = service.prepare(fixture["case_id"])
    with pytest.raises(AgentRuntimeError) as profile_conflict:
        service.prepare(fixture["case_id"], "dashscope-test")
    assert profile_conflict.value.code == "AGENT_PROFILE_CONFLICT"
    state = service.claim(request)
    assert not isinstance(state, InvestigationResult)
    with pytest.raises(AgentRuntimeError, match="lease") as running:
        service.claim(request)
    assert running.value.code == "AGENT_RUN_ALREADY_RUNNING"
    stale_copy = state.model_copy(deep=True)
    with pytest.raises(SystemExit) as interrupted:
        service.checkpoint(
            state,
            node=AgentNode.PLAN,
            next_node=AgentNode.RETRIEVE,
            input_payload={},
            output_payload={},
            latency_ms=1,
        )
    assert interrupted.value.code == 75
    with pytest.raises(AgentRuntimeError) as conflict:
        service.checkpoint(
            stale_copy,
            node=AgentNode.PLAN,
            next_node=AgentNode.RETRIEVE,
            input_payload={},
            output_payload={},
            latency_ms=1,
        )
    assert conflict.value.code == "AGENT_STATE_CONFLICT"
    with database.session() as session:
        expired = utc_now() - timedelta(seconds=1)
        session.execute(
            update(AgentRunRecord)
            .where(AgentRunRecord.id == state.run_id)
            .values(lease_until=expired)
        )
        session.execute(
            update(ProcessingJobRecord)
            .where(ProcessingJobRecord.id == state.job_id)
            .values(lease_until=expired)
        )
    resumed = service.claim(request)
    assert not isinstance(resumed, InvestigationResult)
    assert resumed.state_version == 1
    assert resumed.next_node is AgentNode.RETRIEVE
    with database.session() as session:
        assert session.scalar(
            select(func.count()).select_from(AgentStepRecord).where(
                AgentStepRecord.run_id == state.run_id
            )
        ) == 1
    service.fail(state.run_id, "TEST_END")


@pytest.mark.integration
def test_agent_step_is_append_only(tmp_path: Path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    settings = Settings(database_url=database_url, artifact_root=tmp_path / "artifacts")
    database = Database(database_url)
    fixture = _seed_case(database, settings, "immutable", [("doc", "weapon", 0, 1_000)])
    service = AgentInvestigationService(database, settings)
    request = service.prepare(fixture["case_id"])
    state = service.claim(request)
    assert not isinstance(state, InvestigationResult)
    service.checkpoint(
        state,
        node=AgentNode.PLAN,
        next_node=AgentNode.RETRIEVE,
        input_payload={},
        output_payload={},
        latency_ms=1,
    )
    with database.session() as session:
        step_id = session.scalar(
            select(AgentStepRecord.id).where(AgentStepRecord.run_id == state.run_id)
        )
    assert step_id is not None
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            update(AgentStepRecord)
            .where(AgentStepRecord.id == step_id)
            .values(latency_ms=2)
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(delete(AgentStepRecord).where(AgentStepRecord.id == step_id))
    service.fail(state.run_id, "TEST_END")


@pytest.mark.integration
def test_mock_and_openai_compatible_calls_share_audited_schema(tmp_path: Path) -> None:
    from tests.contract.test_openai_compatible import compatible_server, make_gateway

    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    settings = Settings(database_url=database_url, artifact_root=tmp_path / "artifacts")
    database = Database(database_url)
    fixture = _seed_case(database, settings, "audit", [("doc", "weapon", 0, 1_000)])
    service = AgentInvestigationService(database, settings)
    request = service.prepare(fixture["case_id"])
    state = service.claim(request)
    assert not isinstance(state, InvestigationResult)
    payload = {
        "hypothesis": {
            "requirement_id": fixture["requirement_id"],
            "statement": "inspect evidence",
            "confidence": 0.8,
        },
        "action": {
            "requirement_id": fixture["requirement_id"],
            "tool_name": "search_transcript",
            "query": "weapon",
            "start_ms": None,
            "end_ms": None,
            "limit": 5,
            "rationale": "contract test",
        },
    }
    model_request = ModelRequest(
        role=ModelRole.AGENT,
        messages=[ModelMessage(role="user", content="return a plan")],
        response_schema=PlanOutput,
    )
    mock = AuditedModelGateway(
        database,
        MockGateway(payload=payload),
        run_id=state.run_id,
        case_id=state.case_id,
        job_id=state.job_id,
        node="PLAN_MOCK",
        state_version=0,
        profile="mock",
        requested_model="mock-stage0",
        lease_seconds=30,
    )
    mock_response = asyncio.run(mock.generate(model_request))
    cached_mock_response = asyncio.run(mock.generate(model_request))
    assert cached_mock_response == mock_response
    with compatible_server(content=json.dumps(payload)) as server:
        compatible = AuditedModelGateway(
            database,
            make_gateway(server),
            run_id=state.run_id,
            case_id=state.case_id,
            job_id=state.job_id,
            node="PLAN_COMPATIBLE",
            state_version=0,
            profile="local-compatible",
            requested_model="configured-model",
            lease_seconds=30,
        )
        compatible_response = asyncio.run(compatible.generate(model_request))
    assert mock_response.data == compatible_response.data == payload
    with database.session() as session:
        calls = session.scalars(
            select(ModelCallRecord)
            .where(ModelCallRecord.run_id == state.run_id)
            .order_by(ModelCallRecord.node)
        ).all()
        assert len(calls) == 2
        assert {item.status for item in calls} == {"success"}
        assert {item.request_summary["schema"] for item in calls} == {"PlanOutput"}
        assert all("content" not in item.request_summary for item in calls)
    other = _seed_case(database, settings, "audit_other", [("doc", "weapon", 0, 1_000)])
    assert mock.last_call_id is not None
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            EvidenceRecord(
                id=f"ev_cross_{uuid4().hex}",
                case_id=str(other["case_id"]),
                requirement_id=str(other["requirement_id"]),
                stance="support",
                modality="transcript",
                start_ms=0,
                end_ms=1_000,
                artifact_id=None,
                tool_run_id=None,
                model_call_id=mock.last_call_id,
                model_name="mock-stage0",
                source_ref="search_document:cross",
                summary="cross-case model call",
                confidence=0.5,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
    service.fail(state.run_id, "TEST_END")


@pytest.mark.integration
def test_stage4_catalog_contains_cross_case_constraints_and_append_trigger() -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    with database.engine.connect() as connection:
        constraints = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conname IN "
                    "('fk_tool_runs_agent_run_case', 'fk_model_calls_agent_run_case', "
                    "'fk_evidence_model_call_case')"
                )
            )
        }
        trigger = connection.execute(
            text(
                "SELECT 1 FROM pg_trigger WHERE tgname = 'protect_agent_steps' "
                "AND NOT tgisinternal"
            )
        ).fetchone()
    assert constraints == {
        "fk_tool_runs_agent_run_case",
        "fk_model_calls_agent_run_case",
        "fk_evidence_model_call_case",
    }
    assert trigger == (1,)


@pytest.mark.integration
def test_model_failure_becomes_human_review_while_job_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    settings = Settings(database_url=database_url, artifact_root=tmp_path / "artifacts")
    database = Database(database_url)
    fixture = _seed_case(database, settings, "model_failure", [("doc", "weapon", 0, 1_000)])
    script = tmp_path / "invalid-model.yaml"
    script.write_text("PlanOutput:\n  - invalid: true\n", encoding="utf-8")
    monkeypatch.setenv("EVISTREAM_STAGE4_VERIFY", "1")
    monkeypatch.setenv("EVISTREAM_STAGE4_SCRIPT", str(script))
    runtime = build_agent_runtime(settings, "mock")
    request = runtime.service.prepare(str(fixture["case_id"]))
    execution = asyncio.run(runtime.dispatcher.dispatch(request))
    result = runtime.service.get_result(str(request.payload["run_id"]))
    assert execution.status is JobStatus.SUCCEEDED
    assert result.status is InvestigationStatus.NEEDS_HUMAN_REVIEW
    assert result.stop_reason == "AGENT_MODEL_FAILED"
    assert result.model_call_count == 1


@pytest.mark.integration
def test_invalid_planner_action_fails_job_and_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    settings = Settings(database_url=database_url, artifact_root=tmp_path / "artifacts")
    database = Database(database_url)
    fixture = _seed_case(database, settings, "invalid_action", [("doc", "weapon", 0, 1_000)])
    requirement_id = str(fixture["requirement_id"])
    script = tmp_path / "invalid-action.yaml"
    script.write_text(
        yaml.safe_dump(
            {
                "PlanOutput": [
                    {
                        "hypothesis": {
                            "requirement_id": requirement_id,
                            "statement": "forbidden tool",
                            "confidence": 0.5,
                        },
                        "action": {
                            "requirement_id": requirement_id,
                            "tool_name": "inspect_clip",
                            "query": "",
                            "start_ms": 0,
                            "end_ms": 1_000,
                            "limit": 5,
                            "rationale": "forbidden by requirement capability",
                        },
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVISTREAM_STAGE4_VERIFY", "1")
    monkeypatch.setenv("EVISTREAM_STAGE4_SCRIPT", str(script))
    runtime = build_agent_runtime(settings, "mock")
    request = runtime.service.prepare(str(fixture["case_id"]))
    execution = asyncio.run(runtime.dispatcher.dispatch(request))
    result = runtime.service.get_result(str(request.payload["run_id"]))
    assert execution.status is JobStatus.FAILED
    assert execution.error_code == "AGENT_ACTION_INVALID"
    assert result.status is InvestigationStatus.FAILED
    with database.session() as session:
        job = session.get(ProcessingJobRecord, result.job_id)
        assert job is not None and job.status == "FAILED"


def _seed_case(
    database: Database,
    settings: Settings,
    scenario: str,
    documents: list[tuple[str, str, int, int]],
) -> dict[str, object]:
    suffix = uuid4().hex[:10]
    video_id = f"vid_s4_{scenario}_{suffix}"
    case_id = f"case_s4_{scenario}_{suffix}"
    requirement_id = f"req_s4_{scenario}_{suffix}"
    policy_id = f"test.stage4.{scenario}.{suffix}"
    store = LocalArtifactStore(settings.artifact_root)
    source_uri = store.put_file(SAMPLE_VIDEO, f"videos/{video_id}/source.mp4")
    now = utc_now()
    document_ids: list[str] = []
    with database.session() as session:
        session.add(
            VideoRecord(
                id=video_id,
                original_name=SAMPLE_VIDEO.name,
                artifact_uri=source_uri,
                fingerprint=None,
                duration_ms=30_000,
                width=640,
                height=360,
                container="mp4",
                video_codec="h264",
                has_audio=True,
                audio_codec="aac",
                status="READY",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        transcript_id = f"art_s4_{scenario}_{suffix}"
        session.add(
            ArtifactRecord(
                id=transcript_id,
                video_id=video_id,
                type="TRANSCRIPT",
                uri=store.uri_for_key(f"videos/{video_id}/transcript.json"),
                artifact_metadata={},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PolicyRecord(
                policy_id=policy_id,
                version=1,
                name="Stage 4 test policy",
                severity="HIGH",
                enabled=True,
                lifecycle="PUBLISHED",
                source_yaml="id: test",
                compiled_policy={},
                source_sha256="1" * 64,
                semantic_sha256="2" * 64,
                compiler_version="test",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            CaseRecord(
                id=case_id,
                video_id=video_id,
                policy_id=policy_id,
                policy_version=1,
                model_profile="mock",
                status="READY",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            RequirementRecord(
                id=requirement_id,
                case_id=case_id,
                requirement_key="weapon_presence",
                requirement_type="speech_content",
                source_kind="requirement",
                required=True,
                description="Determine whether a weapon is present",
                suggested_queries=["weapon"],
                modalities=["transcript"],
                tool_capabilities=["search_transcript"],
                semantic_sha256="3" * 64,
                status="PENDING",
                created_at=now,
                updated_at=now,
            )
        )
        for name, content, start_ms, end_ms in documents:
            document_id = f"{name}_{scenario}_{suffix}"
            document_ids.append(document_id)
            session.add(
                SearchDocumentRecord(
                    id=document_id,
                    video_id=video_id,
                    segment_id=None,
                    artifact_id=transcript_id,
                    modality="transcript",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=content,
                    normalized_text=normalize_text(content),
                    keyword_lexemes=search_lexemes(content),
                    embedding=None,
                    created_at=now,
                    updated_at=now,
                )
            )
    return {
        "video_id": video_id,
        "case_id": case_id,
        "requirement_id": requirement_id,
        "document_ids": document_ids,
    }


def _script(requirement_id: object, document_ids: object, scenario: str) -> dict[str, object]:
    requirement = str(requirement_id)
    documents = [str(item) for item in document_ids] if isinstance(document_ids, list) else []
    queries = ["weapon", "threat"] if scenario == "cross_segment" else ["absent"]
    if scenario == "obvious":
        queries = ["weapon threat"]
    plans = [
        {
            "hypothesis": {
                "requirement_id": requirement,
                "statement": f"inspect {query}",
                "confidence": 0.8,
            },
            "action": {
                "requirement_id": requirement,
                "tool_name": "search_transcript",
                "query": query,
                "start_ms": None,
                "end_ms": None,
                "limit": 5,
                "rationale": "controlled fixture",
            },
        }
        for query in queries
    ]
    observations = [
        {
            "source_ref": f"search_document:{document}",
            "summary": "Controlled observation",
            "visible_entities": ["weapon"],
            "uncertainty": 0.1,
        }
        for document in documents[: len(plans)]
    ]
    verification = [
        {
            "evidence": [
                {
                    "source_ref": f"search_document:{documents[index]}",
                    "stance": "support",
                    "start_ms": 0 if index == 0 else 18_300,
                    "end_ms": 7_720 if index == 0 else 30_000,
                    "summary": "Controlled support evidence",
                    "confidence": 0.9,
                }
            ]
        }
        for index in range(min(len(plans), len(documents)))
    ]
    challenges = [
        {
            "actions": [],
            "unresolved_exception": False,
            "contradictory_evidence": False,
            "continue_investigation": scenario == "cross_segment" and index == 0,
            "rationale": "Controlled counter-evidence check",
        }
        for index in range(len(plans))
    ]
    verdict = "NEEDS_HUMAN_REVIEW" if scenario == "insufficient" else "REJECT"
    return {
        "PlanOutput": plans,
        "InspectionObservation": observations,
        "VerificationOutput": verification,
        "ChallengeOutput": challenges,
        "ProvisionalDecision": [
            {
                "verdict": verdict,
                "reason_code": "CONTROLLED_FIXTURE",
                "explanation": "Controlled Stage 4 runtime result",
                "evidence_ids": ["$ALL_EVIDENCE"],
            }
        ],
    }

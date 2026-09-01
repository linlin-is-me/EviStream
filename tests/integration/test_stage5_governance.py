import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from evistream.domain import Verdict
from evistream.governance.aggregation import RequirementAggregator
from evistream.governance.review import HumanGovernanceService
from evistream.governance.service import GovernanceApplicationService
from evistream.governance.timeline import CaseTimelineService
from evistream.governance.types import AggregationConfig
from evistream.policies.compiler import CompiledPolicy, PolicyCompiler
from evistream.policies.schema import PolicyDocument
from evistream.replay.planner import ReplayPlanner
from evistream.replay.service import ReplayApplicationService
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    AgentRunRecord,
    ArtifactRecord,
    CaseRecord,
    EvidenceRecord,
    PolicyRecord,
    ProcessingJobRecord,
    ReplayLineageRecord,
    RequirementRecord,
    ReviewRecord,
    VideoRecord,
)


@pytest.mark.integration
def test_governance_review_appeal_and_timeline_are_idempotent(tmp_path: Path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    fixture = _seed_case(database, tmp_path)
    governance = GovernanceApplicationService(database)
    decision = governance.finalize_case(fixture["case_id"])
    repeated = governance.finalize_case(fixture["case_id"])
    assert decision.decision_id == repeated.decision_id
    assert decision.verdict is Verdict.REJECT

    human = HumanGovernanceService(database)
    review = human.submit_review(
        fixture["case_id"],
        reviewer="reviewer",
        verdict=Verdict.APPROVE,
        note="verified exception",
    )
    repeated_review = human.submit_review(
        fixture["case_id"],
        reviewer="reviewer",
        verdict=Verdict.APPROVE,
        note="verified exception",
    )
    assert review.review_id == repeated_review.review_id
    appeal = human.submit_appeal(
        fixture["case_id"], submitter="submitter", statement="request review"
    )
    resolution = human.resolve_appeal(
        appeal.appeal_id,
        reviewer="appeal-reviewer",
        verdict=Verdict.REJECT,
        note="appeal rejected",
    )
    assert resolution.reviewed_decision_id == review.decision_id

    timeline = CaseTimelineService(database).timeline(fixture["case_id"])
    event_types = [item["event_type"] for item in timeline]
    assert "EVIDENCE" in event_types
    assert event_types.count("DECISION") == 3
    assert event_types.count("REVIEW") == 2
    assert event_types.count("APPEAL") == 2
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            update(ReviewRecord)
            .where(ReviewRecord.id == review.review_id)
            .values(note="forbidden mutation")
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("stances", "expected"),
    [
        (["support"], "SATISFIED"),
        (["contradict"], "NOT_SATISFIED"),
        (["support", "contradict"], "CONFLICTED"),
        (["neutral"], "UNKNOWN"),
    ],
)
def test_requirement_aggregator_four_results(
    tmp_path: Path, stances: list[str], expected: str
) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    fixture = _seed_case(database, tmp_path, stances=stances)
    with database.session() as session:
        requirement = session.get(RequirementRecord, fixture["requirement_id"])
        assert requirement is not None
        outcome = RequirementAggregator().aggregate(
            session, requirement, AggregationConfig()
        )
        assert outcome.status == expected


@pytest.mark.integration
def test_reevaluate_reuses_evidence_without_agent_calls(tmp_path: Path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    fixture = _seed_case(database, tmp_path)
    governance = GovernanceApplicationService(database)
    source_decision = governance.finalize_case(fixture["case_id"])
    with database.session() as session:
        source = session.get(PolicyRecord, (fixture["policy_id"], 1))
        assert source is not None
        compiled = CompiledPolicy.model_validate(source.compiled_policy)
        target = compiled.model_copy(
            update={
                "version": 2,
                "aggregation": AggregationConfig(minimum_confidence=0.75),
                "semantic_sha256": "9" * 64,
            }
        )
        now = utc_now()
        session.add(
            PolicyRecord(
                policy_id=source.policy_id,
                version=2,
                name=source.name,
                severity=source.severity,
                enabled=True,
                lifecycle="PUBLISHED",
                source_yaml="id: test-v2",
                compiled_policy=target.model_dump(mode="json"),
                source_sha256="8" * 64,
                semantic_sha256="9" * 64,
                compiler_version="2",
                created_at=now,
                updated_at=now,
            )
        )
    planner = ReplayPlanner(database)
    preview = planner.preview(fixture["policy_id"], 1, 2)
    assert preview.mode == "REEVALUATE"
    service = ReplayApplicationService(database, planner, governance)
    request = service.prepare(
        fixture["policy_id"], 1, 2, preview.preview_sha256
    )
    replay_job_id = service.claim(request)
    result = asyncio.run(service.execute(replay_job_id))
    assert result.completed_items == 1
    assert result.failed_items == 0
    with database.session() as session:
        target_case = session.scalar(
            select(CaseRecord).where(
                CaseRecord.video_id == fixture["video_id"],
                CaseRecord.policy_id == fixture["policy_id"],
                CaseRecord.policy_version == 2,
            )
        )
        assert target_case is not None
        assert target_case.current_decision_id is not None
        assert target_case.current_decision_id != source_decision.decision_id
        assert (
            session.scalar(
                select(func.count()).select_from(AgentRunRecord).where(
                    AgentRunRecord.case_id == target_case.id
                )
            )
            == 0
        )
        derived = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.case_id == target_case.id)
        )
        assert derived is not None and derived.origin_evidence_id is not None
        lineage = session.scalar(
            select(ReplayLineageRecord).where(
                ReplayLineageRecord.replay_item_id == derived.replay_item_id
            )
        )
        assert lineage is not None
        lineage_id = lineage.id
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            update(ReplayLineageRecord)
            .where(ReplayLineageRecord.id == lineage_id)
            .values(reason_code="forbidden mutation")
        )


def _seed_case(
    database: Database,
    tmp_path: Path,
    *,
    stances: list[str] | None = None,
) -> dict[str, str]:
    suffix = uuid4().hex[:12]
    policy_id = f"test.stage5.{suffix}"
    video_id = f"video_stage5_{suffix}"
    case_id = f"case_stage5_{suffix}"
    requirement_id = f"requirement_stage5_{suffix}"
    artifact_id = f"artifact_stage5_{suffix}"
    job_id = f"job_stage5_{suffix}"
    run_id = f"run_stage5_{suffix}"
    document = PolicyDocument.model_validate(
        {
            "id": policy_id,
            "version": 1,
            "name": "Stage 5 integration",
            "enabled": True,
            "severity": "HIGH",
            "trigger_terms": ["weapon"],
            "requirements": [
                {
                    "id": "presence",
                    "type": "speech_content",
                    "required": True,
                    "description": "weapon is present",
                }
            ],
            "exceptions": [],
            "decision": {
                "reject_when": {"all": ["presence"]},
                "escalate_when": {"any": ["contradictory_evidence"]},
            },
        }
    )
    compiled = PolicyCompiler().compile(document)
    template = compiled.requirements[0]
    now = utc_now()
    media = tmp_path / f"{video_id}.mp4"
    media.write_bytes(b"fixture")
    with database.session() as session:
        session.add(
            VideoRecord(
                id=video_id,
                original_name=media.name,
                artifact_uri=media.resolve().as_uri(),
                fingerprint=None,
                duration_ms=1_000,
                width=1,
                height=1,
                container="mp4",
                video_codec="h264",
                has_audio=False,
                audio_codec=None,
                status="READY",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            ArtifactRecord(
                id=artifact_id,
                video_id=video_id,
                type="TRANSCRIPT",
                uri=media.resolve().as_uri(),
                artifact_metadata={},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PolicyRecord(
                policy_id=policy_id,
                version=1,
                name=compiled.name,
                severity=compiled.severity,
                enabled=True,
                lifecycle="PUBLISHED",
                source_yaml="id: test",
                compiled_policy=compiled.model_dump(mode="json"),
                source_sha256="1" * 64,
                semantic_sha256=compiled.semantic_sha256,
                compiler_version=compiled.compiler_version,
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
                status="INVESTIGATED",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            RequirementRecord(
                id=requirement_id,
                case_id=case_id,
                requirement_key=template.requirement_key,
                requirement_type=template.requirement_type,
                source_kind=template.source_kind,
                required=True,
                description=template.description,
                suggested_queries=template.suggested_queries,
                modalities=template.modalities,
                tool_capabilities=template.tool_capabilities,
                semantic_sha256=template.semantic_sha256,
                status="PENDING",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ProcessingJobRecord(
                id=job_id,
                type="AGENT_INVESTIGATION",
                subject_id=case_id,
                request_key="request_" + suffix,
                correlation_id="correlation_" + suffix,
                status="SUCCEEDED",
                attempt=1,
                max_attempts=3,
                started_at=now,
                finished_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            AgentRunRecord(
                id=run_id,
                run_kind="INVESTIGATION",
                job_id=job_id,
                case_id=case_id,
                model_profile="mock",
                current_node="DECIDE",
                next_node=None,
                state_snapshot={},
                state_version=1,
                status="COMPLETED",
                iteration=1,
                vlm_calls=1,
                consecutive_tool_failures=0,
                total_tool_failures=0,
                stagnant_iterations=0,
                deadline_at=now,
                last_checkpoint_at=now,
                lease_until=None,
                provisional_verdict="APPROVE",
                stop_reason="COMPLETED",
                result_payload={},
                scope_requirement_ids=[],
                created_at=now,
                updated_at=now,
            )
        )
        for index, stance in enumerate(stances or ["support"]):
            session.add(
                EvidenceRecord(
                    id=f"evidence_stage5_{suffix}_{index}",
                    case_id=case_id,
                    requirement_id=requirement_id,
                    stance=stance,
                    modality="transcript",
                    start_ms=0,
                    end_ms=1_000,
                    artifact_id=artifact_id,
                    tool_run_id=None,
                    model_call_id=None,
                    model_name=None,
                    source_ref=f"artifact:{artifact_id}",
                    summary="controlled evidence",
                    confidence=0.9,
                    origin_evidence_id=None,
                    replay_item_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
    return {
        "policy_id": policy_id,
        "video_id": video_id,
        "case_id": case_id,
        "requirement_id": requirement_id,
        "artifact_id": artifact_id,
    }

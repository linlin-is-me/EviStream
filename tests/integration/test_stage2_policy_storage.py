import os
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from evistream.domain import PolicyLifecycle
from evistream.policies.schema import load_policy
from evistream.policies.seeds import apply_demo_seeds, load_case_seeds
from evistream.policies.versioning import (
    CaseApplicationService,
    PolicyVersionError,
    PolicyVersionService,
)
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    ArtifactRecord,
    CaseRecord,
    DecisionEvidenceRecord,
    DecisionRecord,
    EvidenceRecord,
    PolicyRecord,
    RequirementRecord,
    RequirementResultEvidenceRecord,
    RequirementResultRecord,
    ToolRunRecord,
    VideoRecord,
)


@pytest.mark.integration
def test_policy_versions_and_case_requirements_are_persistent(tmp_path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    suffix = uuid4().hex[:10]
    video_id = f"vid_stage2_{suffix}"
    case_id = f"case_stage2_{suffix}"
    with database.session() as session:
        now = utc_now()
        session.add(
            VideoRecord(
                id=video_id,
                original_name="stage2.mp4",
                artifact_uri=f"artifact://stage2/{suffix}/source.mp4",
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

    source = load_policy(Path("configs/policies/violence-weapon-v1.yaml"))
    versions = PolicyVersionService(database)
    assert versions.save_draft(source).lifecycle == PolicyLifecycle.DRAFT
    assert versions.save_draft(source).lifecycle == PolicyLifecycle.DRAFT
    published = versions.publish(source)
    assert published.lifecycle == PolicyLifecycle.PUBLISHED
    assert versions.publish(source).semantic_sha256 == published.semantic_sha256
    assert len(versions.list_versions(source.document.id)) == 1

    changed = tmp_path / "changed.yaml"
    changed.write_text(
        source.source_yaml.replace("暴力与武器展示审核", "修改后的名称"),
        encoding="utf-8",
    )
    with pytest.raises(PolicyVersionError) as conflict:
        versions.publish(load_policy(changed))
    assert conflict.value.code == "POLICY_VERSION_CONFLICT"

    cases = CaseApplicationService(database)
    bundle = cases.create_case(
        video_id,
        source.document.id,
        source.document.version,
        "mock",
        case_id=case_id,
    )
    duplicate = cases.create_case(
        video_id,
        source.document.id,
        source.document.version,
        "mock",
    )
    assert duplicate.case.case_id == bundle.case.case_id
    assert len(bundle.requirements) == 5
    assert {item.source_kind for item in bundle.requirements} == {"requirement", "exception"}
    with database.session() as session:
        assert session.scalar(
            select(func.count()).select_from(CaseRecord).where(CaseRecord.id == case_id)
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(RequirementRecord)
            .where(RequirementRecord.case_id == case_id)
        ) == 5


@pytest.mark.integration
def test_demo_seed_apply_is_transactional_and_idempotent(tmp_path) -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    manifest_path = Path("configs/demo/stage2-cases.yaml")
    manifest = load_case_seeds(manifest_path)
    suffix = uuid4().hex[:10]
    mapping: dict[str, str] = {}
    with database.session() as session:
        now = utc_now()
        for index, fixture_ref in enumerate(sorted({item.fixture_ref for item in manifest.cases})):
            video_id = f"vid_seed_{suffix}_{index}"
            mapping[fixture_ref] = video_id
            session.add(
                VideoRecord(
                    id=video_id,
                    original_name=f"{fixture_ref}.mp4",
                    artifact_uri=f"artifact://stage2/{suffix}/{fixture_ref}.mp4",
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
    map_path = tmp_path / "video-map.yaml"
    map_path.write_text(
        yaml.safe_dump({"videos": mapping}, allow_unicode=True), encoding="utf-8"
    )
    first = apply_demo_seeds(
        database,
        Path("configs/policies"),
        manifest_path,
        map_path,
        model_profile="mock",
    )
    second = apply_demo_seeds(
        database,
        Path("configs/policies"),
        manifest_path,
        map_path,
        model_profile="mock",
    )
    assert len(first.materialized_case_ids) == 9
    assert second.materialized_case_ids == first.materialized_case_ids
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(PolicyRecord)) == 3
        assert session.scalar(
            select(func.count())
            .select_from(CaseRecord)
            .where(CaseRecord.id.in_(first.materialized_case_ids))
        ) == 9
        assert session.scalar(
            select(func.count())
            .select_from(RequirementRecord)
            .where(RequirementRecord.case_id.in_(first.materialized_case_ids))
        ) == 45


@pytest.mark.integration
def test_audit_records_and_cross_case_relationships_are_database_enforced() -> None:
    database_url = os.environ.get("EVISTREAM_DATABASE_URL")
    if not database_url:
        pytest.skip("EVISTREAM_DATABASE_URL is not configured")
    database = Database(database_url)
    suffix = uuid4().hex[:10]
    now = utc_now()
    ids = {name: f"{name}_{suffix}" for name in [
        "video1",
        "video2",
        "artifact1",
        "artifact2",
        "case1",
        "case2",
        "requirement1",
        "requirement2",
        "tool1",
        "tool2",
        "evidence1",
        "evidence2",
        "result1",
        "result2",
        "decision1",
        "decision2",
    ]}
    policy1 = f"test.integrity.one.{suffix}"
    policy2 = f"test.integrity.two.{suffix}"
    draft_policy = f"test.integrity.draft.{suffix}"

    with database.session() as session:
        for number in [1, 2]:
            session.add(
                VideoRecord(
                    id=ids[f"video{number}"],
                    original_name=f"integrity-{number}.mp4",
                    artifact_uri=f"artifact://integrity/{suffix}/{number}.mp4",
                    fingerprint=None,
                    duration_ms=10_000,
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
        for number in [1, 2]:
            session.add(
                ArtifactRecord(
                    id=ids[f"artifact{number}"],
                    video_id=ids[f"video{number}"],
                    type="TRANSCRIPT",
                    uri=f"artifact://integrity/{suffix}/{number}.json",
                    artifact_metadata={},
                    created_at=now,
                    updated_at=now,
                )
            )
        for policy_id, lifecycle in [
            (policy1, "PUBLISHED"),
            (policy2, "PUBLISHED"),
            (draft_policy, "DRAFT"),
        ]:
            session.add(
                PolicyRecord(
                    policy_id=policy_id,
                    version=1,
                    name=policy_id,
                    severity="LOW",
                    enabled=True,
                    lifecycle=lifecycle,
                    source_yaml="id: test",
                    compiled_policy={},
                    source_sha256="0" * 64,
                    semantic_sha256="1" * 64,
                    compiler_version="test",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
        for number, policy_id in [(1, policy1), (2, policy2)]:
            session.add(
                CaseRecord(
                    id=ids[f"case{number}"],
                    video_id=ids[f"video{number}"],
                    policy_id=policy_id,
                    policy_version=1,
                    model_profile="mock",
                    status="READY",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
        for number in [1, 2]:
            session.add(
                RequirementRecord(
                    id=ids[f"requirement{number}"],
                    case_id=ids[f"case{number}"],
                    requirement_key="evidence",
                    requirement_type="speech_content",
                    source_kind="requirement",
                    required=True,
                    description="Evidence",
                    suggested_queries=["evidence"],
                    modalities=["transcript"],
                    tool_capabilities=["search_transcript"],
                    semantic_sha256="2" * 64,
                    status="PENDING",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
        for number in [1, 2]:
            session.add(
                ToolRunRecord(
                    id=ids[f"tool{number}"],
                    run_id=f"run_{number}_{suffix}",
                    case_id=ids[f"case{number}"],
                    requirement_id=ids[f"requirement{number}"],
                    correlation_id=f"corr_{number}_{suffix}",
                    tool_name="search_transcript",
                    request_key=f"{number}" * 64,
                    request_payload={},
                    response_payload={},
                    status="success",
                    latency_ms=1,
                    estimated_cost=0,
                    created_at=now,
                    updated_at=now,
                )
            )

    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            CaseRecord(
                id=f"case_draft_{suffix}",
                video_id=ids["video1"],
                policy_id=draft_policy,
                policy_version=1,
                model_profile="mock",
                status="READY",
                created_at=now,
                updated_at=now,
            )
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            ToolRunRecord(
                id=f"tool_cross_{suffix}",
                run_id=None,
                case_id=ids["case1"],
                requirement_id=ids["requirement2"],
                correlation_id=f"corr_cross_{suffix}",
                tool_name="search_transcript",
                request_key="x" * 64,
                request_payload={},
                response_payload=None,
                status="running",
                latency_ms=0,
                estimated_cost=0,
                created_at=now,
                updated_at=now,
            )
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            DecisionRecord(
                id=f"decision_cross_{suffix}",
                case_id=ids["case1"],
                policy_id=policy2,
                policy_version=1,
                verdict="APPROVE",
                reason_code="INVALID_BINDING",
                source="MACHINE",
                explanation="test",
                decision_metadata={},
                created_at=now,
                updated_at=now,
            )
        )
    with pytest.raises(IntegrityError):
        _insert_evidence(database, ids, suffix, artifact_number=2, tool_number=1)
    with pytest.raises(IntegrityError):
        _insert_evidence(database, ids, suffix, artifact_number=1, tool_number=2)

    _insert_evidence(database, ids, suffix, artifact_number=1, tool_number=1)
    with database.session() as session:
        session.add(
            EvidenceRecord(
                id=ids["evidence2"],
                case_id=ids["case2"],
                requirement_id=ids["requirement2"],
                stance="support",
                modality="transcript",
                start_ms=0,
                end_ms=1_000,
                artifact_id=ids["artifact2"],
                tool_run_id=ids["tool2"],
                source_ref=f"source:{suffix}:2",
                summary="evidence two",
                confidence=0.8,
                created_at=now,
                updated_at=now,
            )
        )
        for number in [1, 2]:
            session.add(
                RequirementResultRecord(
                    id=ids[f"result{number}"],
                    requirement_id=ids[f"requirement{number}"],
                    status="SATISFIED",
                    reason_code="SUPPORTED",
                    aggregator_version="test",
                    input_sha256="3" * 64,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                DecisionRecord(
                    id=ids[f"decision{number}"],
                    case_id=ids[f"case{number}"],
                    policy_id=policy1 if number == 1 else policy2,
                    policy_version=1,
                    verdict="APPROVE",
                    reason_code="SUPPORTED",
                    source="MACHINE",
                    explanation="test",
                    decision_metadata={},
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
        session.add_all(
            [
                RequirementResultEvidenceRecord(
                    result_id=ids["result1"],
                    evidence_id=ids["evidence1"],
                    requirement_id=ids["requirement1"],
                ),
                DecisionEvidenceRecord(
                    decision_id=ids["decision1"],
                    evidence_id=ids["evidence1"],
                    case_id=ids["case1"],
                ),
            ]
        )

    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            RequirementResultEvidenceRecord(
                result_id=ids["result1"],
                evidence_id=ids["evidence2"],
                requirement_id=ids["requirement1"],
            )
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.add(
            DecisionEvidenceRecord(
                decision_id=ids["decision1"],
                evidence_id=ids["evidence2"],
                case_id=ids["case1"],
            )
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            update(EvidenceRecord)
            .where(EvidenceRecord.id == ids["evidence1"])
            .values(summary="mutated")
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            update(PolicyRecord)
            .where(PolicyRecord.policy_id == policy1, PolicyRecord.version == 1)
            .values(name="mutated")
        )
    with pytest.raises(IntegrityError), database.session() as session:
        session.execute(
            delete(DecisionEvidenceRecord).where(
                DecisionEvidenceRecord.decision_id == ids["decision1"]
            )
        )


def _insert_evidence(
    database: Database,
    ids: dict[str, str],
    suffix: str,
    *,
    artifact_number: int,
    tool_number: int,
) -> None:
    now = utc_now()
    with database.session() as session:
        session.add(
            EvidenceRecord(
                id=ids["evidence1"],
                case_id=ids["case1"],
                requirement_id=ids["requirement1"],
                stance="support",
                modality="transcript",
                start_ms=0,
                end_ms=1_000,
                artifact_id=ids[f"artifact{artifact_number}"],
                tool_run_id=ids[f"tool{tool_number}"],
                source_ref=f"source:{suffix}:1",
                summary="evidence one",
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        )

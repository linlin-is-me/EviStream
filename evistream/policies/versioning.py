"""Transactional policy version and case-instantiation services."""

from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evistream.domain import (
    Case,
    CaseStatus,
    Policy,
    PolicyLifecycle,
    Requirement,
    RequirementStatus,
)
from evistream.media.types import VideoStatus
from evistream.policies.compiler import CompiledPolicy, PolicyCompiler
from evistream.policies.schema import LoadedPolicy
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    CaseRecord,
    PolicyRecord,
    RequirementRecord,
    VideoRecord,
)


class PolicyVersionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CaseBundle(BaseModel):
    case: Case
    requirements: list[Requirement]


class PolicyVersionService:
    def __init__(self, database: Database, compiler: PolicyCompiler | None = None) -> None:
        self.database = database
        self.compiler = compiler or PolicyCompiler()

    def save_draft(self, source: LoadedPolicy) -> Policy:
        compiled = self.compiler.compile(source)
        with self.database.session() as session:
            record = save_policy(session, source, compiled, PolicyLifecycle.DRAFT)
            return policy_from_record(record)

    def publish(self, source: LoadedPolicy) -> Policy:
        compiled = self.compiler.compile(source)
        with self.database.session() as session:
            record = save_policy(session, source, compiled, PolicyLifecycle.PUBLISHED)
            return policy_from_record(record)

    def get_version(self, policy_id: str, version: int) -> Policy:
        with self.database.session() as session:
            record = session.get(PolicyRecord, (policy_id, version))
            if record is None:
                raise PolicyVersionError("POLICY_NOT_FOUND", "policy version not found")
            return policy_from_record(record)

    def list_versions(self, policy_id: str) -> list[Policy]:
        with self.database.session() as session:
            records = session.scalars(
                select(PolicyRecord)
                .where(PolicyRecord.policy_id == policy_id)
                .order_by(PolicyRecord.version)
            ).all()
            return [policy_from_record(record) for record in records]


class CaseApplicationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_case(
        self,
        video_id: str,
        policy_id: str,
        policy_version: int,
        model_profile: str,
        *,
        case_id: str | None = None,
    ) -> CaseBundle:
        with self.database.session() as session:
            return create_case(
                session,
                video_id,
                policy_id,
                policy_version,
                model_profile,
                case_id=case_id,
            )


def save_policy(
    session: Session,
    source: LoadedPolicy,
    compiled: CompiledPolicy,
    lifecycle: PolicyLifecycle,
) -> PolicyRecord:
    key = (compiled.policy_id, compiled.version)
    existing = session.get(PolicyRecord, key)
    if existing is not None and existing.lifecycle == PolicyLifecycle.PUBLISHED:
        if (
            lifecycle is PolicyLifecycle.PUBLISHED
            and existing.semantic_sha256 == compiled.semantic_sha256
        ):
            return existing
        raise PolicyVersionError(
            "POLICY_VERSION_CONFLICT", "published policy versions are immutable"
        )
    latest = session.scalar(
        select(func.max(PolicyRecord.version)).where(PolicyRecord.policy_id == compiled.policy_id)
    )
    expected = 1 if latest is None else latest + 1
    if existing is None and compiled.version != expected:
        raise PolicyVersionError(
            "POLICY_VERSION_CONFLICT",
            f"expected policy version {expected}, received {compiled.version}",
        )
    now = utc_now()
    if existing is None:
        existing = PolicyRecord(
            policy_id=compiled.policy_id,
            version=compiled.version,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
    existing.name = compiled.name
    existing.severity = compiled.severity
    existing.enabled = compiled.enabled
    existing.lifecycle = lifecycle
    existing.source_yaml = source.source_yaml
    existing.compiled_policy = compiled.model_dump(mode="json")
    existing.source_sha256 = source.source_sha256
    existing.semantic_sha256 = compiled.semantic_sha256
    existing.compiler_version = compiled.compiler_version
    existing.updated_at = now
    session.flush()
    return existing


def create_case(
    session: Session,
    video_id: str,
    policy_id: str,
    policy_version: int,
    model_profile: str,
    *,
    case_id: str | None = None,
) -> CaseBundle:
    video = session.get(VideoRecord, video_id)
    if video is None:
        raise PolicyVersionError("VIDEO_NOT_FOUND", "video not found")
    if video.status != VideoStatus.READY:
        raise PolicyVersionError("VIDEO_NOT_READY", "video preprocessing is incomplete")
    policy = session.get(PolicyRecord, (policy_id, policy_version))
    if policy is None:
        raise PolicyVersionError("POLICY_NOT_FOUND", "policy version not found")
    if policy.lifecycle != PolicyLifecycle.PUBLISHED:
        raise PolicyVersionError("POLICY_NOT_PUBLISHED", "cases require a published policy")
    existing = session.scalar(
        select(CaseRecord).where(
            CaseRecord.video_id == video_id,
            CaseRecord.policy_id == policy_id,
            CaseRecord.policy_version == policy_version,
        )
    )
    if existing is not None:
        requirements = session.scalars(
            select(RequirementRecord)
            .where(RequirementRecord.case_id == existing.id)
            .order_by(RequirementRecord.requirement_key)
        ).all()
        return CaseBundle(
            case=case_from_record(existing),
            requirements=[requirement_from_record(item) for item in requirements],
        )
    compiled = CompiledPolicy.model_validate(policy.compiled_policy)
    now = utc_now()
    record = CaseRecord(
        id=case_id or f"case_{uuid4().hex}",
        video_id=video_id,
        policy_id=policy_id,
        policy_version=policy_version,
        model_profile=model_profile,
        status=CaseStatus.READY,
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.flush()
    requirement_records = [
        RequirementRecord(
            id=f"req_{uuid4().hex}",
            case_id=record.id,
            requirement_key=item.requirement_key,
            requirement_type=item.requirement_type,
            source_kind=item.source_kind,
            required=item.required,
            description=item.description,
            suggested_queries=item.suggested_queries,
            modalities=item.modalities,
            tool_capabilities=item.tool_capabilities,
            semantic_sha256=item.semantic_sha256,
            status=RequirementStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        for item in compiled.requirements
    ]
    session.add_all(requirement_records)
    session.flush()
    return CaseBundle(
        case=case_from_record(record),
        requirements=[requirement_from_record(item) for item in requirement_records],
    )


def policy_from_record(record: PolicyRecord) -> Policy:
    return Policy(
        policy_id=record.policy_id,
        version=record.version,
        name=record.name,
        severity=record.severity,
        enabled=record.enabled,
        lifecycle=record.lifecycle,
        source_yaml=record.source_yaml,
        compiled=record.compiled_policy,
        source_sha256=record.source_sha256,
        semantic_sha256=record.semantic_sha256,
        compiler_version=record.compiler_version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def case_from_record(record: CaseRecord) -> Case:
    return Case(
        case_id=record.id,
        video_id=record.video_id,
        policy_id=record.policy_id,
        policy_version=record.policy_version,
        model_profile=record.model_profile,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def requirement_from_record(record: RequirementRecord) -> Requirement:
    return Requirement(
        requirement_id=record.id,
        case_id=record.case_id,
        requirement_key=record.requirement_key,
        requirement_type=record.requirement_type,
        source_kind=record.source_kind,
        required=record.required,
        description=record.description,
        suggested_queries=record.suggested_queries,
        modalities=record.modalities,
        tool_capabilities=record.tool_capabilities,
        semantic_sha256=record.semantic_sha256,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )

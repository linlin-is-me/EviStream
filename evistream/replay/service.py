"""Replay materialization, execution, and lineage persistence."""

import json
from datetime import timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from evistream.agent.service import AgentInvestigationService
from evistream.application.types import JobRequest, JobStatus, TaskDispatcher
from evistream.governance.aggregation import RequirementAggregator
from evistream.governance.errors import GovernanceError
from evistream.governance.service import GovernanceApplicationService
from evistream.governance.types import (
    ReplayItemStatus,
    ReplayLineageAction,
    ReplayMode,
    ReplayResult,
)
from evistream.policies.versioning import create_case
from evistream.replay.planner import ReplayPlanner
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    CaseRecord,
    DecisionRecord,
    EvidenceRecord,
    ProcessingJobRecord,
    ReplayItemRecord,
    ReplayJobRecord,
    ReplayLineageRecord,
    RequirementRecord,
)


class ReplayApplicationService:
    def __init__(
        self,
        database: Database,
        planner: ReplayPlanner,
        governance: GovernanceApplicationService,
        agent_service: AgentInvestigationService | None = None,
        agent_dispatcher: TaskDispatcher | None = None,
    ) -> None:
        self.database = database
        self.planner = planner
        self.governance = governance
        self.agent_service = agent_service
        self.agent_dispatcher = agent_dispatcher
        self.aggregator = RequirementAggregator()

    def prepare(
        self,
        policy_id: str,
        source_version: int,
        target_version: int,
        preview_sha256: str,
        *,
        model_profile: str | None = None,
        model_change_policy: str = "keep",
    ) -> JobRequest:
        preview = self.planner.preview(
            policy_id,
            source_version,
            target_version,
            model_change_policy=model_change_policy,
        )
        if preview.preview_sha256 != preview_sha256:
            raise GovernanceError("REPLAY_PREVIEW_STALE", "replay preview has changed")
        request_key = sha256(
            f"POLICY_REPLAY:{preview_sha256}:{model_profile or ''}:"
            f"{model_change_policy}".encode()
        ).hexdigest()
        with self.database.session() as session:
            existing_job = session.scalar(
                select(ProcessingJobRecord).where(
                    ProcessingJobRecord.request_key == request_key
                )
            )
            if existing_job is not None:
                return self._request(session, existing_job.id)
            now = utc_now()
            job_id = f"job_{uuid4().hex}"
            replay_job_id = f"replay_{uuid4().hex}"
            correlation_id = f"corr_{uuid4().hex}"
            session.add(
                ProcessingJobRecord(
                    id=job_id,
                    type="POLICY_REPLAY",
                    subject_id=replay_job_id,
                    request_key=request_key,
                    correlation_id=correlation_id,
                    status=JobStatus.PENDING,
                    attempt=0,
                    max_attempts=3,
                    payload={
                        "replay_job_id": replay_job_id,
                        "model_profile": model_profile,
                    },
                    retryable=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            session.add(
                ReplayJobRecord(
                    id=replay_job_id,
                    processing_job_id=job_id,
                    policy_id=policy_id,
                    source_version=source_version,
                    target_version=target_version,
                    mode=preview.mode,
                    preview_sha256=preview.preview_sha256,
                    model_profile=model_profile,
                    model_change_policy=model_change_policy,
                    status=JobStatus.PENDING,
                    result_payload=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            for plan in preview.cases:
                item_id = "replay_item_" + sha256(
                    f"{replay_job_id}:{plan.source_case_id}".encode()
                ).hexdigest()[:32]
                session.add(
                    ReplayItemRecord(
                        id=item_id,
                        replay_job_id=replay_job_id,
                        source_case_id=plan.source_case_id,
                        target_case_id=None,
                        target_policy_version=target_version,
                        mode=plan.mode,
                        status=(
                            ReplayItemStatus.NEEDS_HUMAN_REVIEW
                            if plan.blocked_reason
                            else ReplayItemStatus.PENDING
                        ),
                        plan_payload=plan.model_dump(mode="json"),
                        result_payload=(
                            {"reason_code": plan.blocked_reason}
                            if plan.blocked_reason
                            else None
                        ),
                        source_decision_id=plan.source_decision_id,
                        target_decision_id=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            return JobRequest(
                job_id=job_id,
                job_type="POLICY_REPLAY",
                request_key=request_key,
                correlation_id=correlation_id,
                payload={"replay_job_id": replay_job_id},
            )

    def claim(self, request: JobRequest, lease_seconds: int = 300) -> str:
        with self.database.session() as session:
            job = session.scalar(
                select(ProcessingJobRecord)
                .where(ProcessingJobRecord.id == request.job_id)
                .with_for_update()
            )
            if job is None or job.type != "POLICY_REPLAY":
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay job not found")
            replay = session.scalar(
                select(ReplayJobRecord).where(
                    ReplayJobRecord.processing_job_id == job.id
                )
            )
            if replay is None or request.payload.get("replay_job_id") != replay.id:
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay state is inconsistent")
            if job.request_key != request.request_key:
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay request key changed")
            now = utc_now()
            if job.status == JobStatus.SUCCEEDED:
                return replay.id
            if job.status == JobStatus.RUNNING and job.lease_until and job.lease_until > now:
                raise GovernanceError("REPLAY_ALREADY_RUNNING", "replay lease is active")
            if job.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay is terminal")
            job.status = JobStatus.RUNNING
            job.attempt += 1
            job.started_at = job.started_at or now
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.updated_at = now
            replay.status = JobStatus.RUNNING
            replay.updated_at = now
            return replay.id

    async def execute(self, replay_job_id: str) -> ReplayResult:
        item_ids: list[str]
        with self.database.session() as session:
            item_ids = list(
                session.scalars(
                    select(ReplayItemRecord.id)
                    .where(ReplayItemRecord.replay_job_id == replay_job_id)
                    .order_by(ReplayItemRecord.id)
                ).all()
            )
        for item_id in item_ids:
            await self._execute_item(item_id)
        return self._complete(replay_job_id)

    async def _execute_item(self, item_id: str) -> None:
        scope: list[str] = []
        origin_result_ids: dict[str, str] = {}
        target_case_id: str
        mode: str
        profile: str
        with self.database.session() as session:
            item = session.scalar(
                select(ReplayItemRecord)
                .where(ReplayItemRecord.id == item_id)
                .with_for_update()
            )
            if item is None or item.status in {
                ReplayItemStatus.COMPLETED,
                ReplayItemStatus.NEEDS_HUMAN_REVIEW,
            }:
                return
            replay = session.get(ReplayJobRecord, item.replay_job_id)
            source_case = session.get(CaseRecord, item.source_case_id)
            if replay is None or source_case is None:
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay item source is missing")
            bundle = create_case(
                session,
                source_case.video_id,
                replay.policy_id,
                replay.target_version,
                replay.model_profile or source_case.model_profile,
            )
            target_case_id = bundle.case.case_id
            if item.target_case_id not in {None, target_case_id}:
                raise GovernanceError(
                    "REPLAY_TARGET_CASE_CONFLICT", "target case belongs to another replay"
                )
            other = session.scalar(
                select(ReplayItemRecord.id).where(
                    ReplayItemRecord.target_case_id == target_case_id,
                    ReplayItemRecord.id != item.id,
                )
            )
            if other is not None:
                raise GovernanceError(
                    "REPLAY_TARGET_CASE_CONFLICT", "target case belongs to another replay"
                )
            item.target_case_id = target_case_id
            item.status = ReplayItemStatus.MATERIALIZED
            item.updated_at = utc_now()
            plan = item.plan_payload
            reusable = set(plan.get("reusable_requirement_keys", []))
            source_requirements = {
                value.requirement_key: value
                for value in session.scalars(
                    select(RequirementRecord).where(
                        RequirementRecord.case_id == source_case.id
                    )
                )
            }
            target_requirements = {
                value.requirement_key: value
                for value in session.scalars(
                    select(RequirementRecord).where(
                        RequirementRecord.case_id == target_case_id
                    )
                )
            }
            for key in sorted(reusable):
                source_requirement = source_requirements.get(key)
                target_requirement = target_requirements.get(key)
                if source_requirement is None or target_requirement is None:
                    continue
                if source_requirement.semantic_sha256 != target_requirement.semantic_sha256:
                    raise GovernanceError(
                        "REPLAY_TARGET_CASE_CONFLICT", "reused requirement semantics changed"
                    )
                if source_requirement.current_result_id is not None:
                    origin_result_ids[key] = source_requirement.current_result_id
                for evidence in session.scalars(
                    select(EvidenceRecord).where(
                        EvidenceRecord.requirement_id == source_requirement.id
                    )
                ):
                    target_id = "evidence_" + sha256(
                        f"{item.id}:{evidence.id}:{target_requirement.id}".encode()
                    ).hexdigest()[:32]
                    if session.get(EvidenceRecord, target_id) is None:
                        session.add(
                            EvidenceRecord(
                                id=target_id,
                                case_id=target_case_id,
                                requirement_id=target_requirement.id,
                                stance=evidence.stance,
                                modality=evidence.modality,
                                start_ms=evidence.start_ms,
                                end_ms=evidence.end_ms,
                                artifact_id=evidence.artifact_id,
                                tool_run_id=None,
                                model_call_id=None,
                                model_name=evidence.model_name,
                                source_ref=evidence.source_ref,
                                summary=evidence.summary,
                                confidence=evidence.confidence,
                                origin_evidence_id=evidence.id,
                                replay_item_id=item.id,
                                created_at=utc_now(),
                                updated_at=utc_now(),
                            )
                        )
                    self._lineage(
                        session,
                        item.id,
                        "EVIDENCE",
                        ReplayLineageAction.REUSED,
                        evidence.id,
                        target_id,
                        "REQUIREMENT_SEMANTICS_UNCHANGED",
                    )
            for key in plan.get("investigate_requirement_keys", []):
                requirement = target_requirements.get(key)
                if requirement is not None:
                    scope.append(requirement.id)
            for key in plan.get("invalidations", []):
                source_requirement = source_requirements.get(
                    key.get("requirement_key", "")
                )
                self._lineage(
                    session,
                    item.id,
                    "REQUIREMENT",
                    ReplayLineageAction.INVALIDATED,
                    source_requirement.id if source_requirement is not None else None,
                    None,
                    key.get("reason", "REQUIREMENT_CHANGED"),
                )
            mode = item.mode
            profile = replay.model_profile or source_case.model_profile
            item.status = (
                ReplayItemStatus.INVESTIGATING if scope else ReplayItemStatus.MATERIALIZED
            )
        if mode == ReplayMode.REINVESTIGATE and scope:
            if self.agent_service is None or self.agent_dispatcher is None:
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "Agent runtime is unavailable")
            agent_request = self.agent_service.prepare(target_case_id, profile, scope)
            execution = await self.agent_dispatcher.dispatch(agent_request)
            if execution.error_code:
                raise GovernanceError(execution.error_code, "replay investigation failed")
        decision = self.governance.finalize_case(
            target_case_id,
            replay_item_id=item_id,
            allow_without_investigation=not scope,
            origin_result_ids=origin_result_ids,
        )
        with self.database.session() as session:
            item = session.get(ReplayItemRecord, item_id)
            if item is None:
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay item disappeared")
            source_decision = (
                session.get(DecisionRecord, item.source_decision_id)
                if item.source_decision_id
                else None
            )
            item.target_decision_id = decision.decision_id
            item.status = (
                ReplayItemStatus.NEEDS_HUMAN_REVIEW
                if decision.verdict == "NEEDS_HUMAN_REVIEW"
                else ReplayItemStatus.COMPLETED
            )
            item.result_payload = {
                "source_verdict": source_decision.verdict if source_decision else None,
                "target_verdict": decision.verdict,
                "changed": source_decision is None
                or source_decision.verdict != decision.verdict,
            }
            for requirement in session.scalars(
                select(RequirementRecord).where(
                    RequirementRecord.case_id == target_case_id,
                    RequirementRecord.current_result_id.is_not(None),
                )
            ):
                origin_result_id = origin_result_ids.get(requirement.requirement_key)
                if origin_result_id is not None:
                    self._lineage(
                        session,
                        item.id,
                        "REQUIREMENT_RESULT",
                        ReplayLineageAction.REUSED,
                        origin_result_id,
                        requirement.current_result_id,
                        "REQUIREMENT_SEMANTICS_UNCHANGED",
                    )
            item.updated_at = utc_now()

    def _complete(self, replay_job_id: str) -> ReplayResult:
        with self.database.session() as session:
            replay = session.get(ReplayJobRecord, replay_job_id)
            if replay is None:
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay job disappeared")
            items = list(
                session.scalars(
                    select(ReplayItemRecord).where(
                        ReplayItemRecord.replay_job_id == replay.id
                    )
                ).all()
            )
            completed = sum(item.status == ReplayItemStatus.COMPLETED for item in items)
            human = sum(
                item.status == ReplayItemStatus.NEEDS_HUMAN_REVIEW for item in items
            )
            failed = sum(item.status == ReplayItemStatus.FAILED for item in items)
            changes = [item.result_payload for item in items if item.result_payload]
            result = ReplayResult(
                replay_job_id=replay.id,
                processing_job_id=replay.processing_job_id,
                status=JobStatus.SUCCEEDED,
                mode=ReplayMode(replay.mode),
                completed_items=completed,
                human_review_items=human,
                failed_items=failed,
                decision_changes=changes,
            )
            now = utc_now()
            replay.status = JobStatus.SUCCEEDED
            replay.result_payload = result.model_dump(mode="json")
            replay.updated_at = now
            job = session.get(ProcessingJobRecord, replay.processing_job_id)
            if job is not None:
                job.status = JobStatus.SUCCEEDED
                job.finished_at = now
                job.lease_until = None
                job.updated_at = now
            return result

    def fail(self, replay_job_id: str, code: str) -> None:
        with self.database.session() as session:
            replay = session.get(ReplayJobRecord, replay_job_id)
            if replay is None:
                return
            replay.status = JobStatus.FAILED
            replay.updated_at = utc_now()
            job = session.get(ProcessingJobRecord, replay.processing_job_id)
            if job is not None:
                job.status = JobStatus.FAILED
                job.error_code = code
                job.finished_at = utc_now()
                job.lease_until = None

    def status(self, job_id: str) -> ReplayResult | dict[str, Any]:
        with self.database.session() as session:
            replay = session.scalar(
                select(ReplayJobRecord).where(ReplayJobRecord.processing_job_id == job_id)
            )
            if replay is None:
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay job not found")
            if replay.result_payload:
                return ReplayResult.model_validate(replay.result_payload)
            return {
                "replay_job_id": replay.id,
                "processing_job_id": replay.processing_job_id,
                "status": replay.status,
                "mode": replay.mode,
            }

    def diff(self, job_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            replay = session.scalar(
                select(ReplayJobRecord).where(ReplayJobRecord.processing_job_id == job_id)
            )
            if replay is None:
                raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay job not found")
            return [
                {
                    "replay_item_id": item.id,
                    "source_case_id": item.source_case_id,
                    "target_case_id": item.target_case_id,
                    "status": item.status,
                    "result": item.result_payload,
                }
                for item in session.scalars(
                    select(ReplayItemRecord)
                    .where(ReplayItemRecord.replay_job_id == replay.id)
                    .order_by(ReplayItemRecord.id)
                )
            ]

    def _request(self, session: Any, job_id: str) -> JobRequest:
        job = session.get(ProcessingJobRecord, job_id)
        replay = session.scalar(
            select(ReplayJobRecord).where(ReplayJobRecord.processing_job_id == job_id)
        )
        if job is None or replay is None:
            raise GovernanceError("REPLAY_NOT_RESUMABLE", "replay job is incomplete")
        return JobRequest(
            job_id=job.id,
            job_type=job.type,
            request_key=job.request_key,
            correlation_id=job.correlation_id,
            payload=job.payload
            or {"replay_job_id": replay.id, "model_profile": replay.model_profile},
        )

    @staticmethod
    def _lineage(
        session: Any,
        replay_item_id: str,
        entity_type: str,
        action: ReplayLineageAction,
        source_id: str | None,
        target_id: str | None,
        reason_code: str,
    ) -> None:
        payload = json.dumps(
            [replay_item_id, entity_type, action, source_id, target_id, reason_code],
            separators=(",", ":"),
        )
        lineage_id = "lineage_" + sha256(payload.encode()).hexdigest()[:32]
        if session.get(ReplayLineageRecord, lineage_id) is None:
            session.add(
                ReplayLineageRecord(
                    id=lineage_id,
                    replay_item_id=replay_item_id,
                    entity_type=entity_type,
                    action=action,
                    source_id=source_id,
                    target_id=target_id,
                    reason_code=reason_code,
                    created_at=utc_now(),
                )
            )

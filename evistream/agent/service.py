"""Transactional lifecycle, leases, checkpoints, and trace queries for investigations."""

import json
from collections.abc import Callable, Sequence
from datetime import timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from evistream.agent.errors import AgentRuntimeError
from evistream.agent.types import (
    AgentNode,
    InvestigationRequirement,
    InvestigationResult,
    InvestigationState,
    InvestigationStatus,
    ProvisionalDecision,
)
from evistream.application.types import JobRequest, JobStatus
from evistream.config import Settings
from evistream.domain import CaseStatus
from evistream.governance.evidence import EvidenceStore
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    AgentRunRecord,
    AgentStepRecord,
    CaseRecord,
    EvidenceRecord,
    ModelCallRecord,
    ProcessingJobRecord,
    RequirementRecord,
    ToolRunRecord,
)

ALLOWED_TRANSITIONS: dict[AgentNode, set[AgentNode | None]] = {
    AgentNode.PLAN: {AgentNode.RETRIEVE},
    AgentNode.RETRIEVE: {AgentNode.INSPECT},
    AgentNode.INSPECT: {AgentNode.VERIFY},
    AgentNode.VERIFY: {AgentNode.CHALLENGE},
    AgentNode.CHALLENGE: {AgentNode.PLAN, AgentNode.DECIDE},
    AgentNode.DECIDE: {None},
}


class PendingEvidence:
    def __init__(
        self,
        *,
        evidence_id: str,
        requirement_id: str,
        stance: str,
        modality: str,
        start_ms: int,
        end_ms: int,
        artifact_id: str | None,
        tool_run_id: str,
        model_call_id: str,
        model_name: str,
        source_ref: str,
        summary: str,
        confidence: float | None,
    ) -> None:
        self.evidence_id = evidence_id
        self.requirement_id = requirement_id
        self.stance = stance
        self.modality = modality
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.artifact_id = artifact_id
        self.tool_run_id = tool_run_id
        self.model_call_id = model_call_id
        self.model_name = model_name
        self.source_ref = source_ref
        self.summary = summary
        self.confidence = confidence


class AgentInvestigationService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        checkpoint_hook: Callable[[InvestigationState], None] | None = None,
        evidence_store: EvidenceStore | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.checkpoint_hook = checkpoint_hook
        self.evidence_store = evidence_store or EvidenceStore()

    def prepare(
        self,
        case_id: str,
        profile: str | None = None,
        scope_requirement_ids: list[str] | None = None,
    ) -> JobRequest:
        with self.database.session() as session:
            case = session.get(CaseRecord, case_id)
            if case is None:
                raise AgentRuntimeError("CASE_NOT_FOUND", f"case not found: {case_id}")
            existing = session.scalar(
                select(AgentRunRecord).where(
                    AgentRunRecord.case_id == case_id,
                    AgentRunRecord.run_kind == "INVESTIGATION",
                )
            )
            if existing is not None:
                if profile is not None and profile != existing.model_profile:
                    raise AgentRuntimeError(
                        "AGENT_PROFILE_CONFLICT", "the existing run uses another model profile"
                    )
                requested_scope = sorted(set(scope_requirement_ids or []))
                if requested_scope and requested_scope != sorted(existing.scope_requirement_ids):
                    raise AgentRuntimeError(
                        "AGENT_SCOPE_CONFLICT", "the existing run uses another requirement scope"
                    )
                if existing.status in {"FAILED", "CANCELLED"}:
                    raise AgentRuntimeError(
                        "AGENT_RUN_NOT_RESUMABLE", "the existing run is terminal"
                    )
                if existing.job_id is None:
                    raise AgentRuntimeError(
                        "AGENT_CHECKPOINT_INVALID", "investigation run has no job"
                    )
                return self._job_request(session, existing.job_id)
            if case.status != CaseStatus.READY:
                raise AgentRuntimeError(
                    "AGENT_CASE_NOT_READY", f"case status is {case.status}"
                )
            selected_profile = profile or case.model_profile
            case.model_profile = selected_profile
            requirements = list(
                session.scalars(
                    select(RequirementRecord)
                    .where(RequirementRecord.case_id == case_id)
                    .order_by(RequirementRecord.requirement_key)
                ).all()
            )
            requested_scope = sorted(set(scope_requirement_ids or []))
            if requested_scope:
                known_ids = {item.id for item in requirements}
                unknown_ids = sorted(set(requested_scope) - known_ids)
                if unknown_ids:
                    raise AgentRuntimeError(
                        "AGENT_ACTION_INVALID",
                        f"requirement scope is outside the case: {unknown_ids}",
                    )
                requirements = [item for item in requirements if item.id in requested_scope]
            if not requirements:
                raise AgentRuntimeError(
                    "AGENT_CASE_NOT_READY", "case has no instantiated requirements"
                )
            now = utc_now()
            run_id = f"run_{uuid4().hex}"
            job_id = f"job_{uuid4().hex}"
            correlation_id = f"corr_{uuid4().hex}"
            request_key = sha256(
                f"AGENT_INVESTIGATION:{case.id}:{case.policy_id}:{case.policy_version}:"
                f"{selected_profile}:{','.join(requested_scope)}".encode()
            ).hexdigest()
            state = InvestigationState(
                run_id=run_id,
                job_id=job_id,
                case_id=case.id,
                policy_id=case.policy_id,
                policy_version=case.policy_version,
                model_profile=selected_profile,
                requirements=[_requirement_snapshot(item) for item in requirements],
                missing_requirement_ids=[
                    item.id
                    for item in requirements
                    if item.required and item.source_kind == "requirement"
                ],
                deadline_at=now + timedelta(seconds=self.settings.agent_timeout_seconds),
                last_checkpoint_at=now,
            )
            session.add(
                ProcessingJobRecord(
                    id=job_id,
                    type="AGENT_INVESTIGATION",
                    subject_id=case.id,
                    request_key=request_key,
                    correlation_id=correlation_id,
                    status=JobStatus.PENDING,
                    attempt=0,
                    max_attempts=self.settings.job_max_attempts,
                    payload={
                        "run_id": run_id,
                        "case_id": case.id,
                        "model_profile": selected_profile,
                        "scope_requirement_ids": requested_scope,
                    },
                    retryable=False,
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
                    case_id=case.id,
                    model_profile=selected_profile,
                    current_node=AgentNode.PLAN,
                    next_node=AgentNode.PLAN,
                    state_snapshot=state.model_dump(mode="json"),
                    state_version=0,
                    status=InvestigationStatus.PENDING,
                    scope_requirement_ids=requested_scope,
                    iteration=0,
                    vlm_calls=0,
                    consecutive_tool_failures=0,
                    total_tool_failures=0,
                    stagnant_iterations=0,
                    deadline_at=state.deadline_at,
                    last_checkpoint_at=now,
                    lease_until=None,
                    provisional_verdict=None,
                    stop_reason=None,
                    result_payload=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            return JobRequest(
                job_id=job_id,
                job_type="AGENT_INVESTIGATION",
                request_key=request_key,
                correlation_id=correlation_id,
                payload={
                    "run_id": run_id,
                    "case_id": case.id,
                    "model_profile": selected_profile,
                    "scope_requirement_ids": requested_scope,
                },
            )

    def build_job_request(self, job_id: str) -> JobRequest:
        with self.database.session() as session:
            return self._job_request(session, job_id)

    def _job_request(self, session: Session, job_id: str) -> JobRequest:
        job = session.get(ProcessingJobRecord, job_id)
        if job is None:
            raise AgentRuntimeError("JOB_NOT_FOUND", f"job not found: {job_id}")
        run = session.scalar(select(AgentRunRecord).where(AgentRunRecord.job_id == job_id))
        if run is None:
            raise AgentRuntimeError("AGENT_CHECKPOINT_INVALID", "job has no Agent run")
        return JobRequest(
            job_id=job.id,
            job_type=job.type,
            request_key=job.request_key,
            correlation_id=job.correlation_id,
                payload=job.payload
                or {
                    "run_id": run.id,
                    "case_id": run.case_id,
                    "model_profile": run.model_profile,
                },
            )

    def claim(self, request: JobRequest) -> InvestigationState | InvestigationResult:
        with self.database.session() as session:
            job = session.get(ProcessingJobRecord, request.job_id)
            run = session.get(AgentRunRecord, request.payload.get("run_id"))
            if job is None or run is None:
                raise AgentRuntimeError("AGENT_CHECKPOINT_INVALID", "job or run is missing")
            if (
                job.type != request.job_type
                or job.request_key != request.request_key
                or job.correlation_id != request.correlation_id
                or job.subject_id != request.payload.get("case_id")
                or run.job_id != job.id
                or run.case_id != request.payload.get("case_id")
                or run.model_profile != request.payload.get("model_profile")
            ):
                raise AgentRuntimeError(
                    "AGENT_CHECKPOINT_INVALID", "request does not match persisted state"
                )
            if run.status in {"COMPLETED", "NEEDS_HUMAN_REVIEW"}:
                return self._result(session, run)
            if run.status in {"FAILED", "CANCELLED"}:
                raise AgentRuntimeError("AGENT_RUN_NOT_RESUMABLE", "run is terminal")
            now = utc_now()
            if run.status == "RUNNING" and run.lease_until is not None and run.lease_until > now:
                raise AgentRuntimeError(
                    "AGENT_RUN_ALREADY_RUNNING", "run lease is still active"
                )
            if (
                job.status == JobStatus.RUNNING
                and job.lease_until is not None
                and job.lease_until > now
            ):
                raise AgentRuntimeError(
                    "AGENT_RUN_ALREADY_RUNNING", "job lease is still active"
                )
            if job.status not in {JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RETRY_WAIT}:
                raise AgentRuntimeError("AGENT_RUN_NOT_RESUMABLE", "job is terminal")
            lease_until = now + timedelta(seconds=self.settings.agent_job_lease_seconds)
            job.status = JobStatus.RUNNING
            job.attempt += 1
            job.started_at = job.started_at or now
            job.finished_at = None
            job.error_code = None
            job.lease_until = lease_until
            job.updated_at = now
            run.status = InvestigationStatus.RUNNING
            run.lease_until = lease_until
            run.updated_at = now
            case = session.get(CaseRecord, run.case_id)
            if case is None:
                raise AgentRuntimeError("AGENT_CHECKPOINT_INVALID", "case disappeared")
            if case.status not in {CaseStatus.READY, CaseStatus.INVESTIGATING}:
                raise AgentRuntimeError("AGENT_CASE_NOT_READY", f"case status is {case.status}")
            case.status = CaseStatus.INVESTIGATING
            case.updated_at = now
            state = _state(run)
            state.status = InvestigationStatus.RUNNING
            state.last_checkpoint_at = now
            run.state_snapshot = state.model_dump(mode="json")
            return state

    def checkpoint(
        self,
        state: InvestigationState,
        *,
        node: AgentNode,
        next_node: AgentNode | None,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        latency_ms: int,
        evidence: Sequence[PendingEvidence] = (),
    ) -> InvestigationState:
        if next_node not in ALLOWED_TRANSITIONS[node]:
            raise AgentRuntimeError(
                "AGENT_TRANSITION_INVALID", f"{node} cannot transition to {next_node}"
            )
        if state.next_node != node:
            raise AgentRuntimeError(
                "AGENT_TRANSITION_INVALID", f"checkpoint expected {state.next_node}, got {node}"
            )
        previous_version = state.state_version
        now = utc_now()
        state.current_node = node
        state.next_node = next_node
        state.state_version += 1
        state.last_checkpoint_at = now
        state.status = InvestigationStatus.RUNNING
        with self.database.session() as session:
            claim_result = session.execute(
                update(AgentRunRecord)
                .where(
                    AgentRunRecord.id == state.run_id,
                    AgentRunRecord.state_version == previous_version,
                    AgentRunRecord.status == InvestigationStatus.RUNNING,
                )
                .values(
                    current_node=node,
                    next_node=next_node,
                    state_snapshot=state.model_dump(mode="json"),
                    state_version=state.state_version,
                    iteration=state.iteration,
                    vlm_calls=state.vlm_calls,
                    consecutive_tool_failures=state.consecutive_tool_failures,
                    total_tool_failures=state.total_tool_failures,
                    stagnant_iterations=state.stagnant_iterations,
                    last_checkpoint_at=now,
                    lease_until=now
                    + timedelta(seconds=self.settings.agent_job_lease_seconds),
                    updated_at=now,
                )
            )
            claimed = getattr(claim_result, "rowcount", 0)
            if claimed != 1:
                raise AgentRuntimeError(
                    "AGENT_STATE_CONFLICT", "another executor advanced this state version"
                )
            session.execute(
                update(ProcessingJobRecord)
                .where(ProcessingJobRecord.id == state.job_id)
                .values(
                    lease_until=now + timedelta(seconds=self.settings.agent_job_lease_seconds),
                    updated_at=now,
                )
            )
            for item in evidence:
                self.evidence_store.append_pending(
                    session,
                    case_id=state.case_id,
                    item=item,
                    now=now,
                )
            session.add(
                AgentStepRecord(
                    id=f"step_{uuid4().hex}",
                    run_id=state.run_id,
                    node=node,
                    iteration=state.iteration,
                    state_version=state.state_version,
                    input_payload=input_payload,
                    output_payload=output_payload,
                    latency_ms=latency_ms,
                    status="success",
                    error_code=None,
                    created_at=now,
                )
            )
        if self.checkpoint_hook is not None:
            self.checkpoint_hook(state)
        return state

    def complete(
        self,
        state: InvestigationState,
        decision: ProvisionalDecision,
        stop_reason: str,
    ) -> InvestigationResult:
        now = utc_now()
        status = (
            InvestigationStatus.NEEDS_HUMAN_REVIEW
            if decision.verdict == "NEEDS_HUMAN_REVIEW"
            else InvestigationStatus.COMPLETED
        )
        state.status = status
        state.provisional_decision = decision
        state.stop_reason = stop_reason
        state.next_node = None
        with self.database.session() as session:
            run = session.get(AgentRunRecord, state.run_id)
            job = session.get(ProcessingJobRecord, state.job_id)
            case = session.get(CaseRecord, state.case_id)
            if run is None or job is None or case is None:
                raise AgentRuntimeError("AGENT_CHECKPOINT_INVALID", "terminal state disappeared")
            if run.state_version != state.state_version:
                raise AgentRuntimeError("AGENT_STATE_CONFLICT", "terminal version changed")
            run.status = status
            run.next_node = None
            run.state_snapshot = state.model_dump(mode="json")
            run.provisional_verdict = decision.verdict
            run.stop_reason = stop_reason
            run.result_payload = decision.model_dump(mode="json")
            run.lease_until = None
            run.updated_at = now
            job.status = JobStatus.SUCCEEDED
            job.finished_at = now
            job.lease_until = None
            job.error_code = None
            job.updated_at = now
            case.status = (
                CaseStatus.NEEDS_HUMAN_REVIEW
                if status is InvestigationStatus.NEEDS_HUMAN_REVIEW
                else CaseStatus.INVESTIGATED
            )
            case.updated_at = now
            return self._result(session, run)

    def fail(self, run_id: str, error_code: str) -> None:
        now = utc_now()
        with self.database.session() as session:
            run = session.get(AgentRunRecord, run_id)
            if run is None:
                return
            run.status = InvestigationStatus.FAILED
            run.stop_reason = error_code
            run.lease_until = None
            run.updated_at = now
            if run.job_id is not None:
                job = session.get(ProcessingJobRecord, run.job_id)
                if job is not None:
                    job.status = JobStatus.FAILED
                    job.error_code = error_code
                    job.finished_at = now
                    job.lease_until = None
                    job.updated_at = now
            case = session.get(CaseRecord, run.case_id)
            if case is not None:
                case.status = CaseStatus.NEEDS_HUMAN_REVIEW
                case.updated_at = now

    def defer_retry(self, run_id: str, error_code: str) -> None:
        now = utc_now()
        with self.database.session() as session:
            run = session.get(AgentRunRecord, run_id)
            if run is None or run.job_id is None:
                raise AgentRuntimeError("AGENT_CHECKPOINT_INVALID", "run job is missing")
            job = session.get(ProcessingJobRecord, run.job_id)
            if job is None:
                raise AgentRuntimeError("AGENT_CHECKPOINT_INVALID", "run job is missing")
            can_retry = job.attempt < job.max_attempts
            if not can_retry:
                run.status = InvestigationStatus.FAILED
                run.stop_reason = error_code
                run.lease_until = None
                run.updated_at = now
                job.status = JobStatus.FAILED
                job.retryable = False
                job.error_code = error_code
                job.finished_at = now
                job.lease_until = None
                job.updated_at = now
                case = session.get(CaseRecord, run.case_id)
                if case is not None:
                    case.status = CaseStatus.NEEDS_HUMAN_REVIEW
                    case.updated_at = now
                return
            interval_index = min(
                max(job.attempt - 1, 0), len(self.settings.job_retry_intervals) - 1
            )
            delay = (
                self.settings.job_retry_intervals[interval_index]
                if self.settings.job_retry_intervals
                else 0
            )
            run.status = InvestigationStatus.PENDING
            run.lease_until = None
            run.stop_reason = error_code
            run.updated_at = now
            job.status = JobStatus.RETRY_WAIT
            job.retryable = True
            job.error_code = error_code
            job.next_attempt_at = now + timedelta(seconds=delay)
            job.lease_until = None
            job.updated_at = now

    def get_result(self, run_id: str) -> InvestigationResult:
        with self.database.session() as session:
            run = session.get(AgentRunRecord, run_id)
            if run is None:
                raise AgentRuntimeError("AGENT_RUN_NOT_FOUND", f"run not found: {run_id}")
            return self._result(session, run)

    def trace(self, run_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            run = session.get(AgentRunRecord, run_id)
            if run is None:
                raise AgentRuntimeError("AGENT_RUN_NOT_FOUND", f"run not found: {run_id}")
            steps = session.scalars(
                select(AgentStepRecord)
                .where(AgentStepRecord.run_id == run_id)
                .order_by(AgentStepRecord.state_version)
            ).all()
            model_calls = session.scalars(
                select(ModelCallRecord)
                .where(ModelCallRecord.run_id == run_id)
                .order_by(ModelCallRecord.created_at, ModelCallRecord.id)
            ).all()
            tool_runs = session.scalars(
                select(ToolRunRecord)
                .where(ToolRunRecord.run_id == run_id)
                .order_by(ToolRunRecord.created_at, ToolRunRecord.id)
            ).all()
            return {
                "result": self._result(session, run).model_dump(mode="json"),
                "steps": [
                    {
                        "node": item.node,
                        "iteration": item.iteration,
                        "state_version": item.state_version,
                        "status": item.status,
                        "latency_ms": item.latency_ms,
                        "error_code": item.error_code,
                        "input": item.input_payload,
                        "output": item.output_payload,
                    }
                    for item in steps
                ],
                "model_calls": [
                    {
                        "model_call_id": item.id,
                        "node": item.node,
                        "role": item.role,
                        "profile": item.profile,
                        "requested_model": item.requested_model,
                        "actual_model": item.actual_model,
                        "status": item.status,
                        "usage": {
                            "prompt_tokens": item.prompt_tokens,
                            "completion_tokens": item.completion_tokens,
                            "total_tokens": item.total_tokens,
                        },
                        "latency_ms": item.latency_ms,
                        "provider_request_id": item.provider_request_id,
                        "error_code": item.error_code,
                        "request_summary": item.request_summary,
                    }
                    for item in model_calls
                ],
                "tool_runs": [
                    {
                        "tool_run_id": item.id,
                        "tool_name": item.tool_name,
                        "request_key": item.request_key,
                        "status": item.status,
                        "latency_ms": item.latency_ms,
                        "error_code": item.error_code,
                    }
                    for item in tool_runs
                ],
            }

    def _result(self, session: Session, run: AgentRunRecord) -> InvestigationResult:
        decision = (
            ProvisionalDecision.model_validate(run.result_payload)
            if run.result_payload is not None
            else None
        )
        counts = {
            "node_count": session.scalar(
                select(func.count()).select_from(AgentStepRecord).where(
                    AgentStepRecord.run_id == run.id
                )
            )
            or 0,
            "tool_count": session.scalar(
                select(func.count()).select_from(ToolRunRecord).where(
                    ToolRunRecord.run_id == run.id
                )
            )
            or 0,
            "model_call_count": session.scalar(
                select(func.count()).select_from(ModelCallRecord).where(
                    ModelCallRecord.run_id == run.id
                )
            )
            or 0,
            "evidence_count": session.scalar(
                select(func.count()).select_from(EvidenceRecord).join(
                    ModelCallRecord, EvidenceRecord.model_call_id == ModelCallRecord.id
                ).where(ModelCallRecord.run_id == run.id)
            )
            or 0,
        }
        return InvestigationResult(
            run_id=run.id,
            job_id=run.job_id,
            case_id=run.case_id,
            status=run.status,
            provisional_decision=decision,
            stop_reason=run.stop_reason,
            state_version=run.state_version,
            **counts,
        )


def _state(record: AgentRunRecord) -> InvestigationState:
    try:
        return InvestigationState.model_validate(record.state_snapshot)
    except ValueError as error:
        raise AgentRuntimeError(
            "AGENT_CHECKPOINT_INVALID", "persisted Agent state is invalid"
        ) from error


def _requirement_snapshot(record: RequirementRecord) -> InvestigationRequirement:
    return InvestigationRequirement(
        requirement_id=record.id,
        requirement_key=record.requirement_key,
        source_kind=record.source_kind,
        required=record.required,
        description=record.description,
        suggested_queries=record.suggested_queries,
        modalities=record.modalities,
        tool_capabilities=record.tool_capabilities,
    )


def canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

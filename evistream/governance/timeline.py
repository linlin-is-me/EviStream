"""Stable, sanitized Case timeline projection."""

from datetime import datetime
from typing import Any

from sqlalchemy import select

from evistream.governance.errors import GovernanceError
from evistream.storage.database import Database
from evistream.storage.models import (
    AgentRunRecord,
    AgentStepRecord,
    AppealEventRecord,
    AppealRecord,
    CaseRecord,
    DecisionRecord,
    EvidenceRecord,
    ModelCallRecord,
    ReplayItemRecord,
    RequirementRecord,
    RequirementResultRecord,
    ReviewRecord,
    ToolRunRecord,
)

_ORDER = {
    "AGENT_STEP": 10,
    "TOOL_RUN": 20,
    "MODEL_CALL": 30,
    "EVIDENCE": 40,
    "REQUIREMENT_RESULT": 50,
    "DECISION": 60,
    "REVIEW": 70,
    "APPEAL": 80,
    "REPLAY": 90,
}


class CaseTimelineService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def timeline(self, case_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            if session.get(CaseRecord, case_id) is None:
                raise GovernanceError("CASE_NOT_FOUND", f"case not found: {case_id}")
            events: list[dict[str, Any]] = []
            run_ids = list(
                session.scalars(
                    select(AgentRunRecord.id).where(AgentRunRecord.case_id == case_id)
                ).all()
            )
            requirement_ids = list(
                session.scalars(
                    select(RequirementRecord.id).where(RequirementRecord.case_id == case_id)
                ).all()
            )
            if run_ids:
                for step in session.scalars(
                    select(AgentStepRecord).where(AgentStepRecord.run_id.in_(run_ids))
                ):
                    events.append(
                        _event(
                            "AGENT_STEP",
                            step.id,
                            step.created_at,
                            {
                                "run_id": step.run_id,
                                "node": step.node,
                                "iteration": step.iteration,
                                "status": step.status,
                                "error_code": step.error_code,
                            },
                        )
                    )
            for tool_run in session.scalars(
                select(ToolRunRecord).where(ToolRunRecord.case_id == case_id)
            ):
                events.append(
                    _event(
                        "TOOL_RUN",
                        tool_run.id,
                        tool_run.created_at,
                        {
                            "tool_name": tool_run.tool_name,
                            "status": tool_run.status,
                            "latency_ms": tool_run.latency_ms,
                            "error_code": tool_run.error_code,
                        },
                    )
                )
            for model_call in session.scalars(
                select(ModelCallRecord).where(ModelCallRecord.case_id == case_id)
            ):
                events.append(
                    _event(
                        "MODEL_CALL",
                        model_call.id,
                        model_call.created_at,
                        {
                            "role": model_call.role,
                            "profile": model_call.profile,
                            "actual_model": model_call.actual_model,
                            "status": model_call.status,
                            "total_tokens": model_call.total_tokens,
                            "latency_ms": model_call.latency_ms,
                            "error_code": model_call.error_code,
                        },
                    )
                )
            for evidence in session.scalars(
                select(EvidenceRecord).where(EvidenceRecord.case_id == case_id)
            ):
                events.append(
                    _event(
                        "EVIDENCE",
                        evidence.id,
                        evidence.created_at,
                        {
                            "requirement_id": evidence.requirement_id,
                            "stance": evidence.stance,
                            "modality": evidence.modality,
                            "start_ms": evidence.start_ms,
                            "end_ms": evidence.end_ms,
                            "source_ref": evidence.source_ref,
                            "origin_evidence_id": evidence.origin_evidence_id,
                        },
                    )
                )
            if requirement_ids:
                for result in session.scalars(
                    select(RequirementResultRecord).where(
                        RequirementResultRecord.requirement_id.in_(requirement_ids)
                    )
                ):
                    events.append(
                        _event(
                            "REQUIREMENT_RESULT",
                            result.id,
                            result.created_at,
                            {
                                "requirement_id": result.requirement_id,
                                "status": result.status,
                                "reason_code": result.reason_code,
                                "sequence": result.sequence,
                                "origin_result_id": result.origin_result_id,
                            },
                        )
                    )
            for decision in session.scalars(
                select(DecisionRecord).where(DecisionRecord.case_id == case_id)
            ):
                events.append(
                    _event(
                        "DECISION",
                        decision.id,
                        decision.created_at,
                        {
                            "verdict": decision.verdict,
                            "reason_code": decision.reason_code,
                            "source": decision.source,
                            "sequence": decision.sequence,
                            "supersedes_decision_id": decision.supersedes_decision_id,
                        },
                    )
                )
            for review in session.scalars(
                select(ReviewRecord).where(ReviewRecord.case_id == case_id)
            ):
                events.append(
                    _event(
                        "REVIEW",
                        review.id,
                        review.created_at,
                        {
                            "reviewer": review.reviewer,
                            "reviewed_decision_id": review.reviewed_decision_id,
                            "decision_id": review.decision_id,
                            "note": review.note,
                        },
                    )
                )
            appeal_ids = list(
                session.scalars(select(AppealRecord.id).where(AppealRecord.case_id == case_id))
            )
            if appeal_ids:
                for appeal_event in session.scalars(
                    select(AppealEventRecord).where(
                        AppealEventRecord.appeal_id.in_(appeal_ids)
                    )
                ):
                    events.append(
                        _event(
                            "APPEAL",
                            appeal_event.id,
                            appeal_event.created_at,
                            {
                                "appeal_id": appeal_event.appeal_id,
                                "sequence": appeal_event.sequence,
                                "event_type": appeal_event.event_type,
                                "actor": appeal_event.actor,
                                "note": appeal_event.note,
                                "decision_id": appeal_event.decision_id,
                            },
                        )
                    )
            for replay_item in session.scalars(
                select(ReplayItemRecord).where(
                    (ReplayItemRecord.source_case_id == case_id)
                    | (ReplayItemRecord.target_case_id == case_id)
                )
            ):
                events.append(
                    _event(
                        "REPLAY",
                        replay_item.id,
                        replay_item.created_at,
                        {
                            "mode": replay_item.mode,
                            "status": replay_item.status,
                            "source_case_id": replay_item.source_case_id,
                            "target_case_id": replay_item.target_case_id,
                            "source_decision_id": replay_item.source_decision_id,
                            "target_decision_id": replay_item.target_decision_id,
                        },
                    )
                )
            return sorted(
                events,
                key=lambda item: (
                    item["created_at"],
                    _ORDER[str(item["event_type"])],
                    item["event_id"],
                ),
            )


def _event(
    event_type: str,
    event_id: str,
    created_at: datetime,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "event_id": event_id,
        "created_at": created_at.isoformat(),
        "payload": payload,
    }

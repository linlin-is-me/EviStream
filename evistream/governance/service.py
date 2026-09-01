"""Transactional formalization of an investigated Case."""

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evistream.domain import CaseStatus, Decision, DecisionSource, Verdict
from evistream.governance.aggregation import RequirementAggregator
from evistream.governance.errors import GovernanceError
from evistream.governance.rule_evaluator import RuleEvaluator
from evistream.policies.compiler import CompiledPolicy
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    AgentRunRecord,
    CaseRecord,
    DecisionEvidenceRecord,
    DecisionRecord,
    DecisionRequirementResultRecord,
    PolicyRecord,
    RequirementRecord,
)


class GovernanceApplicationService:
    def __init__(
        self,
        database: Database,
        aggregator: RequirementAggregator | None = None,
        evaluator: RuleEvaluator | None = None,
    ) -> None:
        self.database = database
        self.aggregator = aggregator or RequirementAggregator()
        self.evaluator = evaluator or RuleEvaluator()

    def finalize_case(
        self,
        case_id: str,
        *,
        replay_item_id: str | None = None,
        allow_without_investigation: bool = False,
        origin_result_ids: dict[str, str] | None = None,
    ) -> Decision:
        with self.database.session() as session:
            case = session.scalar(
                select(CaseRecord).where(CaseRecord.id == case_id).with_for_update()
            )
            if case is None:
                raise GovernanceError("CASE_NOT_FOUND", f"case not found: {case_id}")
            policy_record = session.get(
                PolicyRecord, (case.policy_id, case.policy_version)
            )
            if policy_record is None:
                raise GovernanceError("POLICY_NOT_FOUND", "case policy disappeared")
            if not policy_record.enabled:
                raise GovernanceError("POLICY_DISABLED", "disabled policies are not evaluated")
            run = session.scalar(
                select(AgentRunRecord).where(
                    AgentRunRecord.case_id == case.id,
                    AgentRunRecord.run_kind == "INVESTIGATION",
                )
            )
            if (run is None and not allow_without_investigation) or (
                run is not None
                and run.status not in {"COMPLETED", "NEEDS_HUMAN_REVIEW"}
            ):
                raise GovernanceError(
                    "CASE_NOT_READY_FOR_EVALUATION",
                    "case has no terminal investigation",
                )
            requirements = list(
                session.scalars(
                    select(RequirementRecord)
                    .where(RequirementRecord.case_id == case.id)
                    .order_by(RequirementRecord.requirement_key)
                    .with_for_update()
                ).all()
            )
            policy = CompiledPolicy.model_validate(policy_record.compiled_policy)
            templates = sorted(policy.requirements, key=lambda item: item.requirement_key)
            if [item.requirement_key for item in requirements] != [
                item.requirement_key for item in templates
            ]:
                raise GovernanceError(
                    "DECISION_INPUT_INVALID", "case requirements do not match compiled policy"
                )
            outcomes = [
                self.aggregator.aggregate(
                    session,
                    requirement,
                    policy.aggregation,
                    origin_result_id=(origin_result_ids or {}).get(
                        requirement.requirement_key
                    ),
                    replay_item_id=replay_item_id,
                )
                for requirement in requirements
            ]
            evaluation = self.evaluator.evaluate(
                policy,
                outcomes,
                investigation_status=run.status if run is not None else "COMPLETED",
                investigation_stop_reason=run.stop_reason if run is not None else None,
            )
            existing = session.scalar(
                select(DecisionRecord).where(
                    DecisionRecord.case_id == case.id,
                    DecisionRecord.source == DecisionSource.MACHINE,
                    DecisionRecord.evaluator_version == evaluation.evaluator_version,
                    DecisionRecord.input_sha256 == evaluation.input_sha256,
                )
            )
            if existing is not None:
                case.current_decision_id = existing.id
                return _decision(session, existing)
            current = (
                session.get(DecisionRecord, case.current_decision_id)
                if case.current_decision_id
                else None
            )
            if current is not None and current.source == DecisionSource.HUMAN:
                raise GovernanceError(
                    "DECISION_INPUT_INVALID",
                    "automatic evaluation cannot supersede a human decision",
                )
            sequence = int(
                session.scalar(
                    select(func.coalesce(func.max(DecisionRecord.sequence), 0)).where(
                        DecisionRecord.case_id == case.id
                    )
                )
                or 0
            ) + 1
            now = utc_now()
            record = DecisionRecord(
                id=f"decision_{uuid4().hex}",
                case_id=case.id,
                policy_id=case.policy_id,
                policy_version=case.policy_version,
                verdict=evaluation.verdict,
                reason_code=evaluation.reason_code,
                source=DecisionSource.MACHINE,
                explanation=evaluation.explanation,
                decision_metadata={
                    "agent_provisional_verdict": (
                        run.provisional_verdict if run is not None else None
                    ),
                    "agent_stop_reason": run.stop_reason if run is not None else None,
                    "requirement_statuses": {
                        item.requirement_id: item.status for item in outcomes
                    },
                },
                sequence=sequence,
                evaluator_version=evaluation.evaluator_version,
                input_sha256=evaluation.input_sha256,
                agent_run_id=run.id if run is not None else None,
                supersedes_decision_id=current.id if current is not None else None,
                replay_item_id=replay_item_id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            session.add_all(
                DecisionRequirementResultRecord(
                    decision_id=record.id,
                    result_id=result_id,
                    case_id=case.id,
                )
                for result_id in evaluation.requirement_result_ids
            )
            session.add_all(
                DecisionEvidenceRecord(
                    decision_id=record.id,
                    evidence_id=evidence_id,
                    case_id=case.id,
                )
                for evidence_id in evaluation.evidence_ids
            )
            case.current_decision_id = record.id
            case.status = (
                CaseStatus.NEEDS_HUMAN_REVIEW
                if evaluation.verdict is Verdict.NEEDS_HUMAN_REVIEW
                else CaseStatus.DECIDED
            )
            case.updated_at = now
            session.flush()
            return _decision(session, record)

    def get_current_decision(self, case_id: str) -> Decision:
        with self.database.session() as session:
            case = session.get(CaseRecord, case_id)
            if case is None:
                raise GovernanceError("CASE_NOT_FOUND", f"case not found: {case_id}")
            if case.current_decision_id is None:
                raise GovernanceError("DECISION_NOT_FOUND", "case has no formal decision")
            record = session.get(DecisionRecord, case.current_decision_id)
            if record is None:
                raise GovernanceError("DECISION_NOT_FOUND", "current decision disappeared")
            return _decision(session, record)


def _decision(session: Session, record: DecisionRecord) -> Decision:
    evidence_ids = list(
        session.scalars(
            select(DecisionEvidenceRecord.evidence_id)
            .where(DecisionEvidenceRecord.decision_id == record.id)
            .order_by(DecisionEvidenceRecord.evidence_id)
        ).all()
    )
    return Decision(
        decision_id=record.id,
        case_id=record.case_id,
        policy_id=record.policy_id,
        policy_version=record.policy_version,
        verdict=Verdict(record.verdict),
        reason_code=record.reason_code,
        source=DecisionSource(record.source),
        explanation=record.explanation,
        evidence_ids=evidence_ids,
        decision_metadata=record.decision_metadata,
        sequence=record.sequence,
        evaluator_version=record.evaluator_version,
        input_sha256=record.input_sha256,
        agent_run_id=record.agent_run_id,
        supersedes_decision_id=record.supersedes_decision_id,
        replay_item_id=record.replay_item_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )

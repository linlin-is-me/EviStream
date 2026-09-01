"""Append-only human review and appeal workflows."""

import json
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evistream.domain import CaseStatus, DecisionSource, Verdict
from evistream.governance.errors import GovernanceError
from evistream.governance.types import Appeal, AppealEvent, AppealStatus, Review
from evistream.storage.database import Database, utc_now
from evistream.storage.models import (
    AppealEventRecord,
    AppealRecord,
    CaseRecord,
    DecisionEvidenceRecord,
    DecisionRecord,
    DecisionRequirementResultRecord,
    EvidenceRecord,
    RequirementRecord,
    ReviewRecord,
)

HUMAN_EVALUATOR_VERSION = "human-review-v1"


class HumanGovernanceService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def submit_review(
        self,
        case_id: str,
        *,
        reviewer: str,
        verdict: Verdict,
        note: str,
        evidence_ids: list[str] | None = None,
        request_key: str | None = None,
    ) -> Review:
        request_key = request_key or _request_key(
            "REVIEW", case_id, reviewer, verdict, note, sorted(evidence_ids or [])
        )
        with self.database.session() as session:
            existing = session.scalar(
                select(ReviewRecord).where(ReviewRecord.request_key == request_key)
            )
            if existing is not None:
                return _review(existing)
            case = session.scalar(
                select(CaseRecord).where(CaseRecord.id == case_id).with_for_update()
            )
            if case is None:
                raise GovernanceError("CASE_NOT_FOUND", f"case not found: {case_id}")
            record = _create_review(
                session,
                case,
                reviewer=reviewer,
                verdict=verdict,
                note=note,
                evidence_ids=evidence_ids,
                request_key=request_key,
            )
            return _review(record)

    def submit_appeal(
        self,
        case_id: str,
        *,
        submitter: str,
        statement: str,
        request_key: str | None = None,
    ) -> Appeal:
        request_key = request_key or _request_key(
            "APPEAL", case_id, submitter, statement
        )
        with self.database.session() as session:
            existing = session.scalar(
                select(AppealRecord).where(AppealRecord.request_key == request_key)
            )
            if existing is not None:
                return _appeal(existing)
            case = session.scalar(
                select(CaseRecord).where(CaseRecord.id == case_id).with_for_update()
            )
            if case is None:
                raise GovernanceError("CASE_NOT_FOUND", f"case not found: {case_id}")
            if case.current_decision_id is None:
                raise GovernanceError(
                    "DECISION_NOT_FOUND", "appeals require a formal current decision"
                )
            now = utc_now()
            record = AppealRecord(
                id=f"appeal_{uuid4().hex}",
                case_id=case.id,
                request_key=request_key,
                submitter=submitter,
                statement=statement,
                challenged_decision_id=case.current_decision_id,
                status=AppealStatus.OPEN,
                resolution_review_id=None,
                resolved_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            session.add(
                AppealEventRecord(
                    id=f"appeal_event_{uuid4().hex}",
                    appeal_id=record.id,
                    sequence=1,
                    event_type="SUBMITTED",
                    actor=submitter,
                    note=statement,
                    decision_id=case.current_decision_id,
                    created_at=now,
                )
            )
            case.status = CaseStatus.NEEDS_HUMAN_REVIEW
            case.updated_at = now
            return _appeal(record)

    def resolve_appeal(
        self,
        appeal_id: str,
        *,
        reviewer: str,
        verdict: Verdict,
        note: str,
        evidence_ids: list[str] | None = None,
        request_key: str | None = None,
    ) -> Review:
        request_key = request_key or _request_key(
            "APPEAL_RESOLUTION",
            appeal_id,
            reviewer,
            verdict,
            note,
            sorted(evidence_ids or []),
        )
        with self.database.session() as session:
            existing_review = session.scalar(
                select(ReviewRecord).where(ReviewRecord.request_key == request_key)
            )
            if existing_review is not None:
                return _review(existing_review)
            appeal = session.scalar(
                select(AppealRecord)
                .where(AppealRecord.id == appeal_id)
                .with_for_update()
            )
            if appeal is None:
                raise GovernanceError("APPEAL_NOT_FOUND", f"appeal not found: {appeal_id}")
            if appeal.status == AppealStatus.RESOLVED:
                raise GovernanceError("APPEAL_ALREADY_RESOLVED", "appeal is already resolved")
            case = session.scalar(
                select(CaseRecord)
                .where(CaseRecord.id == appeal.case_id)
                .with_for_update()
            )
            if case is None:
                raise GovernanceError("CASE_NOT_FOUND", "appeal case disappeared")
            review = _create_review(
                session,
                case,
                reviewer=reviewer,
                verdict=verdict,
                note=note,
                evidence_ids=evidence_ids,
                request_key=request_key,
            )
            now = utc_now()
            appeal.status = AppealStatus.RESOLVED
            appeal.resolution_review_id = review.id
            appeal.resolved_at = now
            appeal.updated_at = now
            session.add(
                AppealEventRecord(
                    id=f"appeal_event_{uuid4().hex}",
                    appeal_id=appeal.id,
                    sequence=2,
                    event_type="RESOLVED",
                    actor=reviewer,
                    note=note,
                    decision_id=review.decision_id,
                    created_at=now,
                )
            )
            return _review(review)


def _create_review(
    session: Session,
    case: CaseRecord,
    *,
    reviewer: str,
    verdict: Verdict,
    note: str,
    evidence_ids: list[str] | None,
    request_key: str,
) -> ReviewRecord:
    if not reviewer.strip() or not note.strip():
        raise GovernanceError("REVIEW_INPUT_INVALID", "reviewer and note are required")
    current = (
        session.get(DecisionRecord, case.current_decision_id)
        if case.current_decision_id
        else None
    )
    if current is None:
        raise GovernanceError("DECISION_NOT_FOUND", "review requires a formal decision")
    selected = sorted(set(evidence_ids or _decision_evidence(session, current.id)))
    if verdict is not Verdict.NEEDS_HUMAN_REVIEW and not selected:
        raise GovernanceError(
            "REVIEW_INPUT_INVALID", "conclusive human decisions require Evidence"
        )
    if selected:
        valid_count = int(
            session.scalar(
                select(func.count())
                .select_from(EvidenceRecord)
                .where(EvidenceRecord.case_id == case.id, EvidenceRecord.id.in_(selected))
            )
            or 0
        )
        if valid_count != len(selected):
            raise GovernanceError(
                "REVIEW_INPUT_INVALID", "review Evidence must belong to the Case"
            )
    sequence = int(
        session.scalar(
            select(func.coalesce(func.max(DecisionRecord.sequence), 0)).where(
                DecisionRecord.case_id == case.id
            )
        )
        or 0
    ) + 1
    input_sha256 = _request_key(
        "HUMAN_DECISION", case.id, reviewer, verdict, note, selected, request_key
    )
    now = utc_now()
    decision = DecisionRecord(
        id=f"decision_{uuid4().hex}",
        case_id=case.id,
        policy_id=case.policy_id,
        policy_version=case.policy_version,
        verdict=verdict,
        reason_code="HUMAN_REVIEW",
        source=DecisionSource.HUMAN,
        explanation=note,
        decision_metadata={"reviewer": reviewer},
        sequence=sequence,
        evaluator_version=HUMAN_EVALUATOR_VERSION,
        input_sha256=input_sha256,
        agent_run_id=None,
        supersedes_decision_id=current.id,
        replay_item_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(decision)
    session.flush()
    session.add_all(
        DecisionEvidenceRecord(
            decision_id=decision.id, evidence_id=evidence_id, case_id=case.id
        )
        for evidence_id in selected
    )
    result_ids = list(
        session.scalars(
            select(RequirementRecord.current_result_id).where(
                RequirementRecord.case_id == case.id,
                RequirementRecord.current_result_id.is_not(None),
            )
        ).all()
    )
    session.add_all(
        DecisionRequirementResultRecord(
            decision_id=decision.id, result_id=result_id, case_id=case.id
        )
        for result_id in result_ids
    )
    review = ReviewRecord(
        id=f"review_{uuid4().hex}",
        case_id=case.id,
        request_key=request_key,
        reviewer=reviewer,
        reviewed_decision_id=current.id,
        decision_id=decision.id,
        note=note,
        created_at=now,
    )
    session.add(review)
    case.current_decision_id = decision.id
    case.status = (
        CaseStatus.NEEDS_HUMAN_REVIEW
        if verdict is Verdict.NEEDS_HUMAN_REVIEW
        else CaseStatus.DECIDED
    )
    case.updated_at = now
    session.flush()
    return review


def _decision_evidence(session: Session, decision_id: str) -> list[str]:
    return list(
        session.scalars(
            select(DecisionEvidenceRecord.evidence_id).where(
                DecisionEvidenceRecord.decision_id == decision_id
            )
        ).all()
    )


def _request_key(*values: object) -> str:
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(encoded.encode()).hexdigest()


def _review(record: ReviewRecord) -> Review:
    return Review(
        review_id=record.id,
        case_id=record.case_id,
        reviewer=record.reviewer,
        reviewed_decision_id=record.reviewed_decision_id,
        decision_id=record.decision_id,
        note=record.note,
        request_key=record.request_key,
        created_at=record.created_at,
    )


def _appeal(record: AppealRecord) -> Appeal:
    return Appeal(
        appeal_id=record.id,
        case_id=record.case_id,
        submitter=record.submitter,
        statement=record.statement,
        challenged_decision_id=record.challenged_decision_id,
        status=AppealStatus(record.status),
        resolution_review_id=record.resolution_review_id,
        created_at=record.created_at,
        resolved_at=record.resolved_at,
    )


def appeal_event(record: AppealEventRecord) -> AppealEvent:
    return AppealEvent(
        event_id=record.id,
        appeal_id=record.appeal_id,
        sequence=record.sequence,
        event_type=record.event_type,
        actor=record.actor,
        note=record.note,
        decision_id=record.decision_id,
        created_at=record.created_at,
    )

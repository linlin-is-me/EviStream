"""Deterministic Evidence to RequirementResult aggregation."""

import json
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evistream.domain import EvidenceStance, RequirementStatus
from evistream.governance.evidence import EvidenceStore, EvidenceValidity
from evistream.governance.types import AggregationConfig, AggregationOutcome
from evistream.storage.database import utc_now
from evistream.storage.models import (
    RequirementRecord,
    RequirementResultEvidenceRecord,
    RequirementResultRecord,
)

AGGREGATOR_VERSION = "1"


class RequirementAggregator:
    def __init__(self, evidence_store: EvidenceStore | None = None) -> None:
        self.evidence_store = evidence_store or EvidenceStore()

    def aggregate(
        self,
        session: Session,
        requirement: RequirementRecord,
        config: AggregationConfig,
        *,
        origin_result_id: str | None = None,
        replay_item_id: str | None = None,
    ) -> AggregationOutcome:
        evidence = self.evidence_store.list_for_requirement(session, requirement.id)
        classified = [
            self.evidence_store.classify(session, requirement, item) for item in evidence
        ]
        input_sha256 = _input_hash(requirement, config, classified)
        existing = session.scalar(
            select(RequirementResultRecord).where(
                RequirementResultRecord.requirement_id == requirement.id,
                RequirementResultRecord.aggregator_version == AGGREGATOR_VERSION,
                RequirementResultRecord.input_sha256 == input_sha256,
            )
        )
        if existing is not None:
            requirement.status = existing.status
            requirement.current_result_id = existing.id
            return _outcome(session, existing, classified)

        valid = [item for item in classified if item.valid]
        quality = [
            item
            for item in valid
            if item.evidence.confidence is not None
            and item.evidence.confidence >= config.minimum_confidence
        ]
        support = [
            item for item in quality if item.evidence.stance == EvidenceStance.SUPPORT
        ]
        contradict = [
            item for item in quality if item.evidence.stance == EvidenceStance.CONTRADICT
        ]
        support_met = len(support) >= config.minimum_supporting_evidence
        contradict_met = len(contradict) >= config.minimum_contradicting_evidence
        if support_met and contradict_met:
            status = RequirementStatus.CONFLICTED
            reason = "EVIDENCE_CONFLICT"
        elif support_met:
            status = RequirementStatus.SATISFIED
            reason = "EVIDENCE_SUPPORT_THRESHOLD_MET"
        elif contradict_met:
            status = RequirementStatus.NOT_SATISFIED
            reason = "EVIDENCE_CONTRADICT_THRESHOLD_MET"
        elif not evidence:
            status = RequirementStatus.UNKNOWN
            reason = "EVIDENCE_MISSING"
        elif not valid:
            status = RequirementStatus.UNKNOWN
            reason = "EVIDENCE_SOURCE_INVALID"
        elif not quality:
            status = RequirementStatus.UNKNOWN
            reason = "EVIDENCE_LOW_QUALITY"
        else:
            status = RequirementStatus.UNKNOWN
            reason = "EVIDENCE_UNCERTAIN"

        next_sequence = int(
            session.scalar(
                select(func.coalesce(func.max(RequirementResultRecord.sequence), 0)).where(
                    RequirementResultRecord.requirement_id == requirement.id
                )
            )
            or 0
        ) + 1
        result_id = "rr_" + sha256(
            f"{requirement.id}:{AGGREGATOR_VERSION}:{input_sha256}".encode()
        ).hexdigest()[:32]
        now = utc_now()
        record = RequirementResultRecord(
            id=result_id,
            requirement_id=requirement.id,
            case_id=requirement.case_id,
            sequence=next_sequence,
            status=status,
            reason_code=reason,
            aggregator_version=AGGREGATOR_VERSION,
            input_sha256=input_sha256,
            aggregation_config=config.model_dump(mode="json"),
            origin_result_id=origin_result_id,
            replay_item_id=replay_item_id,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        session.flush()
        used = [item.evidence.id for item in valid]
        session.add_all(
            RequirementResultEvidenceRecord(
                result_id=record.id,
                evidence_id=evidence_id,
                requirement_id=requirement.id,
            )
            for evidence_id in used
        )
        requirement.status = status
        requirement.current_result_id = record.id
        requirement.updated_at = now
        session.flush()
        return _outcome(session, record, classified)


def _input_hash(
    requirement: RequirementRecord,
    config: AggregationConfig,
    classified: list[EvidenceValidity],
) -> str:
    payload = {
        "requirement_semantic_sha256": requirement.semantic_sha256,
        "aggregator_version": AGGREGATOR_VERSION,
        "config": config.model_dump(mode="json"),
        "evidence": [
            {
                "id": item.evidence.id,
                "stance": item.evidence.stance,
                "modality": item.evidence.modality,
                "start_ms": item.evidence.start_ms,
                "end_ms": item.evidence.end_ms,
                "confidence": item.evidence.confidence,
                "source_ref": item.evidence.source_ref,
                "artifact_id": item.evidence.artifact_id,
                "tool_run_id": item.evidence.tool_run_id,
                "model_call_id": item.evidence.model_call_id,
                "origin_evidence_id": item.evidence.origin_evidence_id,
                "valid": item.valid,
                "reason_code": item.reason_code,
                "tool_status": item.tool_status,
                "model_status": item.model_status,
                "artifact_available": item.artifact_available,
            }
            for item in sorted(classified, key=lambda value: value.evidence.id)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def _outcome(
    session: Session,
    record: RequirementResultRecord,
    classified: list[EvidenceValidity],
) -> AggregationOutcome:
    requirement = session.get(RequirementRecord, record.requirement_id)
    if requirement is None:
        raise RuntimeError("requirement result references a missing requirement")
    evidence_ids = list(
        session.scalars(
            select(RequirementResultEvidenceRecord.evidence_id)
            .where(RequirementResultEvidenceRecord.result_id == record.id)
            .order_by(RequirementResultEvidenceRecord.evidence_id)
        ).all()
    )
    all_ids = [item.evidence.id for item in classified]
    return AggregationOutcome(
        result_id=record.id,
        requirement_id=record.requirement_id,
        requirement_key=requirement.requirement_key,
        status=RequirementStatus(record.status),
        reason_code=record.reason_code,
        evidence_ids=evidence_ids,
        valid_evidence_ids=evidence_ids,
        ignored_evidence_ids=sorted(set(all_ids) - set(evidence_ids)),
        aggregator_version=record.aggregator_version,
        input_sha256=record.input_sha256,
    )

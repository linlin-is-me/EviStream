"""Case-scoped Evidence persistence and validity checks."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evistream.governance.errors import GovernanceError
from evistream.storage.models import (
    ArtifactRecord,
    CaseRecord,
    EvidenceRecord,
    ModelCallRecord,
    RequirementRecord,
    ToolRunRecord,
)

VISION_MODALITIES = {"vision", "visual_caption", "clip", "keyframe"}


@dataclass(frozen=True, slots=True)
class EvidenceValidity:
    evidence: EvidenceRecord
    valid: bool
    reason_code: str | None
    tool_status: str | None
    model_status: str | None
    artifact_available: bool | None


class EvidenceStore:
    def append_pending(
        self,
        session: Session,
        *,
        case_id: str,
        item: Any,
        now: datetime,
    ) -> EvidenceRecord:
        existing = session.get(EvidenceRecord, item.evidence_id)
        if existing is not None:
            if existing.case_id != case_id or existing.requirement_id != item.requirement_id:
                raise GovernanceError(
                    "EVIDENCE_SOURCE_INVALID", "stable Evidence ID resolved to another source"
                )
            return existing
        record = EvidenceRecord(
            id=item.evidence_id,
            case_id=case_id,
            requirement_id=item.requirement_id,
            stance=item.stance,
            modality=item.modality,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            artifact_id=item.artifact_id,
            tool_run_id=item.tool_run_id,
            model_call_id=item.model_call_id,
            model_name=item.model_name,
            source_ref=item.source_ref,
            summary=item.summary,
            confidence=item.confidence,
            origin_evidence_id=None,
            replay_item_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        session.flush()
        return record

    def list_for_requirement(
        self, session: Session, requirement_id: str
    ) -> list[EvidenceRecord]:
        return list(
            session.scalars(
                select(EvidenceRecord)
                .where(EvidenceRecord.requirement_id == requirement_id)
                .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
            ).all()
        )

    def classify(
        self,
        session: Session,
        requirement: RequirementRecord,
        evidence: EvidenceRecord,
    ) -> EvidenceValidity:
        if evidence.case_id != requirement.case_id or evidence.requirement_id != requirement.id:
            return self._invalid(evidence, "EVIDENCE_SOURCE_INVALID")
        if evidence.start_ms < 0 or evidence.end_ms <= evidence.start_ms:
            return self._invalid(evidence, "EVIDENCE_SOURCE_INVALID")
        if not _modality_allowed(evidence.modality, requirement.modalities):
            return self._invalid(evidence, "EVIDENCE_SOURCE_INVALID")

        case = session.get(CaseRecord, requirement.case_id)
        if case is None:
            return self._invalid(evidence, "EVIDENCE_SOURCE_INVALID")
        artifact_available: bool | None = None
        if evidence.artifact_id is not None:
            artifact = session.get(ArtifactRecord, evidence.artifact_id)
            artifact_available = artifact is not None and artifact.video_id == case.video_id
            if not artifact_available:
                return self._invalid(
                    evidence,
                    "EVIDENCE_SOURCE_INVALID",
                    artifact_available=artifact_available,
                )

        tool_status: str | None = None
        if evidence.tool_run_id is not None:
            tool = session.get(ToolRunRecord, evidence.tool_run_id)
            tool_status = tool.status if tool is not None else None
            if (
                tool is None
                or tool.case_id != evidence.case_id
                or tool.requirement_id != evidence.requirement_id
                or tool.status not in {"success", "partial"}
            ):
                return self._invalid(
                    evidence,
                    "EVIDENCE_SOURCE_INVALID",
                    tool_status=tool_status,
                    artifact_available=artifact_available,
                )

        model_status: str | None = None
        if evidence.model_call_id is not None:
            model = session.get(ModelCallRecord, evidence.model_call_id)
            model_status = model.status if model is not None else None
            if model is None or model.case_id != evidence.case_id or model.status != "success":
                return self._invalid(
                    evidence,
                    "EVIDENCE_SOURCE_INVALID",
                    tool_status=tool_status,
                    model_status=model_status,
                    artifact_available=artifact_available,
                )

        if evidence.origin_evidence_id is not None:
            origin = session.get(EvidenceRecord, evidence.origin_evidence_id)
            if origin is None:
                return self._invalid(evidence, "EVIDENCE_SOURCE_INVALID")
        elif (
            evidence.artifact_id is None
            and evidence.tool_run_id is None
            and evidence.model_call_id is None
        ):
            return self._invalid(evidence, "EVIDENCE_SOURCE_INVALID")

        return EvidenceValidity(
            evidence=evidence,
            valid=True,
            reason_code=None,
            tool_status=tool_status,
            model_status=model_status,
            artifact_available=artifact_available,
        )

    @staticmethod
    def _invalid(
        evidence: EvidenceRecord,
        reason_code: str,
        *,
        tool_status: str | None = None,
        model_status: str | None = None,
        artifact_available: bool | None = None,
    ) -> EvidenceValidity:
        return EvidenceValidity(
            evidence=evidence,
            valid=False,
            reason_code=reason_code,
            tool_status=tool_status,
            model_status=model_status,
            artifact_available=artifact_available,
        )


def _modality_allowed(modality: str, allowed: list[str]) -> bool:
    normalized = "vision" if modality in VISION_MODALITIES else modality
    return normalized in allowed

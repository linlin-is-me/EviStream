from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evistream.domain import Evidence, RequirementResult


def test_evidence_requires_ordered_time_range() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="end_ms"):
        Evidence(
            evidence_id="ev_1",
            case_id="case_1",
            requirement_id="req_1",
            stance="support",
            modality="vision",
            start_ms=100,
            end_ms=100,
            source_ref="segment_1",
            summary="summary",
            created_at=now,
            updated_at=now,
        )


def test_requirement_result_cannot_be_pending() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="cannot be PENDING"):
        RequirementResult(
            result_id="result_1",
            requirement_id="req_1",
            status="PENDING",
            evidence_ids=[],
            reason_code="NOT_EVALUATED",
            aggregator_version="1",
            input_sha256="0" * 64,
            created_at=now,
            updated_at=now,
        )

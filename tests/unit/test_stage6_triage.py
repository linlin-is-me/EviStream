from pathlib import Path

import pytest

from evistream.policies.compiler import PolicyCompiler
from evistream.policies.schema import load_policy
from evistream.triage.service import ModelErrorOutput, _mock_output, _validate_output
from evistream.triage.types import TriageAction, TriageOutput


def _policy():
    return PolicyCompiler().compile(
        load_policy(Path("configs/policies/violence-weapon-v1.yaml"))
    )


def test_mock_triage_matches_normalized_trigger_terms() -> None:
    policy = _policy()

    matched = _mock_output(policy, "A WEAPON is visible in the scene.")
    skipped = _mock_output(policy, "A calm landscape with no relevant objects.")

    assert matched.action is TriageAction.CREATE_CASE
    assert "weapon" in matched.matched_terms
    assert matched.matched_requirement_keys
    assert skipped.action is TriageAction.SKIP


def test_triage_rejects_references_outside_current_policy() -> None:
    output = TriageOutput(
        action=TriageAction.CREATE_CASE,
        confidence=0.9,
        reason_code="MATCH",
        matched_terms=["fabricated-term"],
        matched_requirement_keys=["fabricated.requirement"],
        summary="invalid references",
    )

    with pytest.raises(ModelErrorOutput) as error:
        _validate_output(_policy(), output)

    assert error.value.code == "MODEL_OUTPUT_INVALID"
    assert error.value.retryable is False

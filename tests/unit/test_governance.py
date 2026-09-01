from pathlib import Path

from evistream.domain import RequirementStatus, Verdict
from evistream.governance.rule_evaluator import RuleEvaluator, evaluate_expression
from evistream.governance.types import (
    AggregationConfig,
    AggregationOutcome,
    RuleTruthValue,
)
from evistream.policies.compiler import PolicyCompiler
from evistream.policies.schema import BooleanExpression, load_policy
from evistream.replay.planner import _diff
from evistream.storage.models import PolicyRecord


def test_aggregation_config_defaults_and_limits() -> None:
    config = AggregationConfig()
    assert config.minimum_confidence == 0.6
    assert config.minimum_supporting_evidence == 1
    assert config.minimum_contradicting_evidence == 1


def test_compiler_v2_hashes_aggregation_but_not_requirement_semantics(tmp_path: Path) -> None:
    original = Path("configs/policies/violence-weapon-v1.yaml").read_text(
        encoding="utf-8"
    )
    changed = original + "\naggregation:\n  minimum_confidence: 0.75\n"
    first_path = tmp_path / "first.yaml"
    second_path = tmp_path / "second.yaml"
    first_path.write_text(original, encoding="utf-8")
    second_path.write_text(changed, encoding="utf-8")
    compiler = PolicyCompiler()
    first = compiler.compile(load_policy(first_path))
    second = compiler.compile(load_policy(second_path))
    assert first.compiler_version == "2"
    assert first.semantic_sha256 != second.semantic_sha256
    assert [item.semantic_sha256 for item in first.requirements] == [
        item.semantic_sha256 for item in second.requirements
    ]


def test_three_value_nested_all_any() -> None:
    expression = BooleanExpression(
        all=["a", BooleanExpression(any=["b", "c"])]
    )
    assert (
        evaluate_expression(
            expression,
            {
                "a": RequirementStatus.SATISFIED,
                "b": RequirementStatus.UNKNOWN,
                "c": RequirementStatus.NOT_SATISFIED,
            },
        )
        is RuleTruthValue.UNKNOWN
    )
    assert (
        evaluate_expression(
            expression,
            {
                "a": RequirementStatus.NOT_SATISFIED,
                "b": RequirementStatus.SATISFIED,
                "c": RequirementStatus.UNKNOWN,
            },
        )
        is RuleTruthValue.FALSE
    )


def test_rule_evaluator_ignores_agent_provisional_verdict() -> None:
    policy = PolicyCompiler().compile(
        load_policy(Path("configs/policies/violence-weapon-v1.yaml"))
    )
    outcomes = []
    for template in policy.requirements:
        status = (
            RequirementStatus.SATISFIED
            if template.source_kind == "requirement"
            else RequirementStatus.NOT_SATISFIED
        )
        outcomes.append(
            AggregationOutcome(
                result_id=f"result_{template.requirement_key}",
                requirement_id=f"id_{template.requirement_key}",
                requirement_key=template.requirement_key,
                status=status,
                reason_code="test",
                evidence_ids=[f"evidence_{template.requirement_key}"],
                valid_evidence_ids=[f"evidence_{template.requirement_key}"],
                ignored_evidence_ids=[],
                aggregator_version="1",
                input_sha256="0" * 64,
            )
        )
    result = RuleEvaluator().evaluate(
        policy,
        outcomes,
        investigation_status="COMPLETED",
        investigation_stop_reason=None,
    )
    assert result.verdict is Verdict.REJECT
    assert result.reason_code == "RULE_REJECT_MATCHED"


def test_rule_evaluator_routes_conflicted_required_result_to_review() -> None:
    policy = PolicyCompiler().compile(
        load_policy(Path("configs/policies/violence-weapon-v1.yaml"))
    )
    outcomes = [
        AggregationOutcome(
            result_id=f"result_{index}",
            requirement_id=f"requirement_{index}",
            requirement_key=template.requirement_key,
            status=(
                RequirementStatus.CONFLICTED
                if index == 0
                else RequirementStatus.NOT_SATISFIED
            ),
            reason_code="test",
            evidence_ids=[f"evidence_{index}"],
            valid_evidence_ids=[f"evidence_{index}"],
            ignored_evidence_ids=[],
            aggregator_version="1",
            input_sha256=f"{index:064x}",
        )
        for index, template in enumerate(policy.requirements)
    ]
    result = RuleEvaluator().evaluate(
        policy,
        outcomes,
        investigation_status="COMPLETED",
        investigation_stop_reason=None,
    )
    assert result.verdict is Verdict.NEEDS_HUMAN_REVIEW
    assert result.reason_code == "RULE_EVIDENCE_CONFLICT"


def test_policy_diff_selects_reevaluate_for_threshold_change(tmp_path: Path) -> None:
    source_text = Path("configs/policies/violence-weapon-v1.yaml").read_text(
        encoding="utf-8"
    )
    target_text = source_text.replace("version: 1", "version: 2") + (
        "\naggregation:\n  minimum_confidence: 0.75\n"
    )
    source_path = tmp_path / "v1.yaml"
    target_path = tmp_path / "v2.yaml"
    source_path.write_text(source_text, encoding="utf-8")
    target_path.write_text(target_text, encoding="utf-8")
    compiler = PolicyCompiler()
    source = compiler.compile(load_policy(source_path))
    target = compiler.compile(load_policy(target_path))
    before = PolicyRecord(
        policy_id=source.policy_id,
        version=1,
        name=source.name,
        severity=source.severity,
        enabled=True,
        lifecycle="PUBLISHED",
        source_yaml=source_text,
        compiled_policy=source.model_dump(mode="json"),
        source_sha256="1" * 64,
        semantic_sha256=source.semantic_sha256,
        compiler_version="2",
    )
    after = PolicyRecord(
        policy_id=target.policy_id,
        version=2,
        name=target.name,
        severity=target.severity,
        enabled=True,
        lifecycle="PUBLISHED",
        source_yaml=target_text,
        compiled_policy=target.model_dump(mode="json"),
        source_sha256="2" * 64,
        semantic_sha256=target.semantic_sha256,
        compiler_version="2",
    )
    diff = _diff(before, after)
    assert diff.mode == "REEVALUATE"
    assert diff.aggregation_changed
    assert not diff.modified_requirement_keys


def test_policy_diff_selects_reinvestigate_for_requirement_change(tmp_path: Path) -> None:
    source_text = Path("configs/policies/violence-weapon-v1.yaml").read_text(
        encoding="utf-8"
    )
    target_text = source_text.replace("version: 1", "version: 2").replace(
        "画面中是否出现暴力场景或受限武器", "画面中是否清晰出现暴力场景或受限武器"
    )
    source_path = tmp_path / "source.yaml"
    target_path = tmp_path / "target.yaml"
    source_path.write_text(source_text, encoding="utf-8")
    target_path.write_text(target_text, encoding="utf-8")
    compiler = PolicyCompiler()
    source = compiler.compile(load_policy(source_path))
    target = compiler.compile(load_policy(target_path))
    before = PolicyRecord(
        policy_id=source.policy_id,
        version=1,
        name=source.name,
        severity=source.severity,
        enabled=True,
        lifecycle="PUBLISHED",
        source_yaml=source_text,
        compiled_policy=source.model_dump(mode="json"),
        source_sha256="1" * 64,
        semantic_sha256=source.semantic_sha256,
        compiler_version="2",
    )
    after = PolicyRecord(
        policy_id=target.policy_id,
        version=2,
        name=target.name,
        severity=target.severity,
        enabled=True,
        lifecycle="PUBLISHED",
        source_yaml=target_text,
        compiled_policy=target.model_dump(mode="json"),
        source_sha256="2" * 64,
        semantic_sha256=target.semantic_sha256,
        compiler_version="2",
    )
    diff = _diff(before, after)
    assert diff.mode == "REINVESTIGATE"
    assert diff.modified_requirement_keys

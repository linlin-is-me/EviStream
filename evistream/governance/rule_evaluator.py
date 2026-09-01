"""Three-valued deterministic policy evaluation."""

import json
from hashlib import sha256

from evistream.domain import RequirementStatus, Verdict
from evistream.governance.types import (
    AggregationOutcome,
    RuleEvaluation,
    RuleTruthValue,
)
from evistream.policies.compiler import CompiledPolicy
from evistream.policies.schema import BooleanExpression

EVALUATOR_VERSION = "1"


class RuleEvaluator:
    def evaluate(
        self,
        policy: CompiledPolicy,
        outcomes: list[AggregationOutcome],
        *,
        investigation_status: str,
        investigation_stop_reason: str | None,
    ) -> RuleEvaluation:
        values = {outcome.requirement_key: outcome.status for outcome in outcomes}
        expected = {template.requirement_key for template in policy.requirements}
        if set(values) != expected:
            raise ValueError("every compiled requirement requires one aggregation outcome")
        result_ids = sorted(outcome.result_id for outcome in outcomes)
        evidence_ids = sorted({item for outcome in outcomes for item in outcome.evidence_ids})

        forced_reason = _investigation_reason(
            investigation_status, investigation_stop_reason
        )
        if forced_reason is not None:
            return self._result(
                Verdict.NEEDS_HUMAN_REVIEW,
                forced_reason,
                "The investigation did not establish a complete automatic basis.",
                result_ids,
                evidence_ids,
                policy,
                outcomes,
            )

        required_unresolved = [
            template.requirement_key
            for template in policy.requirements
            if template.required
            and values[template.requirement_key]
            in {RequirementStatus.UNKNOWN, RequirementStatus.CONFLICTED}
        ]
        if any(values[item] is RequirementStatus.CONFLICTED for item in required_unresolved):
            reason = "RULE_EVIDENCE_CONFLICT"
        elif required_unresolved:
            reason = "RULE_REQUIRED_UNKNOWN"
        else:
            reason = None
        if reason is not None:
            return self._result(
                Verdict.NEEDS_HUMAN_REVIEW,
                reason,
                "At least one mandatory requirement is unresolved.",
                result_ids,
                evidence_ids,
                policy,
                outcomes,
            )

        sentinels = {
            "unresolved_exception": RuleTruthValue.TRUE
            if any(
                template.source_kind == "exception"
                and values[template.requirement_key]
                in {RequirementStatus.UNKNOWN, RequirementStatus.CONFLICTED}
                for template in policy.requirements
            )
            else RuleTruthValue.FALSE,
            "contradictory_evidence": RuleTruthValue.TRUE
            if any(value is RequirementStatus.CONFLICTED for value in values.values())
            else RuleTruthValue.FALSE,
        }
        escalation = evaluate_expression(policy.escalate_when, values, sentinels)
        if escalation is not RuleTruthValue.FALSE:
            return self._result(
                Verdict.NEEDS_HUMAN_REVIEW,
                "RULE_ESCALATION_MATCHED"
                if escalation is RuleTruthValue.TRUE
                else "RULE_EXPRESSION_UNKNOWN",
                "The policy escalation expression requires human review.",
                result_ids,
                evidence_ids,
                policy,
                outcomes,
            )

        rejection = evaluate_expression(policy.reject_when, values, sentinels)
        if rejection is RuleTruthValue.UNKNOWN:
            return self._result(
                Verdict.NEEDS_HUMAN_REVIEW,
                "RULE_EXPRESSION_UNKNOWN",
                "The rejection expression could not be resolved.",
                result_ids,
                evidence_ids,
                policy,
                outcomes,
            )
        exception_satisfied = any(
            template.source_kind == "exception"
            and values[template.requirement_key] is RequirementStatus.SATISFIED
            for template in policy.requirements
        )
        if rejection is RuleTruthValue.TRUE and not exception_satisfied:
            verdict = Verdict.REJECT
            reason = "RULE_REJECT_MATCHED"
            explanation = "The rejection expression is satisfied and no exception applies."
        elif exception_satisfied:
            verdict = Verdict.APPROVE
            reason = "RULE_EXCEPTION_APPLIED"
            explanation = "A compiled policy exception is satisfied."
        else:
            verdict = Verdict.APPROVE
            reason = "RULE_APPROVE_NO_REJECT_MATCH"
            explanation = "The rejection expression is not satisfied."
        if not evidence_ids:
            verdict = Verdict.NEEDS_HUMAN_REVIEW
            reason = "RULE_REQUIRED_UNKNOWN"
            explanation = "A conclusive machine verdict requires at least one valid Evidence."
        return self._result(
            verdict,
            reason,
            explanation,
            result_ids,
            evidence_ids,
            policy,
            outcomes,
        )

    @staticmethod
    def _result(
        verdict: Verdict,
        reason_code: str,
        explanation: str,
        result_ids: list[str],
        evidence_ids: list[str],
        policy: CompiledPolicy,
        outcomes: list[AggregationOutcome],
    ) -> RuleEvaluation:
        payload = {
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "policy_semantic_sha256": policy.semantic_sha256,
            "evaluator_version": EVALUATOR_VERSION,
            "verdict": verdict,
            "reason_code": reason_code,
            "results": [
                {
                    "result_id": item.result_id,
                    "status": item.status,
                    "input_sha256": item.input_sha256,
                }
                for item in sorted(outcomes, key=lambda value: value.requirement_id)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return RuleEvaluation(
            verdict=verdict,
            reason_code=reason_code,
            explanation=explanation,
            requirement_result_ids=result_ids,
            evidence_ids=evidence_ids,
            evaluator_version=EVALUATOR_VERSION,
            input_sha256=sha256(encoded.encode()).hexdigest(),
        )


def evaluate_expression(
    expression: BooleanExpression,
    values: dict[str, RequirementStatus],
    sentinels: dict[str, RuleTruthValue] | None = None,
) -> RuleTruthValue:
    sentinels = sentinels or {}
    children = expression.all or expression.any or []
    evaluated = [
        _leaf(child, values, sentinels)
        if isinstance(child, str)
        else evaluate_expression(child, values, sentinels)
        for child in children
    ]
    if expression.all is not None:
        if RuleTruthValue.FALSE in evaluated:
            return RuleTruthValue.FALSE
        if all(value is RuleTruthValue.TRUE for value in evaluated):
            return RuleTruthValue.TRUE
        return RuleTruthValue.UNKNOWN
    if RuleTruthValue.TRUE in evaluated:
        return RuleTruthValue.TRUE
    if all(value is RuleTruthValue.FALSE for value in evaluated):
        return RuleTruthValue.FALSE
    return RuleTruthValue.UNKNOWN


def _leaf(
    key: str,
    values: dict[str, RequirementStatus],
    sentinels: dict[str, RuleTruthValue],
) -> RuleTruthValue:
    if key in sentinels:
        return sentinels[key]
    value = values.get(key, RequirementStatus.UNKNOWN)
    if value is RequirementStatus.SATISFIED:
        return RuleTruthValue.TRUE
    if value is RequirementStatus.NOT_SATISFIED:
        return RuleTruthValue.FALSE
    return RuleTruthValue.UNKNOWN


def _investigation_reason(status: str, stop_reason: str | None) -> str | None:
    if status == "COMPLETED":
        return None
    if status == "NEEDS_HUMAN_REVIEW":
        if stop_reason and (
            stop_reason.startswith("BUDGET_")
            or stop_reason in {"TOOL_FAILURE_LIMIT", "STAGNATION_LIMIT"}
        ):
            return "INVESTIGATION_INCOMPLETE"
        return "INVESTIGATION_INCOMPLETE"
    return "INVESTIGATION_INCOMPLETE"

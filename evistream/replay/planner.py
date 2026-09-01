"""Read-only deterministic policy diff and replay preview."""

import json
from hashlib import sha256

from sqlalchemy import select

from evistream.domain import PolicyLifecycle
from evistream.governance.errors import GovernanceError
from evistream.governance.types import (
    PolicyDiff,
    ReplayCasePlan,
    ReplayMode,
    ReplayPreview,
)
from evistream.policies.compiler import CompiledPolicy
from evistream.storage.database import Database
from evistream.storage.models import (
    AppealRecord,
    CaseRecord,
    DecisionRecord,
    EvidenceRecord,
    PolicyRecord,
    RequirementRecord,
)


class ReplayPlanner:
    def __init__(self, database: Database) -> None:
        self.database = database

    def preview(
        self,
        policy_id: str,
        source_version: int,
        target_version: int,
        *,
        model_change_policy: str = "keep",
    ) -> ReplayPreview:
        if source_version >= target_version:
            raise GovernanceError(
                "REPLAY_VERSION_ORDER_INVALID", "target version must be newer"
            )
        if model_change_policy not in {"keep", "invalidate-visual"}:
            raise GovernanceError("DECISION_INPUT_INVALID", "invalid model change policy")
        with self.database.session() as session:
            source = session.get(PolicyRecord, (policy_id, source_version))
            target = session.get(PolicyRecord, (policy_id, target_version))
            if source is None or target is None:
                raise GovernanceError("REPLAY_POLICY_NOT_FOUND", "policy version not found")
            if (
                source.lifecycle != PolicyLifecycle.PUBLISHED
                or target.lifecycle != PolicyLifecycle.PUBLISHED
            ):
                raise GovernanceError(
                    "REPLAY_POLICY_NOT_PUBLISHED", "replay requires published versions"
                )
            diff = _diff(source, target)
            cases: list[ReplayCasePlan] = []
            evidence_count = 0
            result_count = 0
            investigation_count = 0
            for case in session.scalars(
                select(CaseRecord)
                .where(
                    CaseRecord.policy_id == policy_id,
                    CaseRecord.policy_version == source_version,
                )
                .order_by(CaseRecord.id)
            ):
                decision = (
                    session.get(DecisionRecord, case.current_decision_id)
                    if case.current_decision_id
                    else None
                )
                open_appeal = session.scalar(
                    select(AppealRecord.id).where(
                        AppealRecord.case_id == case.id,
                        AppealRecord.status == "OPEN",
                    )
                )
                blocked = None
                if decision is None:
                    blocked = "REPLAY_SOURCE_DECISION_MISSING"
                elif open_appeal is not None:
                    blocked = "OPEN_APPEAL"
                requirements = list(
                    session.scalars(
                        select(RequirementRecord).where(
                            RequirementRecord.case_id == case.id
                        )
                    ).all()
                )
                by_key = {item.requirement_key: item for item in requirements}
                reusable = list(diff.unchanged_requirement_keys)
                investigate = sorted(
                    set(diff.added_requirement_keys + diff.modified_requirement_keys)
                )
                invalidations: list[dict[str, str]] = [
                    {"requirement_key": key, "reason": "REQUIREMENT_REMOVED"}
                    for key in diff.removed_requirement_keys
                ]
                invalidations.extend(
                    {
                        "requirement_key": key,
                        "reason": "REQUIREMENT_SEMANTICS_CHANGED",
                    }
                    for key in diff.modified_requirement_keys
                )
                if model_change_policy == "invalidate-visual":
                    for key in list(reusable):
                        requirement = by_key.get(key)
                        if requirement is not None and "vision" in requirement.modalities:
                            reusable.remove(key)
                            investigate.append(key)
                            invalidations.append(
                                {"requirement_key": key, "reason": "MODEL_PROFILE_CHANGED"}
                            )
                mode = ReplayMode.REINVESTIGATE if investigate else diff.mode
                if blocked is None:
                    investigation_count += len(set(investigate))
                    for key in reusable:
                        requirement = by_key.get(key)
                        if requirement is None:
                            continue
                        evidence_count += len(
                            list(
                                session.scalars(
                                    select(EvidenceRecord.id).where(
                                        EvidenceRecord.requirement_id == requirement.id
                                    )
                                )
                            )
                        )
                        result_count += int(requirement.current_result_id is not None)
                cases.append(
                    ReplayCasePlan(
                        source_case_id=case.id,
                        source_decision_id=decision.id if decision is not None else None,
                        mode=mode,
                        reusable_requirement_keys=sorted(reusable),
                        investigate_requirement_keys=sorted(set(investigate)),
                        invalidations=invalidations,
                        blocked_reason=blocked,
                    )
                )
            payload = {
                "policy_id": policy_id,
                "source_version": source_version,
                "target_version": target_version,
                "mode": diff.mode,
                "cases": [item.model_dump(mode="json") for item in cases],
                "model_change_policy": model_change_policy,
            }
            digest = sha256(_canonical(payload).encode()).hexdigest()
            return ReplayPreview(
                policy_id=policy_id,
                source_version=source_version,
                target_version=target_version,
                mode=diff.mode,
                cases=cases,
                affected_case_count=len(cases),
                reusable_evidence_count=evidence_count,
                reusable_result_count=result_count,
                estimated_investigation_count=investigation_count,
                preview_sha256=digest,
            )

    def diff(self, policy_id: str, source_version: int, target_version: int) -> PolicyDiff:
        with self.database.session() as session:
            source = session.get(PolicyRecord, (policy_id, source_version))
            target = session.get(PolicyRecord, (policy_id, target_version))
            if source is None or target is None:
                raise GovernanceError("REPLAY_POLICY_NOT_FOUND", "policy version not found")
            return _diff(source, target)


def _diff(source: PolicyRecord, target: PolicyRecord) -> PolicyDiff:
    before = CompiledPolicy.model_validate(source.compiled_policy)
    after = CompiledPolicy.model_validate(target.compiled_policy)
    before_hashes = {item.requirement_key: item.semantic_sha256 for item in before.requirements}
    after_hashes = {item.requirement_key: item.semantic_sha256 for item in after.requirements}
    unchanged = sorted(
        key
        for key in before_hashes.keys() & after_hashes.keys()
        if before_hashes[key] == after_hashes[key]
    )
    modified = sorted(
        key
        for key in before_hashes.keys() & after_hashes.keys()
        if before_hashes[key] != after_hashes[key]
    )
    added = sorted(after_hashes.keys() - before_hashes.keys())
    removed = sorted(before_hashes.keys() - after_hashes.keys())
    aggregation_changed = before.aggregation != after.aggregation
    evaluator_changed = any(
        (
            before.reject_when != after.reject_when,
            before.escalate_when != after.escalate_when,
            before.severity != after.severity,
            before.enabled != after.enabled,
        )
    )
    mode = ReplayMode.REINVESTIGATE if added or removed or modified else ReplayMode.REEVALUATE
    return PolicyDiff(
        policy_id=source.policy_id,
        source_version=source.version,
        target_version=target.version,
        mode=mode,
        unchanged_requirement_keys=unchanged,
        added_requirement_keys=added,
        removed_requirement_keys=removed,
        modified_requirement_keys=modified,
        aggregation_changed=aggregation_changed,
        evaluator_changed=evaluator_changed,
    )


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

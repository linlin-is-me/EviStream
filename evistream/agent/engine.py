"""Runtime-owned investigation graph, validation, budgets, and provisional decisions."""

import json
from collections.abc import Callable
from hashlib import sha256
from time import perf_counter
from typing import Any

from sqlalchemy import select

from evistream.agent.audit import AuditedModelGateway
from evistream.agent.errors import AgentRuntimeError
from evistream.agent.media import InspectionFrameSampler
from evistream.agent.service import AgentInvestigationService, PendingEvidence
from evistream.agent.types import (
    AgentAction,
    AgentNode,
    ChallengeOutput,
    InspectionObservation,
    InvestigationRequirement,
    InvestigationResult,
    InvestigationState,
    PlanOutput,
    ProvisionalDecision,
    VerificationOutput,
)
from evistream.config import Settings
from evistream.domain import EvidenceStance, Verdict
from evistream.models import ModelMessage, ModelRequest, ModelRole
from evistream.models.types import MediaReference, ModelError
from evistream.storage.database import Database, utc_now
from evistream.storage.models import CaseRecord, EvidenceRecord, VideoRecord
from evistream.tools import ToolExecutor, ToolRequest

GatewayFactory = Callable[[ModelRole, AgentNode, int], AuditedModelGateway]


class InvestigationEngine:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        service: AgentInvestigationService,
        tools: ToolExecutor,
        frames: InspectionFrameSampler,
        gateway_factory: GatewayFactory,
    ) -> None:
        self.database = database
        self.settings = settings
        self.service = service
        self.tools = tools
        self.frames = frames
        self.gateway_factory = gateway_factory

    async def run(self, state: InvestigationState, correlation_id: str) -> InvestigationResult:
        while state.next_node is not None:
            forced = self._budget_stop(state)
            if forced is not None:
                return self.service.complete(
                    state,
                    self._review_decision(state, forced),
                    forced,
                )
            node = state.next_node
            try:
                if node is AgentNode.PLAN:
                    state = await self._plan(state)
                elif node is AgentNode.RETRIEVE:
                    state = await self._retrieve(state, correlation_id)
                elif node is AgentNode.INSPECT:
                    state = await self._inspect(state)
                elif node is AgentNode.VERIFY:
                    state = await self._verify(state)
                elif node is AgentNode.CHALLENGE:
                    state = await self._challenge(state, correlation_id)
                elif node is AgentNode.DECIDE:
                    return await self._decide(state)
                else:
                    raise AgentRuntimeError(
                        "AGENT_TRANSITION_INVALID", f"unknown node: {node}"
                    )
            except ModelError as error:
                if error.retryable:
                    raise
                return self.service.complete(
                    state,
                    self._review_decision(state, "AGENT_MODEL_FAILED"),
                    "AGENT_MODEL_FAILED",
                )
        raise AgentRuntimeError(
            "AGENT_CHECKPOINT_INVALID", "run ended without a provisional decision"
        )

    async def _plan(self, state: InvestigationState) -> InvestigationState:
        started = perf_counter()
        state.iteration += 1
        prompt = {
            "case_id": state.case_id,
            "video_duration_ms": self._video_duration(state.case_id),
            "iteration": state.iteration,
            "requirements": [item.model_dump(mode="json") for item in state.requirements],
            "evidence_ids": state.evidence_ids,
            "missing_requirement_ids": state.missing_requirement_ids,
            "instruction": "Select one requirement and one allowed tool action.",
        }
        output, _, _ = await self._generate(
            state,
            AgentNode.PLAN,
            ModelRole.AGENT,
            PlanOutput,
            prompt,
        )
        plan = PlanOutput.model_validate(output)
        self._validate_action(state, plan.action)
        if plan.hypothesis.requirement_id != plan.action.requirement_id:
            raise AgentRuntimeError(
                "AGENT_ACTION_INVALID", "hypothesis and action target different requirements"
            )
        state.hypotheses.append(plan.hypothesis)
        state.pending_action = plan.action
        return self.service.checkpoint(
            state,
            node=AgentNode.PLAN,
            next_node=AgentNode.RETRIEVE,
            input_payload={"iteration": state.iteration, "evidence_ids": state.evidence_ids},
            output_payload=plan.model_dump(mode="json"),
            latency_ms=_elapsed(started),
        )

    async def _retrieve(
        self, state: InvestigationState, correlation_id: str
    ) -> InvestigationState:
        started = perf_counter()
        action = state.pending_action
        if action is None:
            raise AgentRuntimeError("AGENT_CHECKPOINT_INVALID", "Plan produced no action")
        result = await self.tools.execute(
            action.tool_name,
            _tool_request(state, correlation_id, action),
        )
        if result.status == "failed":
            state.consecutive_tool_failures += 1
            state.total_tool_failures += 1
        else:
            state.consecutive_tool_failures = 0
        state.selected_items = [
            {
                **item.model_dump(mode="json"),
                "tool_run_id": result.tool_run_id,
                "requirement_id": action.requirement_id,
            }
            for item in result.items
        ]
        return self.service.checkpoint(
            state,
            node=AgentNode.RETRIEVE,
            next_node=AgentNode.INSPECT,
            input_payload={"action": action.model_dump(mode="json")},
            output_payload={
                "tool_run_id": result.tool_run_id,
                "status": result.status,
                "item_refs": [item.source_ref for item in result.items],
                "error_code": result.error_code,
            },
            latency_ms=_elapsed(started),
        )

    async def _inspect(self, state: InvestigationState) -> InvestigationState:
        started = perf_counter()
        if not state.selected_items:
            state.observations = []
            return self.service.checkpoint(
                state,
                node=AgentNode.INSPECT,
                next_node=AgentNode.VERIFY,
                input_payload={"item_refs": []},
                output_payload={"observations": []},
                latency_ms=_elapsed(started),
            )
        frames = self.frames.sample(state.case_id, state.selected_items)
        prompt = {
            "items": [_model_item(item) for item in state.selected_items],
            "instruction": "Describe only observable facts relevant to the selected requirement.",
        }
        output, _, _ = await self._generate(
            state,
            AgentNode.INSPECT,
            ModelRole.TRIAGE,
            InspectionObservation,
            prompt,
            media=frames,
        )
        observation = InspectionObservation.model_validate(output)
        valid_refs = {str(item["source_ref"]) for item in state.selected_items}
        if observation.source_ref not in valid_refs:
            raise AgentRuntimeError(
                "AGENT_ACTION_INVALID", "inspection observation forged its source"
            )
        state.observations = [observation]
        state.vlm_calls += 1
        return self.service.checkpoint(
            state,
            node=AgentNode.INSPECT,
            next_node=AgentNode.VERIFY,
            input_payload={
                "item_refs": sorted(valid_refs),
                "frame_count": len(frames),
            },
            output_payload={"observation": observation.model_dump(mode="json")},
            latency_ms=_elapsed(started),
        )

    async def _verify(self, state: InvestigationState) -> InvestigationState:
        started = perf_counter()
        if not state.selected_items:
            return self.service.checkpoint(
                state,
                node=AgentNode.VERIFY,
                next_node=AgentNode.CHALLENGE,
                input_payload={"item_refs": []},
                output_payload={"evidence": []},
                latency_ms=_elapsed(started),
            )
        action = state.pending_action
        if action is None:
            raise AgentRuntimeError("AGENT_CHECKPOINT_INVALID", "Verifier has no action")
        requirement = self._requirement(state, action.requirement_id)
        prompt = {
            "requirement": requirement.model_dump(mode="json"),
            "items": [_model_item(item) for item in state.selected_items],
            "observations": [item.model_dump(mode="json") for item in state.observations],
            "instruction": "Create evidence only from supplied source references and time ranges.",
        }
        frames = self.frames.sample(state.case_id, state.selected_items)
        output, model_call_id, actual_model = await self._generate(
            state,
            AgentNode.VERIFY,
            ModelRole.VERIFIER,
            VerificationOutput,
            prompt,
            media=frames,
        )
        verified = VerificationOutput.model_validate(output)
        if model_call_id is None:
            raise AgentRuntimeError("AGENT_MODEL_FAILED", "Verifier call was not audited")
        pending = self._validate_evidence(
            state,
            action.requirement_id,
            verified,
            model_call_id,
            actual_model,
        )
        for item in pending:
            if item.evidence_id not in state.evidence_ids:
                state.evidence_ids.append(item.evidence_id)
        state.vlm_calls += 1
        self._refresh_missing(state, pending)
        return self.service.checkpoint(
            state,
            node=AgentNode.VERIFY,
            next_node=AgentNode.CHALLENGE,
            input_payload={
                "requirement_id": action.requirement_id,
                "item_refs": [item["source_ref"] for item in state.selected_items],
                "frame_count": len(frames),
            },
            output_payload={"evidence_ids": [item.evidence_id for item in pending]},
            latency_ms=_elapsed(started),
            evidence=pending,
        )

    async def _challenge(
        self, state: InvestigationState, correlation_id: str
    ) -> InvestigationState:
        started = perf_counter()
        prompt = {
            "requirements": [item.model_dump(mode="json") for item in state.requirements],
            "evidence": self._evidence_summary(state),
            "instruction": (
                "Propose counter-evidence queries with find_counter_evidence, including "
                "ordinary conditions, exceptions, and adjacent context."
            ),
        }
        output, _, _ = await self._generate(
            state,
            AgentNode.CHALLENGE,
            ModelRole.AGENT,
            ChallengeOutput,
            prompt,
        )
        challenge = ChallengeOutput.model_validate(output)
        tool_summaries: list[dict[str, Any]] = []
        for action in challenge.actions:
            if action.tool_name != "find_counter_evidence":
                raise AgentRuntimeError(
                    "AGENT_ACTION_INVALID", "Challenge may only use find_counter_evidence"
                )
            self._requirement(state, action.requirement_id)
            result = await self.tools.execute(
                "find_counter_evidence", _tool_request(state, correlation_id, action)
            )
            self._record_tool_status(state, result.status)
            tool_summaries.append(
                {
                    "tool_run_id": result.tool_run_id,
                    "status": result.status,
                    "item_refs": [item.source_ref for item in result.items],
                    "error_code": result.error_code,
                }
            )
        if state.selected_items:
            selected = state.selected_items[0]
            context_action = AgentAction(
                requirement_id=str(selected["requirement_id"]),
                tool_name="expand_temporal_context",
                start_ms=int(selected["start_ms"]),
                end_ms=int(selected["end_ms"]),
                rationale="inspect adjacent temporal context",
            )
            result = await self.tools.execute(
                context_action.tool_name,
                _tool_request(state, correlation_id, context_action),
            )
            self._record_tool_status(state, result.status)
            tool_summaries.append(
                {
                    "tool_run_id": result.tool_run_id,
                    "status": result.status,
                    "item_refs": [item.source_ref for item in result.items],
                    "error_code": result.error_code,
                }
            )
        if len(state.evidence_ids) == state.last_evidence_count:
            state.stagnant_iterations += 1
        else:
            state.stagnant_iterations = 0
        state.last_evidence_count = len(state.evidence_ids)
        if challenge.contradictory_evidence:
            state.contradictory_requirement_ids = sorted(
                {item.requirement_id for item in state.requirements if item.required}
            )
        next_node = (
            AgentNode.PLAN
            if challenge.continue_investigation and self._budget_stop(state) is None
            else AgentNode.DECIDE
        )
        return self.service.checkpoint(
            state,
            node=AgentNode.CHALLENGE,
            next_node=next_node,
            input_payload={"evidence_ids": state.evidence_ids},
            output_payload={
                "challenge": challenge.model_dump(mode="json"),
                "tools": tool_summaries,
            },
            latency_ms=_elapsed(started),
        )

    async def _decide(self, state: InvestigationState) -> InvestigationResult:
        started = perf_counter()
        prompt = {
            "requirements": [item.model_dump(mode="json") for item in state.requirements],
            "evidence": self._evidence_summary(state),
            "instruction": "Return a provisional recommendation for an auditor.",
        }
        output, _, _ = await self._generate(
            state,
            AgentNode.DECIDE,
            ModelRole.AGENT,
            ProvisionalDecision,
            prompt,
        )
        proposed = ProvisionalDecision.model_validate(output)
        decision, stop_reason = self._enforce_decision(state, proposed)
        state.provisional_decision = decision
        state.stop_reason = stop_reason
        state = self.service.checkpoint(
            state,
            node=AgentNode.DECIDE,
            next_node=None,
            input_payload={"evidence_ids": state.evidence_ids},
            output_payload={
                "provisional_decision": decision.model_dump(mode="json"),
                "stop_reason": stop_reason,
            },
            latency_ms=_elapsed(started),
        )
        return self.service.complete(state, decision, stop_reason)

    async def _generate(
        self,
        state: InvestigationState,
        node: AgentNode,
        role: ModelRole,
        schema: type[PlanOutput]
        | type[InspectionObservation]
        | type[VerificationOutput]
        | type[ChallengeOutput]
        | type[ProvisionalDecision],
        payload: dict[str, Any],
        *,
        media: list[MediaReference] | None = None,
    ) -> tuple[dict[str, Any], str | None, str]:
        gateway = self.gateway_factory(role, node, state.state_version)
        request = ModelRequest(
            role=role,
            messages=[
                ModelMessage(
                    role="system",
                    content=(
                        "Return one JSON object that conforms to this JSON Schema: "
                        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
                    ),
                ),
                ModelMessage(
                    role="user",
                    content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            ],
            response_schema=schema,
            media=media or [],
            timeout_seconds=self.settings.process_timeout_seconds,
            trace_id=f"{state.run_id}-{node}-{state.state_version}",
        )
        response = await gateway.generate(request)
        return response.data, gateway.last_call_id, response.actual_model

    def _validate_action(self, state: InvestigationState, action: AgentAction) -> None:
        requirement = self._requirement(state, action.requirement_id)
        if action.tool_name not in requirement.tool_capabilities:
            raise AgentRuntimeError(
                "AGENT_ACTION_INVALID",
                f"tool {action.tool_name} is not allowed for {requirement.requirement_key}",
            )
        if self.tools.registry.get(action.tool_name) is None:
            raise AgentRuntimeError("AGENT_ACTION_INVALID", "Planner selected an unknown tool")
        search_tools = {"search_transcript", "search_ocr", "search_visual_caption"}
        range_tools = {"inspect_clip", "expand_temporal_context", "get_neighbor_segments"}
        if action.tool_name in search_tools and not action.query.strip():
            raise AgentRuntimeError("AGENT_ACTION_INVALID", "search query is empty")
        if action.tool_name in range_tools and action.start_ms is None:
            raise AgentRuntimeError("AGENT_ACTION_INVALID", "time range is required")

    def _validate_evidence(
        self,
        state: InvestigationState,
        requirement_id: str,
        output: VerificationOutput,
        model_call_id: str,
        actual_model: str,
    ) -> list[PendingEvidence]:
        items = {str(item["source_ref"]): item for item in state.selected_items}
        pending: list[PendingEvidence] = []
        for draft in output.evidence:
            source = items.get(draft.source_ref)
            if source is None:
                raise AgentRuntimeError(
                    "AGENT_ACTION_INVALID", "Verifier forged an evidence source"
                )
            if (
                draft.start_ms < int(source["start_ms"])
                or draft.end_ms > int(source["end_ms"])
            ):
                raise AgentRuntimeError(
                    "AGENT_ACTION_INVALID", "evidence exceeds the source time range"
                )
            if str(source["requirement_id"]) != requirement_id:
                raise AgentRuntimeError(
                    "AGENT_ACTION_INVALID", "evidence targets another requirement"
                )
            identity = (
                f"{state.run_id}:{requirement_id}:{draft.source_ref}:{draft.stance}:"
                f"{draft.start_ms}:{draft.end_ms}:{model_call_id}"
            )
            pending.append(
                PendingEvidence(
                    evidence_id=f"ev_{sha256(identity.encode()).hexdigest()[:40]}",
                    requirement_id=requirement_id,
                    stance=draft.stance,
                    modality=str(source["modality"]),
                    start_ms=draft.start_ms,
                    end_ms=draft.end_ms,
                    artifact_id=(
                        str(source["artifact_id"]) if source.get("artifact_id") else None
                    ),
                    tool_run_id=str(source["tool_run_id"]),
                    model_call_id=model_call_id,
                    model_name=actual_model,
                    source_ref=draft.source_ref,
                    summary=draft.summary,
                    confidence=draft.confidence,
                )
            )
        return pending

    def _refresh_missing(
        self, state: InvestigationState, evidence: list[PendingEvidence]
    ) -> None:
        resolved = {
            item.requirement_id
            for item in evidence
            if item.stance in {EvidenceStance.SUPPORT, EvidenceStance.CONTRADICT}
        }
        state.missing_requirement_ids = [
            item for item in state.missing_requirement_ids if item not in resolved
        ]

    def _record_tool_status(self, state: InvestigationState, status: str) -> None:
        if status == "failed":
            state.consecutive_tool_failures += 1
            state.total_tool_failures += 1
        else:
            state.consecutive_tool_failures = 0

    def _requirement(
        self, state: InvestigationState, requirement_id: str
    ) -> InvestigationRequirement:
        for requirement in state.requirements:
            if requirement.requirement_id == requirement_id:
                return requirement
        raise AgentRuntimeError(
            "AGENT_ACTION_INVALID", "action targets a requirement outside the case"
        )

    def _evidence_summary(self, state: InvestigationState) -> list[dict[str, Any]]:
        if not state.evidence_ids:
            return []
        with self.database.session() as session:
            records = session.scalars(
                select(EvidenceRecord)
                .where(EvidenceRecord.id.in_(state.evidence_ids))
                .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
            ).all()
            return [
                {
                    "evidence_id": item.id,
                    "requirement_id": item.requirement_id,
                    "stance": item.stance,
                    "source_ref": item.source_ref,
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "summary": item.summary,
                    "confidence": item.confidence,
                }
                for item in records
            ]

    def _video_duration(self, case_id: str) -> int:
        with self.database.session() as session:
            case = session.get(CaseRecord, case_id)
            video = session.get(VideoRecord, case.video_id) if case is not None else None
            if video is None:
                raise AgentRuntimeError(
                    "AGENT_CHECKPOINT_INVALID", "case video disappeared"
                )
            return video.duration_ms

    def _enforce_decision(
        self, state: InvestigationState, proposed: ProvisionalDecision
    ) -> tuple[ProvisionalDecision, str]:
        known_ids = set(state.evidence_ids)
        if not set(proposed.evidence_ids).issubset(known_ids):
            raise AgentRuntimeError(
                "AGENT_ACTION_INVALID", "provisional decision forged evidence references"
            )
        stop = self._budget_stop(state)
        if proposed.verdict is not Verdict.NEEDS_HUMAN_REVIEW and not proposed.evidence_ids:
            stop = "DECISION_EVIDENCE_MISSING"
        if stop is None and state.missing_requirement_ids:
            stop = "REQUIRED_EVIDENCE_MISSING"
        summary = self._evidence_summary(state)
        stances: dict[str, set[str]] = {}
        for item in summary:
            stances.setdefault(str(item["requirement_id"]), set()).add(str(item["stance"]))
        conflicts = sorted(
            requirement_id
            for requirement_id, values in stances.items()
            if {
                EvidenceStance.SUPPORT.value,
                EvidenceStance.CONTRADICT.value,
            }.issubset(values)
        )
        if conflicts:
            state.contradictory_requirement_ids = conflicts
            stop = "CONTRADICTORY_EVIDENCE"
        if stop is not None:
            return self._review_decision(state, stop), stop
        return proposed, "PROVISIONAL_DECISION"

    def _budget_stop(self, state: InvestigationState) -> str | None:
        if utc_now() >= state.deadline_at:
            return "BUDGET_TIME_EXHAUSTED"
        if (
            state.iteration >= self.settings.agent_max_iterations
            and state.next_node in {AgentNode.PLAN, AgentNode.DECIDE}
        ):
            return "BUDGET_ITERATION_EXHAUSTED"
        if state.vlm_calls >= self.settings.agent_max_vlm_calls:
            return "BUDGET_VLM_EXHAUSTED"
        if (
            state.consecutive_tool_failures
            >= self.settings.agent_max_consecutive_tool_failures
        ):
            return "TOOL_FAILURE_LIMIT"
        if state.stagnant_iterations >= self.settings.agent_max_stagnant_iterations:
            return "STAGNATION_LIMIT"
        return None

    def _review_decision(
        self, state: InvestigationState, reason: str
    ) -> ProvisionalDecision:
        return ProvisionalDecision(
            verdict=Verdict.NEEDS_HUMAN_REVIEW,
            reason_code=reason,
            explanation="The runtime could not establish a complete, non-conflicting basis.",
            evidence_ids=state.evidence_ids,
        )


def _tool_request(
    state: InvestigationState, correlation_id: str, action: AgentAction
) -> ToolRequest:
    return ToolRequest(
        correlation_id=correlation_id,
        run_id=state.run_id,
        case_id=state.case_id,
        requirement_id=action.requirement_id,
        query=action.query,
        start_ms=action.start_ms,
        end_ms=action.end_ms,
        limit=action.limit,
    )


def _model_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_ref": item["source_ref"],
        "modality": item["modality"],
        "start_ms": item["start_ms"],
        "end_ms": item["end_ms"],
        "content": item["content"],
    }


def _elapsed(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))

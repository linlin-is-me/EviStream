"""Deterministic Agent model responses for offline runtime verification."""

import json
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml
from pydantic import ValidationError

from evistream.models.types import (
    ModelCapability,
    ModelError,
    ModelErrorCode,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)


class ScriptedResponses:
    def __init__(self, responses: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.responses = responses or {}
        self.positions: dict[str, int] = {}

    @classmethod
    def load(cls, path: Path) -> "ScriptedResponses":
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("Stage 4 script must be a mapping")
        responses: dict[str, list[dict[str, Any]]] = {}
        for key, value in document.items():
            if not isinstance(key, str) or not isinstance(value, list):
                raise ValueError("Stage 4 script entries must be response lists")
            if not all(isinstance(item, dict) for item in value):
                raise ValueError("Stage 4 scripted responses must be objects")
            responses[key] = value
        return cls(responses)

    def next(self, request: ModelRequest) -> dict[str, Any]:
        name = request.response_schema.__name__
        choices = self.responses.get(name)
        position = self.positions.get(name, 0)
        if choices is not None and position < len(choices):
            self.positions[name] = position + 1
            expanded = _expand_placeholders(choices[position], request)
            if not isinstance(expanded, dict):
                raise ValueError("Stage 4 scripted response must remain an object")
            return expanded
        return _fallback(request)


class ScriptedAgentMockGateway:
    def __init__(self, script: ScriptedResponses, model_name: str = "mock-agent-v1") -> None:
        self.script = script
        self.model_name = model_name

    @property
    def capability(self) -> ModelCapability:
        return ModelCapability(text=True, image=True, video=False, structured_output=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        started = perf_counter()
        payload = self.script.next(request)
        try:
            validated = request.response_schema.model_validate(payload)
        except ValidationError as error:
            raise ModelError(
                ModelErrorCode.OUTPUT_INVALID,
                "scripted Agent payload does not satisfy the response schema",
                retryable=False,
            ) from error
        return ModelResponse(
            data=validated.model_dump(mode="json"),
            actual_model=self.model_name,
            usage=ModelUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            finish_reason="stop",
            provider_request_id=f"mock-{request.response_schema.__name__}",
        )


def _fallback(request: ModelRequest) -> dict[str, Any]:
    name = request.response_schema.__name__
    payload = _user_payload(request)
    if name == "PlanOutput":
        requirements = payload.get("requirements", [])
        missing = set(payload.get("missing_requirement_ids", []))
        selected = next(
            (item for item in requirements if item.get("requirement_id") in missing),
            requirements[0] if requirements else {},
        )
        tools = selected.get("tool_capabilities") or ["search_transcript"]
        tool_name = str(tools[0])
        range_required = tool_name in {
            "inspect_clip",
            "expand_temporal_context",
            "get_neighbor_segments",
        }
        requirement_id = str(selected.get("requirement_id", "missing"))
        query = str(
            (selected.get("suggested_queries") or [selected.get("description", "evidence")])[0]
        )
        return {
            "hypothesis": {
                "requirement_id": requirement_id,
                "statement": str(selected.get("description", "inspect requirement")),
                "confidence": 0.5,
            },
            "action": {
                "requirement_id": requirement_id,
                "tool_name": tool_name,
                "query": "" if range_required else query,
                "start_ms": 0 if range_required else None,
                "end_ms": 1000 if range_required else None,
                "limit": 5,
                "rationale": "deterministic offline investigation",
            },
        }
    if name == "InspectionObservation":
        item = (payload.get("items") or [{}])[0]
        return {
            "source_ref": item.get("source_ref", "missing"),
            "summary": "The supplied item is insufficient for a confident observation.",
            "visible_entities": [],
            "uncertainty": 1,
        }
    if name == "VerificationOutput":
        item = (payload.get("items") or [{}])[0]
        if not item:
            return {"evidence": []}
        return {
            "evidence": [
                {
                    "source_ref": item["source_ref"],
                    "stance": "uncertain",
                    "start_ms": item["start_ms"],
                    "end_ms": item["end_ms"],
                    "summary": "The controlled Mock cannot establish the requirement.",
                    "confidence": 0.2,
                }
            ]
        }
    if name == "ChallengeOutput":
        return {
            "actions": [],
            "unresolved_exception": True,
            "contradictory_evidence": False,
            "continue_investigation": False,
            "rationale": "No additional controlled evidence is available.",
        }
    if name == "ProvisionalDecision":
        return {
            "verdict": "NEEDS_HUMAN_REVIEW",
            "reason_code": "MOCK_EVIDENCE_INSUFFICIENT",
            "explanation": "The deterministic Mock does not assert a business verdict.",
            "evidence_ids": [item["evidence_id"] for item in payload.get("evidence", [])],
        }
    return {}


def _user_payload(request: ModelRequest) -> dict[str, Any]:
    for message in reversed(request.messages):
        if message.role == "user":
            try:
                value = json.loads(message.content)
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def _expand_placeholders(value: Any, request: ModelRequest) -> Any:
    payload = _user_payload(request)
    evidence_ids = [
        item["evidence_id"]
        for item in payload.get("evidence", [])
        if isinstance(item, dict) and "evidence_id" in item
    ]
    if value == "$ALL_EVIDENCE":
        return evidence_ids
    if isinstance(value, list):
        if value == ["$ALL_EVIDENCE"]:
            return evidence_ids
        return [_expand_placeholders(item, request) for item in value]
    if isinstance(value, dict):
        return {key: _expand_placeholders(item, request) for key, item in value.items()}
    return value

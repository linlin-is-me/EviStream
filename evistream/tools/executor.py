"""Validated, persisted and idempotent tool execution."""

import json
from hashlib import sha256
from time import perf_counter
from typing import Literal
from uuid import uuid4

from sqlalchemy import select

from evistream.retrieval.text import normalize_text
from evistream.storage.database import Database, utc_now
from evistream.storage.models import CaseRecord, RequirementRecord, ToolRunRecord
from evistream.tools.registry import ToolRegistry
from evistream.tools.types import ToolRequest, ToolResult


def tool_request_key(tool_name: str, request: ToolRequest) -> str:
    payload = {
        "case_id": request.case_id,
        "requirement_id": request.requirement_id,
        "tool_name": tool_name,
        "query": normalize_text(request.query),
        "start_ms": request.start_ms,
        "end_ms": request.end_ms,
        "limit": request.limit,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


class ToolExecutor:
    def __init__(self, database: Database, registry: ToolRegistry) -> None:
        self.database = database
        self.registry = registry

    async def execute(self, tool_name: str, request: ToolRequest) -> ToolResult:
        key = tool_request_key(tool_name, request)
        error = self._validate(tool_name, request, key)
        if error is not None:
            return error
        with self.database.session() as session:
            existing = session.scalar(
                select(ToolRunRecord).where(
                    ToolRunRecord.run_id == request.run_id,
                    ToolRunRecord.request_key == key,
                )
            )
            if existing is not None:
                if existing.status != "running" and existing.response_payload is not None:
                    return ToolResult.model_validate(existing.response_payload)
                return ToolResult(
                    tool_run_id=existing.id,
                    request_key=key,
                    status="failed",
                    items=[],
                    latency_ms=existing.latency_ms,
                    error_code="TOOL_ALREADY_RUNNING",
                )
            tool_run_id = f"tool_{uuid4().hex}"
            now = utc_now()
            session.add(
                ToolRunRecord(
                    id=tool_run_id,
                    run_id=request.run_id,
                    case_id=request.case_id,
                    requirement_id=request.requirement_id,
                    correlation_id=request.correlation_id,
                    tool_name=tool_name,
                    request_key=key,
                    request_payload=request.model_dump(mode="json"),
                    status="running",
                    latency_ms=0,
                    estimated_cost=0,
                    created_at=now,
                    updated_at=now,
                )
            )

        started = perf_counter()
        tool = self.registry.get(tool_name)
        if tool is None:
            raise RuntimeError("validated tool disappeared from registry")
        try:
            output = await tool.execute(request)
        except Exception:
            output_status: Literal["success", "partial", "failed"] = "failed"
            items = []
            estimated_cost = 0.0
            error_code: str | None = "TOOL_EXECUTION_FAILED"
        else:
            output_status = output.status
            items = output.items
            estimated_cost = output.estimated_cost
            error_code = output.error_code
        result = ToolResult(
            tool_run_id=tool_run_id,
            request_key=key,
            status=output_status,
            items=items,
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            estimated_cost=estimated_cost,
            error_code=error_code,
        )
        with self.database.session() as session:
            record = session.get(ToolRunRecord, tool_run_id)
            if record is None:
                raise RuntimeError("tool run disappeared")
            record.status = result.status
            record.response_payload = result.model_dump(mode="json")
            record.latency_ms = result.latency_ms
            record.estimated_cost = result.estimated_cost
            record.error_code = result.error_code
            record.updated_at = utc_now()
        return result

    def _validate(
        self, tool_name: str, request: ToolRequest, request_key: str
    ) -> ToolResult | None:
        if self.registry.get(tool_name) is None:
            return _failure(request_key, "TOOL_NOT_FOUND")
        with self.database.session() as session:
            case = session.get(CaseRecord, request.case_id)
            if case is None:
                return _failure(request_key, "CASE_NOT_FOUND")
            requirement = session.get(RequirementRecord, request.requirement_id)
            if requirement is None:
                return _failure(request_key, "REQUIREMENT_NOT_FOUND")
            if requirement.case_id != request.case_id:
                return _failure(request_key, "REQUIREMENT_CASE_MISMATCH")
        search_tools = {
            "search_transcript",
            "search_ocr",
            "search_visual_caption",
            "find_counter_evidence",
        }
        range_tools = {"inspect_clip", "expand_temporal_context", "get_neighbor_segments"}
        if tool_name in search_tools and not request.query.strip():
            return _failure(request_key, "TOOL_INPUT_INVALID")
        if tool_name in range_tools and (request.start_ms is None or request.end_ms is None):
            return _failure(request_key, "TOOL_INPUT_INVALID")
        return None


def _failure(request_key: str, code: str) -> ToolResult:
    return ToolResult(
        tool_run_id=f"tool_{uuid4().hex}",
        request_key=request_key,
        status="failed",
        items=[],
        latency_ms=0,
        error_code=code,
    )

"""Persist sanitized, idempotent model-call audit records."""

import json
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from evistream.agent.errors import AgentRuntimeError
from evistream.models.types import (
    ModelCapability,
    ModelError,
    ModelErrorCode,
    ModelGateway,
    ModelRequest,
    ModelResponse,
)
from evistream.storage.database import Database, utc_now
from evistream.storage.models import ModelCallRecord


def audited_request_key(
    request: ModelRequest,
    *,
    run_id: str,
    node: str,
    state_version: int,
) -> tuple[str, dict[str, object]]:
    messages = [
        {
            "role": message.role,
            "length": len(message.content),
            "sha256": sha256(message.content.encode("utf-8")).hexdigest(),
        }
        for message in request.messages
    ]
    media = [
        {
            "kind": item.kind,
            "sha256": sha256(item.uri.encode("utf-8")).hexdigest(),
        }
        for item in request.media
    ]
    summary: dict[str, object] = {
        "role": request.role,
        "schema": request.response_schema.__name__,
        "messages": messages,
        "media": media,
    }
    canonical = json.dumps(
        {
            "run_id": run_id,
            "node": node,
            "state_version": state_version,
            **summary,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest(), summary


class AuditedModelGateway:
    def __init__(
        self,
        database: Database,
        gateway: ModelGateway,
        *,
        run_id: str,
        case_id: str,
        job_id: str,
        node: str,
        state_version: int,
        profile: str,
        requested_model: str,
        lease_seconds: int,
    ) -> None:
        self.database = database
        self.gateway = gateway
        self.run_id = run_id
        self.case_id = case_id
        self.job_id = job_id
        self.node = node
        self.state_version = state_version
        self.profile = profile
        self.requested_model = requested_model
        self.lease_seconds = lease_seconds
        self.last_call_id: str | None = None

    @property
    def capability(self) -> ModelCapability:
        return self.gateway.capability

    async def generate(self, request: ModelRequest) -> ModelResponse:
        key, summary = audited_request_key(
            request,
            run_id=self.run_id,
            node=self.node,
            state_version=self.state_version,
        )
        call_id, cached = self._claim(key, summary)
        self.last_call_id = call_id
        if cached is not None:
            try:
                response = ModelResponse.model_validate(cached)
                request.response_schema.model_validate(response.data)
            except ValidationError as error:
                raise AgentRuntimeError(
                    "AGENT_CHECKPOINT_INVALID", "cached model response is invalid"
                ) from error
            return response
        try:
            response = await self.gateway.generate(request)
        except ModelError as error:
            self._finish_error(call_id, error)
            raise
        self._finish_success(call_id, response)
        return response

    def _claim(
        self, key: str, summary: dict[str, object]
    ) -> tuple[str, dict[str, object] | None]:
        now = utc_now()
        call_id = f"mcall_{uuid4().hex}"
        values = {
            "id": call_id,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "node": self.node,
            "state_version": self.state_version,
            "role": summary["role"],
            "profile": self.profile,
            "requested_model": self.requested_model,
            "actual_model": None,
            "request_key": key,
            "request_summary": summary,
            "response_payload": None,
            "status": "running",
            "attempt": 1,
            "lease_until": now + timedelta(seconds=self.lease_seconds),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "latency_ms": 0,
            "provider_request_id": None,
            "error_code": None,
            "created_at": now,
            "updated_at": now,
        }
        with self.database.session() as session:
            statement = (
                insert(ModelCallRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["run_id", "request_key"])
                .returning(ModelCallRecord.id)
            )
            inserted_id = session.scalar(statement)
            if inserted_id is not None:
                return call_id, None
            record = session.scalar(
                select(ModelCallRecord)
                .where(
                    ModelCallRecord.run_id == self.run_id,
                    ModelCallRecord.request_key == key,
                )
                .with_for_update()
            )
            if record is None:
                raise AgentRuntimeError("AGENT_MODEL_FAILED", "model call claim was lost")
            if record.status == "success" and record.response_payload is not None:
                return record.id, record.response_payload
            if record.status == "failed":
                raise ModelError(
                    _model_error_code(record.error_code),
                    "cached model call failed",
                    retryable=False,
                )
            if record.lease_until is not None and record.lease_until > now:
                raise AgentRuntimeError(
                    "AGENT_RUN_ALREADY_RUNNING", "model call lease is still active"
                )
            record.attempt += 1
            record.lease_until = now + timedelta(seconds=self.lease_seconds)
            record.updated_at = now
            return record.id, None

    def _finish_success(self, call_id: str, response: ModelResponse) -> None:
        with self.database.session() as session:
            record = session.get(ModelCallRecord, call_id)
            if record is None:
                raise AgentRuntimeError("AGENT_MODEL_FAILED", "model call record disappeared")
            record.actual_model = response.actual_model
            record.response_payload = response.model_dump(mode="json")
            record.status = "success"
            record.lease_until = None
            record.prompt_tokens = response.usage.prompt_tokens
            record.completion_tokens = response.usage.completion_tokens
            record.total_tokens = response.usage.total_tokens
            record.latency_ms = response.latency_ms
            record.provider_request_id = response.provider_request_id
            record.updated_at = utc_now()

    def _finish_error(self, call_id: str, error: ModelError) -> None:
        with self.database.session() as session:
            record = session.get(ModelCallRecord, call_id)
            if record is None:
                return
            record.status = "failed"
            record.lease_until = None
            record.error_code = error.code
            record.updated_at = utc_now()


def _model_error_code(value: str | None) -> ModelErrorCode:
    if value is None:
        return ModelErrorCode.UNAVAILABLE
    try:
        return ModelErrorCode(value)
    except ValueError:
        return ModelErrorCode.UNAVAILABLE

import pytest
from pydantic import BaseModel

from evistream.models.mock import MockGateway
from evistream.models.types import (
    ModelError,
    ModelErrorCode,
    ModelMessage,
    ModelRequest,
    ModelRole,
)


class SmokeOutput(BaseModel):
    ok: bool
    summary: str


@pytest.mark.asyncio
async def test_mock_gateway_returns_common_response_contract() -> None:
    gateway = MockGateway()
    request = ModelRequest(
        role=ModelRole.AGENT,
        messages=[ModelMessage(role="user", content="Return stage zero JSON")],
        response_schema=SmokeOutput,
        trace_id="trace_contract",
    )

    response = await gateway.generate(request)

    assert response.data == {"ok": True, "summary": "stage0"}
    assert response.actual_model == "mock-stage0"
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_mock_gateway_rejects_payload_outside_target_schema() -> None:
    gateway = MockGateway(payload={"unexpected": True})
    request = ModelRequest(
        role=ModelRole.AGENT,
        messages=[ModelMessage(role="user", content="Return JSON")],
        response_schema=SmokeOutput,
    )

    with pytest.raises(ModelError) as caught:
        await gateway.generate(request)

    assert caught.value.code is ModelErrorCode.OUTPUT_INVALID
    assert caught.value.retryable is False

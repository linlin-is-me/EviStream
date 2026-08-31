import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest
from pydantic import BaseModel

from evistream.models.openai_compatible import OpenAICompatibleGateway
from evistream.models.types import (
    ModelCapability,
    ModelError,
    ModelErrorCode,
    ModelMessage,
    ModelRequest,
    ModelRole,
)


class SmokeOutput(BaseModel):
    ok: bool
    summary: str


class CompatibleServer(ThreadingHTTPServer):
    response_content: str
    failures_remaining: int
    request_count: int
    last_request: dict[str, Any]
    last_trace_id: str | None


class CompatibleHandler(BaseHTTPRequestHandler):
    server: CompatibleServer

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        self.server.last_request = json.loads(self.rfile.read(content_length))
        self.server.last_trace_id = self.headers.get("X-Request-ID")
        self.server.request_count += 1
        if self.server.failures_remaining:
            self.server.failures_remaining -= 1
            self._send_json(500, {"error": {"message": "temporary failure"}})
            return
        self._send_json(
            200,
            {
                "id": "local-request-1",
                "object": "chat.completion",
                "created": 1,
                "model": "local-compatible-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": self.server.response_content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    def _send_json(self, status: int, document: dict[str, Any]) -> None:
        body = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@contextmanager
def compatible_server(
    *,
    content: str = '{"ok": true, "summary": "stage0"}',
    failures: int = 0,
) -> Iterator[CompatibleServer]:
    server = CompatibleServer(("127.0.0.1", 0), CompatibleHandler)
    server.response_content = content
    server.failures_remaining = failures
    server.request_count = 0
    server.last_request = {}
    server.last_trace_id = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def make_request() -> ModelRequest:
    return ModelRequest(
        role=ModelRole.AGENT,
        messages=[ModelMessage(role="user", content="Return structured JSON")],
        response_schema=SmokeOutput,
        trace_id="trace-local-compatible",
    )


def make_gateway(
    server: CompatibleServer,
    capability: ModelCapability | None = None,
) -> OpenAICompatibleGateway:
    host, port = cast(tuple[str, int], server.server_address)
    return OpenAICompatibleGateway(
        base_url=f"http://{host}:{port}/v1",
        api_key="local-test-key",
        model="configured-model",
        capability=capability or ModelCapability(),
        max_attempts=2,
    )


@pytest.mark.asyncio
async def test_local_openai_compatible_server_uses_common_contract() -> None:
    with compatible_server() as server:
        response = await make_gateway(server).generate(make_request())

    assert response.data == {"ok": True, "summary": "stage0"}
    assert response.actual_model == "local-compatible-model"
    assert response.usage.total_tokens == 15
    assert response.provider_request_id == "local-request-1"
    assert server.last_request["response_format"] == {"type": "json_object"}
    assert server.last_trace_id == "trace-local-compatible"


@pytest.mark.asyncio
async def test_retryable_server_error_is_attempted_twice() -> None:
    with compatible_server(failures=1) as server:
        response = await make_gateway(server).generate(make_request())
        request_count = server.request_count

    assert response.data["ok"] is True
    assert request_count == 2


@pytest.mark.asyncio
async def test_invalid_compatible_output_is_not_retried() -> None:
    with compatible_server(content="not json") as server:
        with pytest.raises(ModelError) as caught:
            await make_gateway(server).generate(make_request())
        request_count = server.request_count

    assert caught.value.code is ModelErrorCode.OUTPUT_INVALID
    assert request_count == 1


@pytest.mark.asyncio
async def test_media_uses_compatible_content_parts_without_provider_fields() -> None:
    from evistream.models.types import MediaReference

    request = ModelRequest(
        role=ModelRole.AGENT,
        messages=[ModelMessage(role="user", content="Inspect the evidence")],
        media=[
            MediaReference(kind="image", uri="https://example.test/frame.jpg"),
            MediaReference(kind="video", uri="https://example.test/clip.mp4"),
        ],
        response_schema=SmokeOutput,
        trace_id="trace-media",
    )
    with compatible_server() as server:
        gateway = make_gateway(server, ModelCapability(image=True, video=True))
        await gateway.generate(request)

    content = server.last_request["messages"][0]["content"]
    assert content == [
        {"type": "text", "text": "Inspect the evidence"},
        {"type": "image_url", "image_url": {"url": "https://example.test/frame.jpg"}},
        {"type": "video_url", "video_url": {"url": "https://example.test/clip.mp4"}},
    ]

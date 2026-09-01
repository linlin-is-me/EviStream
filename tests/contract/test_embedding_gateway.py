import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest

from evistream.models import (
    EmbeddingRequest,
    ModelError,
    ModelErrorCode,
    OpenAICompatibleEmbeddingGateway,
)


class EmbeddingServer(ThreadingHTTPServer):
    request_count: int
    last_request: dict[str, Any]
    invalid_dimension: bool


class EmbeddingHandler(BaseHTTPRequestHandler):
    server: EmbeddingServer

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        self.server.last_request = request
        self.server.request_count += 1
        dimensions = request["dimensions"] + (1 if self.server.invalid_dimension else 0)
        body = json.dumps(
            {
                "id": "embedding-request-1",
                "object": "list",
                "model": "compatible-embedding",
                "data": [
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": [float(index + 1)] * dimensions,
                    }
                    for index, _ in enumerate(request["input"])
                ],
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@contextmanager
def embedding_server(*, invalid_dimension: bool = False) -> Iterator[EmbeddingServer]:
    server = EmbeddingServer(("127.0.0.1", 0), EmbeddingHandler)
    server.request_count = 0
    server.last_request = {}
    server.invalid_dimension = invalid_dimension
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def gateway(server: EmbeddingServer) -> OpenAICompatibleEmbeddingGateway:
    host, port = cast(tuple[str, int], server.server_address)
    return OpenAICompatibleEmbeddingGateway(
        base_url=f"http://{host}:{port}/v1",
        api_key="local-key",
        model="configured-embedding",
    )


@pytest.mark.asyncio
async def test_openai_compatible_embedding_contract() -> None:
    with embedding_server() as server:
        response = await gateway(server).embed(
            EmbeddingRequest(texts=("one", "two"), dimensions=8, trace_id="trace-embed")
        )
        request = server.last_request
    assert [item.index for item in response.vectors] == [0, 1]
    assert all(len(item.values) == 8 for item in response.vectors)
    assert response.actual_model == "compatible-embedding"
    assert response.provider_request_id == "embedding-request-1"
    assert request == {
        "input": ["one", "two"],
        "model": "configured-embedding",
        "dimensions": 8,
        "encoding_format": "float",
    }


@pytest.mark.asyncio
async def test_invalid_embedding_dimension_is_rejected_without_retry() -> None:
    with embedding_server(invalid_dimension=True) as server:
        with pytest.raises(ModelError) as caught:
            await gateway(server).embed(EmbeddingRequest(texts=("one",), dimensions=8))
        count = server.request_count
    assert caught.value.code is ModelErrorCode.OUTPUT_INVALID
    assert count == 1

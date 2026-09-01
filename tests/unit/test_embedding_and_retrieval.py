import pytest

from evistream.media.types import SegmentBoundary
from evistream.models import EmbeddingRequest, MockEmbeddingGateway
from evistream.retrieval.temporal import expand_window, merge_ranges
from evistream.retrieval.text import normalize_text, search_lexemes
from evistream.tools import ToolRequest, tool_request_key


@pytest.mark.asyncio
async def test_mock_embeddings_are_deterministic_and_semantic() -> None:
    gateway = MockEmbeddingGateway()
    request = EmbeddingRequest(
        texts=("weapon safety evidence", "weapon evidence", "cooking recipe"),
        dimensions=32,
    )
    first = await gateway.embed(request)
    second = await gateway.embed(request)
    assert first.vectors == second.vectors
    shared = sum(
        left * right
        for left, right in zip(
            first.vectors[0].values, first.vectors[1].values, strict=True
        )
    )
    unrelated = sum(
        left * right
        for left, right in zip(
            first.vectors[0].values, first.vectors[2].values, strict=True
        )
    )
    assert shared > unrelated


def test_multilingual_normalization_and_lexemes() -> None:
    assert normalize_text("  \uff25\uff36\uff29 Stream\n证据检索  ") == "evi stream 证据检索"
    lexemes = search_lexemes("危险行为 risk")
    assert {"危", "危险", "行为", "risk"}.issubset(set(lexemes.split()))


def test_temporal_expansion_and_merge() -> None:
    assert expand_window(2_000, 4_000, 10_000, 3_000) == SegmentBoundary(
        start_ms=0, end_ms=7_000
    )
    assert merge_ranges(
        [
            SegmentBoundary(start_ms=5_000, end_ms=8_000),
            SegmentBoundary(start_ms=0, end_ms=5_000),
            SegmentBoundary(start_ms=9_000, end_ms=10_000),
        ]
    ) == [
        SegmentBoundary(start_ms=0, end_ms=8_000),
        SegmentBoundary(start_ms=9_000, end_ms=10_000),
    ]


def test_tool_request_key_normalizes_query() -> None:
    base = dict(
        correlation_id="corr",
        run_id="run",
        case_id="case",
        requirement_id="req",
        limit=5,
    )
    first = ToolRequest(query=" Evidence   Retrieval ", **base)
    second = ToolRequest(query="evidence retrieval", **base)
    assert tool_request_key("search_transcript", first) == tool_request_key(
        "search_transcript", second
    )

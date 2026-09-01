# Stage 3 verification

Stage 3 adds provider-neutral text embeddings, PostgreSQL full-text and pgvector search,
RRF fusion, time filters, context expansion and the uniform eight-tool registry.

## Local verification

```bash
make dev-infra
make verify-stage3
evistream embedding-smoke --profile dashscope-test
```

The offline gate migrates an empty disposable database to revision
`0004_pre_stage4_integrity`, downgrades to Stage 2, upgrades again, runs Mock Embedding and
executes the full Python test suite. A sentinel proves that the configured development database
remains unchanged. CI performs the same gate without credentials or external network calls.

The PostgreSQL integration case indexes transcript and visual-description documents,
finds evidence in the expected time range, invokes all eight tools and confirms that
ToolRun and Clip Artifact requests are idempotent.

Local WSL2 verification on 2026-09-01 completed with 73 Python tests passing and 87.12%
coverage. Ruff, strict mypy across 56 source files, the disposable migration cycle, frontend
lint, two Vitest tests and the production frontend build also passed.

## External Embedding verification

The Alibaba Cloud Model Studio OpenAI-compatible endpoint was verified on 2026-09-01:

- Actual model: `text-embedding-v4`
- Dimensions: 1536
- Smoke input usage: 6 tokens
- Smoke latency: 5023 ms
- Provider request ID: `3edc798c-523f-91f6-be07-170eed26badc`
- Video index: 2 documents indexed, 13 tokens, no failures
- Tool result: transcript evidence at 1000–3000 ms, with both keyword and vector ranks

The hardening rerun on the same date exercised the real media pipeline before indexing:

- Chat smoke: `qwen3.8-flash`, 157 tokens, 6439 ms
- Embedding smoke: `text-embedding-v4`, 1536 dimensions, 6 tokens, 4847 ms
- Full media index: 5 documents, 90 tokens, 530 ms, no failures
- Tool result: one transcript hit at 0–7720 ms

Full vectors and API keys were not written to the report or repository.

## Deferred work

VLM clip interpretation, Agent state and checkpoints, Evidence creation and async indexing
remain Stage 4 or later work.

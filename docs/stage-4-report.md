# Stage 4 verification report

Verification date: 2026-09-01.

## Offline gate

`make verify-stage4 PYTHON=python` runs against a random disposable PostgreSQL database. It
checks the empty migration path, `0004 -> 0005`, downgrade and re-upgrade, and legacy manual
ToolRun backfill. A sentinel in the configured development database remains unchanged.

The deterministic CLI fixtures cover three orchestration paths:

| Path | Result | Nodes | Tools | Evidence |
|---|---:|---:|---:|---:|
| Obvious | COMPLETED / provisional REJECT | 6 | 2 | 1 |
| Cross-segment | COMPLETED / provisional REJECT | 11 | 4 | 2 |
| Insufficient | NEEDS_HUMAN_REVIEW | 6 | 1 | 0 |

These fixtures verify state transitions, provenance and recovery. They do not measure review
accuracy. The test suite reports 87.35% total coverage; Ruff and strict mypy pass.

## Real external gate

The external run used the Stage 0 sample video in another disposable database. Media
preprocessing used faster-whisper `tiny.en`, PaddleOCR 3.2.0 and the Gateway vision adapter.
Embedding used `text-embedding-v4` with 1536 dimensions and 66 input tokens.

All Agent, Triage and Verifier calls used `qwen3.8-flash` through the provider-neutral
OpenAI-compatible gateway and one existing API key. The run persisted 10 AgentStep rows, 17
ToolRun rows, 3 Evidence rows and 7 media Artifact rows. Model usage totaled 9,890 prompt
tokens, 21,519 completion tokens and 31,409 tokens; recorded model latency totaled 325,820 ms.
The runtime ended in `NEEDS_HUMAN_REVIEW` with `BUDGET_TIME_EXHAUSTED`, which is an accepted
business outcome and a successful ProcessingJob.

No credential, full prompt, media Data URI or vector appears in the report or ModelCall audit
summary. The disposable database and Artifact directory were removed after verification.

## Known limits

- The 300-second wall-time budget can expire after a long compatible API call because the
  runtime checks the deadline between nodes; Stage 6 may add cooperative cancellation.
- Provider success followed by a crash before audit commit can repeat one model call.
- Stage 4 does not aggregate Evidence, evaluate policy expressions or expose Case APIs.

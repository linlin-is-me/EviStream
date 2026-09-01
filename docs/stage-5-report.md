# Stage 5 verification

## Scope

Stage 5 adds deterministic Evidence aggregation, compiled-rule evaluation, formal machine and
human Decisions, Appeals, Case timelines, policy diff, and selective replay. It does not add HTTP
business endpoints, React pages, Redis, or RQ.

## Local gate

Run under the supported Linux environment:

```bash
make verify-stage5
```

The gate creates a randomly named PostgreSQL database, verifies `0005 -> 0006`, downgrade and
re-upgrade, retains a sentinel in the development database, runs the Stage 4 deterministic
investigations, exercises governance and replay tests, then runs Ruff, strict mypy, pytest and the
80 percent coverage threshold.

## External verification

The 2026-09-01 run used the Stage 0 sample video in a disposable database. Media preprocessing
used faster-whisper `tiny.en`, PaddleOCR, and the Gateway vision adapter. Embedding used
`text-embedding-v4`, 1536 dimensions, and 170 input tokens.

The source formal Decision and target replay both ended in `NEEDS_HUMAN_REVIEW`, which is a
valid business result. A semantic Requirement change selected `REINVESTIGATE`; the ProcessingJob
completed successfully with one human-review item and no failed item. The target investigation
used `qwen3.8-flash` for Agent, Triage, and Verifier. It persisted 11 steps, 9 model calls, 9 tool
runs, 2 Evidence rows, and 4 lineage rows. Model usage was 7,939 prompt tokens, 7,882 completion
tokens, and 15,821 total tokens; recorded latency was 112,203 ms.

The report and audit summaries contain no credential, full prompt, Data URI, or embedding. The
database and artifact directory were removed after verification.

## Known limits

- Replay runs inline until Stage 6 connects the shared handler to RQ.
- Reviewer and submitter are explicit strings; authentication begins with the Stage 6 API.
- Formal evaluation depends on completed Stage 4 Evidence and does not reprocess media.

# Stage 0–3 hardening audit

Date: 2026-09-01

The pre-Stage 4 audit found that the main Stage 0–3 capabilities were present, while several
runtime and database contracts remained weaker than the development specification required.
The `stage/3-hardening` branch closes those gaps without adding Stage 4 behavior.

## Closed findings

- API and CLI media processing now share one runtime factory, `InlineExecutor` and
  `MediaPreprocessJobHandler`. The handler verifies persisted request identity and claims jobs
  atomically.
- Upload limits are enforced while reading the stream. FFprobe duration and resolution limits
  are checked before artifacts are accepted. Silent videos create an empty Transcript Artifact.
- Alembic revision `0004_pre_stage4_integrity` rejects unpublished case policies, mutations of
  published policies and append-only audit records, and cross-case, cross-requirement or
  cross-video evidence relationships.
- Embedding indexing reports batch failures, rejects stale source text and returns non-zero CLI
  status for partial or failed runs.
- Stage 1–3 verification uses controlled, disposable PostgreSQL databases. The runner checks a
  sentinel in the normal development database before and after each gate and removes its test
  database in `finally`.

## Preserved boundaries

Redis, RQ, delayed retry scheduling, Agent state, Evidence aggregation and policy evaluation
remain deferred. Real adapters are explicit local options; CI uses Mock backends and requires no
API key or model download.

## Verification evidence

The final command results, coverage and external adapter measurements are recorded in the Stage 1
and Stage 3 verification reports. No key, complete embedding or model cache is stored in Git.

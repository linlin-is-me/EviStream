# Stage 6 verification report

## Implemented scope

- PostgreSQL-backed RQ dispatch, retries, leases and queue reconstruction.
- Media indexing followed by latest-enabled-policy Triage and idempotent Case creation.
- Video, job, case, governance, policy, profile and Replay HTTP APIs.
- Task center, Case workspace, policy publishing and Replay workspace.
- API, Worker, PostgreSQL, Redis, migration and Web Compose services.

## Local checks

- Ruff: passed.
- strict mypy: passed.
- backend unit tests: 78 passed.
- frontend ESLint: passed.
- frontend Vitest: 2 passed.
- frontend production build: passed.
- migration path `0006 → 0007 → 0006 → 0007`: passed in a disposable database.
- isolated Compose deployment: passed with PostgreSQL, Redis, RQ Worker, API and Web healthy.
- public HTTP workflow: passed for upload, automatic Triage, investigation, formal evaluation, review, appeal and both Replay modes.
- container rebuild recovery: passed; Video, Case and a 32-byte HTTP Range response remained available after API, Worker and Web recreation.

## External gateway smoke checks

Run on 2026-09-01 with the local untracked environment file:

- `qwen3.8-flash`: passed, 152 total tokens, 2633 ms.
- `text-embedding-v4`: passed, 1536 dimensions, 6 prompt tokens, 714 ms.

The Windows host has no FFmpeg executable, so the cumulative host-side Stage 4 fixture stops at frame extraction. The isolated Compose runtime includes FFmpeg and is the authoritative local deployment gate for Stage 6. A full Bailian-backed RQ investigation and the optional real media-adapter acceptance remain pending.

# Stage 6 task runtime

PostgreSQL remains the authoritative task ledger. Redis carries only a `job_id`; workers rebuild the complete `JobRequest` from `processing_jobs.payload` and dispatch it through the same Handler registry used by `InlineExecutor`.

The runtime supports `PENDING → RUNNING → SUCCEEDED`, plus `RETRY_WAIT`, `FAILED`, and `CANCELLED`. Claims use database leases and attempt counters. Duplicate queue messages reuse terminal results, active leases return `JOB_ALREADY_RUNNING`, and expired leases resume from media, Triage, Agent, or Replay checkpoints.

Transient model and database failures enter `RETRY_WAIT`. RQ applies the configured 5 and 30 second intervals, while PostgreSQL records `next_attempt_at`. `evistream jobs-requeue --due-only` reconstructs queue state after Redis loss. Manual retry cannot exceed `max_attempts`.

API and Worker logs use one-line JSON. They include correlation, task, run and case identifiers when available, but exclude credentials, full model input, Data URI values, vectors and server paths.

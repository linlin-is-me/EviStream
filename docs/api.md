# Stage 6 HTTP API

The API prefix is `/api/v1`. Mutating requests accept `Idempotency-Key`; every response carries `X-Correlation-ID`. Errors use `error_code`, a safe `message`, `correlation_id`, and structured `details`.

The public groups cover videos and media content, processing jobs, cases and investigation, human governance, policy versions and Replay, and model-profile discovery. Video and Artifact endpoints resolve database records on the server and support HTTP Range without returning local paths. Lists use cursor pagination.

`/api/v1/health` is compatibility-only. `/api/v1/ready` also verifies the Alembic head and Redis when RQ mode is enabled. The generated OpenAPI document is available at `/openapi.json`. Authentication and tenant isolation remain outside Stage 6.

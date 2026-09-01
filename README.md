# EviStream

EviStream is an evidence-grounded investigation agent for long-form video moderation.
The repository contains the provider-neutral foundation, PostgreSQL-backed media
pipeline, versioned moderation policies, hybrid retrieval and a uniform video-tool layer.
Stage 6 adds automatic policy triage, PostgreSQL-backed RQ jobs, governance APIs and three
React workspaces for tasks, cases, and policy replay.

## Development quick start

The supported development route is Ubuntu under WSL2 or native Linux. Keep the virtual
environment outside the repository:

```bash
python3.11 -m venv ../.venvs/evistream
source ../.venvs/evistream/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
make dev-infra
make migrate
uvicorn apps.api.main:app --reload
```

Install the real local media backends only when they are needed:

```bash
python -m pip install -e ".[dev,asr,ocr]"
# Set EVISTREAM_ASR_BACKEND, EVISTREAM_OCR_BACKEND and
# EVISTREAM_VISION_BACKEND explicitly in .env.
evistream media-ingest tests/fixtures/media/stage0_sample.mp4 --process
evistream seed-demo --check
make verify-stage4
make verify-stage5
make verify-stage6
```

In another terminal:

```bash
pnpm --dir apps/web install
pnpm --dir apps/web dev
```

Open `http://localhost:5173`. The API health endpoint is
`http://localhost:8000/api/v1/health`.

## Stage 0 verification commands

From Ubuntu under WSL2 or another supported Linux environment:

```bash
make doctor
```

The doctor checks Python 3.11, Node 24, pnpm, FFmpeg, Docker Engine, Docker Compose,
and the safe configuration files without printing credential values.

```bash
evistream run-demo-job --message stage0
evistream probe-video tests/fixtures/media/stage0_sample.mp4
evistream model-smoke --profile mock
evistream asr-smoke tests/fixtures/media/stage0_sample.mp4 --backend faster-whisper
pytest
pnpm --dir apps/web lint
pnpm --dir apps/web test -- --run
pnpm --dir apps/web build
```

Real model credentials belong only in `.env`. CI and the offline stage gates always use
Mock adapters. Selecting a real backend with a missing package, model profile or credential
fails with `MEDIA_ADAPTER_UNAVAILABLE`; it never falls back to Mock.
The current verification state is recorded in
[`docs/stage-0-report.md`](docs/stage-0-report.md).

## Stage 3 retrieval

Configure `EVISTREAM_EMBEDDING_MODEL`, then index a processed video and call a tool without
starting an Agent:

```bash
evistream embedding-smoke --profile mock
evistream retrieval-index <video-id> --profile mock
evistream tool-run search_transcript --case-id <case-id> --requirement-id <requirement-id> --query evidence
```

See [`docs/retrieval.md`](docs/retrieval.md) and
[`docs/tool-protocol.md`](docs/tool-protocol.md) for the stable contracts.

## Stage 4 investigation runtime

Start or resume a Case-scoped investigation, then inspect its status and sanitized trace:

```bash
evistream investigate <case-id> --profile mock
evistream investigation-status <run-id>
evistream investigation-trace <run-id>
```

The runtime enforces node transitions, Requirement ownership, tool capabilities, budgets,
evidence provenance and optimistic checkpoints. A provisional approval or rejection moves the
Case to `INVESTIGATED`; incomplete or conflicting evidence moves it to
`NEEDS_HUMAN_REVIEW`. Neither path writes a formal RequirementResult or Decision. See
[`docs/agent-runtime.md`](docs/agent-runtime.md).

## Stage 6 demo deployment

```bash
make demo-up
make verify-deploy
make demo-down
```

The default deployment uses Mock adapters. It runs migration, API, RQ Worker, PostgreSQL,
Redis and the Web application, while preserving database and Artifact volumes across ordinary
container rebuilds. See [`docs/deployment.md`](docs/deployment.md) and
[`docs/api.md`](docs/api.md).

## Project boundaries

Stage 6 ends at the local asynchronous Web workflow. Production authentication, tenant
isolation, formal evaluation data and hardened public deployment remain later stages. See
[`EviStream开发文档.md`](EviStream开发文档.md) for the complete architecture and roadmap.

## License

Apache License 2.0. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

# EviStream

EviStream is an evidence-grounded investigation agent for long-form video moderation.
The repository contains the provider-neutral foundation, PostgreSQL-backed media
pipeline, and Stage 2 moderation domain and YAML policy compiler.

## Development quick start

```powershell
conda activate evistream_env
python -m pip install -e ".[dev,asr]"
Copy-Item .env.example .env
uvicorn apps.api.main:app --reload
```

Start the Stage 1 database and migrate it from WSL2 or Linux:

```bash
make dev-infra
make migrate
evistream media-ingest tests/fixtures/media/stage0_sample.mp4 --process
evistream seed-demo --check
```

In another terminal:

```powershell
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

```powershell
evistream run-demo-job --message stage0
evistream probe-video tests/fixtures/media/stage0_sample.mp4
evistream model-smoke --profile mock
evistream asr-smoke tests/fixtures/media/stage0_sample.mp4 --backend faster-whisper
pytest
pnpm --dir apps/web lint
pnpm --dir apps/web test -- --run
pnpm --dir apps/web build
```

Real model credentials belong only in `.env`. CI always uses the Mock Gateway.
The current verification state is recorded in
[`docs/stage-0-report.md`](docs/stage-0-report.md).

## Project boundaries

Stage 2 adds versioned policies, cases, evidence contracts and deterministic rule
compilation. Evidence aggregation, rule evaluation, queues, retrieval and the Agent
investigation loop remain later stages. See
[`EviStream开发文档.md`](EviStream开发文档.md) for the complete architecture and roadmap.

## License

Apache License 2.0. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

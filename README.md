# EviStream

EviStream is an evidence-grounded investigation agent for long-form video moderation.
The repository is currently implementing Stage 0: a provider-neutral model gateway,
an inline job runtime, media capability probes, and a minimal web status page.

## Stage 0 quick start

```powershell
conda activate evistream_env
python -m pip install -e ".[dev,asr]"
Copy-Item .env.example .env
uvicorn apps.api.main:app --reload
```

In another terminal:

```powershell
pnpm --dir apps/web install
pnpm --dir apps/web dev
```

Open `http://localhost:5173`. The API health endpoint is
`http://localhost:8000/api/v1/health`.

## Stage 0 verification commands

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

Stage 0 deliberately excludes databases, queues, uploads, OCR, retrieval, the Agent
investigation loop, and moderation decisions. See
[`EviStream开发文档.md`](EviStream开发文档.md) for the complete architecture and roadmap.

## License

Apache License 2.0. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

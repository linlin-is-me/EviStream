# Stage 1 verification

Stage 1 adds PostgreSQL with pgvector, Alembic migrations, the local Artifact Store,
persistent media jobs, video registration, scene segmentation, keyframes, ASR, OCR and
visual-description adapter boundaries.

The pre-Stage 4 hardening pass routes both API background work and CLI processing through
`MediaPreprocessJobHandler` and `InlineExecutor`. Jobs are claimed with one conditional database
update; concurrent workers cannot process the same PENDING or RETRY_WAIT record. Existing
SUCCEEDED jobs are reused, while RUNNING and terminal failures return stable error codes.

## Stable commands

```bash
make dev-infra
make migrate
evistream media-ingest tests/fixtures/media/stage0_sample.mp4 --process
pytest
```

The CI pipeline starts pgvector PostgreSQL, migrates an empty database and runs the same
media pipeline with deterministic Mock ASR, OCR and visual description adapters. Real
PaddleOCR and remote model calls remain optional local checks.

The runtime factory selects adapters from `EVISTREAM_ASR_BACKEND`, `EVISTREAM_OCR_BACKEND` and
`EVISTREAM_VISION_BACKEND`. Invalid real-backend configuration returns
`MEDIA_ADAPTER_UNAVAILABLE` and never falls back to Mock. faster-whisper runs in an isolated
worker process because PaddlePaddle and CTranslate2 can conflict at the native runtime layer.

## Persistence contract

- PostgreSQL stores videos, segments, artifacts, search documents and processing jobs.
- `LocalArtifactStore` keeps media and generated files outside the database.
- A media fingerprint and request key prevent duplicate active preprocessing jobs.
- A restarted application can reconstruct video and job state from PostgreSQL and the
  configured Artifact Store root.
- Upload bytes are bounded during streaming; duration and resolution are validated with
  FFprobe before acceptance. Videos without audio persist an empty Transcript Artifact.

## External media verification

The complete real pipeline ran on 2026-09-01 against a disposable PostgreSQL database and a
temporary Artifact root:

- Backends: faster-whisper `tiny.en`, PaddleOCR and `qwen3.8-flash`
- Result after runtime reconstruction: Video `READY`, Job `SUCCEEDED`
- Media latency: 51,425 ms
- Persisted output: 1 Segment, 7 Artifacts, 5 SearchDocuments
- Recovery check: all 5 JSON Artifacts resolved and parsed

No credential, model cache or generated media was written to the repository.

## Known limits

- Stage 1 uses Inline execution; RQ and Redis enter Stage 6 and will reuse the same Handler.
- Embeddings are nullable until Stage 3 builds the retrieval pipeline.
- CI uses deterministic extractors and does not download ASR or OCR models.

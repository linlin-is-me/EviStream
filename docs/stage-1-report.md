# Stage 1 verification

Stage 1 adds PostgreSQL with pgvector, Alembic migrations, the local Artifact Store,
persistent media jobs, video registration, scene segmentation, keyframes, ASR, OCR and
visual-description adapter boundaries.

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

## Persistence contract

- PostgreSQL stores videos, segments, artifacts, search documents and processing jobs.
- `LocalArtifactStore` keeps media and generated files outside the database.
- A media fingerprint and request key prevent duplicate active preprocessing jobs.
- A restarted application can reconstruct video and job state from PostgreSQL and the
  configured Artifact Store root.

## Known limits

- Stage 1 uses Inline execution; RQ and Redis enter Stage 6.
- Embeddings are nullable until Stage 3 builds the retrieval pipeline.
- CI uses deterministic extractors and does not download ASR or OCR models.

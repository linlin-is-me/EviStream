# Stage 6 deployment

The default Compose deployment contains PostgreSQL 16 with pgvector, Redis 7, a one-shot migration service, API, RQ Worker and Nginx-served React application. API and Worker share the Artifact volume. PostgreSQL and Redis stay internal unless `docker-compose.dev.yml` is applied.

```bash
docker compose up -d --build
python scripts/verify_deploy.py
docker compose down
```

The default image uses Mock model and media adapters, while still installing FFmpeg. Set `EVISTREAM_RUNTIME_TARGET=media` to include faster-whisper and PaddleOCR. Credentials remain in the runtime environment and never enter an image layer.

Real-model acceptance is opt-in and reads the local `.env` only while Compose resolves the
override:

```bash
docker compose -f docker-compose.yml -f docker-compose.real.yml up -d --build
```

The default Compose file never forwards model credentials from the host `.env`.

`docker compose down` preserves named volumes. Use `down --volumes` only when the database and Artifact store are intentionally disposable. API and Worker wait for migrations; the API rejects readiness when its database revision differs from Alembic head.

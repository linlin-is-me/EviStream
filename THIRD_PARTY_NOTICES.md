# Third-party notices

Stages 0–3 use the following projects through their published packages or executables.
No source code from these projects is copied into EviStream.

| Project | License | Use |
|---|---|---|
| FastAPI | MIT | HTTP API framework |
| Uvicorn | BSD-3-Clause | ASGI server |
| Pydantic and pydantic-settings | MIT | Configuration and data validation |
| HTTPX | BSD-3-Clause | HTTP client and contract tests |
| OpenAI Python | Apache-2.0 | OpenAI-compatible client |
| SQLAlchemy | MIT | Database mapping and transaction access |
| Alembic | MIT | Database migrations |
| Psycopg | LGPL-3.0 | PostgreSQL driver |
| pgvector-python | MIT | PostgreSQL vector integration |
| PostgreSQL | PostgreSQL License | Reliable application state |
| pgvector | PostgreSQL License | Vector storage and indexes |
| faster-whisper | MIT | Optional ASR adapter |
| PaddleOCR | Apache-2.0 | Optional OCR adapter |
| PaddlePaddle | Apache-2.0 | Optional PaddleOCR inference runtime |
| FFmpeg | LGPL-2.1-or-later / configured components | Media probing and fixture generation |
| PyYAML | MIT | Policy and model-profile parsing |
| Typer | MIT | Command-line interface |
| React | MIT | Web application |
| React Router | MIT | Browser workspace routing |
| Vite | MIT | Frontend development and build |
| Vitest | MIT | Frontend tests |
| Redis | BSD-3-Clause | Disposable task queue state |
| redis-py | MIT | Redis client |
| RQ | BSD-2-Clause | Asynchronous job execution and scheduling |
| Nginx | BSD-2-Clause | Static Web delivery and API reverse proxy |

Exact package versions are recorded by the Python environment and `apps/web/pnpm-lock.yaml`.
Optional model weights retain their publishers' own terms and are downloaded to local caches;
they are not distributed in this repository. This file will be expanded whenever third-party
code, models or datasets enter the project.

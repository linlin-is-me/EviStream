FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONPATH=/app

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
RUN mkdir -p evistream apps/api \
    && touch evistream/__init__.py apps/__init__.py apps/api/__init__.py \
    && python -m pip install .

COPY evistream ./evistream
COPY apps ./apps
COPY migrations ./migrations
COPY alembic.ini ./
COPY configs ./configs

FROM runtime AS media
RUN python -m pip install ".[asr,ocr]"

FROM runtime AS default
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

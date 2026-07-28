# Multi-stage backend image — shared by api and worker (same code, different
# command). uv installs into a project venv in the builder; the runtime stage
# copies only the venv + source. psycopg ships its binary wheel, so no libpq
# apt package is needed (the step-23 lesson, handled in-image).

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
# deps first — this layer caches until pyproject/lock change
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY backend/src ./src
COPY backend/README.md backend/alembic.ini ./
COPY backend/alembic ./alembic
RUN uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

# docker CLI for the code-execution sandbox (worker only uses it when the
# host socket is mounted; api simply never calls it)
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker

RUN useradd --create-home --uid 1000 orchestrator
WORKDIR /app
COPY --from=builder --chown=orchestrator /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER orchestrator

# api:    docker/entrypoint-api.sh (migrations, then uvicorn)
# worker: celery -A orchestrator.worker.celery_app worker --pool=solo
CMD ["uvicorn", "orchestrator.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

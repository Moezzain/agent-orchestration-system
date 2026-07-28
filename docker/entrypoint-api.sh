#!/bin/sh
# API entrypoint: apply migrations, then serve. The worker depends_on the API
# being healthy, so migrations are guaranteed applied before any task runs.
set -e

echo "applying migrations..."
alembic upgrade head

exec uvicorn orchestrator.api.main:app --host 0.0.0.0 --port 8000

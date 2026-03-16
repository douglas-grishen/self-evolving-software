#!/bin/bash
# Backend entrypoint: run Alembic migrations, then start uvicorn
set -e

# Ensure app module is importable by alembic (WORKDIR=/app but not in PYTHONPATH)
export PYTHONPATH=/app:${PYTHONPATH:-}

echo "Running database migrations..."
alembic upgrade head
echo "Migrations complete."

echo "Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

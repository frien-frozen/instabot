#!/bin/sh
set -e

echo "Running database migrations..."
# Use the resilient migrator (repairs unknown alembic_version, never crashes deploy).
# Raw `alembic upgrade head` previously killed Render deploys with set -e.
if ! python -c "from app.database import run_alembic_migrations; run_alembic_migrations()"; then
  echo "WARNING: migration helper exited non-zero; continuing startup"
fi
echo "Migrations step finished."

echo "Starting Instabot on port ${PORT:-8080}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"

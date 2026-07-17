#!/bin/sh
set -e

echo "Starting Instabot on port ${PORT:-8080}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"

#!/usr/bin/env bash
set -euo pipefail
exec uvicorn backend.main:app --host 0.0.0.0 --port "${APP_PORT:-8000}"

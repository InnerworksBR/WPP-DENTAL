#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-3000}"
WORKERS="${WORKERS:-1}"

exec ./.venv/bin/uvicorn src.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"

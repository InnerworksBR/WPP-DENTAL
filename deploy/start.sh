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

if [[ -x "./.venv/bin/uvicorn" ]]; then
  UVICORN_BIN="./.venv/bin/uvicorn"
else
  UVICORN_BIN="$(command -v uvicorn || true)"
fi

if [[ -z "${UVICORN_BIN:-}" ]]; then
  echo "uvicorn nao encontrado. Instale as dependencias antes de iniciar a aplicacao." >&2
  exit 1
fi

exec "$UVICORN_BIN" src.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"

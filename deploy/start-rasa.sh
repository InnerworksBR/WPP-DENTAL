#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

RASA_PORT="${RASA_PORT:-5005}"
RASA_MODELS_DIR="${RASA_MODELS_DIR:-$ROOT_DIR/rasa_assistant/models}"

if ! command -v rasa >/dev/null 2>&1; then
  echo "rasa nao encontrado. Instale o Rasa Pro antes de iniciar o assistente." >&2
  exit 1
fi

mkdir -p "$RASA_MODELS_DIR"

rasa train \
  --config rasa_assistant/config.yml \
  --domain rasa_assistant/domain.yml \
  --data rasa_assistant/flows.yml \
  --out "$RASA_MODELS_DIR"

LATEST_MODEL="$(ls -1t "$RASA_MODELS_DIR"/*.tar.gz 2>/dev/null | head -n 1 || true)"
if [[ -z "$LATEST_MODEL" ]]; then
  echo "Nenhum modelo Rasa foi gerado em $RASA_MODELS_DIR." >&2
  exit 1
fi

exec rasa run \
  --enable-api \
  --host 0.0.0.0 \
  --port "$RASA_PORT" \
  --cors "*" \
  --credentials rasa_assistant/credentials.yml \
  --endpoints rasa_assistant/endpoints.yml \
  --model "$LATEST_MODEL"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ -f ./.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

mkdir -p logs

PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_FILE="./logs/tickflow_instruments.log"
TIMEOUT_SECONDS="${TICKFLOW_INSTRUMENTS_TIMEOUT:-600}"
BATCH_SIZE="${TICKFLOW_INSTRUMENTS_BATCH_SIZE:-200}"
TOKEN_ENV_FILE="${TICKFLOW_ENV_FILE:-.env}"

if [ -z "${TICKFLOW_API_KEY:-}" ] && [ -f "${TOKEN_ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  . "${TOKEN_ENV_FILE}"
fi

if [ -z "${TICKFLOW_API_KEY:-}" ]; then
  echo "$(date '+%F %T %z') [ERROR] 缺少 TICKFLOW_API_KEY，可通过环境变量或 ${TOKEN_ENV_FILE} 提供" | tee -a "${LOG_FILE}"
  exit 1
fi

echo "$(date '+%F %T %z') [INFO] tickflow instruments sync start batch_size=${BATCH_SIZE}" | tee -a "${LOG_FILE}"

if timeout "${TIMEOUT_SECONDS}" env "${PYTHON_BIN}" sync_tickflow_instruments.py --batch-size "${BATCH_SIZE}" >> "${LOG_FILE}" 2>&1; then
  echo "$(date '+%F %T %z') [INFO] tickflow instruments sync done" | tee -a "${LOG_FILE}"
else
  rc=$?
  echo "$(date '+%F %T %z') [WARNING] tickflow instruments sync exit_code=${rc}" | tee -a "${LOG_FILE}"
  exit "${rc}"
fi

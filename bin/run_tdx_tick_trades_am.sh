#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

if [ -f ./.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

mkdir -p logs
export PYTHONPATH="${PROJECT_DIR}/scripts:${PYTHONPATH:-}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_FILE="./logs/tdx_tick_trades_am.log"
TIMEOUT_SECONDS="${TDX_TICK_AM_TIMEOUT:-21600}"
WORKERS="${TDX_TICK_AM_WORKERS:-4}"
CHUNK_SIZE="${TDX_TICK_AM_CHUNK_SIZE:-25}"
PAGE_SIZE="${TDX_TICK_AM_PAGE_SIZE:-1800}"

echo "$(date '+%F %T %z') [INFO] pytdx tick am sync start workers=${WORKERS} chunk_size=${CHUNK_SIZE} page_size=${PAGE_SIZE}" | tee -a "${LOG_FILE}"

if timeout "${TIMEOUT_SECONDS}" "${PYTHON_BIN}" scripts/sync_tdx_tick_trades.py \
  --session am \
  --workers "${WORKERS}" \
  --chunk-size "${CHUNK_SIZE}" \
  --page-size "${PAGE_SIZE}" \
  ${TDX_TICK_AM_EXTRA_ARGS:-} >> "${LOG_FILE}" 2>&1; then
  echo "$(date '+%F %T %z') [INFO] pytdx tick am sync done" | tee -a "${LOG_FILE}"
else
  rc=$?
  echo "$(date '+%F %T %z') [WARNING] pytdx tick am sync exit_code=${rc}" | tee -a "${LOG_FILE}"
  exit "${rc}"
fi

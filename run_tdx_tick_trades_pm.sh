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
LOG_FILE="./logs/tdx_tick_trades_pm.log"
TIMEOUT_SECONDS="${TDX_TICK_PM_TIMEOUT:-28800}"
WORKERS="${TDX_TICK_PM_WORKERS:-4}"
CHUNK_SIZE="${TDX_TICK_PM_CHUNK_SIZE:-25}"
PAGE_SIZE="${TDX_TICK_PM_PAGE_SIZE:-1800}"

echo "$(date '+%F %T %z') [INFO] pytdx tick pm sync start workers=${WORKERS} chunk_size=${CHUNK_SIZE} page_size=${PAGE_SIZE}" | tee -a "${LOG_FILE}"

if timeout "${TIMEOUT_SECONDS}" "${PYTHON_BIN}" sync_tdx_tick_trades.py \
  --session pm \
  --backfill-am \
  --workers "${WORKERS}" \
  --chunk-size "${CHUNK_SIZE}" \
  --page-size "${PAGE_SIZE}" \
  ${TDX_TICK_PM_EXTRA_ARGS:-} >> "${LOG_FILE}" 2>&1; then
  echo "$(date '+%F %T %z') [INFO] pytdx tick pm sync done" | tee -a "${LOG_FILE}"
else
  rc=$?
  echo "$(date '+%F %T %z') [WARNING] pytdx tick pm sync exit_code=${rc}" | tee -a "${LOG_FILE}"
  exit "${rc}"
fi

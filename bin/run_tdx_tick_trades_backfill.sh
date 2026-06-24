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
LOG_FILE="./logs/tdx_tick_trades_backfill.log"
TIMEOUT_SECONDS="${TDX_TICK_BACKFILL_TIMEOUT:-43200}"
WORKERS="${TDX_TICK_BACKFILL_WORKERS:-4}"
CHUNK_SIZE="${TDX_TICK_BACKFILL_CHUNK_SIZE:-25}"
PAGE_SIZE="${TDX_TICK_BACKFILL_PAGE_SIZE:-1800}"
START_DATE="${TDX_TICK_BACKFILL_START_DATE:-2026-04-03}"
END_DATE="${TDX_TICK_BACKFILL_END_DATE:-2024-07-01}"
PROGRESS_FILE="${TDX_TICK_BACKFILL_PROGRESS_FILE:-./logs/tdx_tick_trades_backfill_progress.json}"

echo "$(date '+%F %T %z') [INFO] pytdx tick backfill start start_date=${START_DATE} end_date=${END_DATE} workers=${WORKERS} chunk_size=${CHUNK_SIZE} page_size=${PAGE_SIZE}" | tee -a "${LOG_FILE}"

if timeout "${TIMEOUT_SECONDS}" "${PYTHON_BIN}" scripts/backfill_tdx_tick_trades.py \
  --start-date "${START_DATE}" \
  --end-date "${END_DATE}" \
  --workers "${WORKERS}" \
  --chunk-size "${CHUNK_SIZE}" \
  --page-size "${PAGE_SIZE}" \
  --progress-file "${PROGRESS_FILE}" \
  ${TDX_TICK_BACKFILL_EXTRA_ARGS:-} >> "${LOG_FILE}" 2>&1; then
  echo "$(date '+%F %T %z') [INFO] pytdx tick backfill done" | tee -a "${LOG_FILE}"
else
  rc=$?
  echo "$(date '+%F %T %z') [WARNING] pytdx tick backfill exit_code=${rc}" | tee -a "${LOG_FILE}"
  exit "${rc}"
fi

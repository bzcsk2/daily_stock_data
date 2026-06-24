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
LOG_FILE="./logs/tdx_xdxr.log"
TIMEOUT_SECONDS="${TDX_XDXR_TIMEOUT:-5400}"
WORKERS="${TDX_XDXR_WORKERS:-4}"
CHUNK_SIZE="${TDX_XDXR_CHUNK_SIZE:-250}"

echo "$(date '+%F %T %z') [INFO] pytdx xdxr sync start workers=${WORKERS} chunk_size=${CHUNK_SIZE}" | tee -a "${LOG_FILE}"

if timeout "${TIMEOUT_SECONDS}" "${PYTHON_BIN}" scripts/sync_tdx_xdxr.py --workers "${WORKERS}" --chunk-size "${CHUNK_SIZE}" >> "${LOG_FILE}" 2>&1; then
  echo "$(date '+%F %T %z') [INFO] pytdx xdxr sync done" | tee -a "${LOG_FILE}"
else
  rc=$?
  echo "$(date '+%F %T %z') [WARNING] pytdx xdxr sync exit_code=${rc}" | tee -a "${LOG_FILE}"
  exit "${rc}"
fi

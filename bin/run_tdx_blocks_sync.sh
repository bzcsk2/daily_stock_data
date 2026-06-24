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
LOG_FILE="./logs/tdx_blocks.log"
TIMEOUT_SECONDS="${TDX_BLOCKS_TIMEOUT:-5400}"

echo "$(date '+%F %T %z') [INFO] pytdx blocks sync start" | tee -a "${LOG_FILE}"

if timeout "${TIMEOUT_SECONDS}" "${PYTHON_BIN}" scripts/sync_tdx_blocks.py >> "${LOG_FILE}" 2>&1; then
  echo "$(date '+%F %T %z') [INFO] pytdx blocks sync done" | tee -a "${LOG_FILE}"
else
  rc=$?
  echo "$(date '+%F %T %z') [WARNING] pytdx blocks sync exit_code=${rc}" | tee -a "${LOG_FILE}"
  exit "${rc}"
fi

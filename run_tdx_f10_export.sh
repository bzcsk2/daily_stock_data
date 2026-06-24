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
LOG_FILE="./logs/tdx_f10_export.log"
TIMEOUT_SECONDS="${TDX_F10_EXPORT_TIMEOUT:-21600}"
WORKERS="${TDX_F10_EXPORT_WORKERS:-4}"
CHUNK_SIZE="${TDX_F10_EXPORT_CHUNK_SIZE:-100}"
OUTPUT_DIR="${TDX_F10_OUTPUT_DIR:-./data/finance}"

echo "$(date '+%F %T %z') [INFO] pytdx f10 export start workers=${WORKERS} chunk_size=${CHUNK_SIZE} output_dir=${OUTPUT_DIR}" | tee -a "${LOG_FILE}"

if timeout "${TIMEOUT_SECONDS}" "${PYTHON_BIN}" export_tdx_f10_txts.py --workers "${WORKERS}" --chunk-size "${CHUNK_SIZE}" --output-dir "${OUTPUT_DIR}" >> "${LOG_FILE}" 2>&1; then
  echo "$(date '+%F %T %z') [INFO] pytdx f10 export done" | tee -a "${LOG_FILE}"
else
  rc=$?
  echo "$(date '+%F %T %z') [WARNING] pytdx f10 export exit_code=${rc}" | tee -a "${LOG_FILE}"
  exit "${rc}"
fi

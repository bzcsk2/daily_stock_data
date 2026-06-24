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
BATCH_SIZE="${DAILY_BATCH_SIZE:-200}"
MAX_WORKERS="${DAILY_MAX_WORKERS:-1}"
BATCH_TIMEOUT="${DAILY_BATCH_TIMEOUT:-1200}"
COUNT_TMP="${TMPDIR:-/tmp}/k_download_daily_count.$$"

cleanup() {
  rm -f "${COUNT_TMP}"
}
trap cleanup EXIT

"${PYTHON_BIN}" - <<'PY' > "${COUNT_TMP}"
import logging
from kline_common import load_symbols, latest_trade_date
logger = logging.getLogger("daily_batch_counter")
logger.addHandler(logging.NullHandler())
logger.propagate = False
print(len(load_symbols(logger, as_of_date=latest_trade_date().isoformat())))
PY

TOTAL="$(tr -cd '0-9' < "${COUNT_TMP}")"
TOTAL="${TOTAL:-0}"

echo "$(date '+%F %T %z') [INFO] daily batches total symbols=${TOTAL} batch_size=${BATCH_SIZE}"

OFFSET=0
while [ "${OFFSET}" -lt "${TOTAL}" ]; do
  echo "$(date '+%F %T %z') [INFO] daily batch offset=${OFFSET} limit=${BATCH_SIZE} start"
  if timeout "${BATCH_TIMEOUT}" "${PYTHON_BIN}" scripts/get_new_daily.py \
    --max-workers "${MAX_WORKERS}" \
    --offset "${OFFSET}" \
    --limit "${BATCH_SIZE}"; then
    echo "$(date '+%F %T %z') [INFO] daily batch offset=${OFFSET} done"
  else
    RC=$?
    echo "$(date '+%F %T %z') [WARNING] daily batch offset=${OFFSET} exit_code=${RC}"
  fi
  OFFSET=$((OFFSET + BATCH_SIZE))
done

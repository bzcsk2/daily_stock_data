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
LOG_FILE="./logs/tushare_stock_basic.log"
TIMEOUT_SECONDS="${TUSHARE_STOCK_BASIC_TIMEOUT:-300}"
LIST_STATUS="${TUSHARE_STOCK_BASIC_LIST_STATUS:-L}"
TOKEN_ENV_FILE="${TUSHARE_ENV_FILE:-.env}"

if [ -z "${TUSHARE_TOKEN:-}" ] && [ -f "${TOKEN_ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  . "${TOKEN_ENV_FILE}"
fi

if [ -z "${TUSHARE_TOKEN:-}" ]; then
  echo "$(date '+%F %T %z') [ERROR] 缺少 TUSHARE_TOKEN，可通过环境变量或 ${TOKEN_ENV_FILE} 提供" | tee -a "${LOG_FILE}"
  exit 1
fi

echo "$(date '+%F %T %z') [INFO] tushare stock_basic sync start list_status=${LIST_STATUS}" | tee -a "${LOG_FILE}"

if timeout "${TIMEOUT_SECONDS}" env \
  -u http_proxy -u https_proxy -u all_proxy \
  -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  NO_PROXY="*" \
  "${PYTHON_BIN}" scripts/sync_tushare_stock_basic.py --list-status "${LIST_STATUS}" >> "${LOG_FILE}" 2>&1; then
  echo "$(date '+%F %T %z') [INFO] tushare stock_basic sync done" | tee -a "${LOG_FILE}"
else
  rc=$?
  echo "$(date '+%F %T %z') [WARNING] tushare stock_basic sync exit_code=${rc}" | tee -a "${LOG_FILE}"
  exit "${rc}"
fi

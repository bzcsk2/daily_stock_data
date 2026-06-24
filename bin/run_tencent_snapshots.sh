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

PYTHON_BIN="${SNAPSHOT_PYTHON_BIN:-${PYTHON_BIN:-python3}}"
TIMEOUT_BIN="${SNAPSHOT_TIMEOUT_BIN:-/usr/bin/timeout}"
FLOCK_BIN="${SNAPSHOT_FLOCK_BIN:-/usr/bin/flock}"
MAX_RUNTIME="${SNAPSHOT_MAX_RUNTIME:-8h}"
LOCK_FILE="${SNAPSHOT_LOCK_FILE:-/tmp/k-download-tencent.lock}"
LOCK_CONFLICT_STATUS="${SNAPSHOT_LOCK_CONFLICT_STATUS:-75}"

# 统一在脚本内部抢锁。
# 这样无论是 cron 调起，还是手工直接执行这个脚本，最终都会争用同一把锁，
# 避免像 2026-03-19 这样出现“cron 一条、手工一条”同时采集的双开问题。
set +e
"${FLOCK_BIN}" -n -E "${LOCK_CONFLICT_STATUS}" "${LOCK_FILE}" \
  "${TIMEOUT_BIN}" --signal=TERM "${MAX_RUNTIME}" "${PYTHON_BIN}" scripts/download_quotes_tencent.py
status=$?
set -e

case "${status}" in
  0)
    exit 0
    ;;
  "${LOCK_CONFLICT_STATUS}")
    printf '%s [INFO] 腾讯快照任务已在运行，跳过本次启动\n' "$(date '+%F %T %z')" >&2
    exit 0
    ;;
  124|137)
    printf '%s [INFO] 腾讯快照任务达到最大运行时长（%s），正常停止\n' "$(date '+%F %T %z')" "${MAX_RUNTIME}" >&2
    exit 0
    ;;
  *)
    printf '%s [ERROR] 腾讯快照任务异常退出，exit_code=%s\n' "$(date '+%F %T %z')" "${status}" >&2
    exit "${status}"
    ;;
esac

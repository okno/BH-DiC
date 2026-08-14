#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

if (($#)); then
  case "$1" in
    -h | --help)
      printf 'Usage: %s\nShows only operator-safe BH-DiC runtime metadata.\n' "$0"
      exit 0
      ;;
    *) die "status accepts no arguments" ;;
  esac
fi

require_project_root
printf 'status: '
if pid="$(read_pid 2>/dev/null)" && process_is_running "${pid}" && process_is_bh_dic "${pid}"; then
  printf 'running\nPID: %s\n' "${pid}"
  uptime_value="$(ps -p "${pid}" -o etime= 2>/dev/null | awk '{$1=$1; print}' || true)"
  printf 'uptime: %s\n' "${uptime_value:-unavailable}"
else
  printf 'stopped\nPID: none\nuptime: unavailable\n'
fi

available_kb="$(df -Pk -- "${PROJECT_ROOT}" | awk 'NR==2 {print $4}')"
if [[ "${available_kb}" =~ ^[0-9]+$ ]]; then
  printf 'disk_available_kib: %s\n' "${available_kb}"
else
  printf 'disk_available_kib: unavailable\n'
fi

if database_path="$(sqlite_database_path 2>/dev/null)"; then
  if [[ -f "${database_path}" ]] && python_bin="$(venv_python 2>/dev/null)"; then
    database_state="$("${python_bin}" -c \
      'import sqlite3,sys; c=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True); print(c.execute("PRAGMA quick_check").fetchone()[0]); c.close()' \
      "${database_path}" 2>/dev/null || true)"
    [[ "${database_state}" == "ok" ]] && printf 'database: ok\n' || printf 'database: error\n'
  else
    printf 'database: missing\n'
  fi
else
  printf 'database: configured-remote\n'
fi

if python_bin="$(venv_python 2>/dev/null)" && \
  "${python_bin}" -c 'from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); x=Path(p.chromium.executable_path).is_file(); p.stop(); raise SystemExit(not x)' >/dev/null 2>&1; then
  printf 'browser: installed\n'
else
  printf 'browser: unavailable\n'
fi

session_path="$(absolute_project_path "$(read_env_value DIC_SESSION_STATE_PATH './var/session/dic_session.enc')")"
[[ -f "${session_path}" ]] && printf 'dic_session: present-encrypted\n' || printf 'dic_session: absent\n'
printf 'write_actions: %s\n' "$(read_env_bool ENABLE_WRITE_ACTIONS false)"

app_log="$(runtime_log_dir)/app.jsonl"
if [[ -s "${app_log}" ]] && python_bin="$(venv_python 2>/dev/null)"; then
  last_event="$("${python_bin}" -c '
import json, sys
try:
    lines = [line for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
    event = json.loads(lines[-1])
    safe = {key: event.get(key) for key in ("timestamp", "timestamp_utc", "level", "event", "event_type") if event.get(key) is not None}
    print(json.dumps(safe, ensure_ascii=True, separators=(",", ":")))
except Exception:
    print("unavailable")
' "${app_log}" 2>/dev/null || printf 'unavailable')"
  printf 'last_event: %s\n' "${last_event}"
else
  printf 'last_event: unavailable\n'
fi

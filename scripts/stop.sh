#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

force=false
timeout_seconds=30
while (($#)); do
  case "$1" in
    --force) force=true ;;
    --timeout)
      (($# >= 2)) || die "--timeout requires a number of seconds"
      timeout_seconds="$2"
      shift
      ;;
    -h | --help)
      printf 'Usage: %s [--force] [--timeout SECONDS]\n' "$0"
      exit 0
      ;;
    *) die "unknown stop option: $1" ;;
  esac
  shift
done
[[ "${timeout_seconds}" =~ ^[0-9]+$ ]] && ((timeout_seconds >= 1 && timeout_seconds <= 300)) \
  || die "timeout must be between 1 and 300 seconds"

require_project_root
pid_file="$(runtime_pid_file)"
lock_file="$(runtime_lock_file)"
if ! pid="$(read_pid 2>/dev/null)"; then
  info "BH-DiC is already stopped"
  [[ ! -e "${lock_file}" ]] || remove_project_file "${lock_file}"
  exit 0
fi

if ! process_is_running "${pid}"; then
  warn "removing stale lifecycle files for exited PID ${pid}"
  remove_project_file "${pid_file}"
  [[ ! -e "${lock_file}" ]] || remove_project_file "${lock_file}"
  exit 0
fi
process_is_bh_dic "${pid}" || die "PID ${pid} does not belong to BH-DiC; refusing to signal it"

info "sending SIGTERM to BH-DiC PID ${pid}"
kill -TERM "${pid}"
for ((elapsed = 0; elapsed < timeout_seconds; elapsed++)); do
  if ! process_is_running "${pid}"; then
    remove_project_file "${pid_file}"
    [[ ! -e "${lock_file}" ]] || remove_project_file "${lock_file}"
    info "BH-DiC stopped cleanly"
    exit 0
  fi
  sleep 1
done

if [[ "${force}" != "true" ]]; then
  die "BH-DiC did not stop within ${timeout_seconds}s; rerun with --force only after investigation"
fi
process_is_bh_dic "${pid}" || die "process identity changed; refusing forced termination"
warn "forcing termination of BH-DiC PID ${pid} after explicit --force"
kill -KILL "${pid}"
for _attempt in 1 2 3 4 5; do
  process_is_running "${pid}" || break
  sleep 1
done
process_is_running "${pid}" && die "forced termination did not stop PID ${pid}"
remove_project_file "${pid_file}"
[[ ! -e "${lock_file}" ]] || remove_project_file "${lock_file}"
info "BH-DiC stopped forcibly"

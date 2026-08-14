#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

if (($#)); then
  case "$1" in
    -h | --help)
      printf 'Usage: %s\nStarts one managed BH-DiC background process.\n' "$0"
      exit 0
      ;;
    *) die "start accepts no arguments" ;;
  esac
fi

require_project_root
validate_runtime_config >/dev/null
require_command flock
ensure_runtime_dirs
"${SCRIPT_DIR}/doctor.sh" --quiet

pid_file="$(runtime_pid_file)"
lock_file="$(runtime_lock_file)"
log_dir="$(runtime_log_dir)"
app_log="${log_dir}/app.jsonl"
error_log="${log_dir}/process-errors.log"

exec 9>"${lock_file}"
flock -n 9 || die "another lifecycle operation holds the BH-DiC lock"

if existing_pid="$(read_pid 2>/dev/null)"; then
  if process_is_running "${existing_pid}"; then
    if process_is_bh_dic "${existing_pid}"; then
      die "BH-DiC is already running with PID ${existing_pid}"
    fi
    die "PID file refers to a different live process; refusing to overwrite it"
  fi
  warn "removing stale PID file"
  remove_project_file "${pid_file}"
fi

python_bin="$(venv_python)"
info "starting BH-DiC in the background"
nohup "${python_bin}" -m bh_dic run >>"${app_log}" 2>>"${error_log}" </dev/null &
child_pid=$!
[[ "${child_pid}" =~ ^[0-9]+$ ]] && ((child_pid > 1)) || die "failed to obtain child PID"

pid_tmp="${pid_file}.tmp.${child_pid}"
printf '%s\n' "${child_pid}" >"${pid_tmp}"
chmod 600 -- "${pid_tmp}"
mv -f -- "${pid_tmp}" "${pid_file}"

for _attempt in 1 2 3 4 5; do
  if ! process_is_running "${child_pid}"; then
    remove_project_file "${pid_file}"
    die "BH-DiC exited during startup; inspect ${error_log}"
  fi
  sleep 1
done
process_is_bh_dic "${child_pid}" || {
  remove_project_file "${pid_file}"
  die "started PID does not identify a BH-DiC process"
}
info "BH-DiC is running with PID ${child_pid}"

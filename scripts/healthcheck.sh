#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

process_only=false
while (($#)); do
  case "$1" in
    --process-only) process_only=true ;;
    -h | --help)
      printf 'Usage: %s [--process-only]\n' "$0"
      exit 0
      ;;
    *) die "unknown healthcheck option: $1" ;;
  esac
  shift
done

require_project_root
pid="$(read_pid 2>/dev/null)" || die "BH-DiC PID file is absent"
process_is_running "${pid}" || die "BH-DiC PID is not running"
process_is_bh_dic "${pid}" || die "managed PID does not belong to BH-DiC"
if [[ "${process_only}" == "true" ]]; then
  printf '{"status":"ok","process":"running"}\n'
  exit 0
fi
validate_runtime_config >/dev/null
run_cli health

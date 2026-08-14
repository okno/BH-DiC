#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

if (($#)); then
  case "$1" in
    -h | --help)
      printf 'Usage: %s\nRuns BH-DiC in the foreground.\n' "$0"
      exit 0
      ;;
    *) die "run-foreground accepts no arguments" ;;
  esac
fi

require_project_root
validate_runtime_config >/dev/null
ensure_runtime_dirs
if bot_is_running; then
  die "BH-DiC already has an active managed process"
fi
python_bin="$(venv_python)"
info "starting BH-DiC in the foreground"
exec "${python_bin}" -m bh_dic run

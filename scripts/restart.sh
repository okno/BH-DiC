#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

stop_args=()
while (($#)); do
  case "$1" in
    --force) stop_args+=("--force") ;;
    --timeout)
      (($# >= 2)) || die "--timeout requires a number of seconds"
      stop_args+=("--timeout" "$2")
      shift
      ;;
    -h | --help)
      printf 'Usage: %s [--force] [--timeout SECONDS]\n' "$0"
      exit 0
      ;;
    *) die "unknown restart option: $1" ;;
  esac
  shift
done

require_project_root
"${SCRIPT_DIR}/stop.sh" "${stop_args[@]}"
"${SCRIPT_DIR}/start.sh"

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  printf 'Usage: %s {list|metadata UPLOAD_ID|scan UPLOAD_ID|purge-expired}\n' "$0"
}

(($# >= 1)) || { usage >&2; exit 1; }
command_name="$1"
shift
case "${command_name}" in
  list | purge-expired)
    (($# == 0)) || die "${command_name} accepts no additional arguments"
    ;;
  metadata | scan)
    (($# == 1)) || die "${command_name} requires exactly one upload ID"
    [[ "$1" =~ ^[a-f0-9]{32}$ ]] || die "upload ID must be 32 lowercase hexadecimal characters"
    ;;
  -h | --help) usage; exit 0 ;;
  *) die "unknown files command: ${command_name}" ;;
esac

require_project_root
validate_runtime_config >/dev/null
run_cli files "${command_name}" "$@"

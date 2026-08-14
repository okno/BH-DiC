#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

apply=false
while (($#)); do
  case "$1" in
    --apply) apply=true ;;
    -h | --help)
      printf 'Usage: %s [--apply]\nDefaults to a non-mutating dry run.\n' "$0"
      exit 0
      ;;
    *) die "unknown cleanup option: $1" ;;
  esac
  shift
done

require_project_root
validate_runtime_config >/dev/null
if [[ "${apply}" == "true" ]]; then
  info "applying the implemented expired-upload retention policy"
  run_cli files purge-expired
else
  info "cleanup preview; pass --apply to purge expired uploads"
  run_cli files list
fi

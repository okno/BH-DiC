#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"

require_project_root
require_secure_env
validate_runtime_config

if [[ "${BH_DIC_ENABLE_LIVE_READ_COVERAGE:-false}" != "true" ]]; then
  die "set BH_DIC_ENABLE_LIVE_READ_COVERAGE=true to authorize the read-only live gate"
fi

run_cli dic-read-coverage --live

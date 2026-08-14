#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

audit_timeout=180
while (($#)); do
  case "$1" in
    --audit-timeout)
      (($# >= 2)) || die "--audit-timeout requires seconds"
      audit_timeout="$2"
      shift
      ;;
    -h | --help)
      printf 'Usage: %s [--audit-timeout SECONDS]\n' "$0"
      exit 0
      ;;
    *) die "unknown security-check option: $1" ;;
  esac
  shift
done
[[ "${audit_timeout}" =~ ^[0-9]+$ ]] && ((audit_timeout >= 30 && audit_timeout <= 600)) \
  || die "audit timeout must be between 30 and 600 seconds"

require_project_root
require_venv
require_command timeout
require_command gitleaks
python_bin="$(venv_python)"

"${python_bin}" -m bandit -q -r "${PROJECT_ROOT}/src"
timeout "${audit_timeout}" "${python_bin}" -m pip_audit --skip-editable
gitleaks detect --source "${PROJECT_ROOT}" --no-banner --redact --exit-code 1
info "security checks completed"

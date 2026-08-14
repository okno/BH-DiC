#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

skip_browser=false
skip_tests=false
while (($#)); do
  case "$1" in
    --skip-browser) skip_browser=true ;;
    --skip-tests) skip_tests=true ;;
    -h | --help)
      printf 'Usage: %s [--skip-browser] [--skip-tests]\n' "$0"
      exit 0
      ;;
    *) die "unknown install option: $1" ;;
  esac
  shift
done

require_command uname
require_command git
require_command tar
info "platform: $(uname -s) $(uname -m)"

python_source=""
for candidate in python3.14 python3.13 python3.12 python3; do
  if command -v "${candidate}" >/dev/null 2>&1 && \
    "${candidate}" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' 2>/dev/null; then
    python_source="$(command -v "${candidate}")"
    break
  fi
done
[[ -n "${python_source}" ]] || die "Python 3.12 or newer is required"

if ! venv_python >/dev/null 2>&1; then
  info "creating isolated virtual environment"
  "${python_source}" -m venv "${PROJECT_ROOT}/.venv"
fi
python_bin="$(venv_python)"
"${python_bin}" -m pip install --requirement "${PROJECT_ROOT}/requirements.lock"
"${python_bin}" -m pip install --no-deps --editable "${PROJECT_ROOT}"

ensure_runtime_dirs
chmod 700 -- "${SCRIPT_DIR}"/*.sh

if [[ "${skip_browser}" == "false" ]]; then
  "${SCRIPT_DIR}/browser-install.sh"
fi

database_url="$(read_env_value DATABASE_URL 'sqlite+aiosqlite:///./var/db/bh_dic.sqlite3')"
DATABASE_URL="${database_url}" "${python_bin}" -m alembic \
  -c "${PROJECT_ROOT}/migrations/alembic.ini" upgrade head

if command -v clamdscan >/dev/null 2>&1 || command -v clamd >/dev/null 2>&1; then
  info "ClamAV detected"
else
  warn "ClamAV not detected; document upload will remain unavailable"
fi

if [[ "${skip_tests}" == "false" ]]; then
  "${SCRIPT_DIR}/run-tests.sh"
fi

if [[ -f "${ENV_FILE}" ]]; then
  info "existing .env preserved"
else
  info ".env was not created; run scripts/init-config.sh when ready to configure secrets"
fi
info "installation complete; BH-DiC was not started"

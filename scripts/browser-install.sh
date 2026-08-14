#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

with_deps=false
while (($#)); do
  case "$1" in
    --with-deps) with_deps=true ;;
    -h | --help)
      printf 'Usage: %s [--with-deps]\n' "$0"
      exit 0
      ;;
    *) die "unknown browser-install option: $1" ;;
  esac
  shift
done

require_venv
python_bin="$(venv_python)"
if [[ "${with_deps}" == "true" ]]; then
  info "installing Chromium and required operating-system packages"
  "${python_bin}" -m playwright install --with-deps chromium
else
  info "installing Playwright Chromium for the current user"
  "${python_bin}" -m playwright install chromium
fi
info "Chromium installation complete; no browser process was started"

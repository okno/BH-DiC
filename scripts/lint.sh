#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

if (($#)); then
  case "$1" in
    -h | --help)
      printf 'Usage: %s\nRuns formatting, lint, and strict type checks.\n' "$0"
      exit 0
      ;;
    *) die "lint accepts no arguments" ;;
  esac
fi

require_project_root
require_venv
python_bin="$(venv_python)"
"${python_bin}" -m ruff format --check .
"${python_bin}" -m ruff check .
"${python_bin}" -m mypy src tests

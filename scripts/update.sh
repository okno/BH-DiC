#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

restart_after=false
skip_tests=false
while (($#)); do
  case "$1" in
    --restart) restart_after=true ;;
    --skip-tests) skip_tests=true ;;
    -h | --help)
      printf 'Usage: %s [--restart] [--skip-tests]\n' "$0"
      exit 0
      ;;
    *) die "unknown update option: $1" ;;
  esac
  shift
done

require_project_root
require_command git
require_venv

was_running=false
if bot_is_running; then
  if [[ "${restart_after}" != "true" ]]; then
    die "BH-DiC is running; rerun with --restart to permit a controlled restart"
  fi
  was_running=true
  "${SCRIPT_DIR}/stop.sh"
fi

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  die "working tree is not clean; update aborted before fetching"
fi
branch="$(git symbolic-ref --quiet --short HEAD)" || die "detached HEAD is not supported"
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)" \
  || die "branch ${branch} has no configured upstream"

info "creating a pre-update backup"
"${SCRIPT_DIR}/backup.sh"

remote="${upstream%%/*}"
require_command timeout
timeout 60 git fetch --prune -- "${remote}"
behind="$(git rev-list --count "HEAD..${upstream}")"
ahead="$(git rev-list --count "${upstream}..HEAD")"
if ((ahead > 0 && behind > 0)); then
  die "local and upstream histories diverged; refusing an automatic update"
fi
if ((behind > 0)); then
  git merge --ff-only -- "${upstream}"
  info "fast-forwarded ${branch} by ${behind} commit(s)"
else
  info "repository is already current"
fi

python_bin="$(venv_python)"
"${python_bin}" -m pip install --requirement "${PROJECT_ROOT}/requirements.lock"
"${python_bin}" -m pip install --no-deps --editable "${PROJECT_ROOT}"
database_url="$(read_env_value DATABASE_URL 'sqlite+aiosqlite:///./var/db/bh_dic.sqlite3')"
DATABASE_URL="${database_url}" "${python_bin}" -m alembic \
  -c "${PROJECT_ROOT}/migrations/alembic.ini" upgrade head

if [[ "${skip_tests}" == "false" ]]; then
  "${SCRIPT_DIR}/run-tests.sh"
fi

if [[ "${was_running}" == "true" ]]; then
  "${SCRIPT_DIR}/start.sh"
fi
info "update completed"

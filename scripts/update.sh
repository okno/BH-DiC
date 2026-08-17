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
update_uid="$(effective_user_id)"
require_non_root_update_user "${update_uid}"
require_command git
require_command find
require_venv

require_systemd_safe_for_update
project_tree_is_owned_and_readable "${update_uid}" \
  || die "project ownership/readability preflight failed; repair it before updating as the service/repository owner"
[[ -w "${PROJECT_ROOT}" && -w "${PROJECT_ROOT}/.git" && -w "${PROJECT_ROOT}/.venv" ]] \
  || die "project write-access preflight failed for the service/repository owner"

python_bin="$(venv_python)"
"${python_bin}" -m pip check >/dev/null 2>&1 \
  || die "virtual-environment dependency/readability preflight failed"
"${python_bin}" -c 'import bh_dic' >/dev/null 2>&1 \
  || die "BH-DiC import/readability preflight failed"

git_status="$(git status --porcelain --untracked-files=normal)" \
  || die "unable to inspect the working tree; update aborted before stopping BH-DiC"
if [[ -n "${git_status}" ]]; then
  die "working tree is not clean; update aborted before stopping BH-DiC"
fi
branch="$(git symbolic-ref --quiet --short HEAD)" || die "detached HEAD is not supported"
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)" \
  || die "branch ${branch} has no configured upstream"
behind="$(git rev-list --count "HEAD..${upstream}")"
ahead="$(git rev-list --count "${upstream}..HEAD")"
[[ "${behind}" =~ ^[0-9]+$ && "${ahead}" =~ ^[0-9]+$ ]] \
  || die "git divergence preflight returned invalid counts"
if ((ahead > 0 && behind > 0)); then
  die "local and upstream histories diverged; update aborted before stopping BH-DiC"
fi

pid_file="$(runtime_pid_file)"
managed_pid=""
was_running=false
if [[ -e "${pid_file}" ]]; then
  [[ -f "${pid_file}" && ! -L "${pid_file}" && -r "${pid_file}" ]] \
    || die "PID lifecycle state is unsafe; inspect it and run stop.sh before updating"
  managed_pid="$(read_pid 2>/dev/null)" \
    || die "PID lifecycle state is invalid; inspect it and run stop.sh before updating"
  if ! process_is_running "${managed_pid}" || ! process_is_bh_dic "${managed_pid}"; then
    die "PID lifecycle state is stale or belongs to another process; inspect it and run stop.sh before updating"
  fi
  if [[ "${restart_after}" != "true" ]]; then
    die "BH-DiC is running; rerun with --restart to permit a controlled PID-mode restart"
  fi
  was_running=true
fi

remote="${upstream%%/*}"
require_command timeout
timeout 60 git fetch --prune -- "${remote}"
behind="$(git rev-list --count "HEAD..${upstream}")"
ahead="$(git rev-list --count "${upstream}..HEAD")"
[[ "${behind}" =~ ^[0-9]+$ && "${ahead}" =~ ^[0-9]+$ ]] \
  || die "git divergence check returned invalid counts"
if ((ahead > 0 && behind > 0)); then
  die "local and upstream histories diverged after fetch; update aborted before stopping BH-DiC"
fi

if [[ "${was_running}" == "true" ]]; then
  current_pid="$(read_pid 2>/dev/null)" \
    || die "PID lifecycle state changed before stop; update aborted"
  [[ "${current_pid}" == "${managed_pid}" ]] \
    && process_is_running "${managed_pid}" \
    && process_is_bh_dic "${managed_pid}" \
    || die "PID lifecycle state changed before stop; update aborted"
  "${SCRIPT_DIR}/stop.sh"
  if process_is_running "${managed_pid}" || read_pid >/dev/null 2>&1 || bot_is_running; then
    die "PID-managed BH-DiC did not reach a verified stopped state; update aborted"
  fi
elif [[ -e "${pid_file}" ]]; then
  die "PID lifecycle state appeared during preflight; update aborted before backup"
fi

require_systemd_safe_for_update

info "creating a pre-update backup"
"${SCRIPT_DIR}/backup.sh"

if ((behind > 0)); then
  git merge --ff-only -- "${upstream}"
  info "fast-forwarded ${branch} by ${behind} commit(s)"
else
  info "repository is already current"
fi

"${python_bin}" -m pip install --requirement "${PROJECT_ROOT}/requirements.lock"
"${python_bin}" -m pip install --no-deps --editable "${PROJECT_ROOT}"
"${python_bin}" -m pip check >/dev/null 2>&1 \
  || die "post-install dependency/readability verification failed"
"${python_bin}" -c \
  'from importlib.metadata import version; from bh_dic import __version__; raise SystemExit(version("bh-dic") != __version__)' \
  >/dev/null 2>&1 || die "post-install BH-DiC import/version verification failed"
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

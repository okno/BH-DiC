#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "${SCRIPT_LIB_DIR}/.." && pwd -P)"
readonly SCRIPT_LIB_DIR PROJECT_ROOT
readonly ENV_FILE="${PROJECT_ROOT}/.env"
readonly ENV_EXAMPLE_FILE="${PROJECT_ROOT}/.env.example"
readonly SYSTEMD_SERVICE_UNIT="bh-dic.service"

cd -- "${PROJECT_ROOT}"

QUIET="${QUIET:-false}"

info() {
  if [[ "${QUIET}" != "true" ]]; then
    printf 'INFO: %s\n' "$*"
  fi
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v -- "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

require_project_root() {
  [[ -f "${PROJECT_ROOT}/pyproject.toml" ]] || die "invalid BH-DiC project root"
  [[ -d "${PROJECT_ROOT}/src/bh_dic" ]] || die "BH-DiC source package is missing"
}

absolute_project_path() {
  local raw_path="${1:?path is required}"
  local candidate resolved
  if [[ "${raw_path}" == /* ]]; then
    candidate="${raw_path}"
  else
    candidate="${PROJECT_ROOT}/${raw_path#./}"
  fi
  if command -v realpath >/dev/null 2>&1; then
    resolved="$(realpath -m -- "${candidate}")"
  elif command -v python3 >/dev/null 2>&1; then
    resolved="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "${candidate}")"
  else
    die "realpath or python3 is required to validate paths"
  fi
  case "${resolved}" in
    "${PROJECT_ROOT}" | "${PROJECT_ROOT}"/*) printf '%s\n' "${resolved}" ;;
    *) die "configured path escapes the BH-DiC project root" ;;
  esac
}

read_env_value() {
  local key="${1:?environment key is required}"
  local default_value="${2-}"
  if [[ ! -f "${ENV_FILE}" ]]; then
    printf '%s\n' "${default_value}"
    return 0
  fi
  local value
  value="$(awk -v wanted="${key}" '
    $0 ~ "^[[:space:]]*" wanted "[[:space:]]*=" {
      sub("^[[:space:]]*" wanted "[[:space:]]*=[[:space:]]*", "")
      sub("[[:space:]]+#.*$", "")
      sub("\\r$", "")
      if (($0 ~ /^".*"$/) || ($0 ~ /^\047.*\047$/)) {
        $0 = substr($0, 2, length($0) - 2)
      }
      print
      exit
    }
  ' "${ENV_FILE}")"
  printf '%s\n' "${value:-${default_value}}"
}

read_env_bool() {
  local value
  value="$(read_env_value "$1" "${2:-false}")"
  value="${value,,}"
  case "${value}" in
    true | 1 | yes | on) printf 'true\n' ;;
    false | 0 | no | off | '') printf 'false\n' ;;
    *) die "invalid boolean value for $1" ;;
  esac
}

venv_python() {
  local unix_python="${PROJECT_ROOT}/.venv/bin/python"
  local windows_python="${PROJECT_ROOT}/.venv/Scripts/python.exe"
  if [[ -x "${unix_python}" ]]; then
    printf '%s\n' "${unix_python}"
  elif [[ -x "${windows_python}" ]]; then
    printf '%s\n' "${windows_python}"
  else
    return 1
  fi
}

require_venv() {
  venv_python >/dev/null 2>&1 || die "virtual environment missing; run scripts/install.sh"
}

env_file_mode() {
  stat -c '%a' -- "${ENV_FILE}" 2>/dev/null || stat -f '%Lp' -- "${ENV_FILE}"
}

require_secure_env() {
  [[ -f "${ENV_FILE}" ]] || die ".env is missing; run scripts/init-config.sh and configure it"
  local mode
  mode="$(env_file_mode)"
  [[ "${mode}" == "600" ]] || die ".env permissions must be 0600 (found ${mode})"
}

validate_runtime_config() {
  require_venv
  require_secure_env
  local python_bin
  python_bin="$(venv_python)"
  "${python_bin}" -c 'from bh_dic.config import AppSettings; AppSettings(); print("configuration: valid")'
}

run_cli() {
  require_venv
  local python_bin
  python_bin="$(venv_python)"
  "${python_bin}" -m bh_dic "$@"
}

runtime_data_dir() {
  absolute_project_path "$(read_env_value DATA_DIR './var')"
}

runtime_log_dir() {
  absolute_project_path "$(read_env_value LOG_DIR './var/log')"
}

runtime_pid_file() {
  absolute_project_path "$(read_env_value PID_FILE './var/run/bh-dic.pid')"
}

runtime_lock_file() {
  absolute_project_path "$(read_env_value LOCK_FILE './var/run/bh-dic.lock')"
}

sqlite_database_path() {
  local database_url raw_path
  database_url="$(read_env_value DATABASE_URL 'sqlite+aiosqlite:///./var/db/bh_dic.sqlite3')"
  case "${database_url}" in
    sqlite+aiosqlite:///*)
      raw_path="${database_url#sqlite+aiosqlite:///}"
      absolute_project_path "${raw_path}"
      ;;
    *) return 1 ;;
  esac
}

ensure_runtime_dirs() {
  local data_dir log_dir pid_file lock_file
  data_dir="$(runtime_data_dir)"
  log_dir="$(runtime_log_dir)"
  pid_file="$(runtime_pid_file)"
  lock_file="$(runtime_lock_file)"
  mkdir -p -- \
    "${data_dir}/db" \
    "${log_dir}" \
    "$(dirname -- "${pid_file}")" \
    "$(dirname -- "${lock_file}")" \
    "${data_dir}/session" \
    "${data_dir}/backups" \
    "${data_dir}/traces" \
    "${data_dir}/uploads/quarantine"
  chmod 700 -- \
    "${data_dir}" \
    "${data_dir}/db" \
    "${log_dir}" \
    "$(dirname -- "${pid_file}")" \
    "${data_dir}/session" \
    "${data_dir}/backups" \
    "${data_dir}/traces" \
    "${data_dir}/uploads" \
    "${data_dir}/uploads/quarantine"
}

read_pid() {
  local pid_file pid
  pid_file="$(runtime_pid_file)"
  [[ -f "${pid_file}" ]] || return 1
  IFS= read -r pid <"${pid_file}" || return 1
  [[ "${pid}" =~ ^[0-9]+$ ]] && ((pid > 1)) || die "invalid PID file; inspect ${pid_file}"
  printf '%s\n' "${pid}"
}

process_is_running() {
  local pid="${1:?PID is required}"
  kill -0 "${pid}" 2>/dev/null
}

process_is_bh_dic() {
  local pid="${1:?PID is required}"
  local command_line
  command_line="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  if [[ -z "${command_line}" ]]; then
    # Git Bash ships a compact ps without POSIX output selectors. The full row
    # still provides a safe process-identity fallback for local lifecycle tests.
    command_line="$(ps -f -p "${pid}" 2>/dev/null | awk 'NR == 2 {print}' || true)"
  fi
  [[ -n "${command_line}" ]] || return 1
  [[ "${command_line}" == *"bh_dic"* || "${command_line}" == *"${PROJECT_ROOT}"* ]]
}

bot_is_running() {
  local pid
  pid="$(read_pid 2>/dev/null)" || return 1
  process_is_running "${pid}" && process_is_bh_dic "${pid}"
}

effective_user_id() {
  printf '%s\n' "${EUID}"
}

require_non_root_update_user() {
  local effective_uid="${1:?effective user ID is required}"
  [[ "${effective_uid}" =~ ^[0-9]+$ ]] || die "unable to verify the update user"
  ((effective_uid != 0)) \
    || die "update must not run as root; stop systemd externally and run it as the service/repository owner (normally bh-dic)"
}

systemctl_is_available() {
  command -v systemctl >/dev/null 2>&1
}

systemd_runtime_is_present() {
  [[ -d /run/systemd/system ]]
}

systemd_service_update_state() {
  if ! systemctl_is_available; then
    ! systemd_runtime_is_present || return 2
    printf 'not-found\n'
    return 0
  fi

  local properties key value
  local load_state=""
  local active_state=""
  local load_seen=false
  local active_seen=false
  local property_count=0
  properties="$(LC_ALL=C systemctl show --no-pager \
    --property=LoadState --property=ActiveState \
    "${SYSTEMD_SERVICE_UNIT}" 2>/dev/null)" || return 2

  while IFS='=' read -r key value; do
    case "${key}" in
      LoadState)
        [[ "${load_seen}" == "false" ]] || return 2
        load_seen=true
        load_state="${value}"
        ;;
      ActiveState)
        [[ "${active_seen}" == "false" ]] || return 2
        active_seen=true
        active_state="${value}"
        ;;
      *) return 2 ;;
    esac
    ((property_count += 1))
  done <<<"${properties}"

  [[ "${property_count}" == "2" ]] || return 2
  case "${load_state}:${active_state}" in
    not-found:inactive) printf 'not-found\n' ;;
    loaded:inactive) printf 'inactive\n' ;;
    *) return 1 ;;
  esac
}

require_systemd_safe_for_update() {
  if ! systemd_service_update_state >/dev/null; then
    die "cannot prove ${SYSTEMD_SERVICE_UNIT} is absent or loaded and inactive; stop it externally and verify systemd before updating"
  fi
}

project_tree_is_owned_and_readable() {
  local expected_uid="${1:?expected owner ID is required}"
  local violation
  [[ "${expected_uid}" =~ ^[0-9]+$ ]] || return 1
  violation="$(find "${PROJECT_ROOT}" -xdev \
    \( ! -uid "${expected_uid}" -o ! -readable \) -print -quit 2>/dev/null)" || return 1
  [[ -z "${violation}" ]]
}

require_bot_stopped() {
  if bot_is_running; then
    die "BH-DiC is running; stop it before this operation"
  fi
}

remove_project_file() {
  local target resolved
  target="${1:?target file is required}"
  resolved="$(absolute_project_path "${target}")"
  [[ "${resolved}" != "${PROJECT_ROOT}" ]] || die "refusing to remove the project root"
  rm -f -- "${resolved}"
}

remove_project_tree() {
  local target required_parent resolved parent
  target="${1:?target directory is required}"
  required_parent="${2:?required parent is required}"
  resolved="$(absolute_project_path "${target}")"
  parent="$(absolute_project_path "${required_parent}")"
  [[ "${resolved}" == "${parent}"/* ]] || die "refusing to remove directory outside expected parent"
  [[ "${resolved}" != "${parent}" ]] || die "refusing to remove the expected parent itself"
  rm -rf -- "${resolved}"
}

require_project_root

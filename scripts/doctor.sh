#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

online=false
while (($#)); do
  case "$1" in
    --online) online=true ;;
    --quiet) QUIET=true ;;
    -h | --help)
      printf 'Usage: %s [--online] [--quiet]\n' "$0"
      exit 0
      ;;
    *) die "unknown doctor option: $1" ;;
  esac
  shift
done

failures=0
pass() { info "PASS: $*"; }
fail() { warn "FAIL: $*"; failures=$((failures + 1)); }
python_bin=""
runtime_config_valid=false

if [[ "$(uname -s)" == "Linux" ]]; then pass "Linux operating system"; else fail "Linux is required for deployment"; fi
if command -v git >/dev/null 2>&1; then pass "git available"; else fail "git unavailable"; fi
if command -v flock >/dev/null 2>&1; then pass "flock available"; else fail "flock unavailable"; fi
if venv_python >/dev/null 2>&1; then
  python_bin="$(venv_python)"
  if "${python_bin}" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
    pass "Python runtime is 3.12 or newer"
  else
    fail "Python runtime is older than 3.12"
  fi
  if "${python_bin}" -m pip check >/dev/null 2>&1; then pass "Python dependencies consistent"; else fail "Python dependency check failed"; fi
  database_url="$(read_env_value DATABASE_URL 'sqlite+aiosqlite:///./var/db/bh_dic.sqlite3')"
  case "${database_url}" in
    sqlite+aiosqlite:///*)
      if DATABASE_URL="${database_url}" "${python_bin}" -m alembic \
        -c "${PROJECT_ROOT}/migrations/alembic.ini" current --check-heads >/dev/null 2>&1; then
        pass "database migration is at the current head"
      else
        fail "database migration is missing or not at the current head"
      fi
      ;;
    *) info "non-SQLite database migration state is UNVERIFIED; doctor performs no database network I/O" ;;
  esac
  if "${python_bin}" -c 'from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); path=Path(p.chromium.executable_path); p.stop(); raise SystemExit(not path.is_file())' >/dev/null 2>&1; then
    pass "Playwright Chromium installed"
  else
    fail "Playwright Chromium unavailable"
  fi
else
  fail "virtual environment unavailable"
fi

if [[ -f "${ENV_FILE}" ]]; then
  if [[ "$(env_file_mode)" == "600" ]]; then pass ".env mode is 0600"; else fail ".env mode is not 0600"; fi
  if validate_runtime_config >/dev/null 2>&1; then
    runtime_config_valid=true
    pass "runtime configuration valid"
  else
    fail "runtime configuration incomplete or unsafe"
  fi
else
  fail ".env missing"
fi

data_dir="$(runtime_data_dir)"
if [[ -d "${data_dir}" && -w "${data_dir}" ]]; then pass "runtime data directory writable"; else fail "runtime data directory unavailable"; fi
for directory in \
  "${data_dir}/db" \
  "$(runtime_log_dir)" \
  "$(dirname -- "$(runtime_pid_file)")" \
  "${data_dir}/session" \
  "${data_dir}/backups" \
  "${data_dir}/traces" \
  "${data_dir}/uploads"; do
  if [[ ! -d "${directory}" ]]; then
    fail "required runtime directory is missing: ${directory}"
  elif [[ "$(stat -c '%a' -- "${directory}" 2>/dev/null || stat -f '%Lp' -- "${directory}")" == "700" ]]; then
    pass "runtime directory mode is 0700: ${directory}"
  else
    fail "runtime directory mode must be 0700: ${directory}"
  fi
done
if database_path="$(sqlite_database_path 2>/dev/null)"; then
  if [[ -f "${database_path}" ]]; then pass "SQLite database present"; else fail "SQLite database missing"; fi
else
  pass "non-SQLite database configured"
fi

available_kb="$(df -Pk -- "${PROJECT_ROOT}" | awk 'NR==2 {print $4}')"
if [[ "${available_kb}" =~ ^[0-9]+$ ]] && ((available_kb >= 1048576)); then
  pass "at least 1 GiB disk space available"
else
  fail "less than 1 GiB disk space available"
fi

if [[ "$(read_env_bool CLAMAV_REQUIRED true)" == "true" ]]; then
  if command -v clamdscan >/dev/null 2>&1 || command -v clamd >/dev/null 2>&1; then pass "ClamAV available"; else fail "ClamAV required but unavailable"; fi
else
  pass "ClamAV requirement disabled (document upload must remain disabled)"
fi

if [[ "$(read_env_bool ENABLE_WRITE_ACTIONS false)" == "true" ]]; then
  warn "WRITE ACTIONS ARE ENABLED; verify approvals and individual feature flags before startup"
else
  pass "write-action master switch is disabled"
fi

if [[ "${online}" == "true" && "${runtime_config_valid}" != "true" ]]; then
  fail "online checks skipped because runtime configuration is invalid"
elif [[ "${online}" == "true" ]]; then
  require_command getent
  require_command curl
  provider_metadata="$("${python_bin}" -c '
from urllib.parse import urlsplit
from bh_dic.config import AppSettings
from bh_dic.openai.providers import GROQ_OPENAI_BASE_URL, OPENAI_RESPONSES_BASE_URL

settings = AppSettings()
provider_url = {
    "openai": OPENAI_RESPONSES_BASE_URL,
    "groq": GROQ_OPENAI_BASE_URL,
    "llama": settings.llama_base_url,
}[settings.model_provider]
parsed = urlsplit(provider_url)
print(settings.model_provider)
print(parsed.scheme)
print(parsed.hostname or "")
print(provider_url)
' 2>/dev/null)" || die "validated model provider metadata could not be loaded"
  mapfile -t provider_parts <<<"${provider_metadata}"
  model_provider="${provider_parts[0]-}"
  provider_scheme="${provider_parts[1]-}"
  provider_host="${provider_parts[2]-}"
  provider_url="${provider_parts[3]-}"
  if [[ -z "${model_provider}" || -z "${provider_scheme}" || -z "${provider_host}" || -z "${provider_url}" ]]; then
    die "validated model provider metadata is incomplete"
  fi
  for host in discord.com "${provider_host}" secure.dipendentincloud.it; do
    if getent ahosts "${host}" >/dev/null 2>&1; then pass "DNS resolves ${host}"; else fail "DNS failed for ${host}"; fi
  done
  for entry in \
    "Discord|https|https://discord.com" \
    "selected model provider|${provider_scheme}|${provider_url}" \
    "Dipendenti in Cloud|https|https://secure.dipendentincloud.it"; do
    IFS='|' read -r label scheme url <<<"${entry}"
    curl_args=(--silent --show-error --head --max-time 10 --proto "=${scheme}")
    if [[ "${scheme}" == "https" ]]; then curl_args+=(--tlsv1.2); fi
    if http_code="$(curl "${curl_args[@]}" --output /dev/null --write-out '%{http_code}' "${url}" 2>/dev/null)" && \
      [[ "${http_code}" =~ ^[1-5][0-9][0-9]$ ]]; then
      pass "HTTP endpoint reachable: ${label}"
    else
      fail "HTTP connectivity check failed: ${label}"
    fi
  done
else
  info "online DNS/HTTPS checks skipped; use --online explicitly"
fi

if ((failures > 0)); then
  die "doctor found ${failures} failed check(s)"
fi
info "doctor completed successfully"

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

include_logs=false
while (($#)); do
  case "$1" in
    --include-logs) include_logs=true ;;
    -h | --help)
      printf 'Usage: %s [--include-logs]\n' "$0"
      exit 0
      ;;
    *) die "unknown backup option: $1" ;;
  esac
  shift
done

require_project_root
require_command tar
require_command sha256sum
validate_runtime_config >/dev/null
ensure_runtime_dirs

database_path="$(sqlite_database_path 2>/dev/null)" \
  || die "this backup implementation supports only local SQLite deployments"
[[ -f "${database_path}" && ! -L "${database_path}" ]] || die "SQLite database is missing or unsafe"
backup_dir="$(runtime_data_dir)/backups"
mkdir -p -- "${backup_dir}"
chmod 700 -- "${backup_dir}"
stage="$(mktemp -d "${backup_dir}/.backup.XXXXXXXX")"
chmod 700 -- "${stage}"
cleanup_stage() {
  if [[ -n "${stage:-}" && -d "${stage}" ]]; then
    remove_project_tree "${stage}" "${backup_dir}"
  fi
}
trap cleanup_stage EXIT

python_bin="$(venv_python)"
"${python_bin}" -c '
import sqlite3, sys
source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
    check = target.execute("PRAGMA quick_check").fetchone()
    if check is None or check[0] != "ok":
        raise SystemExit("SQLite integrity check failed")
finally:
    target.close()
    source.close()
' "${database_path}" "${stage}/database.sqlite3"
chmod 600 -- "${stage}/database.sqlite3"

audit_database_url="sqlite+aiosqlite:///${stage}/database.sqlite3"
if ! DATABASE_URL="${audit_database_url}" "${SCRIPT_DIR}/audit-verify.sh" \
  >"${stage}/audit-verification.json"; then
  warn "the backup was captured, but its audit chain did not verify"
fi
chmod 600 -- "${stage}/audit-verification.json"

"${python_bin}" - "${stage}/configuration-safe.json" <<'PY'
import json
import sys

from bh_dic.config import AppSettings

target = sys.argv[1]
with open(target, "x", encoding="utf-8") as stream:
    json.dump(AppSettings().safe_summary(), stream, ensure_ascii=True, separators=(",", ":"))
    stream.write("\n")
PY
chmod 600 -- "${stage}/configuration-safe.json"

mkdir -p -- "${stage}/config"
for name in policies.yaml redaction.yaml policies.example.yaml redaction.example.yaml; do
  source_file="${PROJECT_ROOT}/config/${name}"
  if [[ -f "${source_file}" && ! -L "${source_file}" ]]; then
    if grep -Eiq '(BEGIN [A-Z ]*PRIVATE KEY|Bearer[[:space:]]+[A-Za-z0-9._~-]{16,}|sk-[A-Za-z0-9_-]{16,})' "${source_file}"; then
      die "credential-like material detected in config/${name}; backup aborted"
    fi
    cp -- "${source_file}" "${stage}/config/${name}"
    chmod 600 -- "${stage}/config/${name}"
  fi
done

if [[ "${include_logs}" == "true" ]]; then
  mkdir -p -- "${stage}/logs"
  for name in app discord openai browser audit security; do
    source_file="$(runtime_log_dir)/${name}.jsonl"
    [[ -f "${source_file}" && ! -L "${source_file}" ]] || continue
    "${SCRIPT_DIR}/logs.sh" "${name}" >"${stage}/logs/${name}.jsonl"
    if grep -Eiq '(Bearer[[:space:]]+[A-Za-z0-9._~-]{16,}|sk-[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{16,}|[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}|BEGIN [A-Z ]*PRIVATE KEY)' "${stage}/logs/${name}.jsonl"; then
      die "credential-like material detected in ${name} log; backup aborted"
    fi
    chmod 600 -- "${stage}/logs/${name}.jsonl"
  done
fi

timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
"${python_bin}" - "${stage}/backup-metadata.json" "${timestamp}" "${include_logs}" <<'PY'
import json
import sys

target, timestamp, include_logs = sys.argv[1:]
metadata = {
    "format": 1,
    "created_at_utc": timestamp,
    "database": "sqlite",
    "audit_in_database": True,
    "logs_included": include_logs == "true",
    "environment_file_included": False,
    "browser_session_included": False,
    "uploads_included": False,
}
with open(target, "x", encoding="utf-8") as stream:
    json.dump(metadata, stream, ensure_ascii=True, separators=(",", ":"))
    stream.write("\n")
PY
chmod 600 -- "${stage}/backup-metadata.json"

(
  cd -- "${stage}"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum >SHA256SUMS
)
chmod 600 -- "${stage}/SHA256SUMS"

archive_timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
archive="${backup_dir}/bh-dic-backup-${archive_timestamp}.tar.gz"
[[ ! -e "${archive}" ]] || die "backup archive already exists: ${archive}"
tar -C "${stage}" -czf "${archive}" .
chmod 600 -- "${archive}"
info "backup created: ${archive}"

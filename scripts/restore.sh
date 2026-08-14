#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

archive=""
confirmation=""
while (($#)); do
  case "$1" in
    --confirm)
      (($# >= 2)) || die "--confirm requires the literal value RESTORE"
      confirmation="$2"
      shift
      ;;
    -h | --help)
      printf 'Usage: %s BACKUP.tar.gz --confirm RESTORE\n' "$0"
      exit 0
      ;;
    -*) die "unknown restore option: $1" ;;
    *)
      [[ -z "${archive}" ]] || die "only one backup archive may be restored"
      archive="$1"
      ;;
  esac
  shift
done
[[ -n "${archive}" ]] || die "a backup archive is required"
[[ "${confirmation}" == "RESTORE" ]] || die "restore requires --confirm RESTORE"

require_project_root
require_command tar
require_command sha256sum
validate_runtime_config >/dev/null
require_bot_stopped
ensure_runtime_dirs

backup_dir="$(runtime_data_dir)/backups"
archive="$(absolute_project_path "${archive}")"
[[ "${archive}" == "${backup_dir}"/* ]] || die "archive must be inside the configured backup directory"
[[ -f "${archive}" && ! -L "${archive}" ]] || die "backup archive is missing or unsafe"
case "${archive}" in
  *.tar.gz) ;;
  *) die "backup archive must use the .tar.gz extension" ;;
esac

python_bin="$(venv_python)"
"${python_bin}" - "${archive}" <<'PY'
import pathlib
import sys
import tarfile

allowed_files = {
    "database.sqlite3",
    "audit-verification.json",
    "configuration-safe.json",
    "backup-metadata.json",
    "SHA256SUMS",
    "config/policies.yaml",
    "config/redaction.yaml",
    "config/policies.example.yaml",
    "config/redaction.example.yaml",
    "logs/app.jsonl",
    "logs/discord.jsonl",
    "logs/openai.jsonl",
    "logs/browser.jsonl",
    "logs/audit.jsonl",
    "logs/security.jsonl",
}
total_size = 0
seen_files = set()
with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        name = member.name
        while name.startswith("./"):
            name = name[2:]
        if not name and member.isdir():
            continue
        path = pathlib.PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts:
            raise SystemExit("unsafe archive path")
        if not (member.isdir() or member.isfile()):
            raise SystemExit("links and special archive members are forbidden")
        if member.isfile() and name not in allowed_files:
            raise SystemExit(f"unexpected archive member: {name}")
        if member.isfile() and name in seen_files:
            raise SystemExit(f"duplicate archive member: {name}")
        if member.isfile():
            seen_files.add(name)
        total_size += member.size
        if member.size > 2 * 1024**3 or total_size > 3 * 1024**3:
            raise SystemExit("archive exceeds the restore size limit")
PY

stage="$(mktemp -d "${backup_dir}/.restore.XXXXXXXX")"
chmod 700 -- "${stage}"
cleanup_stage() {
  if [[ -n "${stage:-}" && -d "${stage}" ]]; then
    remove_project_tree "${stage}" "${backup_dir}"
  fi
}
trap cleanup_stage EXIT
tar -C "${stage}" -xzf "${archive}" --no-same-owner --no-same-permissions
[[ -f "${stage}/SHA256SUMS" && -f "${stage}/database.sqlite3" ]] \
  || die "backup archive is incomplete"
"${python_bin}" - "${stage}" <<'PY'
import pathlib
import re
import sys

stage = pathlib.Path(sys.argv[1]).resolve()
expected = {
    path.relative_to(stage).as_posix()
    for path in stage.rglob("*")
    if path.is_file() and path.name != "SHA256SUMS"
}
listed = set()
for line in (stage / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
    match = re.fullmatch(r"[a-f0-9]{64}  (?:\./)?(.+)", line)
    if match is None:
        raise SystemExit("invalid checksum manifest")
    name = match.group(1)
    path = pathlib.PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or name in listed:
        raise SystemExit("unsafe checksum manifest path")
    listed.add(name)
if listed != expected:
    raise SystemExit("checksum manifest does not cover exactly the restored files")
PY
(
  cd -- "${stage}"
  sha256sum --check --strict SHA256SUMS
)

incoming_database_url="sqlite+aiosqlite:///${stage}/database.sqlite3"
DATABASE_URL="${incoming_database_url}" "${SCRIPT_DIR}/audit-verify.sh" >/dev/null \
  || die "incoming backup audit chain is invalid"

current_database="$(sqlite_database_path 2>/dev/null)" \
  || die "restore supports only local SQLite deployments"
if [[ -f "${current_database}" ]]; then
  info "creating a mandatory pre-restore backup"
  "${SCRIPT_DIR}/backup.sh"
fi

mkdir -p -- "$(dirname -- "${current_database}")"
[[ ! -e "${current_database}-wal" ]] || remove_project_file "${current_database}-wal"
[[ ! -e "${current_database}-shm" ]] || remove_project_file "${current_database}-shm"
database_tmp="${current_database}.restore.$$"
cp -- "${stage}/database.sqlite3" "${database_tmp}"
chmod 600 -- "${database_tmp}"
mv -f -- "${database_tmp}" "${current_database}"

for name in policies.yaml redaction.yaml policies.example.yaml redaction.example.yaml; do
  restored_policy="${stage}/config/${name}"
  [[ -f "${restored_policy}" ]] || continue
  target_policy="${PROJECT_ROOT}/config/${name}"
  policy_tmp="${target_policy}.restore.$$"
  cp -- "${restored_policy}" "${policy_tmp}"
  chmod 600 -- "${policy_tmp}"
  mv -f -- "${policy_tmp}" "${target_policy}"
done

database_url="$(read_env_value DATABASE_URL 'sqlite+aiosqlite:///./var/db/bh_dic.sqlite3')"
DATABASE_URL="${database_url}" "${python_bin}" -m alembic \
  -c "${PROJECT_ROOT}/migrations/alembic.ini" upgrade head
"${SCRIPT_DIR}/audit-verify.sh" >/dev/null
info "restore completed; archived logs, .env, sessions, and uploads were not restored"

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

if (($#)); then
  case "$1" in
    -h | --help)
      printf 'Usage: %s\nVerifies the complete HMAC audit chain.\n' "$0"
      exit 0
      ;;
    *) die "audit-verify accepts no arguments" ;;
  esac
fi

require_project_root
validate_runtime_config >/dev/null
python_bin="$(venv_python)"
"${python_bin}" - <<'PY'
import asyncio
import json

from bh_dic.audit.verifier import verify_audit_chain
from bh_dic.config import AppSettings
from bh_dic.database.engine import Database


async def verify() -> int:
    settings = AppSettings()
    if settings.audit_hmac_key is None:
        return 2
    database = Database(settings.database_url)
    try:
        result = await verify_audit_chain(
            database,
            settings.audit_hmac_key.get_secret_value(),
        )
    finally:
        await database.dispose()
    output = {
        "valid": result.valid,
        "event_count": result.event_count,
        "last_sequence": result.last_sequence,
        "failure_sequence": result.failure_sequence,
        "reason": result.reason,
    }
    print(json.dumps(output, ensure_ascii=True, separators=(",", ":")))
    return 0 if result.valid else 2


raise SystemExit(asyncio.run(verify()))
PY

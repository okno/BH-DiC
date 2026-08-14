#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  printf 'Usage: %s {app|discord|openai|browser|audit|security|all} [--follow] [--since ISO-8601] [--correlation-id ID] [--level LEVEL]\n' "$0"
}

(($# >= 1)) || { usage >&2; exit 1; }
category="$1"
shift
case "${category}" in
  app | discord | openai | browser | audit | security | all) ;;
  *) die "unknown log category: ${category}" ;;
esac

follow=false
since=""
correlation_id=""
level=""
while (($#)); do
  case "$1" in
    --follow) follow=true ;;
    --since | --correlation-id | --level)
      (($# >= 2)) || die "$1 requires a value"
      case "$1" in
        --since) since="$2" ;;
        --correlation-id) correlation_id="$2" ;;
        --level) level="${2^^}" ;;
      esac
      shift
      ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown logs option: $1" ;;
  esac
  shift
done
[[ -z "${correlation_id}" || "${correlation_id}" =~ ^[A-Za-z0-9._:-]{8,64}$ ]] \
  || die "invalid correlation ID"
case "${level}" in
  '' | DEBUG | INFO | WARNING | ERROR | CRITICAL) ;;
  *) die "invalid log level" ;;
esac

require_venv
log_dir="$(runtime_log_dir)"
files=()
if [[ "${category}" == "all" ]]; then
  for name in app discord openai browser audit security; do
    candidate="${log_dir}/${name}.jsonl"
    if [[ -e "${candidate}" ]]; then
      [[ -f "${candidate}" && ! -L "${candidate}" ]] || die "unsafe log path: ${candidate}"
      files+=("${candidate}")
    fi
  done
else
  candidate="${log_dir}/${category}.jsonl"
  if [[ -e "${candidate}" ]]; then
    [[ -f "${candidate}" && ! -L "${candidate}" ]] || die "unsafe log path: ${candidate}"
    files+=("${candidate}")
  fi
fi
if ((${#files[@]} == 0)); then
  warn "no matching log files are present"
  exit 0
fi

python_bin="$(venv_python)"
filter_program='
import datetime as dt
import json
import sys

from bh_dic.logging import redact

since_raw, correlation_id, wanted_level = sys.argv[1:4]
since = None
if since_raw:
    try:
        since = dt.datetime.fromisoformat(since_raw.replace("Z", "+00:00"))
        if since.tzinfo is None:
            raise ValueError
        since = since.astimezone(dt.UTC)
    except ValueError:
        raise SystemExit("ERROR: --since must be a timezone-aware ISO-8601 timestamp")
for raw in sys.stdin:
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            continue
        if correlation_id and str(event.get("correlation_id", "")) != correlation_id:
            continue
        if wanted_level and str(event.get("level", "")).upper() != wanted_level:
            continue
        if since is not None:
            timestamp = event.get("timestamp_utc", event.get("timestamp"))
            if not isinstance(timestamp, str):
                continue
            parsed = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.astimezone(dt.UTC) < since:
                continue
        print(json.dumps(redact(event), ensure_ascii=True, separators=(",", ":")), flush=True)
    except (ValueError, TypeError, json.JSONDecodeError):
        continue
'

if [[ "${follow}" == "true" ]]; then
  require_command tail
  tail --follow=name --retry -n +1 -- "${files[@]}" \
    | "${python_bin}" -u -c "${filter_program}" "${since}" "${correlation_id}" "${level}"
else
  "${python_bin}" -u -c "${filter_program}" "${since}" "${correlation_id}" "${level}" \
    < <(awk '{print}' "${files[@]}")
fi

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

if (($#)); then
  case "$1" in
    -h | --help)
      printf 'Usage: %s\nRegisters commands only in the configured Discord guild.\n' "$0"
      exit 0
      ;;
    *) die "register-commands accepts no arguments" ;;
  esac
fi

require_project_root
validate_runtime_config >/dev/null
guild_id="$(read_env_value DISCORD_GUILD_ID)"
[[ "${guild_id}" =~ ^[0-9]+$ ]] || die "DISCORD_GUILD_ID is missing or invalid"
info "registering slash commands in the configured guild only"
run_cli register-commands
info "slash-command registration completed"

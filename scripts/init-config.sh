#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

(($# == 0)) || die "init-config accepts no arguments"
[[ -f "${ENV_EXAMPLE_FILE}" ]] || die ".env.example is missing"
[[ ! -e "${ENV_FILE}" ]] || die ".env already exists and will not be overwritten"

install -m 600 -- "${ENV_EXAMPLE_FILE}" "${ENV_FILE}"
info "created ${ENV_FILE} with mode 0600"
info "fill every required secret and identifier locally, then run scripts/doctor.sh"
info "BH-DiC was not started"

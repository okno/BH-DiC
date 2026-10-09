#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

readonly BRIDGE_USER="bh-dic-bridge"
readonly BRIDGE_GROUP="bh-dic-bridge"
readonly INSTALL_ROOT="/opt/bh-dic-bridge"
readonly CONFIG_ROOT="/etc/bh-dic-bridge"
readonly STATE_ROOT="/var/lib/bh-dic-bridge"
readonly UNIT_TARGET="/etc/systemd/system/bh-dic-planner-bridge.service"

usage() {
  cat <<'EOF'
Usage: provision-bridge-host.sh --source-dir ABSOLUTE_PATH \
  --python-runtime ABSOLUTE_PATH --expected-commit FULL_SHA

Install the isolated Mint planner bridge from one clean, exact Git commit.
The command must run as root. It never creates credentials, enables services,
starts services, or changes SSH/network configuration.
EOF
}

source_dir=""
python_runtime=""
expected_commit=""
while (($#)); do
  case "$1" in
    --source-dir)
      (($# >= 2)) || die "--source-dir requires a value"
      source_dir="$2"
      shift 2
      ;;
    --python-runtime)
      (($# >= 2)) || die "--python-runtime requires a value"
      python_runtime="$2"
      shift 2
      ;;
    --expected-commit)
      (($# >= 2)) || die "--expected-commit requires a value"
      expected_commit="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "unknown bridge provisioning option: $1" ;;
  esac
done

[[ "$(effective_user_id)" == "0" ]] || die "bridge provisioning must run as root"
require_command getent
require_command chmod
require_command chown
require_command cp
require_command git
require_command install
require_command realpath
require_command tar
require_command systemctl
require_command useradd

[[ "${source_dir}" == /* ]] || die "--source-dir must be absolute"
[[ "${python_runtime}" == /* ]] || die "--python-runtime must be absolute"
[[ "${expected_commit}" =~ ^[0-9a-f]{40}$ ]] || die "--expected-commit must be a full SHA"

source_dir="$(realpath -e -- "${source_dir}")"
python_runtime="$(realpath -e -- "${python_runtime}")"
[[ -d "${source_dir}/.git" ]] || die "source directory is not a Git checkout"
[[ -f "${source_dir}/pyproject.toml" ]] || die "source project is missing pyproject.toml"
[[ -f "${source_dir}/requirements.lock" ]] || die "source project is missing requirements.lock"
[[ -f "${source_dir}/infrastructure/systemd/bh-dic-planner-bridge.service.example" ]] \
  || die "planner bridge systemd unit is missing"
[[ -x "${python_runtime}/bin/python3.12" ]] || die "Python 3.12 runtime is incomplete"

actual_commit="$(git -c safe.directory="${source_dir}" -C "${source_dir}" rev-parse HEAD)"
[[ "${actual_commit}" == "${expected_commit}" ]] || die "source commit does not match approval"
[[ -z "$(git -c safe.directory="${source_dir}" -C "${source_dir}" status --porcelain)" ]] \
  || die "source checkout must be clean, including untracked files"
"${python_runtime}/bin/python3.12" -c \
  'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' \
  || die "the supplied runtime is not Python 3.12"

[[ ! -e "${INSTALL_ROOT}" ]] || die "${INSTALL_ROOT} already exists; refusing to overwrite"
[[ ! -e "${CONFIG_ROOT}" ]] || die "${CONFIG_ROOT} already exists; refusing to overwrite"
[[ ! -e "${STATE_ROOT}" ]] || die "${STATE_ROOT} already exists; refusing to overwrite"
[[ ! -e "${UNIT_TARGET}" ]] || die "${UNIT_TARGET} already exists; refusing to overwrite"

if getent passwd "${BRIDGE_USER}" >/dev/null; then
  bridge_record="$(getent passwd "${BRIDGE_USER}")"
  IFS=: read -r bridge_name _ bridge_uid bridge_gid _ bridge_home bridge_shell \
    <<<"${bridge_record}"
  bridge_primary_group="$(getent group "${bridge_gid}" | awk -F: '{print $1}')"
  [[ "${bridge_name}" == "${BRIDGE_USER}" ]] || die "existing bridge account name is invalid"
  [[ "${bridge_uid}" =~ ^[0-9]+$ ]] && ((bridge_uid > 0)) \
    || die "existing bridge account is invalid"
  [[ "${bridge_primary_group}" == "${BRIDGE_GROUP}" ]] \
    || die "existing bridge account has the wrong primary group"
  [[ "${bridge_home}" == "/nonexistent" ]] \
    || die "existing bridge account has the wrong home"
  [[ "${bridge_shell}" == "/usr/sbin/nologin" ]] \
    || die "existing bridge account has the wrong shell"
else
  useradd \
    --system \
    --user-group \
    --home-dir /nonexistent \
    --shell /usr/sbin/nologin \
    "${BRIDGE_USER}"
fi

install -d -m 0755 -o root -g root "${INSTALL_ROOT}"
git -c safe.directory="${source_dir}" -C "${source_dir}" archive \
  --format=tar "${expected_commit}" \
  | tar --extract --directory="${INSTALL_ROOT}" --no-same-owner --no-same-permissions
cp -a -- "${python_runtime}" "${INSTALL_ROOT}/python"
chown -R root:root -- "${INSTALL_ROOT}"
chmod -R go-w -- "${INSTALL_ROOT}"

"${INSTALL_ROOT}/python/bin/python3.12" -m venv "${INSTALL_ROOT}/.venv"
PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 \
  "${INSTALL_ROOT}/.venv/bin/python" -m pip install \
  --requirement "${INSTALL_ROOT}/requirements.lock"
PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 \
  "${INSTALL_ROOT}/.venv/bin/python" -m pip install \
  --no-deps "${INSTALL_ROOT}"
"${INSTALL_ROOT}/.venv/bin/python" -m pip check
chown -R root:root -- "${INSTALL_ROOT}"
chmod -R u=rwX,go=rX -- "${INSTALL_ROOT}"

install -d -m 0750 -o root -g "${BRIDGE_GROUP}" "${CONFIG_ROOT}"
install -d -m 0700 -o "${BRIDGE_USER}" -g "${BRIDGE_GROUP}" \
  "${STATE_ROOT}" \
  "${STATE_ROOT}/codex" \
  "${STATE_ROOT}/codex/work"
install -m 0644 -o root -g root \
  "${INSTALL_ROOT}/infrastructure/systemd/bh-dic-planner-bridge.service.example" \
  "${UNIT_TARGET}"
systemctl daemon-reload

info "bridge host installed from ${expected_commit}"
info "no credential was created and no service was enabled or started"
info "next: provision private mTLS files and ${CONFIG_ROOT}/bridge.env"

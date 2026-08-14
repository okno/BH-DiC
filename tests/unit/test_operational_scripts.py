from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SERVICE = ROOT / "infrastructure" / "systemd" / "bh-dic.service.example"

REQUIRED_SCRIPTS = {
    "install.sh",
    "update.sh",
    "init-config.sh",
    "doctor.sh",
    "browser-install.sh",
    "register-commands.sh",
    "run-foreground.sh",
    "start.sh",
    "stop.sh",
    "restart.sh",
    "status.sh",
    "healthcheck.sh",
    "logs.sh",
    "files.sh",
    "cleanup.sh",
    "backup.sh",
    "restore.sh",
    "audit-verify.sh",
    "run-tests.sh",
    "lint.sh",
    "security-check.sh",
}


def _read(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def _git_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    candidates = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Git"
        / "usr"
        / "bin"
        / "bash.exe",
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def test_required_operational_scripts_exist() -> None:
    assert REQUIRED_SCRIPTS <= {path.name for path in SCRIPTS.glob("*.sh")}


@pytest.mark.parametrize("script", sorted(REQUIRED_SCRIPTS | {"lib.sh"}))
def test_scripts_enforce_fail_closed_shell_baseline(script: str) -> None:
    text = _read(script)
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -Eeuo pipefail" in text
    assert "umask 077" in text
    assert "BASH_SOURCE[0]" in text
    assert not re.search(r"(?m)^\s*(?:source|\.)\s+[^\n]*\.env(?:\s|$)", text)
    assert not re.search(r"(?m)^\s*eval\s", text)
    assert "chmod 777" not in text
    assert "curl -k" not in text
    assert "--insecure" not in text


def test_lifecycle_scripts_guard_process_identity_and_force_kill() -> None:
    start = _read("start.sh")
    stop = _read("stop.sh")
    assert "doctor.sh" in start
    assert "flock -n" in start
    assert "nohup" in start
    assert "process_is_bh_dic" in start
    assert "kill -TERM" in stop
    assert "process_is_bh_dic" in stop
    assert "kill -KILL" in stop
    assert "--force" in stop
    assert stop.index('[[ "${force}" != "true" ]]') < stop.index('kill -KILL "${pid}"')


def test_cli_wrappers_match_the_implemented_cli() -> None:
    assert "run_cli health\n" in _read("healthcheck.sh")
    assert "health --json" not in _read("healthcheck.sh")
    assert "run_cli register-commands\n" in _read("register-commands.sh")
    assert "--guild-only" not in _read("register-commands.sh")
    cleanup = _read("cleanup.sh")
    assert "run_cli files list" in cleanup
    assert "run_cli files purge-expired" in cleanup
    assert "run_cli cleanup" not in cleanup


def test_backup_excludes_secrets_sessions_and_uploads_by_default() -> None:
    backup = _read("backup.sh")
    assert 'environment_file_included": False' in backup
    assert 'browser_session_included": False' in backup
    assert 'uploads_included": False' in backup
    assert '"${PROJECT_ROOT}/.env"' not in backup
    assert '"${data_dir}/session"' not in backup
    assert '"${data_dir}/uploads"' not in backup
    assert "source.backup(target)" in backup
    assert "sha256sum" in backup


def test_restore_requires_confirmation_and_rejects_unsafe_archives() -> None:
    restore = _read("restore.sh")
    assert '"${confirmation}" == "RESTORE"' in restore
    assert "require_bot_stopped" in restore
    assert '".." in path.parts' in restore
    assert "links and special archive members are forbidden" in restore
    assert "sha256sum --check --strict" in restore
    assert "mandatory pre-restore backup" in restore


def test_systemd_example_is_hardened_and_not_self_enabling() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    expected = (
        "User=bh-dic",
        "Group=bh-dic",
        "EnvironmentFile=/opt/bh-dic/.env",
        "ExecStart=/opt/bh-dic/scripts/run-foreground.sh",
        "UMask=0077",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "CapabilityBoundingSet=",
        "ReadWritePaths=/opt/bh-dic/var",
    )
    assert all(setting in text for setting in expected)
    assert "systemctl enable" not in text
    assert "systemctl start" not in text


def test_all_bash_scripts_parse() -> None:
    bash = _git_bash()
    if bash is None:
        pytest.skip("Bash is not installed")
    for script in sorted(SCRIPTS.glob("*.sh")):
        completed = subprocess.run(  # noqa: S603 - executable and inputs are trusted repo paths
            [bash, "-n", str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, f"{script.name}: {completed.stderr}"

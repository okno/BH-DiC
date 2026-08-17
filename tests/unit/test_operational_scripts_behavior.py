from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCRIPTS = ROOT / "scripts"


def _bash() -> str | None:
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


@pytest.fixture
def lifecycle_root(tmp_path: Path) -> Path:
    root = tmp_path / "bh_dic_lifecycle_fixture"
    shutil.copytree(SOURCE_SCRIPTS, root / "scripts")
    (root / "src" / "bh_dic").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='synthetic-bh-dic'\n", encoding="utf-8")
    fake_python = "#!/usr/bin/env bash\nexit 1\n"
    for relative in (Path(".venv/bin/python"), Path(".venv/Scripts/python.exe")):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fake_python, encoding="utf-8", newline="\n")
        path.chmod(0o700)
    for script in (root / "scripts").glob("*.sh"):
        script.chmod(0o700)
    return root


def _run(bash: str, root: Path, script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - Bash and every script are trusted fixture paths
        [bash, str(root / "scripts" / script), *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o700)


def _append_library(root: Path, text: str) -> None:
    library = root / "scripts" / "lib.sh"
    library.write_text(
        library.read_text(encoding="utf-8") + "\n" + text + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _run_update_with_fake_path(
    bash: str, root: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - trusted fixture script and isolated fake PATH
        [
            bash,
            "-c",
            'PATH="./fake-bin:$PATH"; export PATH; exec ./scripts/update.sh "$@"',
            "update-test",
            *arguments,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _override_effective_uid(root: Path, effective_uid: int) -> None:
    _append_library(
        root,
        f"effective_user_id() {{ printf '%s\\n' '{effective_uid}'; }}",
    )


def _write_systemctl(root: Path, body: str) -> None:
    _write_executable(
        root / "fake-bin" / "systemctl",
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> systemctl.calls\n" + body,
    )


def _write_safe_find(root: Path) -> None:
    _write_executable(root / "fake-bin" / "find", "#!/usr/bin/env bash\nexit 0\n")


def _write_safe_git(root: Path, *, fetch_exit: int = 0, diverged: bool = False) -> None:
    ahead = "1" if diverged else "0"
    behind = "1" if diverged else "0"
    _write_executable(
        root / "fake-bin" / "git",
        f"""#!/usr/bin/env bash
printf '%s\n' "$*" >> git.calls
case "$1" in
  status) exit 0 ;;
  symbolic-ref) printf '%s\n' main ;;
  rev-parse) printf '%s\n' origin/main ;;
  rev-list)
    case "$3" in
      HEAD..*) printf '%s\n' {behind} ;;
      *) printf '%s\n' {ahead} ;;
    esac
    ;;
  fetch) exit {fetch_exit} ;;
  *) exit 99 ;;
esac
""",
    )


def _write_timeout_passthrough(root: Path) -> None:
    _write_executable(
        root / "fake-bin" / "timeout",
        """#!/usr/bin/env bash
shift
exec "$@"
""",
    )


def _write_update_python(root: Path) -> None:
    _write_executable(
        root / ".venv" / "bin" / "python",
        """#!/usr/bin/env bash
if [[ "$1" == "-m" && "$2" == "pip" && "$3" == "check" ]]; then
  printf 'pip-check:%s\n' "$(umask)" >> python.calls
elif [[ "$1" == "-c" && "$2" == "import bh_dic" ]]; then
  printf 'import:%s\n' "$(umask)" >> python.calls
elif [[ "$1" == "-m" && "$2" == "pip" && "$3" == "install" ]]; then
  printf 'pip-install:%s\n' "$(umask)" >> python.calls
elif [[ "$1" == "-c" ]]; then
  printf 'import-version:%s\n' "$(umask)" >> python.calls
elif [[ "$1" == "-m" && "$2" == "alembic" ]]; then
  printf 'alembic:%s\n' "$(umask)" >> python.calls
else
  exit 99
fi
""",
    )


def _enable_fake_pid_backend(root: Path) -> None:
    (root / "pid-backend-running").touch()
    pid_file = root / "var" / "run" / "bh-dic.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("4242\n", encoding="utf-8", newline="\n")
    _append_library(
        root,
        """
bot_is_running() {
  [[ -e "${PROJECT_ROOT}/pid-backend-running" && -e "$(runtime_pid_file)" ]]
}
read_pid() {
  [[ -e "$(runtime_pid_file)" ]] || return 1
  IFS= read -r pid <"$(runtime_pid_file)"
  printf '%s\n' "${pid}"
}
process_is_running() { [[ -e "${PROJECT_ROOT}/pid-backend-running" ]]; }
process_is_bh_dic() { [[ -e "${PROJECT_ROOT}/pid-backend-running" ]]; }
""",
    )


def _prepare_safe_update(root: Path, *, fetch_exit: int = 0) -> None:
    _override_effective_uid(root, 4242)
    _write_systemctl(
        root,
        "printf '%s\\n' 'LoadState=not-found' 'ActiveState=inactive'\n",
    )
    _write_safe_find(root)
    _write_safe_git(root, fetch_exit=fetch_exit)
    _write_timeout_passthrough(root)
    _write_update_python(root)


def test_start_fails_closed_before_process_creation_when_config_is_missing(
    lifecycle_root: Path,
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    result = _run(bash, lifecycle_root, "start.sh")
    assert result.returncode != 0
    assert ".env is missing" in result.stderr
    assert not (lifecycle_root / "var" / "run" / "bh-dic.pid").exists()
    assert not (lifecycle_root / "var" / "log" / "app.jsonl").exists()


def test_update_rejects_root_before_all_external_operations(lifecycle_root: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _override_effective_uid(lifecycle_root, 0)
    for command in ("systemctl", "find", "git"):
        _write_executable(
            lifecycle_root / "fake-bin" / command,
            f"#!/usr/bin/env bash\ntouch {command}.called\nexit 99\n",
        )
    _write_executable(
        lifecycle_root / ".venv" / "bin" / "python",
        "#!/usr/bin/env bash\ntouch python.called\nexit 99\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\nexit 99\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart")

    assert result.returncode != 0
    assert "must not run as root" in result.stderr
    assert "service/repository owner" in result.stderr
    for marker in (
        "systemctl.called",
        "find.called",
        "git.called",
        "python.called",
        "backup.called",
    ):
        assert not (lifecycle_root / marker).exists()


@pytest.mark.parametrize(
    ("properties", "exit_code"),
    [
        ("LoadState=loaded\nActiveState=active", 0),
        ("LoadState=loaded\nActiveState=activating", 0),
        ("LoadState=loaded\nActiveState=deactivating", 0),
        ("LoadState=loaded\nActiveState=failed", 0),
        ("LoadState=loaded\nActiveState=unknown", 0),
        ("LoadState=masked\nActiveState=inactive", 0),
        ("LoadState=loaded", 0),
        ("LoadState=loaded\nLoadState=loaded\nActiveState=inactive", 0),
        ("LoadState=loaded\nActiveState=inactive\nPrivateMarker=secret", 0),
        ("", 0),
        ("LoadState=loaded\nActiveState=inactive", 1),
    ],
)
def test_update_rejects_every_unproved_systemd_state_before_preflight_or_mutation(
    lifecycle_root: Path, properties: str, exit_code: int
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _override_effective_uid(lifecycle_root, 4242)
    quoted_lines = " ".join(f"'{line}'" for line in properties.splitlines())
    output = f"printf '%s\\n' {quoted_lines}\n" if quoted_lines else ""
    _write_systemctl(lifecycle_root, output + f"exit {exit_code}\n")
    for command in ("find", "git"):
        _write_executable(
            lifecycle_root / "fake-bin" / command,
            f"#!/usr/bin/env bash\ntouch {command}.called\nexit 99\n",
        )
    _write_executable(
        lifecycle_root / ".venv" / "bin" / "python",
        "#!/usr/bin/env bash\ntouch python.called\nexit 99\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart")

    assert result.returncode != 0
    assert "cannot prove bh-dic.service" in result.stderr
    assert "PrivateMarker" not in result.stderr
    assert (lifecycle_root / "systemctl.calls").read_text(encoding="utf-8").splitlines() == [
        "show --no-pager --property=LoadState --property=ActiveState bh-dic.service"
    ]
    for marker in ("find.called", "git.called", "python.called", "backup.called"):
        assert not (lifecycle_root / marker).exists()


@pytest.mark.parametrize("load_state", ["not-found", "loaded"])
def test_update_accepts_only_explicit_safe_systemd_states_and_preserves_umask(
    lifecycle_root: Path, load_state: str
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _override_effective_uid(lifecycle_root, 4242)
    _write_systemctl(
        lifecycle_root,
        f"printf '%s\\n' 'LoadState={load_state}' 'ActiveState=inactive'\n",
    )
    _write_safe_find(lifecycle_root)
    _write_safe_git(lifecycle_root)
    _write_timeout_passthrough(lifecycle_root)
    _write_update_python(lifecycle_root)
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--skip-tests")

    assert result.returncode == 0, result.stderr
    assert (lifecycle_root / "backup.called").is_file()
    assert (lifecycle_root / "systemctl.calls").read_text(encoding="utf-8").splitlines() == [
        "show --no-pager --property=LoadState --property=ActiveState bh-dic.service",
        "show --no-pager --property=LoadState --property=ActiveState bh-dic.service",
    ]
    assert (lifecycle_root / "python.calls").read_text(encoding="utf-8").splitlines() == [
        "pip-check:0077",
        "import:0077",
        "pip-install:0077",
        "pip-install:0077",
        "pip-check:0077",
        "import-version:0077",
        "alembic:0077",
    ]


def test_update_rechecks_systemd_immediately_before_backup(lifecycle_root: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _override_effective_uid(lifecycle_root, 4242)
    _write_systemctl(
        lifecycle_root,
        """count=0
[[ ! -f systemctl.count ]] || count="$(<systemctl.count)"
((count += 1))
printf '%s\n' "$count" > systemctl.count
printf '%s\n' 'LoadState=loaded'
if [[ "$count" == "1" ]]; then
  printf '%s\n' 'ActiveState=inactive'
else
  printf '%s\n' 'ActiveState=active'
fi
""",
    )
    _write_safe_find(lifecycle_root)
    _write_safe_git(lifecycle_root)
    _write_timeout_passthrough(lifecycle_root)
    _write_update_python(lifecycle_root)
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--skip-tests")

    assert result.returncode != 0
    assert "cannot prove bh-dic.service" in result.stderr
    assert len((lifecycle_root / "systemctl.calls").read_text(encoding="utf-8").splitlines()) == 2
    assert not (lifecycle_root / "backup.called").exists()
    assert (lifecycle_root / "python.calls").read_text(encoding="utf-8").splitlines() == [
        "pip-check:0077",
        "import:0077",
    ]


def test_update_rejects_nonowned_or_unreadable_tree_without_echoing_path(
    lifecycle_root: Path,
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _override_effective_uid(lifecycle_root, 4242)
    _write_systemctl(
        lifecycle_root,
        "printf '%s\\n' 'LoadState=not-found' 'ActiveState=inactive'\n",
    )
    _write_executable(
        lifecycle_root / "fake-bin" / "find",
        "#!/usr/bin/env bash\nprintf '%s\\n' 'private-marker'\n",
    )
    _write_executable(
        lifecycle_root / "fake-bin" / "git",
        "#!/usr/bin/env bash\ntouch git.called\nexit 99\n",
    )
    _write_executable(
        lifecycle_root / ".venv" / "bin" / "python",
        "#!/usr/bin/env bash\ntouch python.called\nexit 99\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root)

    assert result.returncode != 0
    assert "ownership/readability preflight failed" in result.stderr
    assert "private-marker" not in result.stderr
    assert not (lifecycle_root / "git.called").exists()
    assert not (lifecycle_root / "python.called").exists()
    assert not (lifecycle_root / "backup.called").exists()


@pytest.mark.parametrize(
    ("runtime_present", "expected_code"),
    [(False, 0), (True, 2)],
)
def test_systemctl_absence_is_safe_only_on_an_explicit_non_systemd_host(
    lifecycle_root: Path, runtime_present: bool, expected_code: int
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    runtime_result = "return 0" if runtime_present else "return 1"
    _append_library(
        lifecycle_root,
        f"""
systemctl_is_available() {{ return 1; }}
systemd_runtime_is_present() {{ {runtime_result}; }}
""",
    )
    result = subprocess.run(  # noqa: S603 - trusted Bash and fixture library
        [bash, "-c", "source ./scripts/lib.sh; systemd_service_update_state"],
        cwd=lifecycle_root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == expected_code
    if runtime_present:
        assert result.stdout == ""
    else:
        assert result.stdout.strip() == "not-found"


def test_update_git_status_error_aborts_before_fetch_stop_or_backup(lifecycle_root: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    _enable_fake_pid_backend(lifecycle_root)
    _write_executable(
        lifecycle_root / "fake-bin" / "git",
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> git.calls
[[ "$1" != "status" ]] || exit 7
exit 99
""",
    )
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        "#!/usr/bin/env bash\ntouch stop.called\nexit 99\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\nexit 99\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode != 0
    assert "unable to inspect the working tree" in result.stderr
    assert (lifecycle_root / "git.calls").read_text(encoding="utf-8").splitlines() == [
        "status --porcelain --untracked-files=normal"
    ]
    assert (lifecycle_root / "pid-backend-running").exists()
    assert not (lifecycle_root / "stop.called").exists()
    assert not (lifecycle_root / "backup.called").exists()


def test_update_fetch_failure_keeps_pid_service_running_and_skips_backup(
    lifecycle_root: Path,
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root, fetch_exit=8)
    _enable_fake_pid_backend(lifecycle_root)
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        "#!/usr/bin/env bash\ntouch stop.called\nexit 99\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\nexit 99\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode != 0
    assert "fetch --prune -- origin" in (lifecycle_root / "git.calls").read_text(encoding="utf-8")
    assert (lifecycle_root / "pid-backend-running").exists()
    assert not (lifecycle_root / "stop.called").exists()
    assert not (lifecycle_root / "backup.called").exists()


def test_update_post_fetch_divergence_aborts_before_pid_stop(lifecycle_root: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    _enable_fake_pid_backend(lifecycle_root)
    _write_executable(
        lifecycle_root / "fake-bin" / "git",
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> git.calls
case "$1" in
  status | fetch) exit 0 ;;
  symbolic-ref) printf '%s\n' main ;;
  rev-parse) printf '%s\n' origin/main ;;
  rev-list)
    count=0
    [[ ! -f rev-list.count ]] || count="$(<rev-list.count)"
    ((count += 1))
    printf '%s\n' "$count" > rev-list.count
    if ((count <= 2)); then printf '%s\n' 0; else printf '%s\n' 1; fi
    ;;
  *) exit 99 ;;
esac
""",
    )
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        "#!/usr/bin/env bash\ntouch stop.called\nexit 99\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode != 0
    assert "diverged after fetch" in result.stderr
    assert (lifecycle_root / "pid-backend-running").exists()
    assert not (lifecycle_root / "stop.called").exists()
    assert not (lifecycle_root / "backup.called").exists()


@pytest.mark.parametrize("failure", ["dirty", "diverged"])
def test_dirty_or_locally_diverged_repository_aborts_before_fetch_and_pid_stop(
    lifecycle_root: Path, failure: str
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    _enable_fake_pid_backend(lifecycle_root)
    dirty_status = "printf '%s\\n' 'private-marker'" if failure == "dirty" else "exit 0"
    count = "1" if failure == "diverged" else "0"
    _write_executable(
        lifecycle_root / "fake-bin" / "git",
        f"""#!/usr/bin/env bash
printf '%s\n' "$*" >> git.calls
case "$1" in
  status) {dirty_status} ;;
  symbolic-ref) printf '%s\n' main ;;
  rev-parse) printf '%s\n' origin/main ;;
  rev-list) printf '%s\n' {count} ;;
  fetch) touch fetch.called; exit 99 ;;
  *) exit 99 ;;
esac
""",
    )
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        "#!/usr/bin/env bash\ntouch stop.called\nexit 99\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode != 0
    assert "private-marker" not in result.stderr
    assert not (lifecycle_root / "fetch.called").exists()
    assert not (lifecycle_root / "stop.called").exists()
    assert not (lifecycle_root / "backup.called").exists()


def test_running_pid_without_restart_aborts_before_fetch(lifecycle_root: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    _enable_fake_pid_backend(lifecycle_root)
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        "#!/usr/bin/env bash\ntouch stop.called\nexit 99\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--skip-tests")

    assert result.returncode != 0
    assert "controlled PID-mode restart" in result.stderr
    assert "fetch " not in (lifecycle_root / "git.calls").read_text(encoding="utf-8")
    assert (lifecycle_root / "pid-backend-running").exists()
    assert not (lifecycle_root / "stop.called").exists()
    assert not (lifecycle_root / "backup.called").exists()


@pytest.mark.parametrize("pid_text", ["not-a-pid\n", "99999999\n"])
def test_invalid_or_stale_pid_state_aborts_before_fetch_or_mutation(
    lifecycle_root: Path, pid_text: str
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    pid_file = lifecycle_root / "var" / "run" / "bh-dic.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text(pid_text, encoding="utf-8", newline="\n")

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode != 0
    assert "PID lifecycle state" in result.stderr
    assert "fetch " not in (lifecycle_root / "git.calls").read_text(encoding="utf-8")
    assert not (lifecycle_root / "backup.called").exists()


@pytest.mark.parametrize(("stop_exit", "remove_pid"), [(9, False), (0, False)])
def test_pid_stop_failure_or_still_running_prevents_backup(
    lifecycle_root: Path, stop_exit: int, remove_pid: bool
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    _enable_fake_pid_backend(lifecycle_root)
    removal = "rm -f pid-backend-running var/run/bh-dic.pid" if remove_pid else ":"
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        f"#!/usr/bin/env bash\ntouch stop.called\n{removal}\nexit {stop_exit}\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode != 0
    assert (lifecycle_root / "stop.called").is_file()
    assert not (lifecycle_root / "backup.called").exists()
    assert len((lifecycle_root / "systemctl.calls").read_text(encoding="utf-8").splitlines()) == 1


def test_backup_failure_after_verified_stop_never_restarts_or_installs(
    lifecycle_root: Path,
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    _enable_fake_pid_backend(lifecycle_root)
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        "#!/usr/bin/env bash\nrm -f pid-backend-running var/run/bh-dic.pid\ntouch stop.called\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\nexit 11\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "start.sh",
        "#!/usr/bin/env bash\ntouch start.called\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode != 0
    assert (lifecycle_root / "stop.called").is_file()
    assert (lifecycle_root / "backup.called").is_file()
    assert not (lifecycle_root / "start.called").exists()
    assert not (lifecycle_root / "pid-backend-running").exists()
    assert (lifecycle_root / "python.calls").read_text(encoding="utf-8").splitlines() == [
        "pip-check:0077",
        "import:0077",
    ]


def test_pid_restart_happy_path_stops_verifies_updates_and_restarts(
    lifecycle_root: Path,
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    _enable_fake_pid_backend(lifecycle_root)
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        "#!/usr/bin/env bash\nrm -f pid-backend-running var/run/bh-dic.pid\ntouch stop.called\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "start.sh",
        "#!/usr/bin/env bash\ntouch start.called\n",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode == 0, result.stderr
    assert (lifecycle_root / "stop.called").is_file()
    assert (lifecycle_root / "backup.called").is_file()
    assert (lifecycle_root / "start.called").is_file()
    assert not (lifecycle_root / "pid-backend-running").exists()
    assert len((lifecycle_root / "systemctl.calls").read_text(encoding="utf-8").splitlines()) == 2


def test_post_install_verification_failure_blocks_migration_and_restart(
    lifecycle_root: Path,
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    _enable_fake_pid_backend(lifecycle_root)
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        "#!/usr/bin/env bash\nrm -f pid-backend-running var/run/bh-dic.pid\ntouch stop.called\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "start.sh",
        "#!/usr/bin/env bash\ntouch start.called\n",
    )
    _write_executable(
        lifecycle_root / ".venv" / "bin" / "python",
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> python.calls
if [[ "$1" == "-m" && "$2" == "pip" && "$3" == "check" ]]; then
  count=0
  [[ ! -f pip-check.count ]] || count="$(<pip-check.count)"
  ((count += 1))
  printf '%s\n' "$count" > pip-check.count
  ((count == 1))
else
  exit 0
fi
""",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode != 0
    assert "post-install dependency/readability verification failed" in result.stderr
    calls = (lifecycle_root / "python.calls").read_text(encoding="utf-8")
    assert "-m alembic" not in calls
    assert not (lifecycle_root / "start.called").exists()


def test_post_install_import_version_failure_blocks_migration_and_restart(
    lifecycle_root: Path,
) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    _prepare_safe_update(lifecycle_root)
    _enable_fake_pid_backend(lifecycle_root)
    _write_executable(
        lifecycle_root / "scripts" / "stop.sh",
        "#!/usr/bin/env bash\nrm -f pid-backend-running var/run/bh-dic.pid\ntouch stop.called\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "backup.sh",
        "#!/usr/bin/env bash\ntouch backup.called\n",
    )
    _write_executable(
        lifecycle_root / "scripts" / "start.sh",
        "#!/usr/bin/env bash\ntouch start.called\n",
    )
    _write_executable(
        lifecycle_root / ".venv" / "bin" / "python",
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> python.calls
if [[ "$1" == "-c" && "$2" != "import bh_dic" ]]; then exit 12; fi
exit 0
""",
    )

    result = _run_update_with_fake_path(bash, lifecycle_root, "--restart", "--skip-tests")

    assert result.returncode != 0
    assert "post-install BH-DiC import/version verification failed" in result.stderr
    calls = (lifecycle_root / "python.calls").read_text(encoding="utf-8")
    assert "-m alembic" not in calls
    assert not (lifecycle_root / "start.called").exists()


def test_status_and_stop_manage_only_the_synthetic_owned_process(lifecycle_root: Path) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("Bash is not installed")

    if os.name == "nt":
        # MSYS2 signals may be forwarded through the shared Windows console rather
        # than a POSIX child process group. Exercise the stale-PID path instead of
        # risking a signal outside the test-owned worker.
        synthetic_pid = "99999999"
        probe = subprocess.run(  # noqa: S603 - trusted Git Bash executable
            [bash, "-lc", f"kill -0 {synthetic_pid}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert probe.returncode != 0
        run_dir = lifecycle_root / "var" / "run"
        run_dir.mkdir(parents=True)
        pid_file = run_dir / "bh-dic.pid"
        lock_file = run_dir / "bh-dic.lock"
        pid_file.write_text(f"{synthetic_pid}\n", encoding="utf-8", newline="\n")
        lock_file.touch()

        status = _run(bash, lifecycle_root, "status.sh")
        assert status.returncode == 0, status.stderr
        assert "status: stopped" in status.stdout
        stopped = _run(bash, lifecycle_root, "stop.sh")
        assert stopped.returncode == 0, stopped.stderr
        assert "stale lifecycle files" in stopped.stderr
        assert not pid_file.exists()
        assert not lock_file.exists()
        return

    worker = subprocess.Popen(  # noqa: S603 - the worker is created by this isolated test
        [bash, "-c", "exec -a bh_dic-synthetic-worker sleep 300"],
        cwd=lifecycle_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pid_file = lifecycle_root / "var" / "run" / "bh-dic.pid"
    try:
        assert worker.poll() is None
        synthetic_pid = str(worker.pid)
        pid_file.parent.mkdir(parents=True)
        pid_file.write_text(f"{synthetic_pid}\n", encoding="utf-8", newline="\n")

        status = _run(bash, lifecycle_root, "status.sh")
        assert status.returncode == 0, status.stderr
        assert "status: running" in status.stdout
        assert f"PID: {synthetic_pid}" in status.stdout

        stop_process = subprocess.Popen(  # noqa: S603 - trusted fixture script
            [bash, str(lifecycle_root / "scripts" / "stop.sh"), "--timeout", "10"],
            cwd=lifecycle_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Reap the owned child while stop.sh polls it; otherwise a POSIX zombie
        # remains visible to kill -0 until this test calls wait().
        worker.wait(timeout=5)
        stop_stdout, stop_stderr = stop_process.communicate(timeout=15)
        assert stop_process.returncode == 0, stop_stderr
        assert "stopped cleanly" in stop_stdout
        assert not pid_file.exists()
    finally:
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)

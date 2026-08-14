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

        stopped = _run(bash, lifecycle_root, "stop.sh", "--timeout", "10")
        assert stopped.returncode == 0, stopped.stderr
        assert "stopped cleanly" in stopped.stdout
        worker.wait(timeout=5)
        assert not pid_file.exists()
    finally:
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)

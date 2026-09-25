"""Integration tests against a real Docker Sandboxes microVM (opt-in).

Run with::

    pytest -m integration

Skipped unless ``sbx`` is installed, authenticated, and virtualization works.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import time

import pytest

from deepagents_sbx import SbxSandbox

pytestmark = pytest.mark.integration


def _run_sbx(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["sbx", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


# ------------------------------------------------------------------------- environment


def test_IT_ENV_01_python3_present(sandbox: SbxSandbox) -> None:
    result = sandbox.execute("python3 --version")

    assert result.exit_code == 0, result.output
    assert "Python 3" in result.output


def test_IT_ENV_02_posix_utils_present(sandbox: SbxSandbox) -> None:
    names = "sh awk grep find stat sed head tail base64 timeout mkdir cp rm python3"
    result = sandbox.execute(
        f'for b in {names}; do command -v "$b" >/dev/null || {{ echo missing:$b; exit 1; }}; done',
    )

    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------- lifecycle


def test_IT_LIFE_01_create_exec_remove(sbx_name: str) -> None:
    backend = SbxSandbox(name=sbx_name, timeout=60, auto_remove=False, pull="missing")
    try:
        assert sbx_name in [info.name for info in backend.transport.list()]
        assert backend.execute("echo ok").output.strip() == "ok"
    finally:
        backend.remove()

    assert sbx_name not in [info.name for info in backend.transport.list()]


def test_IT_LIFE_02_stopped_sandbox_autostarts(sandbox: SbxSandbox) -> None:
    stopped = _run_sbx("stop", sandbox.name)
    assert stopped.returncode == 0, stopped.stderr

    result = sandbox.execute("echo ok")

    assert result.exit_code == 0, result.output
    assert "ok" in result.output


def test_IT_LIFE_05_auto_remove_true_deletes(sbx_name: str) -> None:
    backend = SbxSandbox(name=sbx_name, timeout=60, auto_remove=True, pull="missing")
    backend.execute("true")
    backend.close()

    assert sbx_name not in [info.name for info in backend.transport.list()]


# -------------------------------------------------------------------------------- exec


def test_IT_EXEC_01_exit_code_and_streams(sandbox: SbxSandbox) -> None:
    result = sandbox.execute("echo to-stdout; echo to-stderr >&2; exit 7")

    assert result.exit_code == 7
    assert "to-stdout" in result.output
    assert "to-stderr" in result.output


def test_IT_EXEC_02_large_output_is_truncated(sandbox: SbxSandbox) -> None:
    result = sandbox.execute("yes | head -c 200000000", timeout=60)

    assert result.truncated is True
    assert len(result.output.encode("utf-8")) <= sandbox.max_output_bytes


def test_IT_EXEC_03_timeout_kills_remote_process(sandbox: SbxSandbox) -> None:
    started = time.monotonic()
    result = sandbox.execute("sleep 600", timeout=3)
    elapsed = time.monotonic() - started

    assert elapsed < 20
    assert result.exit_code is None
    assert "timed out" in result.output

    probe = sandbox.execute("pgrep -x sleep || true", timeout=10)
    assert "sleep" not in probe.output.replace("pgrep", "")


# -------------------------------------------------------------------------- filesystem


def test_IT_FS_01_full_file_ops(sandbox: SbxSandbox) -> None:
    workspace = "/home/agent/workspace/it-fs"
    assert sandbox.upload_files([(f"{workspace}/a.txt", b"hello needle\n")])[0].error is None

    read = sandbox.read(f"{workspace}/a.txt")
    assert read.error is None
    assert read.file_data is not None
    assert "needle" in read.file_data["content"]

    edit = sandbox.edit(f"{workspace}/a.txt", "needle", "NEEDLE")
    assert edit.error is None

    grep = sandbox.grep("NEEDLE", path=workspace)
    assert grep.error is None
    assert grep.matches

    glob = sandbox.glob("**/*.txt", path=workspace)
    assert glob.error is None
    assert glob.matches

    listed = sandbox.ls(workspace)
    assert listed.error is None
    assert listed.entries

    deleted = sandbox.delete(f"{workspace}/a.txt")
    assert deleted.error is None


def test_IT_FS_02_binary_round_trip(sandbox: SbxSandbox) -> None:
    payload = secrets.token_bytes(5 * 1024 * 1024)
    remote = "/home/agent/workspace/blob.bin"

    assert sandbox.upload_files([(remote, payload)])[0].error is None
    downloaded = sandbox.download_files([remote])

    assert downloaded[0].error is None
    assert downloaded[0].content == payload


# ------------------------------------------------------------------------- workspace


def test_IT_WS_01_workspace_bind_mount(tmp_path) -> None:
    (tmp_path / "from-host.txt").write_text("host-side\n", encoding="utf-8")
    name = f"dagsbx-it-{secrets.token_hex(4)}"
    backend = SbxSandbox(name=name, workspace=str(tmp_path), timeout=60, pull="missing")

    try:
        # The host workspace is mounted at the same absolute path.
        result = backend.execute(f"cat {tmp_path}/from-host.txt")
        assert result.exit_code == 0, result.output
        assert "host-side" in result.output

        # Writes inside the sandbox are visible on the host.
        backend.execute(f"echo written-inside > {tmp_path}/from-sandbox.txt")
        assert (tmp_path / "from-sandbox.txt").read_text(encoding="utf-8").strip() == "written-inside"
    finally:
        backend.remove()


# --------------------------------------------------------------------------- isolation


def test_IT_ISO_01_sandboxes_are_isolated(sbx_name: str) -> None:
    other_name = f"{sbx_name}-b"
    first = SbxSandbox(name=sbx_name, timeout=60, pull="missing")
    second = SbxSandbox(name=other_name, timeout=60, pull="missing")
    try:
        first.execute("echo first > /home/agent/workspace/shared.txt")
        second.execute("echo second > /home/agent/workspace/shared.txt")

        first_read = first.execute("cat /home/agent/workspace/shared.txt")
        second_read = second.execute("cat /home/agent/workspace/shared.txt")

        assert first_read.output.strip() == "first"
        assert second_read.output.strip() == "second"

        hostname_a = first.execute("hostname").output.strip()
        hostname_b = second.execute("hostname").output.strip()
        assert hostname_a != hostname_b
    finally:
        first.remove()
        second.remove()


# ---------------------------------------------------------------------- nested docker


def test_IT_DOCKER_01_nested_docker_daemon(sandbox: SbxSandbox) -> None:
    result = sandbox.execute("docker run --rm hello-world", timeout=180)

    assert result.exit_code == 0, result.output
    assert "Hello from Docker!" in result.output


# ------------------------------------------------------------------------------- dcode


@pytest.mark.skipif(shutil.which("dcode") is None, reason="dcode not installed")
def test_IT_DCODE_01_registers_sbx_provider() -> None:
    proc = subprocess.run(  # noqa: S603
        ["dcode", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    # The `sbx` provider should be discoverable once deepagents-sbx is installed.
    providers = subprocess.run(  # noqa: S603
        ["dcode", "sandbox", "list"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert "sbx" in (providers.stdout + providers.stderr)

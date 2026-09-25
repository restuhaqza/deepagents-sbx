"""Unit tests for the CLI transport (spec IDs UT-CMD-*, UT-EXEC-*, UT-ERR-*, UT-CP-*).

Everything runs against the fake ``sbx`` shim; no Docker, no login.
"""

from __future__ import annotations

import os
import time

import pytest

from deepagents_sbx.errors import (
    SbxAuthError,
    SbxNotFoundError,
    SbxNotInstalledError,
    SbxPolicyError,
    SbxTimeoutError,
)
from deepagents_sbx.transport import CliSbxTransport
from tests.conftest import FakeSbx


def _cli(remote_timeout: bool = False) -> CliSbxTransport:
    """A transport with the remote `timeout` wrapper off, for raw-argv assertions."""
    return CliSbxTransport(binary="sbx", remote_timeout=remote_timeout)


# --------------------------------------------------------------------------- UT-CMD


def test_UT_CMD_01_command_is_an_argv_array(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout="")
    _cli().exec("sandbox-1", "ls -la && rm -rf x", timeout=None)

    assert fake_sbx.argvs()[-1] == ["exec", "sandbox-1", "sh", "-c", "ls -la && rm -rf x"]


def test_UT_CMD_02_metacharacters_survive_byte_for_byte(fake_sbx: FakeSbx) -> None:
    command = """printf '%s' 'a "b" $HOME $(echo x) | ; *' > /tmp/out.txt"""
    fake_sbx.configure(stdout="")
    _cli().exec("sandbox-1", command, timeout=None)

    assert fake_sbx.argvs()[-1][4] == command


# --------------------------------------------------------------------------- UT-EXEC


def test_UT_EXEC_01_exit_code_zero_parsed(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout="ok", code=0)
    result = _cli().exec("s", "echo ok")

    assert result.exit_code == 0
    assert result.output == "ok"


def test_UT_EXEC_02_stdout_and_stderr_both_captured(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout="to-stdout\n", stderr="to-stderr\n", code=0)
    result = _cli().exec("s", "both")

    assert "to-stdout" in result.output
    assert "to-stderr" in result.output


def test_UT_EXEC_03_non_zero_exit_is_preserved(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout="boom", code=3)
    result = _cli().exec("s", "false")

    assert result.exit_code == 3
    assert "boom" in result.output


def test_UT_EXEC_04_truncated_flag_and_cap(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stream_mb=1)
    result = _cli().exec("s", "yes", max_output_bytes=1000)

    assert result.truncated is True
    assert len(result.output.encode("utf-8")) <= 1000


def test_UT_EXEC_05_streams_and_kills_instead_of_buffering(fake_sbx: FakeSbx) -> None:
    # 64 MiB emitted with a 4 KiB cap. If the parent buffered first, its RSS
    # would grow by the full stream; because we kill at the cap it must stay
    # tiny. resource is POSIX-only, so skip elsewhere.
    resource = pytest.importorskip("resource")
    fake_sbx.configure(stream_mb=64, code=0)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.monotonic()
    result = _cli().exec("s", "yes", max_output_bytes=4096)
    elapsed = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert result.truncated is True
    assert len(result.output) <= 4096
    assert elapsed < 20
    # macOS reports bytes, Linux KiB.
    growth = after - before
    growth_bytes = growth if os.uname().sysname == "Darwin" else growth * 1024
    assert growth_bytes < 16 * 1024 * 1024, f"parent RSS grew {growth_bytes} bytes"


def test_UT_EXEC_06_timeout_kills_the_call(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(sleep=30)
    started = time.monotonic()
    with pytest.raises(SbxTimeoutError):
        _cli().exec("s", "sleep 30", timeout=0.5)
    elapsed = time.monotonic() - started

    assert elapsed < 5


def test_UT_EXEC_07_remote_timeout_exit_124(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(code=124, stderr="timed out")
    with pytest.raises(SbxTimeoutError) as excinfo:
        _cli(remote_timeout=True).exec("s", "sleep 600", timeout=3)

    assert excinfo.value.exit_code == 124
    assert "timed out after 3s" in str(excinfo.value)
    # The command was wrapped in the sandbox-side `timeout` binary, passed as
    # separate argv elements (no nested shell quoting).
    assert fake_sbx.argvs()[-1] == ["exec", "s", "timeout", "-k", "5s", "3s", "sh", "-c", "sleep 600"]


# --------------------------------------------------------------------------- UT-ERR


def test_UT_ERR_01_not_authenticated_has_actionable_hint(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout="error: not logged in\n", code=1)
    with pytest.raises(SbxAuthError) as excinfo:
        _cli().create("demo")

    assert "Run 'sbx login' first" in str(excinfo.value)


def test_UT_ERR_01b_uninitialized_policy_has_actionable_hint(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout="error: global network policy has not been initialized\n", code=1)
    with pytest.raises(SbxPolicyError) as excinfo:
        _cli().create("demo")

    assert "sbx policy init" in str(excinfo.value)


def test_UT_ERR_02_missing_sandbox_maps_to_not_found(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout="error: no such sandbox 'nope'\n", code=1)
    with pytest.raises(SbxNotFoundError):
        _cli().remove("nope")


def test_UT_ERR_03_missing_binary_raises_clear_error(tmp_path, monkeypatch) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(SbxNotInstalledError) as excinfo:
        CliSbxTransport(binary="sbx").list()

    assert "sbx" in str(excinfo.value)
    assert "PATH" in str(excinfo.value)


# --------------------------------------------------------------------------- UT-CP


def test_UT_CP_01_upload_command_shape(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure()
    _cli().upload("sandbox-1", "/tmp/stage/file.txt", "/a/b.txt")

    assert fake_sbx.argvs()[-1] == ["cp", "/tmp/stage/file.txt", "sandbox-1:/a/b.txt"]


def test_UT_CP_02_download_command_shape(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure()
    _cli().download("sandbox-1", "/a/b.txt", "/tmp/stage/file.txt")

    assert fake_sbx.argvs()[-1] == ["cp", "sandbox-1:/a/b.txt", "/tmp/stage/file.txt"]

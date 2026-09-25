"""Test doubles for :class:`deepagents_sbx.transport.SbxTransport`.

* :class:`SpyTransport` records calls and returns scripted results -- used by
  backend unit tests, where asserting on the *shape* of transport calls matters.
* :class:`LocalTransport` actually runs commands with the host shell, so
  :class:`BaseSandbox`'s real helper scripts execute during contract tests.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents_sbx.errors import SbxCommandError, SbxNotFoundError
from deepagents_sbx.transport import (
    DEFAULT_MAX_OUTPUT_BYTES,
    CommandResult,
    SandboxInfo,
    SbxTransport,
)


@dataclass
class Call:
    """One recorded transport invocation."""

    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)


def _ok(argv: tuple[str, ...] = ("sbx",), output: str = "") -> CommandResult:
    return CommandResult(argv=argv, output=output, exit_code=0)


class SpyTransport(SbxTransport):
    """Records every call; returns scripted or default results."""

    def __init__(
        self,
        *,
        sandboxes: list[SandboxInfo] | None = None,
        exec_results: list[CommandResult] | None = None,
        upload_errors: list[str | None] | None = None,
        download_errors: list[str | None] | None = None,
        download_contents: list[bytes | None] | None = None,
        exec_error: Exception | None = None,
    ) -> None:
        self.calls: list[Call] = []
        self._sandboxes = list(sandboxes or [])
        self._exec_results = list(exec_results or [])
        self._upload_errors = list(upload_errors or [])
        self._download_errors = list(download_errors or [])
        self._download_contents = list(download_contents or [])
        self._exec_error = exec_error

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(Call(method, args, kwargs))

    def methods(self, name: str) -> list[Call]:
        return [call for call in self.calls if call.method == name]

    # -- SbxTransport ------------------------------------------------------

    def exec(
        self,
        sandbox: str,
        command: str,
        *,
        timeout: float | None = None,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CommandResult:
        self._record("exec", sandbox, command, timeout=timeout, max_output_bytes=max_output_bytes)
        if self._exec_error is not None:
            raise self._exec_error
        if self._exec_results:
            return self._exec_results.pop(0)
        return _ok()

    def upload(self, sandbox: str, local_path: str, remote_path: str) -> CommandResult:
        mode = stat.S_IMODE(os.stat(local_path).st_mode)
        self._record("upload", sandbox, local_path, remote_path, mode=mode)
        error = self._upload_errors.pop(0) if self._upload_errors else None
        if error is not None:
            raise SbxCommandError(error)
        return _ok()

    def download(self, sandbox: str, remote_path: str, local_path: str) -> CommandResult:
        self._record("download", sandbox, remote_path, local_path)
        error = self._download_errors.pop(0) if self._download_errors else None
        if error is not None:
            raise SbxCommandError(error)
        content = self._download_contents.pop(0) if self._download_contents else b""
        Path(local_path).write_bytes(content or b"")
        return _ok()

    def create(
        self,
        name: str,
        *,
        agent: str = "shell",
        workspace: str | None = None,
        cpus: int | None = None,
        memory: str | None = None,
        profile: str | None = None,
        pull: str | None = None,
        ttl: str | None = None,
        on_timeout: str | None = None,
    ) -> CommandResult:
        self._record(
            "create",
            name,
            agent=agent,
            workspace=workspace,
            cpus=cpus,
            memory=memory,
            profile=profile,
            pull=pull,
            ttl=ttl,
            on_timeout=on_timeout,
        )
        return _ok()

    def remove(self, name: str, *, force: bool = True) -> CommandResult:
        self._record("remove", name, force=force)
        return _ok()

    def list(self) -> list[SandboxInfo]:
        self._record("list")
        return list(self._sandboxes)

    def inspect(self, name: str) -> dict[str, Any] | None:
        self._record("inspect", name)
        return None


class LocalTransport(SbxTransport):
    """Runs commands with the host ``sh`` against real host paths.

    Contract tests pass absolute paths inside a ``tmp_path``; because host and
    "sandbox" are the same filesystem, no path mapping is needed and the real
    ``python3`` helper scripts from ``BaseSandbox`` execute unchanged.

    POSIX-only: it requires ``sh`` and ``python3`` on the test host.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []

    def exec(
        self,
        sandbox: str,
        command: str,
        *,
        timeout: float | None = None,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CommandResult:
        self.calls.append(Call("exec", (sandbox, command)))
        try:
            proc = subprocess.run(  # noqa: S603
                ["sh", "-c", command],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(argv=("sh", "-c", command), output="timed out", exit_code=None)
        output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        truncated = len(output) > max_output_bytes
        return CommandResult(
            argv=("sh", "-c", command),
            output=output[:max_output_bytes],
            exit_code=proc.returncode,
            truncated=truncated,
        )

    def upload(self, sandbox: str, local_path: str, remote_path: str) -> CommandResult:
        self.calls.append(Call("upload", (sandbox, local_path, remote_path)))
        target = Path(remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, target)
        return _ok()

    def download(self, sandbox: str, remote_path: str, local_path: str) -> CommandResult:
        self.calls.append(Call("download", (sandbox, remote_path, local_path)))
        source = Path(remote_path)
        if not source.exists():
            raise SbxNotFoundError(f"no such file: {remote_path}")
        shutil.copyfile(source, local_path)
        return _ok()

    def create(
        self,
        name: str,
        *,
        agent: str = "shell",
        workspace: str | None = None,
        cpus: int | None = None,
        memory: str | None = None,
        profile: str | None = None,
        pull: str | None = None,
        ttl: str | None = None,
        on_timeout: str | None = None,
    ) -> CommandResult:
        return _ok()

    def remove(self, name: str, *, force: bool = True) -> CommandResult:
        return _ok()

    def list(self) -> list[SandboxInfo]:
        return []

    def inspect(self, name: str) -> dict[str, Any] | None:
        return None

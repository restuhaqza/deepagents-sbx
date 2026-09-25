"""Transport layer for Docker Sandboxes (``sbx``).

:class:`SbxTransport` is the seam between :class:`~deepagents_sbx.backend.SbxSandbox`
and whatever actually talks to Docker Sandboxes. The first implementation,
:class:`CliSbxTransport`, shells out to the ``sbx`` CLI, which is the only
*supported* local interface (there is no documented local REST API).

Two independent axes are meant to stay swappable behind this interface:

* **environment** -- local vs. cloud;
* **implementation** -- CLI subprocess vs. an HTTP client vs. the official
  ``@docker/sandboxes`` TypeScript SDK.

Cloud transport (a hand-rolled REST client, or the official SDK from JS) can be
dropped in later without touching ``SbxSandbox``.

Design notes
------------
* Commands are ALWAYS passed as an argv array with ``shell=False`` on the host.
  The user's command is wrapped in a single ``sh -c`` *inside* the sandbox, so
  no host-side interpolation happens.
* Output is streamed and the subprocess is killed the moment ``max_output_bytes``
  is reached. It is never fully buffered and then trimmed -- a noisy command
  would otherwise be able to exhaust host memory.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .errors import (
    SbxAuthError,
    SbxCommandError,
    SbxError,
    SbxNotFoundError,
    SbxNotInstalledError,
    SbxPolicyError,
    SbxTimeoutError,
)

DEFAULT_MAX_OUTPUT_BYTES: int = 512_000
"""Default cap on captured command output, mirroring the Python backend's cap."""

_READ_CHUNK: int = 64 * 1024
_KILL_GRACE_SECONDS: float = 5.0
_BINARY: str = "sbx"
_TIMEOUT_EXIT_CODE: int = 124
"""Exit status reported by coreutils ``timeout(1)`` when it kills a command."""

_AUTH_MARKERS: tuple[str, ...] = (
    "not logged in",
    "not authenticated",
    "you are not logged in",
    "please log in",
    "please run sbx login",
    "run 'sbx login'",
    'run "sbx login"',
    "authentication required",
    "unauthorized",
    "no credentials",
)

_POLICY_MARKERS: tuple[str, ...] = (
    "global network policy has not been initialized",
    "sbx policy init",
)

_NOT_FOUND_MARKERS: tuple[str, ...] = (
    "no such sandbox",
    "sandbox not found",
    "no sandbox named",
    "does not exist",
    "not found",
)

_LOGIN_HINT = "Run 'sbx login' first."
_POLICY_HINT = "Run 'sbx policy init <allow-all|balanced|deny-all>' first."


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Raw result of one ``sbx`` invocation."""

    argv: tuple[str, ...]
    output: str
    exit_code: int | None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        """Whether the command exited zero."""
        return self.exit_code == 0


@dataclass(frozen=True, slots=True)
class SandboxInfo:
    """One row of ``sbx ls --json``.

    ``id`` is the stable per-sandbox identifier; ``name`` is the mutable display
    name. ``raw`` keeps the untouched record for callers that need a field this
    dataclass does not model yet.
    """

    id: str
    name: str
    status: str | None = None
    agent: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


def _ensure_binary(binary: str) -> None:
    """Fail fast when ``binary`` cannot be resolved.

    An absolute/relative path is trusted as-is; a bare name is looked up on
    ``PATH``. This is what turns a missing ``sbx`` into
    :class:`SbxNotInstalledError` instead of a bare ``FileNotFoundError``.
    """
    if os.sep in binary or (os.altsep and os.altsep in binary):
        return
    if shutil.which(binary) is None:
        raise SbxNotInstalledError(
            f"{binary!r} was not found on PATH. Install Docker Sandboxes and "
            "make sure the 'sbx' CLI is available (https://docs.docker.com/ai/sandboxes/)."
        )


def _kill(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort SIGKILL of the subprocess and its process group."""
    if proc.poll() is not None:
        return
    with contextlib.suppress(Exception):
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - exercised on Windows only
            proc.kill()
    with contextlib.suppress(Exception):
        proc.kill()


def _run(
    argv: Sequence[str],
    *,
    timeout: float | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run ``argv`` streaming its combined output, with a hard size cap.

    Args:
        argv: Argument array. Never a shell string.
        timeout: Host-side deadline in seconds; the process group is killed when
            it elapses.
        max_output_bytes: Once this many bytes have been captured the process is
            killed and ``truncated`` is set on the result.
        env: Optional environment override.

    Returns:
        A :class:`CommandResult` with decoded, merged stdout+stderr.

    Raises:
        SbxNotInstalledError: The binary is missing.
        SbxTimeoutError: The deadline elapsed.
    """
    argv = [str(arg) for arg in argv]
    _ensure_binary(argv[0])

    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            env=None if env is None else {**os.environ, **env},
            start_new_session=(os.name == "posix"),
        )
    except FileNotFoundError as exc:
        raise SbxNotInstalledError(f"{argv[0]!r} could not be executed.") from exc
    except OSError as exc:
        raise SbxError(f"Failed to start {argv[0]!r}: {exc}") from exc

    buffer = bytearray()
    state = {"truncated": False}

    def _reader() -> None:
        stream = proc.stdout
        if stream is None:  # pragma: no cover - stdout is always a pipe here
            return
        with contextlib.suppress(OSError, ValueError):
            while True:
                chunk = stream.read(_READ_CHUNK)
                if not chunk:
                    return
                remaining = max_output_bytes - len(buffer)
                if remaining <= 0:
                    state["truncated"] = True
                    _kill(proc)
                    return
                if len(chunk) > remaining:
                    buffer.extend(chunk[:remaining])
                    state["truncated"] = True
                    _kill(proc)
                    return
                buffer.extend(chunk)

    reader = threading.Thread(target=_reader, name="sbx-reader", daemon=True)
    reader.start()

    timed_out = False
    exit_code: int | None
    try:
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            exit_code = proc.wait(timeout=_KILL_GRACE_SECONDS)
        if proc.poll() is None:  # pragma: no cover - SIGKILL is immediate
            exit_code = None
    finally:
        with contextlib.suppress(Exception):
            if proc.stdout is not None:
                proc.stdout.close()
        reader.join(timeout=_KILL_GRACE_SECONDS)

    output = bytes(buffer).decode("utf-8", errors="replace")
    if timed_out:
        raise SbxTimeoutError(
            f"Command timed out after {timeout}s: {' '.join(argv[:3])} ...",
            argv=tuple(argv),
            exit_code=None,
            output=output,
        )
    return CommandResult(
        argv=tuple(argv),
        output=output,
        exit_code=exit_code,
        truncated=state["truncated"],
    )


def classify_failure(result: CommandResult) -> SbxError:
    """Map a non-zero :class:`CommandResult` onto the most specific error.

    Detection is message-based because the ``sbx`` CLI does not emit stable
    machine-readable error codes. Anything unrecognized becomes a
    :class:`SbxCommandError` carrying argv/exit/output.
    """
    text = result.output.lower()
    if any(marker in text for marker in _POLICY_MARKERS):
        return SbxPolicyError(f"{result.output.strip()}\n{_POLICY_HINT}")
    if any(marker in text for marker in _AUTH_MARKERS):
        return SbxAuthError(f"{result.output.strip()}\n{_LOGIN_HINT}")
    if any(marker in text for marker in _NOT_FOUND_MARKERS):
        return SbxNotFoundError(result.output.strip() or "Sandbox not found.")
    return SbxCommandError(
        result.output.strip() or f"Command failed with exit code {result.exit_code}",
        argv=result.argv,
        exit_code=result.exit_code,
        output=result.output,
    )


class SbxTransport(ABC):
    """Abstract operations :class:`SbxSandbox` needs from Docker Sandboxes."""

    @abstractmethod
    def exec(
        self,
        sandbox: str,
        command: str,
        *,
        timeout: float | None = None,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CommandResult:
        """Run ``command`` inside ``sandbox`` via ``sh -c``."""

    @abstractmethod
    def upload(self, sandbox: str, local_path: str, remote_path: str) -> CommandResult:
        """Copy a host file into the sandbox."""

    @abstractmethod
    def download(self, sandbox: str, remote_path: str, local_path: str) -> CommandResult:
        """Copy a file out of the sandbox onto the host."""

    @abstractmethod
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
    ) -> CommandResult:
        """Create a new sandbox."""

    @abstractmethod
    def remove(self, name: str, *, force: bool = True) -> CommandResult:
        """Delete a sandbox and its resources."""

    @abstractmethod
    def list(self) -> list[SandboxInfo]:
        """Return all sandboxes known to ``sbx``."""

    @abstractmethod
    def inspect(self, name: str) -> Mapping[str, Any] | None:
        """Return ``sbx inspect --json`` for ``name``, or ``None`` if absent."""

    def exists(self, name: str) -> bool:
        """Whether a sandbox with this name or stable id exists."""
        return any(name in (info.name, info.id) for info in self.list())


class CliSbxTransport(SbxTransport):
    """Local transport that shells out to the ``sbx`` CLI.

    Args:
        binary: Path or name of the CLI (default ``"sbx"``).
        env: Extra environment variables merged over ``os.environ``.
        remote_timeout: When ``True`` (default), a sandbox-side ``timeout`` wraps
            long commands so the *remote* process dies too. The host-side kill is
            always active as a backstop. Set to ``False`` on images without
            coreutils ``timeout``.
    """

    def __init__(
        self,
        binary: str = _BINARY,
        *,
        env: Mapping[str, str] | None = None,
        remote_timeout: bool = True,
        remote_kill_after: float = 5.0,
    ) -> None:
        self.binary = binary
        self.env = dict(env or {})
        self.remote_timeout = remote_timeout
        self.remote_kill_after = remote_kill_after

    # -- internals ---------------------------------------------------------

    def _run(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CommandResult:
        return _run(
            [self.binary, *args],
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            env=self.env or None,
        )

    def _check(self, result: CommandResult) -> CommandResult:
        if not result.ok:
            raise classify_failure(result)
        return result

    def _timeout_prefix(self, timeout: float | None) -> list[str]:
        """Argv prefix that wraps the command in the sandbox's coreutils ``timeout``.

        Returned as separate argv elements (``timeout -k 5s 120s``) rather than a
        nested shell string, so no additional quoting is needed: ``sbx exec``
        forwards each argv element verbatim to ``execve``.
        """
        if not self.remote_timeout or timeout is None or timeout <= 0:
            return []
        kill_after = max(1.0, float(self.remote_kill_after))
        return ["timeout", "-k", f"{kill_after:g}s", f"{int(timeout)}s"]

    # -- SbxTransport ------------------------------------------------------

    def exec(
        self,
        sandbox: str,
        command: str,
        *,
        timeout: float | None = None,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CommandResult:
        if timeout is not None and timeout <= 0:
            timeout = None
        prefix = self._timeout_prefix(timeout)
        # When the sandbox-side `timeout` wrapper is active it should fire first,
        # so give the host deadline a grace window; otherwise the host SIGKILL
        # would race the remote kill and could orphan the remote process.
        host_timeout = timeout
        if prefix and timeout is not None:
            host_timeout = timeout + max(1.0, float(self.remote_kill_after)) + 1.0
        try:
            result = self._run(
                ["exec", sandbox, *prefix, "sh", "-c", command],
                timeout=host_timeout,
                max_output_bytes=max_output_bytes,
            )
        except SbxTimeoutError as exc:
            # The host deadline carries the remote-kill grace; report the
            # caller's timeout, not the internal grace window.
            raise SbxTimeoutError(
                f"Command timed out after {timeout}s inside sandbox {sandbox!r}.",
                argv=exc.argv,
                exit_code=None,
                output=exc.output,
            ) from exc
        # When the sandbox-side wrapper is active, `timeout(1)` exits 124.
        if result.exit_code == _TIMEOUT_EXIT_CODE and prefix:
            raise SbxTimeoutError(
                f"Command timed out after {timeout}s inside sandbox {sandbox!r}.",
                argv=result.argv,
                exit_code=_TIMEOUT_EXIT_CODE,
                output=result.output,
            )
        return result

    def upload(self, sandbox: str, local_path: str, remote_path: str) -> CommandResult:
        return self._check(self._run(["cp", local_path, f"{sandbox}:{remote_path}"]))

    def download(self, sandbox: str, remote_path: str, local_path: str) -> CommandResult:
        return self._check(self._run(["cp", f"{sandbox}:{remote_path}", local_path]))

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
    ) -> CommandResult:
        args: list[str] = ["create", "--name", name]
        if cpus:
            args += ["--cpus", str(int(cpus))]
        if memory:
            args += ["--memory", memory]
        if profile:
            args += ["--profile", profile]
        if pull:
            args += ["--pull", pull]
        args.append(agent)
        if workspace:
            args.append(workspace)
        return self._check(self._run(args))

    def remove(self, name: str, *, force: bool = True) -> CommandResult:
        args = ["rm"]
        if force:
            args.append("--force")
        args.append(name)
        return self._check(self._run(args))

    def list(self) -> list[SandboxInfo]:
        result = self._check(self._run(["ls", "--json"]))
        return parse_sandbox_list(result.output)

    def inspect(self, name: str) -> Mapping[str, Any] | None:
        result = self._run(["inspect", name, "--json"])
        if not result.ok:
            error = classify_failure(result)
            if isinstance(error, SbxNotFoundError):
                return None
            raise error
        try:
            data = json.loads(result.output)
        except json.JSONDecodeError as exc:
            raise SbxCommandError(
                f"Could not parse 'sbx inspect --json' output: {exc}",
                argv=result.argv,
                exit_code=result.exit_code,
                output=result.output,
            ) from exc
        return data if isinstance(data, Mapping) else None


def parse_sandbox_list(payload: str) -> list[SandboxInfo]:
    """Parse the JSON emitted by ``sbx ls --json``.

    Tolerates both a ``{"sandboxes": [...]}`` wrapper and a bare array, and both
    ``id`` and ``name`` fields being absent.
    """
    text = payload.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SbxCommandError(f"Could not parse 'sbx ls --json' output: {exc}", output=payload) from exc

    records: Any = data.get("sandboxes", []) if isinstance(data, Mapping) else data
    if not isinstance(records, Sequence):
        return []

    infos: list[SandboxInfo] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        name = str(record.get("name") or record.get("id") or "")
        if not name:
            continue
        infos.append(
            SandboxInfo(
                id=str(record.get("id") or name),
                name=name,
                status=_optional_str(record.get("status") or record.get("state")),
                agent=_optional_str(record.get("agent")),
                raw=record,
            )
        )
    return infos


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "DEFAULT_MAX_OUTPUT_BYTES",
    "CliSbxTransport",
    "CommandResult",
    "SandboxInfo",
    "SbxTransport",
    "classify_failure",
    "parse_sandbox_list",
]

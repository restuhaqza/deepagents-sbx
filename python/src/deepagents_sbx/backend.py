"""deepagents sandbox backend backed by a Docker Sandboxes microVM.

:class:`SbxSandbox` implements the four things
:class:`deepagents.backends.sandbox.BaseSandbox` requires -- ``execute()``,
``upload_files()``, ``download_files()``, and ``id``. Every other operation
(``read``, ``write``, ``edit``, ``ls``, ``grep``, ``glob``, ``delete``) is
derived by the base class and runs through ``execute()``.

The Python base class routes read/edit (and part of glob/grep) through a
server-side ``python3`` script, so the sandbox image MUST ship ``python3``.
The built-in ``shell`` agent image (``docker/sandbox-templates:shell-docker``,
Ubuntu) does. The JavaScript port has no such requirement -- it is pure POSIX.
"""

from __future__ import annotations

import contextlib
import logging
import os
import posixpath
import shlex
import tempfile
import uuid
from collections.abc import Mapping
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from .errors import SbxError, SbxNotFoundError, SbxTimeoutError
from .transport import (
    DEFAULT_MAX_OUTPUT_BYTES,
    CliSbxTransport,
    SbxTransport,
    classify_failure,
)

logger = logging.getLogger(__name__)

DEFAULT_AGENT: str = "shell"
"""Built-in sbx agent for a generic Linux sandbox (no agent framework)."""

DEFAULT_WORKING_DIR: str = "/home/agent/workspace"
"""Directory ``sbx exec`` starts in for a workspace-less ``shell`` sandbox."""

DEFAULT_TIMEOUT: int = 120
"""Default per-command timeout in seconds."""

_UPLOAD_MODE: int = 0o666
"""Permissions for staged uploads.

``sbx cp`` preserves the source file's mode *and* ownership, and the host user's
uid is not the sandbox ``agent`` uid. A ``0600`` staging file therefore lands
unreadable (and, worse, unwritable by ``edit``) inside the VM. Staging with
``0666`` makes the file usable by the sandbox user regardless of uid mapping.
"""


def _is_absolute_safe_path(path: str) -> bool:
    """Whether ``path`` is a safe absolute sandbox path.

    Rejects empty/relative paths and ``..`` traversal, matching the file
    operation contract the base class expects.
    """
    if not path or not path.startswith("/"):
        return False
    return ".." not in path.split("/")


def _error_code(exc: SbxError, default: str = "permission_denied") -> str:
    """Map a transport error onto a standardized ``FileOperationError`` code."""
    text = str(exc).lower()
    if "permission denied" in text:
        return "permission_denied"
    if "is a directory" in text:
        return "is_directory"
    if isinstance(exc, SbxNotFoundError) or "no such file" in text or "not found" in text:
        return "file_not_found"
    return default


class SbxSandbox(BaseSandbox):
    """deepagents sandbox backend backed by a Docker Sandboxes microVM.

    Args:
        name: Sandbox name. Auto-generated when omitted. This is the handle used
            for every subsequent ``sbx`` call; the stable numeric/uuid id is
            exposed by :attr:`id`.
        agent: Built-in sbx agent to create the sandbox from (default
            ``"shell"`` -- plain Ubuntu, no agent framework).
        workspace: Host directory to bind-mount into the sandbox. When ``None``
            the agent works inside the VM filesystem only and starts in
            :data:`DEFAULT_WORKING_DIR`.
        cpus: CPU count for ``sbx create --cpus`` (``None`` = sbx default).
        memory: Memory for ``sbx create --memory`` (e.g. ``"4g"``).
        profile: Governance profile for ``sbx create --profile``.
        transport: Transport implementation. Defaults to a
            :class:`~deepagents_sbx.transport.CliSbxTransport` (local CLI).
        timeout: Default command timeout in seconds. ``0``/``None`` disables it.
        max_output_bytes: Output cap; the subprocess is killed once reached.
        auto_remove: When ``True`` (default) :meth:`close` deletes the sandbox.
        auto_create: When ``True`` (default) the sandbox is created on
            construction if it does not already exist.
        pull: Image pull policy passed through to ``sbx create`` (e.g.
            ``"missing"`` to avoid re-pulling an unchanged image).
        cloud: Target Docker Cloud Sandboxes (``sbx --cloud …``) instead of a
            local microVM. Cloud sandboxes have no host workspace, bill per
            shape, and size via :data:`~deepagents_sbx.transport.CLOUD_SHAPES`.
        ttl: Cloud-only time-to-live (e.g. ``"2h"``) before the sandbox times
            out. Strongly recommended for cost control.
        on_timeout: Cloud-only behaviour when ``ttl`` lapses: ``"delete"``
            (default) or ``"stop"``.
    """

    def __init__(
        self,
        name: str | None = None,
        *,
        agent: str = DEFAULT_AGENT,
        workspace: str | None = None,
        cpus: int | None = None,
        memory: str | None = None,
        profile: str | None = None,
        transport: SbxTransport | None = None,
        timeout: int | None = DEFAULT_TIMEOUT,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        auto_remove: bool = True,
        auto_create: bool = True,
        pull: str | None = None,
        cloud: bool = False,
        ttl: str | None = None,
        on_timeout: str | None = None,
    ) -> None:
        self._transport: SbxTransport = transport or CliSbxTransport(cloud=cloud)
        self.cloud: bool = bool(cloud or getattr(self._transport, "cloud", False))
        if self.cloud and workspace:
            msg = (
                "Cloud sandboxes have no host workspace; pass workspace=None "
                "and retrieve results with download_files()."
            )
            raise ValueError(msg)
        self.name: str = name or f"deepagents-sbx-{uuid.uuid4().hex[:8]}"
        self.agent = agent
        self.workspace = workspace
        self.cpus = cpus
        self.memory = memory
        self.profile = profile
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.auto_remove = auto_remove
        self.ttl_value = ttl
        self.on_timeout = on_timeout
        self._id: str | None = None
        self._removed = False

        if auto_create and not self._transport.exists(self.name):
            logger.debug("Creating sbx sandbox %r (agent=%s, cloud=%s)", self.name, agent, self.cloud)
            self._transport.create(
                self.name,
                agent=agent,
                workspace=workspace,
                cpus=cpus,
                memory=memory,
                profile=profile,
                pull=pull,
                ttl=ttl,
                on_timeout=on_timeout,
            )

    # -- construction helpers ---------------------------------------------

    @classmethod
    def attach(cls, name: str, **kwargs: object) -> SbxSandbox:
        """Attach to an existing sandbox without creating it.

        Raises:
            SbxNotFoundError: If no sandbox with this name/id exists.
        """
        kwargs["auto_create"] = False
        sandbox = cls(name=name, **kwargs)  # type: ignore[arg-type]
        if not sandbox._transport.exists(name):
            raise SbxNotFoundError(f"No sandbox named {name!r}")
        return sandbox

    @property
    def transport(self) -> SbxTransport:
        """The underlying transport (useful for advanced callers/tests)."""
        return self._transport

    @property
    def working_dir(self) -> str:
        """Directory new commands start in."""
        return self.workspace or DEFAULT_WORKING_DIR

    # -- BaseSandbox required interface ------------------------------------

    @property
    def id(self) -> str:
        """Stable sandbox id from ``sbx ls --json``, falling back to the name."""
        if self._id is None:
            self._id = self._resolve_id()
        return self._id

    def _resolve_id(self) -> str:
        try:
            for info in self._transport.list():
                if self.name in (info.name, info.id):
                    return info.id
        except SbxError as exc:  # pragma: no cover - defensive
            logger.debug("Could not resolve stable id for %r: %s", self.name, exc)
        return self.name

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute ``command`` inside the microVM via ``sh -c``.

        A timeout is reported as an :class:`ExecuteResponse` with
        ``exit_code=None`` (the protocol's "could not be determined") and an
        explanatory ``output`` rather than an exception, so a slow command does
        not tear down the agent loop. Configuration errors (missing CLI, missing
        login, uninitialized policy, vanished sandbox) still raise.
        """
        effective = self.timeout if timeout is None else timeout
        if effective is not None and effective <= 0:
            effective = None
        try:
            result = self._transport.exec(
                self.name,
                command,
                timeout=effective,
                max_output_bytes=self.max_output_bytes,
            )
        except SbxTimeoutError as exc:
            message = f"Error: command timed out after {effective}s."
            if exc.output:
                message += f"\n{exc.output}"
            return ExecuteResponse(output=message, exit_code=None, truncated=False)
        return ExecuteResponse(
            output=result.output,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files, one response per input, supporting partial success."""
        responses: list[FileUploadResponse] = []
        for path, data in files:
            if not _is_absolute_safe_path(path):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            tmp_path: str | None = None
            try:
                parent = posixpath.dirname(path)
                if parent and parent != "/":
                    self._ensure_dir(parent)
                with tempfile.NamedTemporaryFile(prefix="sbx-upload-", delete=False) as fh:
                    fh.write(data)
                    tmp_path = fh.name
                os.chmod(tmp_path, _UPLOAD_MODE)
                self._transport.upload(self.name, tmp_path, path)
                responses.append(FileUploadResponse(path=path, error=None))
            except OSError as exc:
                responses.append(FileUploadResponse(path=path, error=str(exc)))
            except SbxError as exc:
                responses.append(FileUploadResponse(path=path, error=_error_code(exc)))
            finally:
                if tmp_path is not None:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_path)
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files, one response per input, supporting partial success."""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            if not _is_absolute_safe_path(path):
                responses.append(FileDownloadResponse(path=path, content=None, error="invalid_path"))
                continue
            tmp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(prefix="sbx-download-", delete=False) as fh:
                    tmp_path = fh.name
                self._transport.download(self.name, path, tmp_path)
                with open(tmp_path, "rb") as fh:
                    content = fh.read()
                responses.append(FileDownloadResponse(path=path, content=content, error=None))
            except SbxNotFoundError as exc:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error=_error_code(exc, "file_not_found"))
                )
            except OSError as exc:
                responses.append(FileDownloadResponse(path=path, content=None, error=str(exc)))
            except SbxError as exc:
                responses.append(FileDownloadResponse(path=path, content=None, error=_error_code(exc)))
            finally:
                if tmp_path is not None:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_path)
        return responses

    # -- lifecycle ---------------------------------------------------------

    def _ensure_dir(self, remote_dir: str) -> None:
        result = self._transport.exec(
            self.name,
            f"mkdir -p {shlex.quote(remote_dir)}",
            timeout=self.timeout if self.timeout and self.timeout > 0 else None,
            max_output_bytes=self.max_output_bytes,
        )
        if not result.ok:
            raise classify_failure(result)

    def ttl(self) -> Mapping[str, Any] | None:
        """Return the cloud sandbox's TTL/expiration (cloud-only)."""
        return self._transport.ttl(self.name)

    def extend_ttl(self, duration: str) -> Mapping[str, Any] | None:
        """Extend the cloud sandbox's TTL by ``duration`` (e.g. ``"2h"``)."""
        return self._transport.extend_ttl(self.name, duration)

    def remove(self) -> None:
        """Remove the sandbox and its resources (idempotent).

        Deliberately **not** named ``delete``: :class:`BaseSandbox` reserves
        ``delete(file_path)`` for the file-deletion tool.
        """
        if self._removed:
            return
        try:
            self._transport.remove(self.name, force=True)
        except SbxNotFoundError:
            logger.debug("Sandbox %r already gone", self.name)
        self._removed = True

    def close(self) -> None:
        """Delete the sandbox when ``auto_remove`` is set."""
        if self.auto_remove:
            self.remove()

    def __enter__(self) -> SbxSandbox:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["DEFAULT_AGENT", "DEFAULT_TIMEOUT", "DEFAULT_WORKING_DIR", "SbxSandbox"]

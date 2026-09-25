"""Exceptions raised by the Docker Sandboxes (`sbx`) transport.

All errors derive from :class:`SbxError` so callers can catch the whole family
with one ``except``. Configuration problems that the user must fix (a missing
CLI, a missing login, an uninitialized policy) raise a distinct subclass so the
message can carry an actionable hint.
"""

from __future__ import annotations


class SbxError(RuntimeError):
    """Base class for every Docker Sandboxes transport failure."""


class SbxNotInstalledError(SbxError):
    """The ``sbx`` binary could not be found on ``PATH``.

    Raised before any subprocess runs, so it never masks a real command failure.
    """


class SbxAuthError(SbxError):
    """``sbx`` is installed but not signed in.

    Docker Sandboxes is login-gated, so this is a first-run setup problem rather
    than a bug in the command being executed.
    """


class SbxPolicyError(SbxError):
    """The global sbx network policy has not been initialized.

    ``sbx`` refuses to start any sandbox until ``sbx policy init`` has been run
    once. The hint on the exception names the command.
    """


class SbxNotFoundError(SbxError):
    """The named sandbox does not exist (or was removed concurrently)."""


class SbxCommandError(SbxError):
    """``sbx`` exited non-zero for a command the transport could not interpret.

    Carries the argv, exit code, and captured output so the caller can render a
    useful message instead of a bare traceback.
    """

    def __init__(
        self,
        message: str,
        *,
        argv: tuple[str, ...] = (),
        exit_code: int | None = None,
        output: str = "",
    ) -> None:
        super().__init__(message)
        self.argv = argv
        self.exit_code = exit_code
        self.output = output


class SbxTimeoutError(SbxCommandError):
    """A transport-level call exceeded its deadline.

    Subclasses :class:`SbxCommandError` so callers that only handle command
    failures still catch it, while ``output`` preserves whatever was captured
    before the kill.
    """


__all__ = [
    "SbxAuthError",
    "SbxCommandError",
    "SbxError",
    "SbxNotFoundError",
    "SbxNotInstalledError",
    "SbxPolicyError",
    "SbxTimeoutError",
]

"""Docker Sandboxes (``sbx``) microVM backend for Deep Agents.

Python port. Provides :class:`SbxSandbox`, a
:class:`deepagents.backends.sandbox.BaseSandbox` implementation backed by a
Docker Sandboxes microVM (its own kernel plus a private Docker daemon), plus
:class:`SbxProvider` for Deep Agents Code (``dcode --sandbox sbx``).
"""

from __future__ import annotations

from .backend import (
    DEFAULT_AGENT,
    DEFAULT_TIMEOUT,
    DEFAULT_WORKING_DIR,
    SbxSandbox,
)
from .errors import (
    SbxAuthError,
    SbxCommandError,
    SbxError,
    SbxNotFoundError,
    SbxNotInstalledError,
    SbxPolicyError,
    SbxShapeError,
    SbxTimeoutError,
)
from .transport import (
    CLOUD_SHAPES,
    DEFAULT_MAX_OUTPUT_BYTES,
    CliSbxTransport,
    CommandResult,
    SandboxInfo,
    SbxTransport,
    classify_failure,
    parse_memory_mib,
    parse_sandbox_list,
    resolve_cloud_shape,
)

__version__ = "0.1.0"

__all__ = [
    "CLOUD_SHAPES",
    "DEFAULT_AGENT",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT",
    "DEFAULT_WORKING_DIR",
    "CliSbxTransport",
    "CommandResult",
    "SandboxInfo",
    "SbxAuthError",
    "SbxCommandError",
    "SbxError",
    "SbxNotFoundError",
    "SbxNotInstalledError",
    "SbxPolicyError",
    "SbxSandbox",
    "SbxShapeError",
    "SbxTimeoutError",
    "SbxTransport",
    "__version__",
    "classify_failure",
    "parse_memory_mib",
    "parse_sandbox_list",
    "resolve_cloud_shape",
]

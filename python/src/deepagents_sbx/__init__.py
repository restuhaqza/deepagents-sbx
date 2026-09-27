"""Docker Sandboxes (``sbx``) microVM backend for Deep Agents.

Python port. Provides :class:`SbxSandbox`, a
:class:`deepagents.backends.sandbox.BaseSandbox` implementation backed by a
Docker Sandboxes microVM (its own kernel plus a private Docker daemon), plus
:class:`SbxProvider` for Deep Agents Code (``dcode --sandbox sbx``).
"""

from __future__ import annotations

from .backend import (
    DEFAULT_AGENT,
    DEFAULT_MAX_DOWNLOAD_BYTES,
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
    CONTROL_MAX_OUTPUT_BYTES,
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
from .transport_api import (
    DEFAULT_CLOUD_IMAGE,
    ENDPOINT_PERMISSIONS,
    MANAGEMENT_BASE_URL,
    ApiSbxTransport,
    Fetch,
    HttpResponse,
    classify_http_failure,
    format_duration,
    parse_duration_seconds,
    urllib_fetch,
)

__version__ = "0.2.0"

__all__ = [
    "CLOUD_SHAPES",
    "CONTROL_MAX_OUTPUT_BYTES",
    "DEFAULT_AGENT",
    "DEFAULT_CLOUD_IMAGE",
    "DEFAULT_MAX_DOWNLOAD_BYTES",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT",
    "DEFAULT_WORKING_DIR",
    "ENDPOINT_PERMISSIONS",
    "MANAGEMENT_BASE_URL",
    "ApiSbxTransport",
    "CliSbxTransport",
    "CommandResult",
    "Fetch",
    "HttpResponse",
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
    "classify_http_failure",
    "format_duration",
    "parse_duration_seconds",
    "parse_memory_mib",
    "parse_sandbox_list",
    "resolve_cloud_shape",
    "urllib_fetch",
]

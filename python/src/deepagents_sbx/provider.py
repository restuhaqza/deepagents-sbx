"""Deep Agents Code (``dcode``) sandbox provider for Docker Sandboxes.

Published under the ``deepagents_code.sandbox_providers`` entry-point group as
``sbx``, so ``dcode --sandbox sbx`` resolves to :class:`SbxProvider` once
``deepagents-sbx`` is installed.

This module imports ``deepagents_code``, which ships as the ``code`` extra. It is
deliberately *not* imported by :mod:`deepagents_sbx` so that using the bare
:class:`~deepagents_sbx.backend.SbxSandbox` backend does not require
``deepagents-code``.
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents_code.integrations.sandbox_provider import (
    SandboxInstallHint,
    SandboxProvider,
    SandboxProviderMetadata,
)

from .backend import DEFAULT_WORKING_DIR, SbxSandbox
from .errors import SbxNotFoundError
from .transport import CliSbxTransport, SbxTransport

logger = logging.getLogger(__name__)

PROVIDER_NAME: str = "sbx"
CLOUD_PROVIDER_NAME: str = "sbx-cloud"
PACKAGE_NAME: str = "deepagents-sbx"
BACKEND_MODULE: str = "deepagents_sbx.backend"

_SANDBOX_KEYS: frozenset[str] = frozenset(
    {
        "agent",
        "workspace",
        "cpus",
        "memory",
        "profile",
        "timeout",
        "max_output_bytes",
        "auto_remove",
        "pull",
        "cloud",
        "ttl",
        "on_timeout",
    }
)


class SbxProvider(SandboxProvider):
    """Create/delete Docker Sandboxes microVMs for Deep Agents Code."""

    @property
    def metadata(self) -> SandboxProviderMetadata:
        """Static capability description consumed by the dcode registry."""
        return SandboxProviderMetadata(
            name=PROVIDER_NAME,
            working_dir=DEFAULT_WORKING_DIR,
            install=SandboxInstallHint(kind="package", name=PACKAGE_NAME),
            supports_sandbox_id=True,
            supports_snapshot_name=False,
            backend_module=BACKEND_MODULE,
        )

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SbxSandbox:
        """Attach to ``sandbox_id`` if given, otherwise create a new sandbox.

        Unknown keyword arguments (e.g. ``[sandboxes.providers.sbx.params]``
        keys this version does not model yet) are ignored with a debug log
        rather than raising, so config files stay forward-compatible.
        """
        unknown = set(kwargs) - _SANDBOX_KEYS
        if unknown:
            logger.debug("Ignoring unsupported sbx provider params: %s", sorted(unknown))
        params = {key: value for key, value in kwargs.items() if key in _SANDBOX_KEYS}

        if sandbox_id:
            return SbxSandbox.attach(sandbox_id, **params)
        return SbxSandbox(**params)

    def delete(
        self,
        *,
        sandbox_id: str,
        **kwargs: Any,
    ) -> None:
        """Delete the sandbox, tolerating one that is already gone."""
        transport: SbxTransport = kwargs.get("transport") or CliSbxTransport()
        try:
            transport.remove(sandbox_id, force=True)
        except SbxNotFoundError:
            logger.debug("Sandbox %r already removed", sandbox_id)


class SbxCloudProvider(SbxProvider):
    """Provider that targets Docker Cloud Sandboxes.

    Registered as the ``sbx-cloud`` entry point so ``dcode --sandbox sbx-cloud``
    selects cloud. Cloud sandboxes have no host workspace, bill per shape, and
    support a TTL.
    """

    @property
    def metadata(self) -> SandboxProviderMetadata:
        """Static capability description for the cloud provider."""
        return SandboxProviderMetadata(
            name=CLOUD_PROVIDER_NAME,
            working_dir=DEFAULT_WORKING_DIR,
            install=SandboxInstallHint(kind="package", name=PACKAGE_NAME),
            supports_sandbox_id=True,
            supports_snapshot_name=False,
            backend_module=BACKEND_MODULE,
        )

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SbxSandbox:
        """Like :meth:`SbxProvider.get_or_create`, but always cloud."""
        kwargs["cloud"] = True
        return super().get_or_create(sandbox_id=sandbox_id, **kwargs)

    def delete(
        self,
        *,
        sandbox_id: str,
        **kwargs: Any,
    ) -> None:
        """Delete the cloud sandbox, tolerating one that is already gone."""
        kwargs.setdefault("transport", CliSbxTransport(cloud=True))
        super().delete(sandbox_id=sandbox_id, **kwargs)


__all__ = [
    "BACKEND_MODULE",
    "CLOUD_PROVIDER_NAME",
    "PACKAGE_NAME",
    "PROVIDER_NAME",
    "SbxCloudProvider",
    "SbxProvider",
]

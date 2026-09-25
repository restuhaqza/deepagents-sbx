"""Billable Docker Cloud Sandboxes integration tests (opt-in).

These create **real cloud sandboxes that cost money**. They only run when
``SBX_CLOUD_INTEGRATION=1`` is set, and every sandbox is created with a short
TTL plus ``auto_remove`` so nothing is left running.

    SBX_CLOUD_INTEGRATION=1 pytest -m integration -k CLOUD

Requires `sbx login` to a Docker account with a Cloud Sandboxes subscription.
"""

from __future__ import annotations

import os
import secrets

import pytest

from deepagents_sbx import SbxSandbox

pytestmark = pytest.mark.integration

if os.environ.get("SBX_CLOUD_INTEGRATION") != "1":
    pytest.skip(
        "set SBX_CLOUD_INTEGRATION=1 to run billable cloud integration tests",
        allow_module_level=True,
    )


def _name() -> str:
    return f"dagsbx-cloud-{secrets.token_hex(3)}"


@pytest.fixture
def cloud_sandbox() -> SbxSandbox:
    """A micro-shape cloud sandbox with a 10-minute TTL, removed on teardown."""
    sandbox = SbxSandbox(
        name=_name(),
        cloud=True,
        cpus=1,
        memory="2g",
        ttl="10m",
        on_timeout="delete",
        timeout=60,
        auto_remove=True,
    )
    try:
        yield sandbox
    finally:
        sandbox.remove()


def test_IT_CLOUD_01_lifecycle(cloud_sandbox: SbxSandbox) -> None:
    assert cloud_sandbox.cloud is True
    assert cloud_sandbox.id  # resolved id (or name)

    result = cloud_sandbox.execute("echo cloud-ok")
    assert result.exit_code == 0, result.output
    assert "cloud-ok" in result.output

    listed = [info.name for info in cloud_sandbox.transport.list()]
    assert cloud_sandbox.name in listed


def test_IT_CLOUD_02_python3_present(cloud_sandbox: SbxSandbox) -> None:
    result = cloud_sandbox.execute("python3 --version")

    assert result.exit_code == 0, result.output
    assert "Python 3" in result.output


def test_IT_CLOUD_03_binary_round_trip(cloud_sandbox: SbxSandbox) -> None:
    payload = secrets.token_bytes(1024 * 1024)
    remote = "/home/agent/workspace/cloud-blob.bin"

    assert cloud_sandbox.upload_files([(remote, payload)])[0].error is None
    downloaded = cloud_sandbox.download_files([remote])

    assert downloaded[0].error is None
    assert downloaded[0].content == payload


def test_IT_CLOUD_04_ttl_is_readable(cloud_sandbox: SbxSandbox) -> None:
    ttl = cloud_sandbox.ttl()

    assert ttl is not None
    assert ttl  # non-empty JSON object


def test_IT_CLOUD_05_ttl_can_be_extended(cloud_sandbox: SbxSandbox) -> None:
    extended = cloud_sandbox.extend_ttl("5m")

    assert extended is not None


def test_IT_CLOUD_06_workspace_is_rejected() -> None:
    with pytest.raises(ValueError, match="no host workspace"):
        SbxSandbox(name=_name(), cloud=True, workspace="/tmp/project", auto_create=False)


def test_IT_CLOUD_07_remove_removes_from_cloud() -> None:
    name = _name()
    sandbox = SbxSandbox(name=name, cloud=True, cpus=1, memory="2g", ttl="10m", auto_remove=False)
    assert name in [info.name for info in sandbox.transport.list()]

    sandbox.remove()

    assert name not in [info.name for info in sandbox.transport.list()]

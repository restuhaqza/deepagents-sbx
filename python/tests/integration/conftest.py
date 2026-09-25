"""Integration fixtures: require a real, authenticated ``sbx`` + virtualization."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Iterator

import pytest

from deepagents_sbx import SbxSandbox

pytestmark = pytest.mark.integration


def _sbx_ready() -> bool:
    if shutil.which("sbx") is None:
        return False
    try:
        proc = subprocess.run(  # noqa: S603
            ["sbx", "ls", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


@pytest.fixture(scope="session")
def sbx_ready() -> None:
    if not _sbx_ready():
        pytest.skip("requires a working, authenticated `sbx` CLI and virtualization")


@pytest.fixture
def sandbox(sbx_ready: None) -> Iterator[SbxSandbox]:
    """A fresh, uniquely named sandbox, removed on teardown."""
    name = f"dagsbx-it-{uuid.uuid4().hex[:8]}"
    backend = SbxSandbox(name=name, timeout=60, auto_remove=True, pull="missing")
    try:
        yield backend
    finally:
        backend.remove()


@pytest.fixture
def sbx_name() -> str:
    return f"dagsbx-it-{uuid.uuid4().hex[:8]}"

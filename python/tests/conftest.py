"""Shared pytest fixtures.

Unit tests never touch Docker: they put an executable ``sbx`` shim first on
``PATH``. The shim records every argv it receives (as one JSON array per line)
and emits canned output chosen through environment variables, so tests can
assert on exactly what the transport asked ``sbx`` to do.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

_SHIM = """#!/usr/bin/env python3
import json
import os
import sys
import time

argv = sys.argv[1:]
log = os.environ.get("FAKE_SBX_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\\n")

sleep = os.environ.get("FAKE_SBX_SLEEP")
if sleep:
    time.sleep(float(sleep))

stream_mb = os.environ.get("FAKE_SBX_STREAM_MB")
if stream_mb:
    remaining = int(stream_mb) * 1024 * 1024
    chunk = b"x" * 65536
    out = sys.stdout.buffer
    while remaining > 0:
        out.write(chunk)
        out.flush()
        remaining -= len(chunk)
    sys.exit(int(os.environ.get("FAKE_SBX_CODE", "0")))

stdout = os.environ.get("FAKE_SBX_STDOUT", "")
stderr = os.environ.get("FAKE_SBX_STDERR", "")
if stdout:
    sys.stdout.write(stdout)
    sys.stdout.flush()
if stderr:
    sys.stderr.write(stderr)
    sys.stderr.flush()
sys.exit(int(os.environ.get("FAKE_SBX_CODE", "0")))
"""


@dataclass
class FakeSbx:
    """Handle to the installed shim, with helpers to configure and inspect it."""

    directory: Path
    log: Path
    shim: Path
    monkeypatch: pytest.MonkeyPatch

    def configure(
        self,
        *,
        stdout: str | None = None,
        stderr: str | None = None,
        code: int | None = None,
        sleep: float | None = None,
        stream_mb: int | None = None,
    ) -> None:
        """Set the canned response for subsequent shim invocations."""
        mapping = {
            "FAKE_SBX_STDOUT": stdout,
            "FAKE_SBX_STDERR": stderr,
            "FAKE_SBX_CODE": None if code is None else str(code),
            "FAKE_SBX_SLEEP": None if sleep is None else str(sleep),
            "FAKE_SBX_STREAM_MB": None if stream_mb is None else str(stream_mb),
        }
        for key, value in mapping.items():
            if value is None:
                self.monkeypatch.delenv(key, raising=False)
            else:
                self.monkeypatch.setenv(key, value)

    def argvs(self) -> list[list[str]]:
        """Every argv the shim has received so far, oldest first."""
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line]

    def clear(self) -> None:
        """Forget recorded invocations."""
        self.log.unlink(missing_ok=True)


@pytest.fixture
def fake_sbx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSbx:
    """Install an executable ``sbx`` shim first on ``PATH``."""
    shim = tmp_path / "sbx"
    shim.write_text(_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_SBX_LOG", str(log))
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    return FakeSbx(directory=tmp_path, log=log, shim=shim, monkeypatch=monkeypatch)

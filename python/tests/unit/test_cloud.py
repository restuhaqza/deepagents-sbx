"""Cloud transport unit tests (spec ID prefix UT-CLOUD-*).

Everything runs against the fake ``sbx`` shim, so no cloud API is contacted and
nothing is billed.
"""

from __future__ import annotations

import pytest

from deepagents_sbx import SbxSandbox
from deepagents_sbx.errors import SbxError, SbxShapeError
from deepagents_sbx.provider import CLOUD_PROVIDER_NAME, SbxCloudProvider
from deepagents_sbx.transport import (
    CLOUD_SHAPES,
    CliSbxTransport,
    parse_memory_mib,
    resolve_cloud_shape,
)
from tests.conftest import FakeSbx
from tests.fakes import SpyTransport


def _cloud(remote_timeout: bool = False) -> CliSbxTransport:
    return CliSbxTransport(binary="sbx", remote_timeout=remote_timeout, cloud=True)


# ------------------------------------------------------------------------- argv shape


def test_UT_CLOUD_01_cloud_flag_is_injected_first(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout="")
    _cloud().exec("s", "echo hi")

    assert fake_sbx.argvs()[-1] == ["--cloud", "exec", "s", "sh", "-c", "echo hi"]


def test_UT_CLOUD_02_create_uses_shape_sizing(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure()
    _cloud().create("demo", cpus=4, memory="8g")

    assert fake_sbx.argvs()[-1] == [
        "--cloud",
        "create",
        "--name",
        "demo",
        "--cpus",
        "4",
        "--memory",
        "8192m",
        "shell",
    ]


def test_UT_CLOUD_05_create_defaults_to_small_shape(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure()
    _cloud().create("demo")

    argv = fake_sbx.argvs()[-1]
    assert argv[4:8] == ["--cpus", "2", "--memory", "4096m"]


def test_UT_CLOUD_03_invalid_shape_fails_before_running(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure()
    with pytest.raises(SbxShapeError) as excinfo:
        _cloud().create("demo", cpus=3)

    assert "not a billable cloud shape" in str(excinfo.value)
    assert fake_sbx.argvs() == []


def test_UT_CLOUD_04_workspace_is_rejected(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure()
    with pytest.raises(SbxError) as excinfo:
        _cloud().create("demo", workspace="/host/proj")

    assert "no host workspace" in str(excinfo.value)
    assert fake_sbx.argvs() == []


def test_UT_CLOUD_06_ttl_flags_pass_through(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure()
    _cloud().create("demo", ttl="2h", on_timeout="stop")

    argv = fake_sbx.argvs()[-1]
    assert argv[argv.index("--ttl") : argv.index("--ttl") + 2] == ["--ttl", "2h"]
    assert argv[argv.index("--on-timeout") : argv.index("--on-timeout") + 2] == ["--on-timeout", "stop"]


def test_UT_CLOUD_07_inspect_is_rejected_in_cloud(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure()
    with pytest.raises(SbxError, match="not supported in cloud mode"):
        _cloud().inspect("demo")


def test_UT_CLOUD_08_ttl_verbs(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout='{"expires_at": "2026-09-25T18:00:00Z"}')
    _cloud().ttl("demo")
    assert fake_sbx.argvs()[-1] == ["--cloud", "ttl", "demo", "--json"]

    _cloud().extend_ttl("demo", "2h")
    assert fake_sbx.argvs()[-1] == ["--cloud", "ttl", "+2h", "demo", "--json"]

    # A leading '+' is not doubled.
    _cloud().extend_ttl("demo", "+30m")
    assert fake_sbx.argvs()[-1] == ["--cloud", "ttl", "+30m", "demo", "--json"]


def test_UT_CLOUD_09_ttl_is_cloud_only(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure()
    with pytest.raises(SbxError, match="cloud-only"):
        CliSbxTransport(remote_timeout=False).ttl("demo")


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("4g", 4096),
        ("8192m", 8192),
        ("2048", 2048),
        ("2GiB", 2048),
        ("32gb", 32768),
        ("nonsense", None),
    ],
)
def test_UT_CLOUD_10_parse_memory_mib(value: str, expected: int | None) -> None:
    assert parse_memory_mib(value) == expected


def test_UT_CLOUD_11_resolve_cloud_shape() -> None:
    assert resolve_cloud_shape(None, None) == "small"
    assert resolve_cloud_shape(16, "32g") == "xl"
    assert set(CLOUD_SHAPES) == {"micro", "small", "medium", "large", "xl"}
    with pytest.raises(SbxShapeError):
        resolve_cloud_shape(8, "8g")


# --------------------------------------------------------------------------- backend


def test_UT_CLOUD_12_backend_rejects_workspace() -> None:
    with pytest.raises(ValueError, match="no host workspace"):
        SbxSandbox(name="demo", cloud=True, workspace="/host/proj", auto_create=False)


def test_UT_CLOUD_13_backend_forwards_cloud_options() -> None:
    transport = SpyTransport()
    sandbox = SbxSandbox(name="demo", transport=transport, cloud=True, ttl="1h", on_timeout="stop")

    create = transport.methods("create")[0]
    assert create.kwargs["ttl"] == "1h"
    assert create.kwargs["on_timeout"] == "stop"
    assert sandbox.cloud is True


# -------------------------------------------------------------------------- provider


def test_UT_CLOUD_14_provider_metadata_and_forced_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeSandbox:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("deepagents_sbx.provider.SbxSandbox", FakeSandbox)
    provider = SbxCloudProvider()

    assert provider.metadata.name == CLOUD_PROVIDER_NAME
    assert provider.metadata.supports_sandbox_id is True

    provider.get_or_create(name="demo", memory="4g")
    assert captured["cloud"] is True


def test_UT_CLOUD_15_provider_delete_uses_cloud_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeTransport:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def remove(self, name: str, *, force: bool = True) -> None:
            captured["removed"] = (name, force)

    monkeypatch.setattr("deepagents_sbx.provider.CliSbxTransport", FakeTransport)
    SbxCloudProvider().delete(sandbox_id="sbx_123")

    assert captured["cloud"] is True
    assert captured["removed"] == ("sbx_123", True)

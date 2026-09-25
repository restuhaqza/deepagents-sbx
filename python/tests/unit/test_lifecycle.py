"""Lifecycle unit tests (UT-ID-*, UT-CREATE-*)."""

from __future__ import annotations

from deepagents_sbx import SbxSandbox
from deepagents_sbx.transport import CliSbxTransport, SandboxInfo
from tests.conftest import FakeSbx
from tests.fakes import SpyTransport

# --------------------------------------------------------------------------- UT-ID


def test_UT_ID_01_id_uses_stable_identifier() -> None:
    transport = SpyTransport(sandboxes=[SandboxInfo(id="7e733fad-2eeb", name="demo")])
    sandbox = SbxSandbox(name="demo", transport=transport)

    assert sandbox.id == "7e733fad-2eeb"


def test_UT_ID_01b_id_falls_back_to_name() -> None:
    transport = SpyTransport(sandboxes=[])
    sandbox = SbxSandbox(name="demo", transport=transport, auto_create=False)

    assert sandbox.id == "demo"


# ------------------------------------------------------------------------ UT-CREATE


def test_UT_CREATE_01_existing_sandbox_is_reused() -> None:
    transport = SpyTransport(sandboxes=[SandboxInfo(id="x", name="demo")])
    SbxSandbox(name="demo", transport=transport)

    assert transport.methods("create") == []


def test_UT_CREATE_01b_missing_sandbox_is_created() -> None:
    transport = SpyTransport(sandboxes=[])
    SbxSandbox(name="demo", transport=transport)

    creates = transport.methods("create")
    assert len(creates) == 1
    assert creates[0].kwargs["agent"] == "shell"


def test_UT_CREATE_02_create_flags_reach_the_cli(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout='{"sandboxes": []}')
    SbxSandbox(
        name="demo",
        cpus=4,
        memory="8g",
        profile="balanced",
        transport=CliSbxTransport(remote_timeout=False),
    )

    create = next(argv for argv in fake_sbx.argvs() if argv[:1] == ["create"])
    assert create == [
        "create",
        "--name",
        "demo",
        "--cpus",
        "4",
        "--memory",
        "8g",
        "--profile",
        "balanced",
        "shell",
    ]


def test_UT_CREATE_03_no_workspace_appends_no_path(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout='{"sandboxes": []}')
    SbxSandbox(name="demo", transport=CliSbxTransport(remote_timeout=False))

    create = next(argv for argv in fake_sbx.argvs() if argv[:1] == ["create"])
    assert create == ["create", "--name", "demo", "shell"]


def test_UT_CREATE_03b_workspace_appends_host_path(fake_sbx: FakeSbx) -> None:
    fake_sbx.configure(stdout='{"sandboxes": []}')
    SbxSandbox(name="demo", workspace="/host/project", transport=CliSbxTransport(remote_timeout=False))

    create = next(argv for argv in fake_sbx.argvs() if argv[:1] == ["create"])
    assert create == ["create", "--name", "demo", "shell", "/host/project"]

"""Backend unit tests (transport mocked): path safety, partial success, timeouts."""

from __future__ import annotations

from deepagents_sbx import SbxSandbox
from deepagents_sbx.errors import SbxTimeoutError
from deepagents_sbx.transport import CommandResult
from tests.fakes import SpyTransport


def _sandbox(transport: SpyTransport, **kwargs: object) -> SbxSandbox:
    return SbxSandbox(name="demo", transport=transport, auto_create=False, **kwargs)  # type: ignore[arg-type]


# ----------------------------------------------------------------------- path safety


def test_UT_CP_03_upload_rejects_unsafe_paths() -> None:
    transport = SpyTransport()
    sandbox = _sandbox(transport)

    responses = sandbox.upload_files(
        [
            ("relative.txt", b"x"),
            ("/a/../b.txt", b"x"),
            ("/ok/deep/file.txt", b"x"),
        ]
    )

    assert responses[0].error == "invalid_path"
    assert responses[1].error == "invalid_path"
    assert responses[2].error is None
    # Only the safe path was uploaded.
    assert len(transport.methods("upload")) == 1


def test_UT_CP_03b_download_rejects_unsafe_paths() -> None:
    transport = SpyTransport()
    sandbox = _sandbox(transport)

    responses = sandbox.download_files(["etc/passwd", "/a/../../etc/passwd"])

    assert all(response.error == "invalid_path" for response in responses)
    assert transport.methods("download") == []


# -------------------------------------------------------------------- partial success


def test_CT_TR_01_upload_partial_success() -> None:
    transport = SpyTransport(upload_errors=[None, "permission denied"])
    sandbox = _sandbox(transport)

    responses = sandbox.upload_files([("/a.txt", b"1"), ("/b.txt", b"2")])

    assert responses[0].error is None
    assert responses[1].error == "permission_denied"


def test_CT_TR_02_download_partial_success() -> None:
    transport = SpyTransport(
        download_errors=[None, "no such file"],
        download_contents=[b"data"],
    )
    sandbox = _sandbox(transport)

    responses = sandbox.download_files(["/a.txt", "/missing.txt"])

    assert responses[0].content == b"data"
    assert responses[0].error is None
    assert responses[1].content is None
    assert responses[1].error == "file_not_found"


def test_upload_creates_parent_directory_first() -> None:
    transport = SpyTransport()
    sandbox = _sandbox(transport)

    sandbox.upload_files([("/deep/dir/file.txt", b"x")])

    mkdir_calls = [call for call in transport.methods("exec") if "mkdir -p" in call.args[1]]
    assert len(mkdir_calls) == 1
    assert "/deep/dir" in mkdir_calls[0].args[1]


def test_upload_stages_a_world_readable_file() -> None:
    # `sbx cp` preserves mode + ownership; a 0600 staging file lands unreadable
    # and un-editable by the sandbox user.
    transport = SpyTransport()
    sandbox = _sandbox(transport)

    sandbox.upload_files([("/ok.txt", b"x")])

    assert transport.methods("upload")[0].kwargs["mode"] == 0o666


# ---------------------------------------------------------------------------- execute


def test_execute_returns_transport_result() -> None:
    transport = SpyTransport(exec_results=[CommandResult(argv=("sbx",), output="hello", exit_code=0)])
    sandbox = _sandbox(transport)

    result = sandbox.execute("echo hello")

    assert result.output == "hello"
    assert result.exit_code == 0
    assert result.truncated is False


def test_execute_timeout_becomes_error_response() -> None:
    transport = SpyTransport(exec_error=SbxTimeoutError("host kill", output="partial output"))
    sandbox = _sandbox(transport, timeout=5)

    result = sandbox.execute("sleep 100")

    assert result.exit_code is None
    assert "timed out after 5s" in result.output
    assert "partial output" in result.output


def test_execute_forwards_default_timeout() -> None:
    transport = SpyTransport()
    sandbox = _sandbox(transport, timeout=42)

    sandbox.execute("true")

    assert transport.methods("exec")[0].kwargs["timeout"] == 42


def test_execute_zero_timeout_means_no_timeout() -> None:
    transport = SpyTransport()
    sandbox = _sandbox(transport, timeout=0)

    sandbox.execute("true")

    assert transport.methods("exec")[0].kwargs["timeout"] is None


# ---------------------------------------------------------------------------- lifecycle


def test_delete_removes_sandbox_once() -> None:
    transport = SpyTransport()
    sandbox = _sandbox(transport)

    sandbox.remove()
    sandbox.remove()

    assert len(transport.methods("remove")) == 1


def test_close_respects_auto_remove_false() -> None:
    transport = SpyTransport()
    sandbox = _sandbox(transport, auto_remove=False)

    sandbox.close()

    assert transport.methods("remove") == []


def test_close_removes_when_auto_remove_true() -> None:
    transport = SpyTransport()
    sandbox = _sandbox(transport, auto_remove=True)

    sandbox.close()

    assert len(transport.methods("remove")) == 1

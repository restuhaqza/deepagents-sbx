"""Contract tests: conformance to the deepagents ``BaseSandbox`` interface.

These deliberately do NOT mock the base class. A :class:`LocalTransport` runs
the real server-side ``python3``/POSIX helper scripts that ``BaseSandbox`` emits,
against real files under ``tmp_path``. That exercises the glue the backend is
responsible for -- ``execute`` output shape, upload/download round-trips, path
handling -- without needing Docker or a login.

IDs follow the spec's ``CT-*`` scheme so Python and JS parity can be compared.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import subprocess
import tempfile

import pytest
from deepagents.backends.sandbox import BaseSandbox

from deepagents_sbx import SbxSandbox
from tests.fakes import LocalTransport


def _grep_supports_null_separator() -> bool:
    """Whether the host ``grep`` implements ``-Z`` (NUL-separated filenames).

    ``BaseSandbox``'s fast grep path uses ``grep -rHnFZ``. macOS ships BSD grep,
    which does not emit the NUL separator -- and its ``--version`` misleadingly
    says "GNU compatible", so version sniffing is unreliable. Real sbx images
    are Ubuntu (GNU grep), so this only gates the local-shell contract harness.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write("needle\n")
        probe = handle.name
    try:
        proc = subprocess.run(  # noqa: S603
            ["grep", "-rHnFZ", "needle", probe],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            os.unlink(probe)
    return b"\x00" in proc.stdout


@pytest.fixture
def sandbox() -> SbxSandbox:
    return SbxSandbox(name="contract", transport=LocalTransport(), auto_create=False)


# ------------------------------------------------------------------------------ exec


def test_CT_EXEC_01_printf_roundtrip(sandbox: SbxSandbox) -> None:
    result = sandbox.execute("printf hi")

    assert result.output == "hi"
    assert result.exit_code == 0


# -------------------------------------------------------------------------- filesystem


def test_CT_FS_01_read_slice(sandbox: SbxSandbox, tmp_path) -> None:
    path = tmp_path / "poem.txt"
    sandbox.write(str(path), "l1\nl2\nl3\nl4\n")

    result = sandbox.read(str(path), offset=1, limit=2)

    assert result.error is None
    assert result.file_data is not None
    assert result.file_data["content"] == "l2\nl3"
    assert result.start_line == 2
    assert result.end_line == 3
    assert result.next_offset == 3


def test_CT_FS_02_read_missing_surfaces_error(sandbox: SbxSandbox, tmp_path) -> None:
    result = sandbox.read(str(tmp_path / "nope.txt"))

    assert result.error is not None
    assert result.file_data is None


def test_CT_FS_03_write_creates_file(sandbox: SbxSandbox, tmp_path) -> None:
    path = tmp_path / "nested" / "created.txt"

    result = sandbox.write(str(path), "hello")

    assert result.error is None
    assert path.read_text(encoding="utf-8") == "hello"


def test_CT_FS_04_write_overwrites_existing(sandbox: SbxSandbox, tmp_path) -> None:
    # NB: the Python BaseSandbox `write()` intentionally overwrites (unlike the
    # JS port's "already exists" guard); this pins that behavior.
    path = tmp_path / "existing.txt"
    path.write_text("old", encoding="utf-8")

    result = sandbox.write(str(path), "new")

    assert result.error is None
    assert path.read_text(encoding="utf-8") == "new"


def test_CT_FS_05_edit_unique(sandbox: SbxSandbox, tmp_path) -> None:
    path = tmp_path / "edit.txt"
    path.write_text("alpha beta gamma", encoding="utf-8")

    result = sandbox.edit(str(path), "beta", "BETA")

    assert result.error is None
    assert result.occurrences == 1
    assert path.read_text(encoding="utf-8") == "alpha BETA gamma"


def test_CT_FS_06_edit_ambiguous_without_replace_all(sandbox: SbxSandbox, tmp_path) -> None:
    path = tmp_path / "ambiguous.txt"
    path.write_text("x x x", encoding="utf-8")

    result = sandbox.edit(str(path), "x", "y", replace_all=False)

    assert result.error is not None
    assert "appears multiple times" in result.error


def test_CT_FS_07_edit_replace_all(sandbox: SbxSandbox, tmp_path) -> None:
    path = tmp_path / "all.txt"
    path.write_text("x x x", encoding="utf-8")

    result = sandbox.edit(str(path), "x", "y", replace_all=True)

    assert result.error is None
    assert result.occurrences == 3
    assert path.read_text(encoding="utf-8") == "y y y"


def test_CT_FS_08_ls_lists_entries(sandbox: SbxSandbox, tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    result = sandbox.ls(str(tmp_path))

    assert result.error is None
    assert result.entries is not None
    by_name = {entry["path"].rsplit("/", 1)[-1]: entry for entry in result.entries}
    assert by_name["sub"]["is_dir"] is True
    assert by_name["file.txt"]["is_dir"] is False


@pytest.mark.skipif(not _grep_supports_null_separator(), reason="host grep lacks -Z NUL output (BSD grep)")
def test_CT_FS_09_grep_finds_matches(sandbox: SbxSandbox, tmp_path) -> None:
    (tmp_path / "a.txt").write_text("needle here\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing\n", encoding="utf-8")

    result = sandbox.grep("needle", path=str(tmp_path))

    assert result.error is None
    assert result.matches is not None
    assert len(result.matches) == 1
    assert result.matches[0]["text"] == "needle here"


def test_CT_FS_10_glob_matches_pattern(sandbox: SbxSandbox, tmp_path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x", encoding="utf-8")
    (tmp_path / "pkg" / "readme.md").write_text("x", encoding="utf-8")

    result = sandbox.glob("**/*.py", path=str(tmp_path))

    assert result.error is None
    assert result.matches is not None
    assert [match["path"] for match in result.matches] == [str(tmp_path / "pkg" / "mod.py")]


def test_CT_FS_11_delete_then_missing(sandbox: SbxSandbox, tmp_path) -> None:
    path = tmp_path / "gone.txt"
    path.write_text("x", encoding="utf-8")

    first = sandbox.delete(str(path))
    second = sandbox.delete(str(path))

    assert first.error is None
    assert not path.exists()
    assert second.error is not None


# ------------------------------------------------------------------------- transfers


def test_CT_TR_03_binary_round_trip(sandbox: SbxSandbox, tmp_path) -> None:
    payload = secrets.token_bytes(5 * 1024 * 1024)
    remote = str(tmp_path / "blob.bin")

    upload = sandbox.upload_files([(remote, payload)])
    assert upload[0].error is None

    download = sandbox.download_files([remote])
    assert download[0].error is None
    assert download[0].content == payload


def test_CT_TR_02b_download_missing_is_flagged(sandbox: SbxSandbox, tmp_path) -> None:
    result = sandbox.download_files([str(tmp_path / "not-there.bin")])

    assert result[0].error == "file_not_found"
    assert result[0].content is None


# ------------------------------------------------------------------------ identity


def test_CT_ID_01_id_is_stable_nonempty(sandbox: SbxSandbox) -> None:
    assert isinstance(sandbox.id, str)
    assert sandbox.id
    assert sandbox.id == sandbox.id


def test_CT_TYPE_01_is_a_base_sandbox(sandbox: SbxSandbox) -> None:
    assert isinstance(sandbox, BaseSandbox)
    assert isinstance(sandbox, SbxSandbox)
    assert callable(sandbox.execute)

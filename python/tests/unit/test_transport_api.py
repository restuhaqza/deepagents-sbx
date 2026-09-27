"""Unit tests for the Docker Sandboxes REST API transport (spec IDs UT-API-*).

Every test drives :class:`ApiSbxTransport` through an injected fake ``Fetch``,
so no network is touched and nothing is billed.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
from typing import Any

import pytest

from deepagents_sbx import SbxSandbox
from deepagents_sbx.errors import (
    SbxAuthError,
    SbxCommandError,
    SbxError,
    SbxNotFoundError,
    SbxShapeError,
    SbxTimeoutError,
)
from deepagents_sbx.transport_api import (
    DEFAULT_CLOUD_IMAGE,
    ApiSbxTransport,
    HttpResponse,
    classify_http_failure,
    format_duration,
    parse_duration_seconds,
)

BASE = "https://api.test"
ENDPOINT = "https://sandbox.test"


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class Recorded:
    """One captured HTTP request."""

    def __init__(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> None:
        parsed = urllib.parse.urlparse(url)
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body
        self.path = parsed.path
        self.query = urllib.parse.parse_qs(parsed.query)


class FakeFetch:
    """Scripted :class:`Fetch` keyed by ``(method, path)``."""

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self._routes: dict[tuple[str, str], Any] = {}

    def route(self, method: str, path: str, response: Any) -> FakeFetch:
        self._routes[(method, path)] = response
        return self

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float | None,
    ) -> HttpResponse:
        record = Recorded(method, url, dict(headers), body)
        self.requests.append(record)
        handler = self._routes.get((method, record.path))
        if handler is None:
            raise AssertionError(f"unexpected request: {method} {record.path}")
        return handler(record) if callable(handler) else handler

    def last(self) -> Recorded:
        return self.requests[-1]


def resp(status: int = 200, payload: Any = None, body: bytes = b"") -> HttpResponse:
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    return HttpResponse(status, {}, body)


def sandbox(
    name: str = "demo",
    uid: str = "sbx_1",
    *,
    status: str = "running",
    etag: str = '"v1"',
    uri: str = ENDPOINT,
) -> dict[str, Any]:
    return {
        "name": f"sandboxes/{uid}",
        "uid": uid,
        "displayName": name,
        "core": {
            "status": status,
            "etag": etag,
            "agent": "shell",
            "endpoint": {"uri": uri, "protocol": "http", "credentialAudience": "aud"},
        },
    }


def transport(fetch: FakeFetch, **kwargs: Any) -> ApiSbxTransport:
    options: dict[str, Any] = {
        "access_token": "tok",
        "base_url": BASE,
        "fetch": fetch,
        "poll_interval": 0,
        "poll_timeout": 2,
        "remote_timeout": False,
    }
    options.update(kwargs)
    return ApiSbxTransport(**options)


def standard(fetch: FakeFetch, record: dict[str, Any] | None = None) -> dict[str, Any]:
    """Register the happy-path routes: list, get, and endpoint credential."""
    rec = record or sandbox()
    uid = str(rec.get("uid") or "sbx_1")
    fetch.route("GET", "/v1/sandboxes", resp(200, {"sandboxes": [rec], "nextPageToken": ""}))
    fetch.route("GET", f"/v1/sandboxes/{uid}", resp(200, rec))
    fetch.route(
        "POST",
        f"/v1/sandboxes/{uid}/endpoint-credentials",
        resp(
            200,
            {
                "token": "eptok",
                "expireTime": "2030-01-01T00:00:00Z",
                "permissions": ["sandboxesExec"],
                "sandbox": f"sandboxes/{uid}",
                "audience": "aud",
            },
        ),
    )
    return rec


# --------------------------------------------------------------------------- exec


def test_UT_API_01_exec_sends_cmd_argv_and_decodes_base64() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route(
        "POST",
        "/v1/processes/exec",
        resp(200, {"exitCode": 0, "stdout": b64("hello\n"), "stderr": b64("warn\n")}),
    )

    result = transport(fetch).exec("demo", "echo hello")

    assert result.exit_code == 0
    assert result.output == "hello\nwarn\n"
    request = fetch.last()
    assert request.headers["Authorization"] == "Bearer eptok"
    assert json.loads(request.body or b"{}") == {"cmd": ["sh", "-c", "echo hello"]}


def test_UT_API_02_exec_wraps_remote_timeout_prefix() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("POST", "/v1/processes/exec", resp(200, {"exitCode": 0, "stdout": "", "stderr": ""}))

    transport(fetch, remote_timeout=True).exec("demo", "sleep 600", timeout=3)

    assert json.loads(fetch.last().body or b"{}")["cmd"] == [
        "timeout",
        "-k",
        "5s",
        "3s",
        "sh",
        "-c",
        "sleep 600",
    ]


def test_UT_API_03_exec_timeout_exit_124_raises() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("POST", "/v1/processes/exec", resp(200, {"exitCode": 124, "stdout": "", "stderr": "timed out"}))

    with pytest.raises(SbxTimeoutError) as excinfo:
        transport(fetch, remote_timeout=True).exec("demo", "sleep 600", timeout=3)

    assert excinfo.value.exit_code == 124


def test_UT_API_04_exec_truncates_at_cap() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("POST", "/v1/processes/exec", resp(200, {"exitCode": 0, "stdout": b64("x" * 1000), "stderr": ""}))

    result = transport(fetch).exec("demo", "yes", max_output_bytes=10)

    assert result.truncated is True
    assert len(result.output.encode("utf-8")) <= 10


def test_UT_API_05_exec_incomplete_flag_marks_truncated() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route(
        "POST",
        "/v1/processes/exec",
        resp(200, {"exitCode": 0, "stdout": b64("ok"), "stderr": "", "incomplete": True}),
    )

    assert transport(fetch).exec("demo", "cmd").truncated is True


def test_UT_API_06_exec_unknown_sandbox_is_not_found() -> None:
    fetch = FakeFetch()
    fetch.route("GET", "/v1/sandboxes", resp(200, {"sandboxes": [], "nextPageToken": ""}))

    with pytest.raises(SbxNotFoundError):
        transport(fetch).exec("ghost", "ls")


def test_UT_API_07_exec_invalid_base64_falls_back_to_raw() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("POST", "/v1/processes/exec", resp(200, {"exitCode": 0, "stdout": "not-base64!!", "stderr": ""}))

    assert transport(fetch).exec("demo", "cmd").output == "not-base64!!"


# --------------------------------------------------------------------------- files


def test_UT_API_08_upload_puts_raw_body_with_mode(tmp_path) -> None:
    local = tmp_path / "f.txt"
    local.write_bytes(b"payload")
    os.chmod(local, 0o644)
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("PUT", "/v1/files/content", resp(200, {}))

    transport(fetch).upload("demo", str(local), "/remote/f.txt")

    request = fetch.last()
    assert request.body == b"payload"
    assert request.query["path"] == ["/remote/f.txt"]
    assert request.query["mode"] == [str(0o644)]
    assert request.headers["Content-Type"] == "application/octet-stream"


def test_UT_API_09_download_writes_bytes(tmp_path) -> None:
    target = tmp_path / "out.bin"
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("GET", "/v1/files/content", HttpResponse(200, {}, b"file-bytes"))

    transport(fetch).download("demo", "/remote/f.txt", str(target))

    assert target.read_bytes() == b"file-bytes"
    assert fetch.last().headers["Accept"] == "application/octet-stream"


def test_UT_API_10_download_missing_file_is_not_found() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("GET", "/v1/files/content", resp(404, {"code": "notFound", "message": "no such file"}))

    with pytest.raises(SbxNotFoundError):
        transport(fetch).download("demo", "/nope", "/tmp/nope")


# --------------------------------------------------------------------------- create


def test_UT_API_11_create_builds_body_and_waits_for_running() -> None:
    fetch = FakeFetch()
    fetch.route("POST", "/v1/sandboxes", resp(202, sandbox(status="creating")))
    fetch.route("GET", "/v1/sandboxes/sbx_1", resp(200, sandbox(status="running")))

    transport(fetch).create("demo", cpus=2, memory="4g", ttl="10m")

    post = next(r for r in fetch.requests if r.method == "POST" and r.path == "/v1/sandboxes")
    body = json.loads(post.body or b"{}")
    assert body["displayName"] == "demo"
    assert body["resources"] == {"cpus": 2, "memoryMib": "4096"}
    assert body["imageRef"] == DEFAULT_CLOUD_IMAGE
    assert body["features"]["timeouts"]["timeout"] == "600s"


def test_UT_API_12_create_rejects_workspace_before_any_request() -> None:
    fetch = FakeFetch()

    with pytest.raises(SbxError, match="no host workspace"):
        transport(fetch).create("demo", workspace="/host/project")

    assert fetch.requests == []


def test_UT_API_13_create_rejects_non_billable_shape() -> None:
    fetch = FakeFetch()

    with pytest.raises(SbxShapeError):
        transport(fetch).create("demo", cpus=3)

    assert fetch.requests == []


def test_UT_API_14_create_failed_status_raises() -> None:
    fetch = FakeFetch()
    fetch.route("POST", "/v1/sandboxes", resp(202, sandbox(status="creating")))
    failed = sandbox(status="failed")
    failed["failure"] = {"code": "internal", "message": "boom"}
    fetch.route("GET", "/v1/sandboxes/sbx_1", resp(200, failed))

    with pytest.raises(SbxError, match="failed to start"):
        transport(fetch).create("demo")


def test_UT_API_15_create_unknown_agent_has_no_image() -> None:
    fetch = FakeFetch()

    with pytest.raises(SbxError, match="no known cloud image"):
        transport(fetch).create("demo", agent="mystery")

    assert fetch.requests == []


def test_UT_API_16_create_accepts_an_explicit_image_ref() -> None:
    fetch = FakeFetch()
    fetch.route("POST", "/v1/sandboxes", resp(201, sandbox(status="running")))

    transport(fetch).create("demo", agent="ghcr.io/acme/agent:1")

    body = json.loads(fetch.last().body or b"{}")
    assert body["imageRef"] == "ghcr.io/acme/agent:1"


# --------------------------------------------------------------------------- remove


def test_UT_API_17_remove_sends_etag_and_force() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("DELETE", "/v1/sandboxes/sbx_1", HttpResponse(204, {}, b""))

    transport(fetch).remove("demo", force=True)

    request = fetch.last()
    assert request.headers["If-Match"] == '"v1"'
    assert request.query["force"] == ["true"]


def test_UT_API_18_remove_polls_until_deleted() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("DELETE", "/v1/sandboxes/sbx_1", resp(202, sandbox(status="deleting")))
    calls = {"n": 0}

    def get_handler(_request: Recorded) -> HttpResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return resp(200, sandbox(status="deleting"))
        return resp(404, {"code": "notFound", "message": "gone"})

    fetch.route("GET", "/v1/sandboxes/sbx_1", get_handler)

    transport(fetch).remove("demo")

    assert calls["n"] == 2


def test_UT_API_19_remove_missing_sandbox_is_not_found() -> None:
    fetch = FakeFetch()
    fetch.route("GET", "/v1/sandboxes", resp(200, {"sandboxes": [], "nextPageToken": ""}))

    with pytest.raises(SbxNotFoundError):
        transport(fetch).remove("ghost")


# --------------------------------------------------------------------------- list / inspect / ttl


def test_UT_API_20_list_paginates() -> None:
    fetch = FakeFetch()
    fetch.route(
        "GET",
        "/v1/sandboxes",
        lambda req: (
            resp(200, {"sandboxes": [sandbox("a", "u1")], "nextPageToken": "t1"})
            if "pageToken" not in req.query
            else resp(200, {"sandboxes": [sandbox("b", "u2")], "nextPageToken": ""})
        ),
    )

    infos = transport(fetch).list()

    assert [info.name for info in infos] == ["a", "b"]
    assert [info.id for info in infos] == ["u1", "u2"]


def test_UT_API_21_inspect_returns_none_when_absent() -> None:
    fetch = FakeFetch()
    fetch.route("GET", "/v1/sandboxes", resp(200, {"sandboxes": [], "nextPageToken": ""}))

    assert transport(fetch).inspect("missing") is None


def test_UT_API_22_ttl_reads_effective_features() -> None:
    record = sandbox()
    record["effectiveFeatures"] = {"timeouts": {"onTimeout": "delete", "initialTimeout": "600s"}}
    fetch = FakeFetch()
    standard(fetch, record)

    assert transport(fetch).ttl("demo") == {"onTimeout": "delete", "initialTimeout": "600s"}


def test_UT_API_23_extend_ttl_converts_duration_to_seconds() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route(
        "POST",
        "/v1/sandboxes/sbx_1/renew-timeout",
        resp(200, {**sandbox(), "effectiveFeatures": {"timeouts": {"initialTimeout": "7200s"}}}),
    )

    result = transport(fetch).extend_ttl("demo", "2h")

    assert json.loads(fetch.last().body or b"{}") == {"timeout": "7200s"}
    assert result == {"initialTimeout": "7200s"}


# --------------------------------------------------------------------------- auth


def test_UT_API_24_missing_credentials_raise_auth_error() -> None:
    fetch = FakeFetch()

    with pytest.raises(SbxAuthError):
        ApiSbxTransport(base_url=BASE, fetch=fetch).list()

    assert fetch.requests == []


def test_UT_API_25_pat_is_exchanged_once_and_reused() -> None:
    fetch = FakeFetch()
    fetch.route("POST", "/v2/auth/token", resp(200, {"access_token": "exchanged"}))
    fetch.route("GET", "/v1/sandboxes", resp(200, {"sandboxes": [], "nextPageToken": ""}))
    transport = ApiSbxTransport(docker_id="id", personal_access_token="pat", base_url=BASE, fetch=fetch)

    transport.list()
    transport.list()

    exchanges = [r for r in fetch.requests if r.path == "/v2/auth/token"]
    assert len(exchanges) == 1
    listing = next(r for r in fetch.requests if r.path == "/v1/sandboxes")
    assert listing.headers["Authorization"] == "Bearer exchanged"


def test_UT_API_26_expired_pat_refreshes_and_retries_once() -> None:
    fetch = FakeFetch()
    calls = {"exchange": 0, "list": 0}

    def exchange(_request: Recorded) -> HttpResponse:
        calls["exchange"] += 1
        return resp(200, {"access_token": f"tok-{calls['exchange']}"})

    def list_handler(_request: Recorded) -> HttpResponse:
        calls["list"] += 1
        if calls["list"] == 1:
            return resp(401, {"code": "unauthenticated", "message": "expired"})
        return resp(200, {"sandboxes": [], "nextPageToken": ""})

    fetch.route("POST", "/v2/auth/token", exchange)
    fetch.route("GET", "/v1/sandboxes", list_handler)
    transport = ApiSbxTransport(docker_id="id", personal_access_token="pat", base_url=BASE, fetch=fetch)

    assert transport.list() == []
    assert calls["exchange"] == 2


def test_UT_API_27_token_provider_is_used_per_request() -> None:
    fetch = FakeFetch()
    fetch.route("GET", "/v1/sandboxes", resp(200, {"sandboxes": [], "nextPageToken": ""}))
    transport = ApiSbxTransport(token_provider=lambda: "dynamic", base_url=BASE, fetch=fetch)

    transport.list()

    assert fetch.last().headers["Authorization"] == "Bearer dynamic"


def test_UT_API_28_endpoint_credential_is_cached() -> None:
    fetch = FakeFetch()
    standard(fetch)
    fetch.route("POST", "/v1/processes/exec", resp(200, {"exitCode": 0, "stdout": "", "stderr": ""}))
    api = transport(fetch)

    api.exec("demo", "one")
    api.exec("demo", "two")

    credentials = [r for r in fetch.requests if r.path.endswith("/endpoint-credentials")]
    assert len(credentials) == 1


def test_UT_API_29_unix_socket_endpoint_is_rejected() -> None:
    record = sandbox()
    record["core"]["endpoint"] = {"uri": ENDPOINT, "protocol": "unixSocket", "credentialAudience": ""}
    fetch = FakeFetch()
    standard(fetch, record)

    with pytest.raises(SbxError, match="Unix-socket"):
        transport(fetch).exec("demo", "ls")


# --------------------------------------------------------------------------- helpers / misc


@pytest.mark.parametrize(
    ("value", "expected"),
    [("90s", 90.0), ("10m", 600.0), ("2h", 7200.0), ("1.5h", 5400.0), ("45", 45.0)],
)
def test_UT_API_30_parse_duration_seconds(value: str, expected: float) -> None:
    assert parse_duration_seconds(value) == expected


def test_UT_API_31_format_duration() -> None:
    assert format_duration(600) == "600s"
    assert format_duration(1.5) == "1.5s"


def test_UT_API_32_parse_duration_rejects_garbage() -> None:
    with pytest.raises(SbxError):
        parse_duration_seconds("soon")


def test_UT_API_33_classify_http_failure_mapping() -> None:
    assert isinstance(classify_http_failure(404, b'{"code":"notFound"}', method="GET", url="u"), SbxNotFoundError)
    assert isinstance(classify_http_failure(401, b"", method="GET", url="u"), SbxAuthError)
    assert isinstance(classify_http_failure(504, b"", method="GET", url="u"), SbxTimeoutError)
    assert isinstance(
        classify_http_failure(429, b'{"code":"resourceExhausted","message":"slow"}', method="GET", url="u"),
        SbxCommandError,
    )


def test_UT_API_34_server_error_surfaces_stable_message() -> None:
    fetch = FakeFetch()
    fetch.route("GET", "/v1/sandboxes", resp(500, {"code": "internal", "message": "kBoom"}))

    with pytest.raises(SbxCommandError, match="kBoom"):
        transport(fetch).list()


def test_UT_API_35_sandbox_backend_marks_cloud_from_transport() -> None:
    fetch = FakeFetch()
    backend = SbxSandbox(name="demo", transport=transport(fetch), auto_create=False)

    assert backend.cloud is True


def test_UT_API_36_sandbox_backend_rejects_workspace_for_api_transport() -> None:
    fetch = FakeFetch()

    with pytest.raises(ValueError, match="no host workspace"):
        SbxSandbox(name="demo", transport=transport(fetch), auto_create=False, workspace="/host")

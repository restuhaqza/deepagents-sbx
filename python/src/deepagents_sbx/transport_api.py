"""HTTP transport for Docker Cloud Sandboxes (the Sandboxes REST API).

:class:`ApiSbxTransport` is the API-backed sibling of
:class:`~deepagents_sbx.transport.CliSbxTransport`: it performs the same
``SbxTransport`` operations over HTTPS instead of spawning the ``sbx`` CLI, so
cloud sandboxes can be driven without a child process.

Scope
-----
The Docker Sandboxes API is **cloud-only** and **experimental**, so this
transport sets ``cloud = True``. Local sandboxes still require the CLI -- their
``sandboxd`` daemon exposes only an undocumented Unix-socket API.

Verified against the Sandboxes API v1 OpenAPI spec
(<https://docs.docker.com/reference/api/sandboxes/latest/>):

* ``exec`` runs ``POST {endpoint}/v1/processes/exec`` with an argv ``cmd``
  vector; ``stdout``/``stderr`` come back base64-encoded with an ``incomplete``
  truncation flag.
* Files are raw ``application/octet-stream`` on
  ``PUT``/``GET {endpoint}/v1/files/content``.
* Lifecycle uses ``POST``/``GET``/``DELETE /v1/sandboxes[...]``; deletion needs
  the resource's ``If-Match`` etag.
* TTL is ``features.timeouts`` on create, ``effectiveFeatures.timeouts`` on
  read, and ``POST /v1/sandboxes/{id}/renew-timeout`` to extend. Durations are
  seconds strings (``"600s"``).

Auth is independent of ``sbx login``: pass a fixed ``access_token``, a
``token_provider`` callable, or a Docker ID + personal access token (exchanged
for a short-lived bearer). Sandbox endpoint calls use a separate short-lived
credential minted per sandbox.

Only the standard library (``urllib``) is used, so the package keeps its single
runtime dependency.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import (
    SbxAuthError,
    SbxCommandError,
    SbxError,
    SbxNotFoundError,
    SbxTimeoutError,
)
from .transport import (
    CLOUD_SHAPES,
    DEFAULT_MAX_OUTPUT_BYTES,
    CommandResult,
    SandboxInfo,
    SbxTransport,
    remote_timeout_prefix,
    resolve_cloud_shape,
)

MANAGEMENT_BASE_URL: str = "https://connect.docker.com/sandboxes"
"""Docker Cloud Sandboxes management API base (append ``/v1`` routes)."""

PAT_EXCHANGE_URL: str = "https://hub.docker.com/v2/auth/token"
"""Docker Hub endpoint that exchanges a PAT for a short-lived access token."""
DEFAULT_CLOUD_IMAGE: str = "docker/sandbox-templates:shell-docker"
"""Image used for the ``shell`` agent when creating a cloud sandbox.

The API has no ``kit: "shell"`` string field: bundled kits require prepared v2
artifacts that only the TypeScript SDK ships. Python therefore creates from a
registry image ref. Pass ``image=...`` on the transport, or an agent value
containing ``/``/``:``, to override.
"""

ENDPOINT_PERMISSIONS: tuple[str, ...] = ("sandboxesExec", "sandboxesFilesRead", "sandboxesFilesWrite")
"""Endpoint permissions requested for a sandbox's short-lived credential."""

DEFAULT_ENDPOINT_TTL: str = "300s"
"""Requested lifetime of a sandbox endpoint credential (the server caps it)."""

_API_PREFIX: str = "/v1"
_DEFAULT_USER_AGENT: str = "deepagents-sbx"
_TIMEOUT_EXIT_CODE: int = 124
_ENDPOINT_TTL_SECONDS: float = 300.0
_ENDPOINT_TTL_MARGIN: float = 30.0

_HTTP_BAD_REQUEST: int = 400
_HTTP_UNAUTHORIZED: int = 401
_HTTP_FORBIDDEN: int = 403
_HTTP_NOT_FOUND: int = 404
_HTTP_REQUEST_TIMEOUT: int = 408
_HTTP_ACCEPTED: int = 202
_HTTP_GATEWAY_TIMEOUT: int = 504

_AUTH_HINT = (
    "Provide Docker Cloud Sandboxes credentials: an access_token, a token_provider, "
    "or docker_id + personal_access_token (a Docker PAT with the 'sandbox:use' permission)."
)

_DURATION_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>s|m|h|d)?$")
_DURATION_UNITS: dict[str, float] = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_duration_seconds(value: str) -> float:
    """Parse a human duration (``"90s"``, ``"10m"``, ``"2h"``, ``"1.5h"``) to seconds.

    The API's ``Duration`` accepts only a seconds string, so CLI-style values are
    normalized through :func:`format_duration`. A bare number is treated as
    seconds.

    Raises:
        SbxError: If the value cannot be parsed.
    """
    text = str(value).strip().lower()
    match = _DURATION_RE.match(text)
    if match is None:
        raise SbxError(f"Could not parse duration {value!r}; use e.g. '90s', '10m', or '2h'.")
    amount = float(match.group("value"))
    unit = match.group("unit")
    return amount if unit is None else amount * _DURATION_UNITS[unit]


def format_duration(seconds: float) -> str:
    """Render seconds as the API's ``Duration`` string (``"600s"``)."""
    if seconds < 0:
        raise SbxError("Durations must not be negative.")
    if float(seconds).is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:.9f}".rstrip("0").rstrip(".") + "s"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """A minimal HTTP response: status, headers, and raw body bytes."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class Fetch(Protocol):
    """Callable used for every HTTP request the transport makes.

    The default (:func:`urllib_fetch`) wraps :func:`urllib.request.urlopen`.
    Inject a fake in tests, or a proxy/custom-CA transport in production.
    """

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float | None,
    ) -> HttpResponse: ...


def urllib_fetch(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float | None,
) -> HttpResponse:
    """Default :class:`Fetch`: a single ``urllib`` request, HTTP errors returned as data.

    Transport-level failures (DNS, connection, timeout) raise
    :class:`SbxError`/:class:`SbxTimeoutError`; HTTP statuses are returned so the
    caller can classify them from the API's structured error body.
    """
    # Safe: the base URL is a fixed https endpoint (or a caller-supplied one).
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return HttpResponse(int(response.status), dict(response.headers), response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(int(exc.code), dict(exc.headers or {}), exc.read())
    except TimeoutError as exc:
        raise SbxTimeoutError(f"HTTP request timed out: {method} {url}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise SbxTimeoutError(f"HTTP request timed out: {method} {url}") from exc
        raise SbxError(f"HTTP request failed: {method} {url}: {exc.reason}") from exc


def classify_http_failure(status: int, body: bytes, *, method: str, url: str) -> SbxError:
    """Map a non-2xx HTTP response onto the most specific :class:`SbxError`.

    Uses the API's stable ``code`` when present, then falls back to the HTTP
    status. Mirrors :func:`deepagents_sbx.transport.classify_failure` so callers
    see the same exception family regardless of transport.
    """
    code, message = _error_code_and_message(body)
    detail = message or f"HTTP {status}"
    context = f"{detail} ({method} {url})"
    if code == "unauthenticated" or status == _HTTP_UNAUTHORIZED:
        return SbxAuthError(f"{context}\n{_AUTH_HINT}")
    if code == "notFound" or status == _HTTP_NOT_FOUND:
        return SbxNotFoundError(message or f"Not found: {method} {url}")
    if code == "deadlineExceeded" or status in {_HTTP_REQUEST_TIMEOUT, _HTTP_GATEWAY_TIMEOUT}:
        return SbxTimeoutError(context)
    if code == "permissionDenied" or status == _HTTP_FORBIDDEN:
        return SbxCommandError(f"Permission denied: {context}", output=message or "")
    return SbxCommandError(context, output=message or "")


class ApiSbxTransport(SbxTransport):
    """Transport that drives Docker Cloud Sandboxes over the REST API.

    Args:
        access_token: A fixed management bearer token.
        token_provider: Callable returning a management bearer token; takes
            precedence over ``access_token`` and is called on every request.
        docker_id: Docker ID used with ``personal_access_token`` to exchange for
            a short-lived bearer (cached and refreshed on ``401``).
        personal_access_token: Docker PAT with the ``sandbox:use`` permission.
        base_url: Management API base; defaults to
            :data:`MANAGEMENT_BASE_URL`.
        image: Registry image ref used when ``agent="shell"`` (the API has no
            bundled-kit string field). Defaults to :data:`DEFAULT_CLOUD_IMAGE`.
        fetch: HTTP implementation; defaults to :func:`urllib_fetch`.
        remote_timeout: When ``True`` (default) a sandbox-side ``timeout`` wraps
            long commands so the *remote* process dies too.
        remote_kill_after: Seconds the sandbox-side ``timeout`` waits after
            SIGTERM before SIGKILL.
        control_timeout: Host-side deadline in seconds for control-plane calls
            (list/get/create/delete/credential). ``None``/non-positive disables.
        poll_interval: Initial seconds between create/delete status polls.
        poll_timeout: Maximum seconds to wait for a create/delete to settle.
        endpoint_ttl: Requested lifetime of a sandbox endpoint credential.
        user_agent: ``User-Agent`` header value.
    """

    cloud = True

    def __init__(
        self,
        *,
        access_token: str | None = None,
        token_provider: Callable[[], str] | None = None,
        docker_id: str | None = None,
        personal_access_token: str | None = None,
        base_url: str = MANAGEMENT_BASE_URL,
        image: str = DEFAULT_CLOUD_IMAGE,
        fetch: Fetch | None = None,
        remote_timeout: bool = True,
        remote_kill_after: float = 5.0,
        control_timeout: float | None = 120.0,
        poll_interval: float = 1.0,
        poll_timeout: float = 300.0,
        endpoint_ttl: str = DEFAULT_ENDPOINT_TTL,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._access_token = access_token
        self._token_provider = token_provider
        self._docker_id = docker_id
        self._pat = personal_access_token
        self._base_url = base_url.rstrip("/")
        self._image = image
        self._fetch: Fetch = fetch or urllib_fetch
        self.remote_timeout = remote_timeout
        self.remote_kill_after = remote_kill_after
        self.control_timeout = control_timeout
        self.poll_interval = max(0.05, poll_interval)
        self.poll_timeout = max(0.0, poll_timeout)
        self.endpoint_ttl = endpoint_ttl
        self._endpoint_ttl_seconds = parse_duration_seconds(endpoint_ttl)
        self._user_agent = user_agent
        self._cached_token: str | None = None
        self._endpoint_cache: dict[str, tuple[float, str, str]] = {}
        self._id_cache: dict[str, str] = {}

    # -- internals: auth ---------------------------------------------------

    @property
    def _control_deadline(self) -> float | None:
        value = self.control_timeout
        return value if value is not None and value > 0 else None

    def _management_token(self, *, refresh: bool = False) -> str:
        if refresh:
            self._cached_token = None
        if self._cached_token is not None:
            return self._cached_token
        if self._token_provider is not None:
            return self._token_provider()
        if self._access_token is not None:
            return self._access_token
        if self._docker_id and self._pat:
            self._cached_token = self._exchange_pat()
            return self._cached_token
        raise SbxAuthError(f"No Docker Cloud Sandboxes credentials configured.\n{_AUTH_HINT}")

    def _exchange_pat(self) -> str:
        payload = json.dumps({"identifier": self._docker_id, "secret": self._pat}).encode("utf-8")
        response = self._fetch(
            "POST",
            PAT_EXCHANGE_URL,
            {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": self._user_agent},
            payload,
            self._control_deadline,
        )
        if response.status >= _HTTP_BAD_REQUEST:
            raise SbxAuthError(
                f"Could not exchange the Docker PAT for an access token (HTTP {response.status}).\n{_AUTH_HINT}"
            )
        try:
            token = json.loads(response.body.decode("utf-8"))["access_token"]
        except (ValueError, KeyError, UnicodeDecodeError) as exc:
            raise SbxAuthError("The Docker token exchange response did not include an access_token.") from exc
        return str(token)

    # -- internals: HTTP ---------------------------------------------------

    def _send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        return self._fetch(method, url, {"User-Agent": self._user_agent, **headers}, body, timeout)

    def _management_request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        allow: Sequence[int] = (200,),
        timeout: float | None = None,
    ) -> HttpResponse:
        url = self._management_url(path, params)
        deadline = self._control_deadline if timeout is None else timeout
        payload = None if body is None else json.dumps(body).encode("utf-8")
        attempt = 0
        while True:
            request_headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self._management_token(refresh=attempt > 0)}",
            }
            if payload is not None:
                request_headers["Content-Type"] = "application/json"
            if headers:
                request_headers.update(headers)
            response = self._send(method, url, headers=request_headers, body=payload, timeout=deadline)
            if response.status in allow:
                return response
            error = classify_http_failure(response.status, response.body, method=method, url=url)
            # A cached PAT bearer may have expired; refresh once and retry.
            if isinstance(error, SbxAuthError) and attempt == 0 and self._cached_token is not None:
                attempt += 1
                continue
            raise error

    def _endpoint_request(
        self,
        method: str,
        uri: str,
        path: str,
        token: str,
        *,
        body: bytes | None = None,
        params: Mapping[str, str] | None = None,
        content_type: str | None = None,
        accept: str = "application/json",
        allow: Sequence[int] = (200,),
        timeout: float | None = None,
    ) -> HttpResponse:
        url = self._endpoint_url(uri, path, params)
        headers = {"Accept": accept, "Authorization": f"Bearer {token}"}
        if content_type is not None:
            headers["Content-Type"] = content_type
        response = self._send(method, url, headers=headers, body=body, timeout=timeout)
        if response.status in allow:
            return response
        raise classify_http_failure(response.status, response.body, method=method, url=url)

    def _management_url(self, path: str, params: Mapping[str, str] | None = None) -> str:
        return _join_url(self._base_url, path, params)

    @staticmethod
    def _endpoint_url(uri: str, path: str, params: Mapping[str, str] | None = None) -> str:
        return _join_url(uri, path, params)

    # -- internals: sandbox lookup ----------------------------------------

    @staticmethod
    def _id_from_name(name: str) -> str | None:
        if name.startswith("sandboxes/"):
            return name.split("/", 1)[1] or None
        return None

    def _find_id(self, name: str) -> str | None:
        cached = self._id_cache.get(name)
        if cached is not None:
            return cached
        direct = self._id_from_name(name)
        if direct is not None:
            return direct
        self.list()  # populates _id_cache as a side effect
        return self._id_cache.get(name)

    def _require_id(self, name: str) -> str:
        uid = self._find_id(name)
        if uid is None:
            raise SbxNotFoundError(f"No sandbox named {name!r}.")
        return uid

    def _get_sandbox(self, uid: str) -> Mapping[str, Any] | None:
        response = self._management_request(
            "GET",
            f"/sandboxes/{urllib.parse.quote(uid, safe='')}",
            allow=(200, _HTTP_NOT_FOUND),
        )
        if response.status == _HTTP_NOT_FOUND:
            return None
        return _decode_json_object(response.body)

    # -- internals: endpoint credential ------------------------------------

    def _endpoint(self, uid: str) -> tuple[str, str]:
        """Return ``(endpoint_uri, endpoint_bearer)`` for ``uid``, caching the credential."""
        now = time.monotonic()
        cached = self._endpoint_cache.get(uid)
        if cached is not None and cached[0] > now:
            return cached[1], cached[2]
        record = self._get_sandbox(uid)
        if record is None:
            raise SbxNotFoundError(f"No sandbox with id {uid!r}.")
        core = record.get("core")
        endpoint = core.get("endpoint") if isinstance(core, Mapping) else None
        if not isinstance(endpoint, Mapping) or not endpoint.get("uri"):
            raise SbxError(f"Sandbox {uid!r} has no API endpoint (is it running?).")
        if endpoint.get("protocol") == "unixSocket":
            raise SbxError(
                "The sandbox exposes only a local Unix-socket endpoint, which this transport does not support."
            )
        credential = self._create_endpoint_credential(uid)
        token = str(credential["token"])
        expiry = now + max(1.0, self._endpoint_ttl_seconds - _ENDPOINT_TTL_MARGIN)
        uri = str(endpoint["uri"])
        self._endpoint_cache[uid] = (expiry, uri, token)
        return uri, token

    def _create_endpoint_credential(self, uid: str) -> Mapping[str, Any]:
        response = self._management_request(
            "POST",
            f"/sandboxes/{urllib.parse.quote(uid, safe='')}/endpoint-credentials",
            body={"permissions": list(ENDPOINT_PERMISSIONS), "ttl": self.endpoint_ttl},
            allow=(200,),
        )
        data = _decode_json_object(response.body)
        if "token" not in data:
            raise SbxError("The endpoint credential response did not include a token.")
        return data

    # -- internals: waiting -------------------------------------------------

    def _wait_until_running(self, uid: str, *, name: str) -> Mapping[str, Any]:
        deadline = time.monotonic() + self.poll_timeout
        delay = self.poll_interval
        while True:
            record = self._get_sandbox(uid)
            if record is None:
                raise SbxNotFoundError(f"Sandbox {name!r} disappeared while starting.")
            core = record.get("core") if isinstance(record.get("core"), Mapping) else None
            status = core.get("status") if isinstance(core, Mapping) else None
            if status == "running":
                return record
            if status == "failed":
                raise SbxError(f"Cloud sandbox {name!r} failed to start: {record.get('failure') or 'unknown failure'}.")
            if time.monotonic() >= deadline:
                raise SbxTimeoutError(
                    f"Cloud sandbox {name!r} did not reach 'running' within {self.poll_timeout:g}s "
                    f"(last status: {status!r})."
                )
            time.sleep(delay)
            delay = min(delay * 2, 5.0)

    def _wait_until_deleted(self, uid: str, *, name: str) -> None:
        deadline = time.monotonic() + self.poll_timeout
        delay = self.poll_interval
        while True:
            if self._get_sandbox(uid) is None:
                return
            if time.monotonic() >= deadline:
                raise SbxTimeoutError(f"Cloud sandbox {name!r} was not deleted within {self.poll_timeout:g}s.")
            time.sleep(delay)
            delay = min(delay * 2, 5.0)

    def _image_for(self, agent: str) -> str:
        value = agent.strip()
        if not value or value == "shell":
            return self._image
        if "/" in value or ":" in value:
            return value
        raise SbxError(
            f"Agent {agent!r} has no known cloud image. Pass a registry image ref "
            f"(e.g. {DEFAULT_CLOUD_IMAGE!r}) as agent=, or set image= on the transport."
        )

    # -- SbxTransport ------------------------------------------------------

    def exec(
        self,
        sandbox: str,
        command: str,
        *,
        timeout: float | None = None,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CommandResult:
        if timeout is not None and timeout <= 0:
            timeout = None
        uid = self._require_id(sandbox)
        uri, token = self._endpoint(uid)
        prefix = remote_timeout_prefix(
            timeout,
            enabled=self.remote_timeout,
            kill_after=self.remote_kill_after,
        )
        argv = [*prefix, "sh", "-c", command]
        host_timeout = timeout
        if prefix and timeout is not None:
            host_timeout = timeout + max(1.0, float(self.remote_kill_after)) + 1.0
        response = self._endpoint_request(
            "POST",
            uri,
            "/processes/exec",
            token,
            body=json.dumps({"cmd": argv}).encode("utf-8"),
            content_type="application/json",
            timeout=host_timeout,
        )
        data = _decode_json_object(response.body)
        output = _decode_bytes_field(data.get("stdout")) + _decode_bytes_field(data.get("stderr"))
        truncated = bool(data.get("incomplete"))
        encoded = output.encode("utf-8")
        if len(encoded) > max_output_bytes:
            output = encoded[:max_output_bytes].decode("utf-8", errors="replace")
            truncated = True
        raw_exit = data.get("exitCode")
        exit_code = int(raw_exit) if isinstance(raw_exit, int) else None
        result_argv = ("api", "exec", uid)
        if exit_code == _TIMEOUT_EXIT_CODE and prefix:
            raise SbxTimeoutError(
                f"Command timed out after {timeout}s inside sandbox {sandbox!r}.",
                argv=result_argv,
                exit_code=_TIMEOUT_EXIT_CODE,
                output=output,
            )
        return CommandResult(argv=result_argv, output=output, exit_code=exit_code, truncated=truncated)

    def upload(self, sandbox: str, local_path: str, remote_path: str) -> CommandResult:
        uid = self._require_id(sandbox)
        uri, token = self._endpoint(uid)
        data = _read_bytes(local_path)
        mode = stat.S_IMODE(os.stat(local_path).st_mode)
        self._endpoint_request(
            "PUT",
            uri,
            "/files/content",
            token,
            body=data,
            params={"path": remote_path, "mode": str(mode)},
            content_type="application/octet-stream",
            allow=(200,),
        )
        return CommandResult(argv=("api", "upload", uid, remote_path), output="", exit_code=0)

    def download(self, sandbox: str, remote_path: str, local_path: str) -> CommandResult:
        uid = self._require_id(sandbox)
        uri, token = self._endpoint(uid)
        response = self._endpoint_request(
            "GET",
            uri,
            "/files/content",
            token,
            params={"path": remote_path},
            accept="application/octet-stream",
            allow=(200,),
        )
        with open(local_path, "wb") as handle:
            handle.write(response.body)
        return CommandResult(argv=("api", "download", uid, remote_path), output="", exit_code=0)

    def create(
        self,
        name: str,
        *,
        agent: str = "shell",
        workspace: str | None = None,
        cpus: int | None = None,
        memory: str | None = None,
        profile: str | None = None,
        pull: str | None = None,
        ttl: str | None = None,
        on_timeout: str | None = None,
    ) -> CommandResult:
        if workspace:
            raise SbxError(
                "Cloud sandboxes have no host workspace; omit 'workspace' "
                "(download results with download_files() instead)."
            )
        shape = resolve_cloud_shape(cpus, memory)
        shape_cpus, shape_mib = CLOUD_SHAPES[shape]
        body: dict[str, Any] = {
            "displayName": name,
            "resources": {"cpus": shape_cpus, "memoryMib": str(shape_mib)},
            "imageRef": self._image_for(agent),
        }
        if pull == "always":
            body["pullPolicy"] = "always"
        if ttl:
            timeouts: dict[str, Any] = {"timeout": format_duration(parse_duration_seconds(ttl))}
            if on_timeout:
                timeouts["onTimeout"] = on_timeout
            body["features"] = {"timeouts": timeouts}
        response = self._management_request("POST", "/sandboxes", body=body, allow=(201, 202))
        data = _decode_json_object(response.body)
        uid = data.get("uid") or self._id_from_name(str(data.get("name", "")))
        if not uid:
            raise SbxError("The CreateSandbox response did not include a sandbox id.")
        uid = str(uid)
        self._id_cache[name] = uid
        self._endpoint_cache.pop(uid, None)
        core = data.get("core") if isinstance(data.get("core"), Mapping) else None
        status = core.get("status") if isinstance(core, Mapping) else None
        if status != "running":
            self._wait_until_running(uid, name=name)
        return CommandResult(argv=("api", "create", name), output="", exit_code=0)

    def remove(self, name: str, *, force: bool = True) -> CommandResult:
        uid = self._require_id(name)
        record = self._get_sandbox(uid)
        if record is None:
            raise SbxNotFoundError(f"No sandbox named {name!r}.")
        core = record.get("core") if isinstance(record.get("core"), Mapping) else None
        etag = core.get("etag") if isinstance(core, Mapping) else None
        if not etag:
            raise SbxError(f"Sandbox {name!r} is missing its etag; refusing to delete without a precondition.")
        response = self._management_request(
            "DELETE",
            f"/sandboxes/{urllib.parse.quote(uid, safe='')}",
            headers={"If-Match": str(etag)},
            params={"force": "true"} if force else None,
            allow=(202, 204),
        )
        self._id_cache.pop(name, None)
        self._endpoint_cache.pop(uid, None)
        if response.status == _HTTP_ACCEPTED:
            self._wait_until_deleted(uid, name=name)
        return CommandResult(argv=("api", "rm", name), output="", exit_code=0)

    def list(self) -> list[SandboxInfo]:
        infos: list[SandboxInfo] = []
        page_token: str | None = None
        while True:
            params = {"pageSize": "100"}
            if page_token:
                params["pageToken"] = page_token
            response = self._management_request("GET", "/sandboxes", params=params, allow=(200,))
            data = _decode_json_object(response.body)
            records = data.get("sandboxes")
            if isinstance(records, Sequence) and not isinstance(records, (str, bytes)):
                for record in records:
                    info = _sandbox_info(record)
                    if info is not None:
                        infos.append(info)
                        self._id_cache.setdefault(info.name, info.id)
                        self._id_cache.setdefault(info.id, info.id)
            next_token = data.get("nextPageToken")
            if not next_token:
                return infos
            page_token = str(next_token)

    def inspect(self, name: str) -> Mapping[str, Any] | None:
        uid = self._find_id(name)
        if uid is None:
            return None
        return self._get_sandbox(uid)

    def ttl(self, name: str) -> Mapping[str, Any] | None:
        uid = self._require_id(name)
        record = self._get_sandbox(uid)
        if record is None:
            raise SbxNotFoundError(f"No sandbox named {name!r}.")
        return _effective_timeouts(record)

    def extend_ttl(self, name: str, duration: str) -> Mapping[str, Any] | None:
        uid = self._require_id(name)
        response = self._management_request(
            "POST",
            f"/sandboxes/{urllib.parse.quote(uid, safe='')}/renew-timeout",
            body={"timeout": format_duration(parse_duration_seconds(duration))},
            allow=(200,),
        )
        return _effective_timeouts(_decode_json_object(response.body))


# -- module helpers --------------------------------------------------------


def _join_url(base: str, path: str, params: Mapping[str, str] | None = None) -> str:
    url = f"{base.rstrip('/')}{_API_PREFIX}/{path.lstrip('/')}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return url


def _read_bytes(local_path: str) -> bytes:
    with open(local_path, "rb") as handle:
        return handle.read()


def _decode_json_object(payload: bytes) -> Mapping[str, Any]:
    try:
        data: Any = json.loads(payload.decode("utf-8")) if payload else {}
    except (ValueError, UnicodeDecodeError) as exc:
        raise SbxCommandError(
            f"Could not parse a Sandboxes API JSON response: {exc}",
            output=payload.decode("utf-8", errors="replace"),
        ) from exc
    if not isinstance(data, Mapping):
        raise SbxCommandError("The Sandboxes API returned a non-object JSON payload.", output=str(data))
    return data


def _decode_bytes_field(value: Any) -> str:
    """Decode a ``format: byte`` field (base64) into text, falling back to raw."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    text = str(value)
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return text
    return raw.decode("utf-8", errors="replace")


def _error_code_and_message(body: bytes) -> tuple[str | None, str | None]:
    if not body:
        return None, None
    try:
        data: Any = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, None
    if not isinstance(data, Mapping):
        return None, None
    code = data.get("code")
    message = data.get("message")
    return (
        str(code) if code is not None else None,
        str(message) if message is not None else None,
    )


def _terminal_segment(value: str) -> str:
    return value.rsplit("/", 1)[-1] if value else ""


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sandbox_info(record: Any) -> SandboxInfo | None:
    if not isinstance(record, Mapping):
        return None
    raw_name = str(record.get("name") or "")
    display = record.get("displayName")
    name = str(display) if display else (raw_name or str(record.get("uid") or ""))
    if not name:
        return None
    uid = _terminal_segment(str(record.get("uid") or raw_name)) or name
    core = _as_mapping(record.get("core"))
    status = core.get("status")
    agent = core.get("agent")
    return SandboxInfo(
        id=uid,
        name=name,
        status=None if status is None else str(status),
        agent=None if agent is None else str(agent),
        raw=record,
    )


def _effective_timeouts(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    features = record.get("effectiveFeatures")
    if not isinstance(features, Mapping):
        return None
    timeouts = features.get("timeouts")
    return timeouts if isinstance(timeouts, Mapping) else None


__all__ = [
    "DEFAULT_CLOUD_IMAGE",
    "DEFAULT_ENDPOINT_TTL",
    "ENDPOINT_PERMISSIONS",
    "MANAGEMENT_BASE_URL",
    "PAT_EXCHANGE_URL",
    "ApiSbxTransport",
    "Fetch",
    "HttpResponse",
    "classify_http_failure",
    "format_duration",
    "parse_duration_seconds",
    "urllib_fetch",
]

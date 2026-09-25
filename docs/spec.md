# Implementation spec & verified findings

This is the engineering spec for `deepagents-sbx`, reconciled against the real
`sbx` CLI (v0.45.1), `deepagents` Python (0.7.19), and `deepagents` JS (1.14.1).

## Goal

A community sandbox backend that runs Deep Agents tools inside a Docker
Sandboxes (`sbx`) microVM — its own kernel plus a private Docker daemon — with
Python and JavaScript implementations that share one transport contract.

## Naming

One name for the repo, PyPI, and npm: **`deepagents-sbx`**. The `deepagents-`
prefix describes the *harness*; the reserved `langchain-` prefix is avoided
because it implies official endorsement.

## Verified environment (M0)

Probed against a real `shell` sandbox (`docker/sandbox-templates:shell-docker`,
Ubuntu 26.04):

| Fact | Result |
|---|---|
| `python3` present | ✅ `/usr/bin/python3`, Python 3.14.4 |
| coreutils `timeout` present | ✅ `/usr/bin/timeout` |
| POSIX utils (`sh awk grep find stat sed head tail base64 mkdir cp rm`) | ✅ all present |
| `docker` CLI present | ✅ (private daemon inside the VM) |
| user / `$HOME` | `agent` / `/home/agent` |
| default working dir (no workspace) | `/home/agent/workspace` |
| workspace bind mount | virtiofs, mounted at the **same absolute path** as the host dir; `PWD` becomes that path |
| `sbx exec` streams | merged stdout+stderr; exit code preserved |
| `sbx exec` on a stopped sandbox | auto-starts it |
| `sbx cp` into a missing directory | fails (`tar extract failed`) → parent `mkdir -p` is mandatory |
| `sbx ls --json` | `{"sandboxes":[{"name","id","agent","status","last_used_at","created_at"}]}` |
| `sbx inspect --json` | `{..., "state": "running", ...}` (field is `state`, not `status`) |
| network policy | must be initialized once (`sbx policy init <mode>`) before any sandbox starts |

## Architecture

```
Agent → SbxSandbox (BaseSandbox) → SbxTransport → sbx CLI → microVM
```

`SbxTransport` isolates the environment (local/cloud) and implementation
(CLI/HTTP/SDK). `SbxSandbox` implements only what `BaseSandbox` requires and is
otherwise environment-agnostic.

### Required interface

Python (`deepagents.backends.sandbox.BaseSandbox`, 0.7.19):

```python
execute(command: str, *, timeout: int | None = None) -> ExecuteResponse
upload_files(files: list[tuple[str, bytes]]) -> list[FileUploadResponse]
download_files(paths: list[str]) -> list[FileDownloadResponse]
@property
def id(self) -> str
```

JavaScript (`deepagents` 1.14.1 `BaseSandbox implements SandboxBackendProtocolV2`):

```ts
abstract readonly id: string
abstract execute(command: string): MaybePromise<ExecuteResponse>
abstract uploadFiles(files: [string, Uint8Array][]): MaybePromise<FileUploadResponse[]>
abstract downloadFiles(paths: string[]): MaybePromise<FileDownloadResponse[]>
```

The JS base class is **pure POSIX** (awk/find/stat) — no Python or Node on the
sandbox host. The Python base class uses a server-side `python3` script for
`read`/`edit` and parts of `grep`/`glob`, hence the `python3` requirement.

## Design decisions

1. **Transport = CLI shell-out for local.** The only supported local interface;
   no documented local REST API. Cloud (M5) uses a hand-rolled REST client
   (Python & JS) or the official `@docker/sandboxes` TS SDK (JS only).
2. **argv arrays, `shell=False` on the host.** The command is wrapped in a single
   `sh -c` inside the sandbox.
3. **Streaming with a hard cap.** The subprocess is killed at
   `max_output_bytes`; output is never fully buffered then trimmed.
4. **Remote + host timeout.** The sandbox-side coreutils `timeout` kills the
   remote process; the host process group is a backstop with a grace window.
5. **Timeout is an `ExecuteResponse`, not an exception.** `exit_code=None` +
   explanatory output keeps the agent loop alive. Configuration errors raise
   (`SbxNotInstalledError`, `SbxAuthError`, `SbxPolicyError`, `SbxNotFoundError`).
6. **Explicit lifecycle.** `auto_create` on construction, `auto_remove` on
   `close()`; `SbxSandbox.attach()` for reattach-only. Sandbox teardown is
   `remove()`, not `delete()` — `BaseSandbox.delete(file_path)` already owns the
   file-deletion tool.

## Error taxonomy

| Condition | Exception | Hint |
|---|---|---|
| binary missing | `SbxNotInstalledError` | install link |
| not signed in | `SbxAuthError` | `Run 'sbx login' first.` |
| policy uninitialized | `SbxPolicyError` | `Run 'sbx policy init <mode>' first.` |
| sandbox absent | `SbxNotFoundError` | — |
| unrecognized non-zero exit | `SbxCommandError` | argv + exit + output |
| deadline exceeded | `SbxTimeoutError` (⊂ `SbxCommandError`) | — |

## API mapping

| Backend need | `sbx` invocation |
|---|---|
| create | `sbx create --name <n> [--cpus N] [--memory M] [--profile P] [--pull X] shell [PATH]` |
| execute | `sbx exec <n> sh -c <cmd>` |
| upload | temp host file → `mkdir -p` → `sbx cp <tmp> <n>:<dest>` |
| download | `sbx cp <n>:<src> <tmp>` → read |
| id | `id` from `sbx ls --json`, fallback name |
| delete | `sbx rm --force <n>` |
| inspect | `sbx inspect <n> --json` (local only) |

### Cloud mode (`--cloud`)

`--cloud` is a global `sbx` flag, so the same transport is reused with the flag
injected. Differences verified against v0.45.1:

| Backend need | `sbx --cloud` invocation |
|---|---|
| create | `sbx --cloud create --name <n> --cpus <shape-cpu> --memory <shape-mib>m [--ttl D] [--on-timeout delete\|stop] shell` |
| execute / upload / download / delete / list | identical to local, with `--cloud` |
| id | `sbx --cloud ls --json` → stable `id` is an `sbx_*` string |
| inspect | ❌ not implemented in cloud mode → the transport raises `SbxError` |
| ttl | `sbx --cloud ttl <n> --json`; extend with `sbx --cloud ttl +DURATION <n> --json` |

Billable shapes: `micro` (1/2048), `small` (2/4096, default), `medium`
(4/8192), `large` (8/16384), `xl` (16/32768). Cloud sandboxes have no host
workspace; `create` rejects one.

## Testing strategy

| Level | Needs login / virtualization | Harness |
|---|---|---|
| `UT-*` unit | no | fake `sbx` shim on `PATH` recording argv |
| `CT-*` contract | no | `BaseSandbox` against a local-shell transport |
| `IT-*` integration | yes | real microVM, `pytest -m integration` |

## Milestones

| # | Deliverable | Status |
|---|---|---|
| M0 | transport spike, image verification | ✅ |
| M1 | Python `SbxSandbox` + tests | ✅ |
| M2 | `SbxProvider` + dcode entry point | ✅ |
| M3 | JS `SbxSandbox` + tests | ✅ |
| M4 | integration matrix, README, publish | ☐ publish pending (integration green on macOS) |
| M5 | cloud transport | ✅ `sbx --cloud` CLI mode, Python & JS |

## Corrections to the original spec

| Original claim | Reality (verified) |
|---|---|
| CT-FS-04: `write()` on an existing path refuses with "already exists" | Python `BaseSandbox.write()` **overwrites**; the JS port has a different contract. |
| Sandbox teardown method `delete()` | Must be `remove()`: `BaseSandbox.delete(file_path)` already owns the file-deletion tool, so overriding it breaks that tool. |
| `sbx cp` upload preserves file permissions | It preserves the source mode *and* ownership; a `0600` staging file is unreadable/unwritable by the sandbox user, so uploads are staged `0666`. |
| JS and Python base classes are equivalent | They differ: JS `ls` marks directories with a trailing `/`; JS `glob` returns paths relative to the search root; JS `read` returns `content` (not `file_data.content`); JS `grep` output is colon-parsed (`path:line:text`) while Python uses NUL separators (GNU `grep -Z`). |
| Cloud needs a REST client or the official TS SDK | `--cloud` is a **global CLI flag** covering create/exec/cp/rm/ls/stop/attach/ports/policy/ttl, so the CLI transport serves cloud too — no REST client or SDK required. |
| `sbx inspect` works everywhere | ❌ Not implemented in `--cloud` mode (v0.45.1); the transport raises. Cloud metadata comes from `ls`. |
| Cloud sandbox ids are names | `sbx --cloud ls --json` returns a stable `sbx_*` id; `SbxSandbox.id` resolves to it. |
| Cloud sizing accepts arbitrary CPU/memory | It must land on a billable shape; the backend validates and raises `SbxShapeError` before any API call. |
| `sbx ls --json` field `status` also on inspect | `inspect` uses `state`; `ls` uses `status`. |
| Working dir when a workspace is mounted | the host path itself (virtiofs mounts at the same absolute path), not `/home/agent/workspace`. |
| `SandboxProviderMetadata` shape | confirmed exactly (`name`, `working_dir`, `install`, `supports_sandbox_id`, `supports_snapshot_name`, `backend_module`). |

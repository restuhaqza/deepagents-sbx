# Usage

> New here? Start with [Concepts](concepts.md) for the mental model and
> [Use cases](use-cases.md) for scenario recipes. This page is the reference.

## Prerequisites

1. **Docker Sandboxes CLI** — install the `sbx` CLI (it is not a `pip`/`npm`
   dependency). macOS: `brew trust docker/tap && brew install docker/tap/sbx` ·
   Ubuntu 24.04+: `curl -fsSL https://get.docker.com | sudo SBX=1 sh` · Windows:
   `winget install -h Docker.sbx`. See <https://docs.docker.com/ai/sandboxes/install/>.
   Local sandboxes additionally need hardware virtualization (KVM on Linux,
   Hypervisor Platform on Windows); **cloud does not**.
2. **Sign in** — `sbx login` (Docker Sandboxes is login-gated; the same account
   covers local and cloud).
3. **Initialize the network policy once** — `sbx policy init balanced`
   (`allow-all` / `balanced` / `deny-all`). `sbx` refuses to start any sandbox
   until this has been run. Cloud keeps a **separate** policy store:
   `sbx --cloud policy init balanced`.

> Steps 1–3 are for the default CLI transport. The cloud-only
> [API transport](#api-transport-cloud-only) needs none of them (no `sbx`
> binary, no `sbx login`, no `sbx policy`), but does need its own credentials.

Verify:

```bash
sbx ls --json     # should print {"sandboxes": []} on a clean install
```

## Python

### Basic

```python
from deepagents import create_deep_agent
from deepagents_sbx import SbxSandbox

with SbxSandbox(name="my-agent") as backend:
    agent = create_deep_agent(model="anthropic:claude-sonnet-4-5", backend=backend)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Create a Python CLI and run its tests"}]}
    )
```

`with` removes the sandbox on exit (`auto_remove=True`, the default). Keep it
around instead:

```python
backend = SbxSandbox(name="my-agent", auto_remove=False)
...
backend.remove()   # explicit
```

### Constructor options

| Option | Default | Meaning |
|---|---|---|
| `name` | `deepagents-sbx-<random>` | Sandbox handle. Auto-generated if omitted. |
| `agent` | `"shell"` | Built-in sbx agent image. `shell` = plain Ubuntu. |
| `workspace` | `None` | Host dir to bind-mount at the same absolute path inside the VM. `None` = VM-only filesystem. |
| `cpus` | sbx default | `sbx create --cpus`. |
| `memory` | sbx default | `sbx create --memory`, e.g. `"4g"`. |
| `profile` | sbx default | Governance profile, e.g. `"balanced"`. |
| `transport` | `CliSbxTransport()` | Swap the transport implementation. Use `ApiSbxTransport` for the cloud API — see [API transport](#api-transport-cloud-only). |
| `timeout` | `120` | Per-command timeout (seconds). `0`/`None` disables. |
| `max_output_bytes` | `524288` | Output cap; the command is killed at the cap. |
| `max_download_bytes` | `52428800` | Cap on a single downloaded file (50 MiB); larger files fail with `file_too_large`. `0`/`None` disables. |
| `auto_remove` | `True` | Delete on `close()`. |
| `auto_create` | `True` | Create on construction if missing. |
| `pull` | sbx default | Image pull policy, e.g. `"missing"`. |
| `cloud` | `False` | Target Docker Cloud Sandboxes instead of a local microVM. |
| `ttl` | sandbox default | Cloud-only time-to-live, e.g. `"2h"`. Recommended for cost control. |
| `on_timeout` | `"delete"` | Cloud-only: `"delete"` or `"stop"` when the TTL lapses. |

### Transport tuning

Control-plane commands (`create`, `rm`, `ls`, `inspect`, `cp`, `ttl`) run with a
host-side deadline so a stalled CLI cannot block the caller forever. Tune it on
the transport:

```python
from deepagents_sbx import CliSbxTransport, SbxSandbox

backend = SbxSandbox(
    transport=CliSbxTransport(
        control_timeout=300,    # seconds; 0/None disables (default 120)
        remote_timeout=False,   # no sandbox-side `timeout` wrapper
    )
)
```

`remote_timeout=False` is for images without coreutils `timeout`; the host
deadline is still applied as a backstop.

`ApiSbxTransport` takes the same `remote_timeout` / `remote_kill_after` knobs plus
its own `control_timeout`; see [API transport](#api-transport-cloud-only).

### Attaching and reattaching

```python
backend = SbxSandbox.attach("my-agent")   # raises SbxNotFoundError if absent
print(backend.id)                          # stable id from `sbx ls --json`
```

`execute()` reaches the sandbox by name; `sbx exec` auto-starts a stopped
sandbox, so reattaching after `sbx stop` needs no extra logic.

### Error handling

```python
from deepagents_sbx import SbxAuthError, SbxPolicyError, SbxNotFoundError

try:
    backend = SbxSandbox()
except SbxAuthError:       # not logged in  → run `sbx login`
    ...
except SbxPolicyError:     # policy uninitialized → run `sbx policy init balanced`
    ...
```

All errors derive from `SbxError`. A command that exceeds its timeout is *not*
an exception at the `execute()` boundary: it returns
`ExecuteResponse(output="...timed out...", exit_code=None)`.

## Deep Agents Code

```bash
pip install "deepagents-sbx[code]"
dcode --sandbox sbx
```

The provider advertises `working_dir=/home/agent/workspace` and
`supports_sandbox_id=True` (so `--sandbox-id` reattach works). Per-provider
parameters go under `[sandboxes.providers.sbx.params]` in
`~/.deepagents/config.toml`:

```toml
[sandboxes.providers.sbx.params]
memory = "8g"
cpus = 4
profile = "balanced"
```

Unknown params are ignored with a debug log rather than raising, so configs stay
forward-compatible.

## Cloud Sandboxes

Docker Cloud Sandboxes are paid and have no host workspace. The default
transport is the same CLI with the global `sbx --cloud` flag:

> Cloud requires an active **Docker Agentic Platform subscription**, uses a
> **separate** credential / secret / policy store from local, and defaults to a
> **1-hour TTL** (service default — set `ttl` explicitly). Verify access with
> `sbx --cloud diagnose`.

```python
with SbxSandbox(cloud=True, cpus=1, memory="2g", ttl="10m") as backend:
    backend.execute("echo hello")
    artifacts = backend.download_files(["/home/agent/workspace/out.txt"])
```

```ts
const backend = new SbxSandbox({ cloud: true, cpus: 1, memory: "2g", ttl: "10m" });
```

### Billable shapes

Sizing must land exactly on a shape (the pair is validated before any API call,
raising `SbxShapeError`):

| Shape | vCPU | Memory |
|---|---|---|
| `micro` | 1 | 2048 MiB |
| `small` (default) | 2 | 4096 MiB |
| `medium` | 4 | 8192 MiB |
| `large` | 8 | 16384 MiB |
| `xl` | 16 | 32768 MiB |

```python
from deepagents_sbx import resolve_cloud_shape
resolve_cloud_shape(4, "8g")   # -> "medium"
```

### TTL

Always set a TTL so an abandoned sandbox does not keep billing:

```python
backend.ttl()              # {"expires_at": ..., "expires_in_seconds": ...}
backend.extend_ttl("30m")  # push the expiration out
```

### Deep Agents Code

```bash
dcode --sandbox sbx-cloud
```

```toml
[sandboxes.providers.sbx-cloud.params]
cpus = 4
memory = "8g"
ttl = "2h"
```

### Cloud limitations

- **No workspace bind mount** — `workspace=` raises `ValueError`. Retrieve results with
  `download_files()`.
- **`inspect()` is unsupported** in cloud mode via the CLI (`sbx inspect` is not
  implemented with `--cloud`); use `list()` for metadata, or the
  [API transport](#api-transport-cloud-only), where `inspect()` works.
- **Egress defaults to `deny-all`.** The cloud account policy is separate from
  the local one; set it with `sbx --cloud policy init balanced` before expecting
  package installs or network calls to work.
  ```bash
  sbx --cloud policy ls                 # Default: deny-all
  sbx --cloud policy init balanced      # or allow-all / deny-all
  ```
- **No `docker`-in-sandbox guarantee** beyond what the cloud image provides.

## API transport (cloud only)

By default cloud goes through the same `sbx` CLI with a global `--cloud` flag.
You can instead drive Docker Cloud Sandboxes over Docker's **experimental**
[Sandboxes API](https://docs.docker.com/ai/sandboxes-api/) with **no child
process**, by swapping the transport. It is **additive and cloud-only**: local
sandboxes still require the CLI, and `CliSbxTransport` remains the default.

Reach for it when `sbx` is not installed on the machine that runs the agent, or
when you want the API's structured errors, real `inspect()`, and pagination.

> [!WARNING]
> The API and the JS SDK are experimental — interfaces may change. Cloud compute
> is billable, and this path needs its **own** credentials: `sbx login` is not
> reused.

### Python

Standard library only — no extra dependency.

```python
from deepagents_sbx import ApiSbxTransport, SbxSandbox

transport = ApiSbxTransport(
    docker_id="your-docker-id",
    personal_access_token="dckr_pat_…",   # needs the `sandbox:use` permission
)
with SbxSandbox(cloud=True, transport=transport, ttl="10m") as backend:
    backend.execute("echo hello")
```

| Option | Meaning |
|---|---|
| `access_token` | A fixed management bearer token. |
| `token_provider` | Callable returning a bearer token; called per request. |
| `docker_id` + `personal_access_token` | Exchanged for a short-lived bearer, cached and refreshed on `401`. |
| `base_url` | Management API base (default `https://connect.docker.com/sandboxes`). |
| `image` | Registry image for `agent="shell"` (default `docker/sandbox-templates:shell-docker`). The API has no bundled-kit string field, so Python creates from an `imageRef`. |
| `control_timeout` | Host deadline in seconds for control-plane calls (default `120`; `0` disables). |
| `poll_interval` / `poll_timeout` | Backoff and overall bound while waiting for a create/delete to settle. |
| `fetch` | Inject a custom HTTP callable (proxy, custom CA, tests). |

Bundled kit names other than `shell` have no Python image mapping; pass an
explicit registry ref as `agent=`, e.g. `agent="ghcr.io/acme/agent:1"`.

### JavaScript

Built on the official [`@docker/sandboxes`](https://www.npmjs.com/package/@docker/sandboxes)
SDK, declared as an **optional peer dependency** and loaded lazily:

```bash
npm install @docker/sandboxes
```

```ts
import { pat } from "@docker/sandboxes";
import { ApiSbxTransport, SbxSandbox } from "deepagents-sbx";

const transport = new ApiSbxTransport({
  sdkOptions: {
    auth: pat({ username: process.env.DOCKER_ID!, personalAccessToken: process.env.DOCKER_PAT! }),
  },
});
const backend = new SbxSandbox({ cloud: true, ttl: "10m", transport });
try {
  await backend.execute("echo hello");
} finally {
  await backend.close();
}
```

Because the SDK is optional, merely importing `deepagents-sbx` never requires
it; a missing SDK raises an actionable error only when the transport is used.
`ApiSbxTransport` also supports `client` (a pre-built `Sandboxes`) and `loadSdk`
(a custom module loader), and `close()` releases the client it created.

The JS transport can launch the SDK's bundled kits (e.g. `agent: "claude"`),
unlike the Python port.

### Differences from the CLI path

| | CLI (`--cloud`) | API transport |
|---|---|---|
| `sbx` binary | required | **not** required |
| Auth | `sbx login` session | OAuth / PAT (`sandbox:use`) |
| `inspect()` | not implemented in cloud | works |
| Bundled kits | any agent name | Python: `imageRef` only; JS: SDK kits |
| Errors | string-matched from CLI output | API error `code` + HTTP status |
| Local sandboxes | ✅ | ❌ (cloud only) |

## Network policy

```bash
sbx policy init balanced                     # recommended
sbx policy allow network pypi.org:443        # narrow allow
sbx policy deny  network example.com:443     # narrow deny
```

Per-sandbox rules can also be set at creation with `--deny-network` via
`sbx create`; the Python backend does not expose this yet (see roadmap).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `SbxNotInstalledError: 'sbx' was not found on PATH` | Install Docker Sandboxes. |
| `SbxAuthError` | `sbx login`. |
| `SbxPolicyError` | `sbx policy init balanced`. |
| `python3` not found inside the sandbox | The Python backend needs Python 3 in the image. The built-in `shell` image has it; a custom `--template` must too. Use the JS backend otherwise. |
| File ops fail with a Python traceback | Same root cause as above. |
| `sbx create` says the name exists | The backend reuses an existing sandbox of the same name; pass a different `name` for an isolated one. |

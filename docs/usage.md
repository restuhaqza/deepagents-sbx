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
| `transport` | `CliSbxTransport()` | Swap the transport implementation. |
| `timeout` | `120` | Per-command timeout (seconds). `0`/`None` disables. |
| `max_output_bytes` | `524288` | Output cap; the command is killed at the cap. |
| `auto_remove` | `True` | Delete on `close()`. |
| `auto_create` | `True` | Create on construction if missing. |
| `pull` | sbx default | Image pull policy, e.g. `"missing"`. |
| `cloud` | `False` | Target Docker Cloud Sandboxes instead of a local microVM. |
| `ttl` | sandbox default | Cloud-only time-to-live, e.g. `"2h"`. Recommended for cost control. |
| `on_timeout` | `"delete"` | Cloud-only: `"delete"` or `"stop"` when the TTL lapses. |

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

Docker Cloud Sandboxes are paid and have no host workspace. The same transport
is used with the global `sbx --cloud` flag:

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
- **`inspect()` is unsupported** in cloud mode (`sbx inspect` is not implemented
  with `--cloud`); use `list()` for metadata.
- **Egress defaults to `deny-all`.** The cloud account policy is separate from
  the local one; set it with `sbx --cloud policy init balanced` before expecting
  package installs or network calls to work.
  ```bash
  sbx --cloud policy ls                 # Default: deny-all
  sbx --cloud policy init balanced      # or allow-all / deny-all
  ```
- **No `docker`-in-sandbox guarantee** beyond what the cloud image provides.

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

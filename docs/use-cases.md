# Use cases

Copy-paste recipes for the scenarios `deepagents-sbx` is built for. Every recipe
uses the Python API unless noted; the JavaScript API mirrors it (see
[§ 9](#9-python--javascript-parity) and [`js/README.md`](../js/README.md) for the
full JS option table).

Prerequisites for all of them:

```bash
sbx login
sbx policy init balanced        # one-time, before the first sandbox starts
```

`local` sandboxes are free; `cloud=True` sandboxes are **billable**.

The agent examples below pass a `model=...`, so you also need that model
provider's credentials (for example `ANTHROPIC_API_KEY`). Sandbox setup does not
require a model — you can call `backend.execute(...)` directly.

---

## 1. Coding agent runs generated code safely

**Scenario.** You give an agent a task ("build a CLI, write tests, run them") and
it writes and executes code you didn't review.

**Use it when** you want the agent's `execute`/`write`/`read`/`ls` tool calls to
land inside a disposable microVM instead of your host.

```python
from deepagents import create_deep_agent
from deepagents_sbx import SbxSandbox

with SbxSandbox(memory="4g") as backend:
    agent = create_deep_agent(model="anthropic:claude-sonnet-4-5", backend=backend)
    agent.invoke(
        {"messages": [{"role": "user", "content": "Create a Python package with tests and run them"}]}
    )
```

**Notes**

- `with` removes the sandbox on exit (`auto_remove=True`).
- Without a `workspace`, the agent works in the VM filesystem only, starting in
  `/home/agent/workspace`.
- Keep it around for inspection: `SbxSandbox(auto_remove=False)` then
  `backend.remove()` when done.

---

## 2. Run untrusted code with egress control

**Scenario.** The agent will run arbitrary, possibly hostile code. You want a
hard boundary *and* a say in what it can reach on the network.

**Use it when** the code must be able to install a few known packages but must
not phone home anywhere else.

```bash
sbx policy init balanced                       # baseline
sbx policy allow network pypi.org:443 files.pythonhosted.org:443
sbx policy deny  network example.com:443       # narrow deny
sbx policy ls                                  # review
```

```python
from deepagents_sbx import SbxSandbox

with SbxSandbox(memory="4g") as backend:
    print(backend.execute("pip install requests").output)
    print(backend.execute("curl -sS https://example.com").output)  # blocked
```

**Notes**

- The **local** policy applies to your `sbx` install on this machine (global, not
  per sandbox). The CLI can set per-sandbox rules at creation with
  `--deny-network`, but this package does not expose them yet.
- Cloud has its **own** account policy, defaulting to `deny-all`:
  `sbx --cloud policy ls` / `sbx --cloud policy init balanced`. The two are
  independent.
- See [Usage § Network policy](usage.md#network-policy).

---

## 3. Docker-in-sandbox

**Scenario.** The agent needs to `docker build`, run compose, or use
testcontainers.

**Use it when** your workload needs a Docker daemon *inside* the sandbox — this
is the capability that distinguishes a microVM from a container backend.

```python
from deepagents_sbx import SbxSandbox

with SbxSandbox(memory="4g") as backend:
    # confirm the daemon is available (the built-in `shell` image ships Docker)
    print(backend.execute("docker info >/dev/null 2>&1 && echo docker-ok").output)

    backend.execute("printf 'FROM alpine\\nCMD [\"echo\",\"hi from inside\"]\\n' > /home/agent/workspace/Dockerfile")
    print(
        backend.execute(
            "cd /home/agent/workspace && docker build -t demo . && docker run --rm demo"
        ).output
    )
```

**Notes**

- The daemon is **private to the microVM** — no host socket is exposed.
- Builds are discarded with the sandbox; cache doesn't survive unless you keep it.

---

## 4. Bind-mount an existing repository

**Scenario.** You have a real project on the host and want the agent to work on
it (install deps, run tests, make changes).

**Use it when** you want the agent to operate on an existing tree rather than a
blank VM.

```python
from deepagents_sbx import SbxSandbox

with SbxSandbox(workspace="/Users/me/code/my-project", memory="4g") as backend:
    print(backend.execute("npm ci && npm test").output)
```

**Notes**

- The host directory is mounted at the **same absolute path** inside the VM, and
  commands start there (`working_dir == workspace`).
- Changes the agent makes are visible on the host — this is a shared, not copied,
  mount. Use a clean checkout/branch if that matters.
- `workspace=` is local-only; cloud has no bind mount (see [§ 6](#6-cloud-burst--artifact-retrieval)).

---

## 5. Daily-driver terminal agent (`dcode`)

**Scenario.** You want Deep Agents Code to run its tools in a microVM as your
everyday coding agent.

```bash
pip install "deepagents-sbx[code]"
dcode --sandbox sbx            # local microVM
dcode --sandbox sbx-cloud      # Docker Cloud Sandboxes (paid)
```

Per-provider parameters live in `~/.deepagents/config.toml`:

```toml
[sandboxes.providers.sbx.params]
memory = "8g"
cpus = 4

[sandboxes.providers.sbx-cloud.params]
cpus = 4
memory = "8g"
ttl = "2h"
on_timeout = "delete"
```

**Notes**

- Deep Agents Code (`dcode`) is the CLI front-end of Deep Agents. Installing the
  `[code]` extra registers `sbx` and `sbx-cloud` as its sandbox providers.
- The providers expose a stable sandbox id, so
  `dcode --sandbox sbx --sandbox-id <id>` can reattach to an existing sandbox.
- Unknown params are ignored with a debug log, so configs stay forward-compatible.

---

## 6. Cloud burst & artifact retrieval

**Scenario.** A heavy job (big build, data processing) is too much for your
laptop. Offload it to a bigger cloud shape for a bounded time, then pull the
results back.

```bash
sbx --cloud policy init balanced     # cloud egress defaults to deny-all
```

```python
from deepagents_sbx import SbxSandbox

with SbxSandbox(cloud=True, cpus=4, memory="8g", ttl="30m", on_timeout="delete") as backend:
    print(backend.ttl())                                  # {"expires_at": ..., ...}
    backend.extend_ttl("15m")

    # write large output to a FILE, not to stdout (see Notes), then fetch it
    result = backend.execute(
        "./heavy-job.sh > /home/agent/workspace/out.tar.gz 2>&1",
        timeout=1800,
    )
    print(result.exit_code)

    downloaded = backend.download_files(["/home/agent/workspace/out.tar.gz"])
    with open("out.tar.gz", "wb") as fh:
        fh.write(downloaded[0].content)
```

**Notes**

- **Always set `ttl`**, and prefer `on_timeout="delete"` so an abandoned sandbox
  stops billing.
- Sizing must land on a billable shape (`micro`/`small`/`medium`/`large`/`xl`);
  an invalid pair raises `SbxShapeError` before any API call.
- **Large stdout is different in the cloud.** Cloud exec sessions have a
  server-side retention window, so a chatty command can fail with
  `failed_precondition: requested output no longer retained` instead of hitting
  `max_output_bytes`. Redirect large output to a file and download it.
- Runnable end-to-end example (create → exec → files → TTL → remove):
  <https://github.com/restuhaqza/deepagents-sbx-playground>.

---

## 7. Ephemeral CI agent runs

**Scenario.** Run an agent in CI where it must not touch the runner's state, and
where a clean machine is required every time.

**Use it when** you want a reproducible, disposable environment per run.

```python
import sys

from deepagents import create_deep_agent
from deepagents_sbx import SbxSandbox


def main() -> int:
    with SbxSandbox(memory="2g", timeout=600) as backend:
        agent = create_deep_agent(model="anthropic:claude-sonnet-4-5", backend=backend)
        result = agent.invoke({"messages": [{"role": "user", "content": "Run the checks in ./scripts/verify.md"}]})
        return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
```

**Notes**

- `auto_remove=True` (default) plus the `with` block guarantees teardown even on
  failure.
- The library's own **unit + contract** tests run without Docker (a fake `sbx`
  shim), so CI can test the package cheaply; real-microVM **integration** tests
  are opt-in. See [spec.md § Testing strategy](spec.md#testing-strategy).

---

## 8. Security research / red teaming

**Scenario.** You want to detonate a payload or probe behaviour in a machine that
is thrown away afterwards.

```python
from deepagents_sbx import SbxSandbox

with SbxSandbox(memory="4g", workspace="/tmp/payloads") as backend:
    print(backend.execute("./poc.sh").output)
# microVM and its private Docker daemon are gone
```

**Notes**

- The microVM has its own kernel and is disposable — useful for containment, but
  this project is **not** a security product. Keep the host patched and don't
  treat the boundary as invulnerable.
- Start from `deny-all` egress and add rules deliberately.

---

## 9. Python ↔ JavaScript parity

**Scenario.** You're integrating with Deep Agents in Python or in Node.

| | Python | JavaScript |
|---|---|---|
| Install | `pip install deepagents-sbx` | `npm install deepagents-sbx deepagents` |
| Version floor | Python 3.12+, `deepagents>=0.7.19` | Node 20+, `deepagents>=1` |
| Class | `SbxSandbox` | `SbxSandbox` |
| Timeout option | `timeout=` (seconds) | `timeout=` (seconds) |
| Output cap | `max_output_bytes` | `maxOutputBytes` |
| Teardown | `remove()` / `close()` | `remove()` / `close()` |
| Cloud | `cloud=True, ttl=..., on_timeout=...` | `cloud: true, ttl: ..., onTimeout: ...` |
| In-sandbox `python3` | Required (the `shell` image has it) | Not required (pure POSIX) |

```ts
import { createDeepAgent } from "deepagents";
import { SbxSandbox } from "deepagents-sbx";

const backend = new SbxSandbox({ memory: "4g" });
try {
  const agent = createDeepAgent({ model, backend });
  await agent.invoke({ messages: [{ role: "user", content: "Run the test suite" }] });
} finally {
  await backend.close();
}
```

---

## Common patterns

**Reattach to a running sandbox**

```python
backend = SbxSandbox.attach("my-sandbox")   # raises SbxNotFoundError if absent
print(backend.id)                            # stable id from `sbx ls --json`
```

`attach()` accepts either the sandbox **name** you chose or its stable **id**.

**Keep the sandbox for manual poking**

```python
backend = SbxSandbox(name="scratch", auto_remove=False)
...             # inspect it later with: sbx exec scratch sh
backend.remove()
```

**Time-box a command**

```python
response = backend.execute("./long.sh", timeout=30)
if response.exit_code is None:
    print("timed out:", response.output)
```

**Handle configuration errors**

```python
from deepagents_sbx import SbxAuthError, SbxPolicyError

try:
    backend = SbxSandbox()
except SbxAuthError:      # → sbx login
    ...
except SbxPolicyError:    # → sbx policy init balanced
    ...
```

---

## Next steps

- [Usage reference](usage.md) — full option list, errors, troubleshooting.
- [Concepts](concepts.md) — why microVMs, and when to choose something else.
- [Cloud playground](https://github.com/restuhaqza/deepagents-sbx-playground) — runnable cloud verification.

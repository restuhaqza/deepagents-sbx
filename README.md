# deepagents-sbx

> A Docker Sandboxes (`sbx`) microVM sandbox backend for [Deep Agents](https://github.com/langchain-ai/deepagents) — Python & JavaScript.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/deepagents-sbx.svg)](https://pypi.org/project/deepagents-sbx/)
[![npm](https://img.shields.io/npm/v/deepagents-sbx.svg)](https://www.npmjs.com/package/deepagents-sbx)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange)

Give your Deep Agents agent a **real machine to work in**: every command runs
inside an `sbx` microVM with its **own Linux kernel** and a **private Docker
daemon** — on your laptop (free) or in Docker Cloud Sandboxes (paid). One
`pip install` / `npm install`, no Dockerfile, no host pollution.

## What is this?

Deep Agents can run its tools in a *sandbox backend*. `deepagents-sbx` is one:
it implements the Deep Agents `BaseSandbox` protocol on top of
[Docker Sandboxes](https://docs.docker.com/ai/sandboxes/) (`sbx`), Docker's
microVM-based sandbox.

So when your agent writes and runs code, installs packages, or builds containers,
it does that **inside a disposable microVM** — not on your host.

| Stack | Install | Entry point |
|---|---|---|
| Python | `pip install deepagents-sbx` | `from deepagents_sbx import SbxSandbox` |
| JavaScript | `npm install deepagents-sbx deepagents` | `import { SbxSandbox } from "deepagents-sbx"` |
| Deep Agents Code | `pip install "deepagents-sbx[code]"` | `dcode --sandbox sbx` |

## Why a microVM (and not just a container)?

| Property | What it gives the agent |
|---|---|
| microVM with its own kernel | A hard isolation boundary — a kernel-level escape in the sandbox doesn't reach your host kernel |
| Private Docker daemon inside the VM | The agent can `docker build` / `docker run` (testcontainers, compose, image builds) |
| Network allow/deny policy | Real egress control for arbitrary, LLM-generated code |
| Disposable | Remove it and it's gone — no leftover images, containers or state |
| Free locally | The `sbx` CLI drives a local microVM; no metered compute (a Docker account is still required to sign in) |

> Most Deep Agents sandbox backends (LangSmith, Daytona, Modal, Runloop, Vercel,
> E2B, plain Docker) are ordinary containers. `deepagents-sbx` is the one that
> runs inside an `sbx` microVM.

## When to use it

**Good fit**

- You run **LLM-generated or otherwise untrusted code** and want a hard boundary.
- Your agent needs **Docker inside the sandbox** (image builds, compose, testcontainers).
- You want **egress control** — allow PyPI/npm, deny everything else.
- You want **reproducible, ephemeral** agent runs that don't touch your machine.
- You want **local-first** (free) with an optional **cloud burst** for heavier jobs.

**Probably not the right tool**

- You need GPU compute (reach for a GPU cloud).
- You want a long-lived shared dev box rather than disposable sandboxes.
- You can't install Docker Sandboxes or don't have a Docker account.

Full decision guide: [docs/concepts.md](docs/concepts.md).

## Quickstart (60 seconds)

**Requirements:** Python 3.12+ (`deepagents>=0.7.19`) or Node 20+ (`deepagents>=1`), plus the free `sbx` CLI.

Prerequisites (once):

1. **Install the `sbx` CLI** (it is not a `pip`/`npm` dependency). macOS:
   `brew trust docker/tap && brew install docker/tap/sbx` · Ubuntu 24.04+:
   `curl -fsSL https://get.docker.com | sudo SBX=1 sh` · Windows:
   `winget install -h Docker.sbx`. See the
   [install docs](https://docs.docker.com/ai/sandboxes/install/). Local sandboxes
   also need hardware virtualization; **cloud does not**.
2. **Sign in and initialize the local policy:**

```bash
sbx login
sbx policy init balanced
```

Cloud uses the **same Docker account and `sbx login`**, but keeps **separate
secrets and network policy** in cloud stores (`sbx --cloud secret set …`,
`sbx --cloud policy …`) and needs an active Docker Agentic Platform subscription. Verify access
with `sbx --cloud diagnose`, then initialize policy with
`sbx --cloud policy init balanced`.

**Python**

```bash
pip install "deepagents-sbx[code]"    # [code] also registers the dcode provider
```

```python
from deepagents import create_deep_agent
from deepagents_sbx import SbxSandbox

with SbxSandbox(memory="4g") as backend:          # creates the microVM, removes it on exit
    agent = create_deep_agent(model="anthropic:claude-sonnet-4-5", backend=backend)
    agent.invoke({"messages": [{"role": "user", "content": "Create a Python CLI and run its tests"}]})
```

**JavaScript**

```bash
npm install deepagents-sbx deepagents
```

```ts
import { createDeepAgent } from "deepagents";
import { SbxSandbox } from "deepagents-sbx";

const backend = new SbxSandbox({ memory: "4g" }); // created lazily on first use
try {
  const agent = createDeepAgent({ model, backend });
  await agent.invoke({ messages: [{ role: "user", content: "Run the test suite" }] });
} finally {
  await backend.close(); // removes the microVM when autoRemove is set (default)
}
```

**Deep Agents Code**

```bash
dcode --sandbox sbx          # local microVM
dcode --sandbox sbx-cloud    # Docker Cloud Sandboxes (paid)
```

## Cloud sandboxes

Same API, `cloud=True`:

```python
with SbxSandbox(cloud=True, cpus=1, memory="2g", ttl="10m") as backend:
    backend.execute("./heavy-job.sh > /home/agent/workspace/out.tar.gz 2>&1", timeout=1800)
    artifacts = backend.download_files(["/home/agent/workspace/out.tar.gz"])
```

Cloud sandboxes are **billable**, have **no host bind-mount**, and are validated
against billable shapes (`micro` … `xl`) *before* any API call. Always set a
`ttl`. Details: [docs/usage.md § Cloud](docs/usage.md#cloud-sandboxes).

There's a runnable end-to-end playground for the cloud path:
<https://github.com/restuhaqza/deepagents-sbx-playground>.

## Documentation

| Doc | What's in it |
|---|---|
| [docs/index.md](docs/index.md) | Map of the docs — **start here** |
| [docs/concepts.md](docs/concepts.md) | Mental model, microVM vs container, when (not) to use it |
| [docs/use-cases.md](docs/use-cases.md) | Copy-paste recipes for common scenarios |
| [docs/usage.md](docs/usage.md) | API reference: constructor, methods, cloud, errors, troubleshooting |
| [docs/spec.md](docs/spec.md) | Implementation spec + verified findings (internal / contributor) |

Package quickstarts (what appears on the registries):
[Python](python/README.md) · [JavaScript](js/README.md).

## How it works (short version)

`SbxSandbox` implements the four members Deep Agents' `BaseSandbox` needs —
`execute()`, `upload_files()`, `download_files()`, and `id`. Every other file
operation (`read`, `write`, `edit`, `ls`, `grep`, `glob`, `delete`) is derived by
the base class and routed through `execute()`.

It talks to Docker Sandboxes through the `sbx` CLI (the only *supported* local
interface). Cloud uses the same transport: `--cloud` is a global flag, so
create/exec/cp/rm/ttl share one code path. The transport sits behind a
`SbxTransport` seam, so a REST client or the official `@docker/sandboxes` SDK can
be dropped in later without touching `SbxSandbox`.

More: [docs/concepts.md](docs/concepts.md) and [docs/spec.md](docs/spec.md).

## Status

Alpha (`0.1.0`), published on
[PyPI](https://pypi.org/project/deepagents-sbx/) and
[npm](https://www.npmjs.com/package/deepagents-sbx). Milestones and the verified
environment matrix live in [docs/spec.md](docs/spec.md#milestones).

## License

MIT — see [LICENSE](LICENSE).

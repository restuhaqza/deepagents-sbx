# Concepts

This page is the mental model. Read it before the API reference — it explains
*what* the project is, *why* a microVM matters, and *when* to reach for it.

## What is a "sandbox backend"?

Deep Agents hands its tools a `BaseSandbox` implementation — the thing that
actually runs commands and reads/writes files. Swap that implementation and the
agent's tool calls run somewhere else.

`deepagents-sbx` is one such implementation. Instead of running on your host (or
in an ordinary container), the agent runs inside a
[Docker Sandboxes](https://docs.docker.com/ai/sandboxes/) **microVM**.

```text
Agent loop (host)
   │  execute() / read() / write() / ls() / grep() …
   ▼
SbxSandbox  (deepagents BaseSandbox)
   │  sbx exec / sbx cp / sbx rm  (argv, no host shell)
   ▼
Docker Sandboxes  ──►  microVM: own kernel + private Docker daemon
```

Only four things are implemented directly; everything else is derived:

| Implemented by `SbxSandbox` | Derived by `BaseSandbox` (routed through `execute()`) |
|---|---|
| `execute()`, `upload_files()`, `download_files()`, `id` | `read`, `write`, `edit`, `delete`, `ls`, `grep`, `glob` |

## What Docker Sandboxes (`sbx`) is

`sbx` is Docker's sandbox runner. It starts a lightweight **microVM** — its own
Linux kernel, its own filesystem, and a **private Docker daemon** — and exposes
it through a CLI: `sbx exec` to run a command, `sbx cp` to move files, `sbx rm`
to throw it away, `sbx ttl` to manage cloud lifetimes.

`deepagents-sbx` drives that CLI. There is no REST client or SDK dependency;
cloud uses the same CLI with the global `--cloud` flag.

## microVM vs container

| | `deepagents-sbx` (microVM) | Typical container backend |
|---|---|---|
| Isolation | Own kernel — a kernel bug in the sandbox is not a host-kernel bug | Shared host kernel (namespaces + cgroups) |
| Docker inside the sandbox | A private, sandbox-local Docker daemon | Usually requires mounting the host socket or isn't available |
| Egress control | Per-host allow/deny network policy | Usually the host's network |
| Startup | A VM boots (seconds) | Near-instant |
| Cost | Free locally; billable in Docker Cloud | Depends on the host |

The trade-off is deliberate: you pay a few seconds of boot time for a stronger
boundary and a Docker daemon the agent can use freely.

## Mental model

**Lifecycle.** The sandbox is created if it doesn't exist and removed by
`close()` / `remove()` when `auto_remove=True` (the default). Use it as a context
manager and cleanup is automatic:

```python
with SbxSandbox(memory="4g") as backend:   # create
    backend.execute("...")                 # use
# removed on exit
```

Creation timing differs by language:

- **Python** creates in the constructor (`auto_create=True`, the default), so
  `SbxSandbox(...)` boots the microVM immediately.
- **JavaScript** creates **lazily** on the first `execute`/upload/download, or
  eagerly via the static `SbxSandbox.create()`.

**Working directory.** Commands start in `/home/agent/workspace` when no
workspace is mounted. With `workspace=...`, the host directory is bind-mounted at
*the same absolute path* inside the VM (via virtiofs) and commands start there.

**Local vs cloud.**

| | Local (`cloud=False`) | Cloud (`cloud=True`) |
|---|---|---|
| Cost | Free | Billable per shape |
| Host bind-mount | ✅ `workspace=...` | ❌ none — retrieve results with `download_files()` |
| Lifetime | Until you remove it | TTL-managed (`ttl=`, `on_timeout=`) |
| Sizing | `cpus`/`memory` free-form | Must land on a billable shape (`micro`…`xl`) |
| Egress default | Your policy | `deny-all` (separate account policy) |

**Network policy scope.** The local policy applies to your `sbx` install on this
machine; the cloud policy applies to your Docker account. They are independent —
initializing one does not configure the other.

**Transport.** Everything goes through a `SbxTransport` seam. The default
`CliSbxTransport` shells out to `sbx`; a REST client or the official
`@docker/sandboxes` SDK can be dropped in later without changing `SbxSandbox`.

**Timeouts.** The constructor takes a default per-command `timeout` in seconds
(`execute(cmd, timeout=...)` overrides it for one call; `0`/`None` disables it). A
command that exceeds its timeout is *not* an exception at the `execute()`
boundary — it returns `ExecuteResponse(output="…timed out…", exit_code=None)` so
the agent loop survives. Configuration problems (missing CLI, not logged in,
uninitialized policy) do raise.

## When to use it — and when not

**Use it when**

- You execute **LLM-generated or untrusted code** and want a hard boundary.
- The agent needs **Docker inside the sandbox** (build images, compose, testcontainers).
- You want **egress control** over arbitrary generated code.
- You want **ephemeral, reproducible** runs that don't dirty your host.
- You want **local-first** (free) with an optional **cloud burst**.

**Look elsewhere when**

- You need **GPUs** — a GPU cloud provider is a better fit.
- You want a **long-lived shared dev box**, not disposable sandboxes.
- You can't install Docker Sandboxes / have no Docker account.
- Boot latency matters more than isolation — a container backend is faster.

## Which backend should I use?

There are many Deep Agents sandbox backends. Pick by what you need:

| Need | Better choice |
|---|---|
| Hard isolation (own kernel), Docker-in-sandbox, local & free | **`deepagents-sbx`** |
| GPU workloads | A GPU cloud backend (e.g. Modal) |
| Managed cloud devboxes, minimal setup | A hosted provider (e.g. Daytona, E2B, Runloop, Vercel) |
| Plain containers already on your machine | A Docker-based backend |
| LangSmith-managed sandboxes | The LangSmith backend |

The distinguishing features here are **microVM isolation** and a **private
Docker daemon**, plus a **free local** mode.

## Guarantees and non-goals

**Guarantees**

- Commands are passed as **argv arrays**, never concatenated into a host shell
  string; your command is wrapped in a single `sh -c` *inside* the sandbox.
- Output is **streamed and bounded** — the process is killed at the output cap
  rather than buffering an unbounded amount in host memory.
- Timeouts kill the **remote** process (sandbox-side `timeout`), with a host-side
  process-group kill as a backstop.
- Cloud sizing is validated **before** any billable API call.

**Non-goals**

- It is not a security product or a substitute for a hardened, patched host.
- It does not provide GPU acceleration, persistent volumes across sandboxes, or
  cross-sandbox orchestration (see the roadmap in [spec.md](spec.md#milestones)).

## Glossary

| Term | Meaning |
|---|---|
| **Docker Sandboxes** | Docker's sandbox runner. Runs a lightweight **microVM** and exposes it through the `sbx` CLI. |
| **`sbx`** | The Docker Sandboxes command-line tool this project drives (`sbx exec`, `sbx cp`, `sbx rm`, `sbx ttl`, …). Not installed by `pip`/`npm` — install it separately. |
| **Docker Cloud Sandboxes** | The paid, remote flavour of Docker Sandboxes. Selected with `sbx --cloud` / `SbxSandbox(cloud=True)`. |
| **microVM** | A small virtual machine with its **own Linux kernel**, unlike a container which shares the host kernel. |
| **`shell` image** | The default sbx agent image (`docker/sandbox-templates:shell-docker`, Ubuntu). It ships `python3` (needed by the Python backend) and a Docker daemon. Choose another with `agent=...`. |
| **shape** | A billable cloud size: `micro` (1 vCPU/2 GiB), `small` (2/4), `medium` (4/8), `large` (8/16), `xl` (16/32). |
| **TTL** | Cloud-only time-to-live before the sandbox times out (`ttl=` / `on_timeout="delete"|"stop"`). |
| **output cap** | `max_output_bytes` (Python) / `maxOutputBytes` (JS), default **524288** (512 KiB). The command is killed once reached. |
| **transport** | The `SbxTransport` abstraction; the default `CliSbxTransport` shells out to `sbx`. |
| **virtiofs** | The mechanism used to bind-mount a host directory into the microVM (local `workspace=`). |
| **`dcode`** | Deep Agents Code, the CLI front-end of Deep Agents. The `[code]` extra registers the `sbx` and `sbx-cloud` providers for it. |
| **`name` vs `id`** | `name` is the mutable handle you choose; `id` is the stable identifier from `sbx ls --json`. `attach()` accepts either. |

## Next steps

- [Use cases](use-cases.md) — copy-paste recipes for concrete scenarios.
- [Usage reference](usage.md) — every option, method, error and gotcha.
- [Spec](spec.md) — how it is implemented and what was verified.

# deepagents-sbx (JavaScript)

Docker Sandboxes (`sbx`) microVM sandbox backend for
[Deep Agents](https://github.com/langchain-ai/deepagents).

Implements the `deepagents` JavaScript `BaseSandbox` (`SandboxBackendProtocolV2`)
on top of a Docker Sandboxes microVM — its own kernel plus a private Docker
daemon.

## Install

```bash
npm install deepagents-sbx deepagents
```

Requires Node 20+ and the free [`sbx` CLI](https://docs.docker.com/ai/sandboxes/),
installed and signed in (`sbx login`; `sbx policy init balanced` once).

## Use

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

Bind-mount a host project (mounted at the same absolute path inside the VM):

```ts
const backend = new SbxSandbox({ workspace: "/path/to/project" });
```

Attach to an existing sandbox without creating one:

```ts
const backend = await SbxSandbox.attach("my-sandbox");
```

### Options

| Option | Default | Meaning |
|---|---|---|
| `name` | `deepagents-sbx-<random>` | Sandbox handle. |
| `agent` | `"shell"` | Built-in sbx agent image. |
| `workspace` | — | Host dir bind-mounted at the same absolute path. |
| `cpus` / `memory` / `profile` | sbx defaults | `sbx create` sizing/governance. |
| `transport` | `CliSbxTransport` | Swap the transport implementation. |
| `timeout` | `120` | Per-command timeout (seconds). |
| `maxOutputBytes` | `524288` | Output cap; the child is killed at the cap. |
| `autoRemove` | `true` | Delete on `close()`. |
| `autoCreate` | `true` | Create on first use if missing. |
| `pull` | sbx default | Image pull policy, e.g. `"missing"`. |

Unlike the Python port, the JS backend is pure POSIX and needs no `python3`
inside the sandbox.

## Testing

```bash
npm test                # unit + contract (fake sbx, no Docker)
npm run test:integration  # real microVMs (needs sbx login + virtualization)
```

## License

MIT

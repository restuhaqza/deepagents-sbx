# deepagents-sbx (Python)

Docker Sandboxes (`sbx`) microVM sandbox backend for
[Deep Agents](https://github.com/langchain-ai/deepagents).

This package implements
[`BaseSandbox`](https://reference.langchain.com/python/deepagents/backends/sandbox/BaseSandbox)
on top of a Docker Sandboxes microVM — its own kernel plus a private Docker
daemon — so agents run arbitrary code behind a hard isolation boundary.

## Install

```bash
pip install deepagents-sbx            # backend only
pip install "deepagents-sbx[code]"    # + Deep Agents Code provider
```

Requires **Python 3.12+** and the free
[`sbx` CLI](https://docs.docker.com/ai/sandboxes/) installed and logged in
(`sbx login`). Local sandboxes always need the CLI; for Docker Cloud Sandboxes
you can skip it with the opt-in [API transport](#cloud-via-the-api-optional).

## Use

```python
from deepagents import create_deep_agent
from deepagents_sbx import SbxSandbox

with SbxSandbox(memory="4g") as backend:          # creates + auto-removes
    agent = create_deep_agent(model="anthropic:...", backend=backend)
    agent.invoke({"messages": [{"role": "user", "content": "Run the test suite"}]})
```

Bind-mount a host project (mounted at the same absolute path inside the VM):

```python
with SbxSandbox(workspace="/path/to/project") as backend:
    ...
```

## Cloud via the API (optional)

Cloud normally uses the same CLI with `--cloud`. To drive Docker Cloud Sandboxes
over Docker's experimental
[Sandboxes API](https://docs.docker.com/ai/sandboxes-api/) with **no `sbx`
process**, swap in `ApiSbxTransport` (standard library only, no new dependency):

```python
from deepagents_sbx import ApiSbxTransport, SbxSandbox

transport = ApiSbxTransport(docker_id="you", personal_access_token="dckr_pat_…")
with SbxSandbox(cloud=True, transport=transport, ttl="10m") as backend:
    backend.execute("echo hello")
```

Auth is independent of `sbx login` (OAuth or a PAT with the `sandbox:use`
permission). The API is experimental and cloud-only.

## Deep Agents Code

Installing the `code` extra registers the `sbx` provider:

```bash
dcode --sandbox sbx
```

See the [repository README](https://github.com/restuhaqza/deepagents-sbx) for
the full architecture, API mapping, and testing strategy.

## License

MIT

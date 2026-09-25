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
(`sbx login`).

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

## Deep Agents Code

Installing the `code` extra registers the `sbx` provider:

```bash
dcode --sandbox sbx
```

See the [repository README](https://github.com/restuhaqza/deepagents-sbx) for
the full architecture, API mapping, and testing strategy.

## License

MIT

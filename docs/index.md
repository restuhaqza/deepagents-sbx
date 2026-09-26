# deepagents-sbx documentation

A Docker Sandboxes (`sbx`) microVM sandbox backend for
[Deep Agents](https://github.com/langchain-ai/deepagents) — Python & JavaScript.

## Start here

| If you… | Read |
|---|---|
| are deciding whether this is the right tool | [Concepts](concepts.md) |
| want a quick definition of a term | [Glossary](concepts.md#glossary) |
| want to see it work in 5 minutes | [Quickstart](../README.md#quickstart-60-seconds) |
| have a specific scenario in mind | [Use cases](use-cases.md) |
| need the API details | [Usage reference](usage.md) |
| want implementation internals | [Spec](spec.md) |

## By role

- **Evaluator / newcomer** → [Concepts](concepts.md) → [Use cases](use-cases.md) → [Quickstart](../README.md#quickstart-60-seconds)
- **User / operator** → [Usage reference](usage.md) (cloud, network policy, errors) → [Use cases](use-cases.md)
- **Contributor** → [Spec](spec.md) (architecture, API mapping, testing, release)

## By task

| Task | Where |
|---|---|
| Run an agent locally | [Use cases § 1](use-cases.md#1-coding-agent-runs-generated-code-safely) |
| Run untrusted code with egress rules | [Use cases § 2](use-cases.md#2-run-untrusted-code-with-egress-control) |
| Build/run Docker images in the sandbox | [Use cases § 3](use-cases.md#3-docker-in-sandbox) |
| Point the agent at an existing repo | [Use cases § 4](use-cases.md#4-bind-mount-an-existing-repository) |
| Use it as a terminal coding agent (`dcode`) | [Use cases § 5](use-cases.md#5-daily-driver-terminal-agent-dcode) |
| Offload a heavy job to the cloud | [Use cases § 6](use-cases.md#6-cloud-burst--artifact-retrieval) |
| Run agents reproducibly in CI | [Use cases § 7](use-cases.md#7-ephemeral-ci-agent-runs) |
| Choose between Python and JS | [Use cases § 9](use-cases.md#9-python--javascript-parity) |
| Understand errors and fix failures | [Usage § Troubleshooting](usage.md#troubleshooting) |
| Control network egress | [Usage § Network policy](usage.md#network-policy) |
| Understand the isolation model | [Concepts § microVM vs container](concepts.md#microvm-vs-container) |

## Package quickstarts

These are the READMEs that ship on the registries:

- Python: [`python/README.md`](../python/README.md)
- JavaScript: [`js/README.md`](../js/README.md)

## Runnable examples

- Cloud playground (create → exec → file ops → TTL → remove, with a PASS/FAIL report):
  <https://github.com/restuhaqza/deepagents-sbx-playground>

## Not sure where a document belongs?

- "What/why/should I use this" → `concepts.md`
- "How do I do X" → `use-cases.md`
- "What are the exact options/behaviours" → `usage.md`
- "How is it built / how do I contribute" → `spec.md`

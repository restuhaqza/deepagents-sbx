# Security Policy

## Supported versions

`deepagents-sbx` is pre-1.0. Security fixes are applied to the latest `0.x`
release only.

| Version | Supported |
| ------- | --------- |
| latest `0.x` | ✅ |
| older `0.x`  | ❌ |

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

The preferred channel is GitHub's private vulnerability reporting:

- <https://github.com/restuhaqza/deepagents-sbx/security/advisories/new>

If you cannot use that, email **hi@restuhaqza.dev**.

Please include:

- a description of the issue and its impact,
- steps to reproduce (a minimal proof of concept if possible),
- the affected version(s) and platform (Python or JavaScript, OS),
- any suggested fix or mitigation.

## What to expect

- **Acknowledgement** within 3 business days.
- **Initial assessment** within 7 business days.
- We will keep you informed as we work on a fix and will credit you in the
  advisory and changelog if you would like.

Please give us a reasonable window to fix and release before public disclosure.

## Scope

`deepagents-sbx` is the **host-side integration layer** between Deep Agents and
Docker Sandboxes (`sbx`). It builds `sbx` command lines, parses their output,
enforces timeouts and size limits, and exposes a Deep Agents sandbox backend.

**In scope** (report to us):

- command or argument injection on the host,
- unsafe path handling that escapes the intended workspace,
- ways to bypass the host-side timeouts or memory/output/download caps,
- unsafe parsing of `sbx` output,
- leakage of credentials or host paths into sandbox-visible state.

**Out of scope** (report upstream to Docker): the isolation guarantees of the
microVM itself and the `sbx` CLI/daemon. However, if our code undermines that
isolation — for example by defeating the sandbox boundary or exposing host
resources it should not — that **is** in scope; tell us.

## Safe harbor

We consider security research conducted in good faith under this policy to be
authorized. We will not pursue or support legal action against researchers who
follow it, and we will work with you to understand and resolve the issue.

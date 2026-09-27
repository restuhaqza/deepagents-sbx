# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While pre-1.0 (`0.x`), minor releases may include breaking changes.

## [Unreleased]

### Added

- Python: `ApiSbxTransport`, an opt-in transport that drives Docker Cloud
  Sandboxes over the [Sandboxes REST API](https://docs.docker.com/reference/api/sandboxes/latest/)
  without spawning the `sbx` CLI. Authentication is independent of `sbx login`
  (`access_token`, a `token_provider`, or a Docker ID + personal access token).
  The CLI transport remains the default and the only supported local interface.
  See #15.
- JavaScript: `ApiSbxTransport`, the matching opt-in transport built on the
  official `@docker/sandboxes` SDK. The SDK is an **optional peer dependency**
  (loaded lazily), so the base install stays dependency-free. See #14.

## [0.1.0] - 2026-09-25

### Added

- Initial public release of `deepagents-sbx` for Python (PyPI) and JavaScript (npm).
- `SbxSandbox`: a Deep Agents sandbox backend running commands inside a Docker
  Sandboxes (`sbx`) microVM with its own kernel and a private Docker daemon.
- Local (`sbx`) and cloud (`sbx-cloud`) providers for Deep Agents Code.
- Cross-port conformance: Python and JavaScript share behavior pinned by contract tests.

[Unreleased]: https://github.com/restuhaqza/deepagents-sbx/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/restuhaqza/deepagents-sbx/releases/tag/v0.1.0

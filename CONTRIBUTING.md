# Contributing to deepagents-sbx

Thanks for your interest in improving `deepagents-sbx`! This project gives
Deep Agents a real microVM to work in, for both Python and JavaScript. Contributions
of all kinds are welcome: bug reports, docs, tests, and code.

By participating you agree to abide by our [Code of Conduct](.github/CODE_OF_CONDUCT.md).

## Ways to contribute

- **Report a bug** or **request a feature** — open an issue using the templates.
- **Improve the docs** — `README.md` and `docs/`.
- **Send a pull request** — see [Pull requests](#pull-requests).
- **Review or triage** — comments on open issues and PRs are always useful.

For anything non-trivial, please open an issue first so we can agree on the
approach before you invest time. **Do not** report security issues in public;
see [SECURITY.md](.github/SECURITY.md).

## Repository layout

```
python/    Python package (deepagents-sbx on PyPI)
js/        TypeScript package (deepagents-sbx on npm)
docs/      Cross-cutting docs: concepts, usage, use-cases, spec
.github/   CI, release workflow, community files
Makefile   Convenience wrappers around the common tasks
```

Both ports implement the **same behavior**; the contract tests pin shared
semantics. If you change one port, update the other and the contract tests,
unless the change is genuinely language-specific.

## Development setup

### Python (3.12+)

```bash
cd python
uv venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
```

Common tasks:

```bash
.venv/bin/python -m pytest -q                 # unit + contract tests (no Docker)
.venv/bin/python -m ruff check src tests      # lint
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy                      # type check (strict)
```

Integration tests require a real `sbx` install, a login, and virtualization.
They are excluded by default:

```bash
.venv/bin/python -m pytest -m integration -q --no-cov
```

### JavaScript (Node 20+)

```bash
cd js
npm ci
```

Common tasks:

```bash
npm test                 # unit + contract tests
npm run test:coverage    # tests with coverage thresholds
npm run lint             # eslint
npm run typecheck        # tsc --noEmit
npm run build            # tsup
```

### From the repo root

```bash
make test           # Python tests (coverage gate)
make integration    # Python integration tests (needs sbx)
make lint           # Python lint
make typecheck      # Python types
make test-js        # JS tests (coverage gate)
make lint-js        # JS lint
make typecheck-js   # JS types
make build          # build both packages
make help           # list all targets
```

## Tests

- Every behavior change should come with a test.
- Tests carry a stable ID in their description (`UT-TR-01`, `UT-CMD-03`,
  `CT-TR-02`, …) so they can be referenced in reviews and issues. Follow the
  existing scheme in `python/tests` and `js/tests`.
- `UT-*` = unit, `CT-*` = contract. Keep the two ports' contract tests aligned.
- Coverage is enforced at **85%** on both ports (`--cov-fail-under` for Python,
  `thresholds` for Vitest). CI fails below that.

## Releasing

A release is a version bump on `main`, a tag, and (optionally) a publish.

1. **Bump every version source.** They are checked by `verify-version` at tag
   time, and a stale one is easy to miss:

   | Source | How |
   |---|---|
   | `python/pyproject.toml` | `version = "X.Y.Z"` |
   | `python/src/deepagents_sbx/__init__.py` | `__version__ = "X.Y.Z"` |
   | `python/uv.lock` | `uv lock` (usage below) — **not** hand-edited |
   | `js/package.json` | `version: "X.Y.Z"` |
   | `js/src/index.ts` | `export const VERSION = "X.Y.Z"` |
   | `js/package-lock.json` | `npm install --package-lock-only` |

   ```bash
   cd python && uv lock                            # sync uv.lock to pyproject
   cd js && npm install --package-lock-only        # sync package-lock to package.json
   ```

   > Bumping `pyproject.toml` without re-running `uv lock` breaks CI: every
   > Python job installs with `uv sync --locked` and fails on the stale lockfile.

2. **Promote the changelog.** Move `Unreleased` to `## [X.Y.Z] - YYYY-MM-DD`
   and update the compare links at the bottom.

3. **Merge to `main`** via PR, then tag the merged commit: `git tag -a vX.Y.Z`.
   Tagging a side branch means the published artifacts do not match `main`.

4. **Publish.** Both registries are opt-in and off by default:
   - **CI (recommended):** set `PUBLISH_PYPI=true` / `PUBLISH_NPM=true`, then push
     the tag. PyPI uses trusted publishing (OIDC) when `PYPI_TOKEN` is unset;
     a `PYPI_TOKEN` secret takes precedence and bypasses OIDC.
   - **Manual:** `cd python && rm -rf dist && uv build && uv publish --token pypi-…`
     and `cd js && npm publish --access public`.
     `npm publish --provenance` fails outside CI — omit it locally, but note the
     published artifact then has no attestation.

   > `uv publish` globs `dist/*`. Delete stale artifacts first, or it will try to
   > re-upload an old version and fail.

5. **Create the GitHub Release** for the tag using the changelog section as the body.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add sbx-cloud provider
fix: settle exec even when a grandchild holds stdio
docs: document the download cap
test: cover control-plane timeout
chore: bump dev dependencies
```

- Keep the subject imperative and under ~72 characters.
- Use the body to explain *why*, not just *what*.
- Reference issues with `#123` where relevant.

## Pull requests

1. Branch from `main` (`fix/…`, `feat/…`, `docs/…`, `chore/…`).
2. Keep the PR focused; split unrelated changes.
3. Fill in the pull request template.
4. Make sure CI is green: lint, format, type-check, tests, and build across
   Python 3.12–3.14 and Node 20/22 on Ubuntu and macOS.
5. Update [CHANGELOG.md](CHANGELOG.md) under `Unreleased` for user-visible changes.
6. If you touch one port, update the other.

`main` is protected: a pull request, resolved conversations, and passing
status checks are required. Maintainers can bypass in emergencies.

### Licensing of contributions

Contributions are accepted under the [MIT License](LICENSE) — inbound equals
outbound. By opening a pull request you confirm you have the right to license
your contribution under MIT. No CLA or DCO sign-off is required.

## Versioning and support

- The project follows [Semantic Versioning](https://semver.org/).
- While pre-1.0 (`0.x`), minor releases **may** include breaking changes; patch
  releases are fixes and non-breaking additions only.
- All notable changes are recorded in [CHANGELOG.md](CHANGELOG.md).
- Supported: the latest `0.x` release. Python `>=3.12`, Node `>=20`.
- Python and JavaScript are versioned in lockstep from a single tag (`vX.Y.Z`).

## Getting help

- Questions and ideas: open an issue.
- Security: see [SECURITY.md](.github/SECURITY.md).

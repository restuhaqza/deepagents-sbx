.PHONY: help test lint fmt typecheck build integration clean publish-python publish-js

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

test: ## Run Python unit + contract tests (no Docker)
	cd python && .venv/bin/python -m pytest -q

integration: ## Run Python integration tests (needs sbx login + virtualization)
	cd python && .venv/bin/python -m pytest -m integration -q

lint: ## Lint Python
	cd python && .venv/bin/python -m ruff check src tests

fmt: ## Format Python
	cd python && .venv/bin/python -m ruff format src tests

typecheck: ## Type-check Python
	cd python && .venv/bin/python -m mypy

build: ## Build both packages
	cd python && uv build
	cd js && npm run build

publish-python: ## Publish the Python package (needs PyPI credentials)
	cd python && uv build && uv publish

publish-js: ## Publish the npm package (needs `npm login`)
	cd js && npm run build && npm publish --access public

clean: ## Remove build/test artifacts
	rm -rf python/dist python/build python/.venv js/dist js/node_modules
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

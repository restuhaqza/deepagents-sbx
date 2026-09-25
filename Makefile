.PHONY: help test lint fmt typecheck build integration clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

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

build: ## Build the Python wheel + sdist
	cd python && uv build

clean: ## Remove build/test artifacts
	rm -rf python/dist python/build python/.venv js/dist js/node_modules
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

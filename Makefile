.PHONY: help install test lint format type-check clean build docs docs-serve docs-clean docs-linkcheck

help: # show this help message
	@echo "Available commands:"
	@echo "  install         - sync runtime deps + project from uv.lock (no dev group)"
	@echo "  test            - run tests in parallel with 100% coverage requirement"
	@echo "  test-examples   - run tests for examples in parallel with coverage"
	@echo "  lint            - run linting with ruff"
	@echo "  format          - format code with ruff"
	@echo "  type-check      - run type checking with mypy"
	@echo "  clean           - clean build artifacts"
	@echo "  build           - build the package"
	@echo "  pre-commit-install - install pre-commit hooks"
	@echo "  pre-commit-run  - run pre-commit on all files"
	@echo "  ci              - run all CI checks locally with 100% coverage"
	@echo "  dev-setup       - setup development environment"
	@echo "  docs            - build documentation"
	@echo "  docs-serve      - build and serve documentation"
	@echo "  docs-clean      - clean documentation build"
	@echo "  build-js        - compile next.ts to next.min.js via esbuild"
	@echo "  test-js         - run JavaScript unit tests with vitest"
	@echo "  docs-linkcheck  - check documentation links"

install: # install the package (editable) using the lockfile
	uv sync --locked --no-dev

test: # run tests with 100% coverage requirement
	uv run pytest tests/ -n auto --cov=next --cov-report=html --cov-report=term-missing --cov-fail-under=100
	npm run test:js

test-examples: # run tests for examples with coverage
	@set -e; \
	missing_tests=0; \
	for example_dir in examples/*/; do \
		if [ -d "$$example_dir" ] && [ -f "$$example_dir/manage.py" ]; then \
			if [ ! -d "$$example_dir/tests" ] && [ ! -f "$$example_dir/tests.py" ]; then \
				missing_tests=1; \
			fi; \
		fi; \
	done; \
	if [ $$missing_tests -eq 1 ]; then \
		echo "ERROR: Some examples are missing tests!"; \
		echo "Each example must have either tests/ directory or tests.py file"; \
		exit 1; \
	fi; \
	for example_dir in examples/*/; do \
		if [ -d "$$example_dir" ] && [ -f "$$example_dir/manage.py" ]; then \
			if [ -d "$$example_dir/tests" ]; then \
				cd "$$example_dir" && uv run pytest tests/ -n auto --cov=. --cov-config=../.coveragerc --cov-report=term-missing; \
				cd - > /dev/null; \
			elif [ -f "$$example_dir/tests.py" ]; then \
				cd "$$example_dir" && uv run pytest tests.py -n auto --cov=. --cov-config=../.coveragerc --cov-report=term-missing; \
				cd - > /dev/null; \
			fi; \
		fi; \
	done

lint: # run linting with ruff
	uv run ruff check next/ tests/ examples/ --fix
	uv run ruff format --check next/ tests/ examples/
	npm run lint:js

format:
	uv run ruff check next/ tests/ examples/ --fix
	uv run ruff format next/ tests/ examples/
	npm run format:check

type-check: # run type checking with mypy
	uv run mypy next/

clean: # clean build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .pytest_cache/
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete

build:
	npm run build:next
	uv build

pre-commit-install: # install pre-commit hooks
	uv run pre-commit install

pre-commit-run: # run pre-commit on all files
	uv run pre-commit run --all-files

ci: # run all CI checks locally with 100% coverage
	make lint
	make type-check
	make build-js
	make lint-js
	make test-js
	make test
	make test-examples

dev-setup: # setup development environment
	uv sync --locked --dev
	make pre-commit-install

docs: # build documentation
	uv sync --locked --group docs
	uv run sphinx-build docs docs/_build

docs-serve: docs # build and serve documentation
	@echo "Opening documentation in browser..."
	@open docs/_build/index.html

docs-clean: # clean documentation build
	rm -rf docs/_build/*

docs-linkcheck: # check documentation links
	uv sync --locked --group docs
	uv run sphinx-build -b linkcheck docs docs/_build

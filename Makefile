.PHONY: help install test bench lint format type-check clean build docs docs-serve docs-clean docs-linkcheck install-js build-js test-js lint-js format-js format-js-check test-examples

# Allow CI to point at a prebuilt venv's pytest (bypassing `uv run` and its sync step)
PYTEST ?= uv run pytest

help: # show this help message
	@echo "Available commands:"
	@echo "  install         - sync runtime deps + project from uv.lock (no dev group)"
	@echo "  test            - run tests in parallel with 100% coverage requirement"
	@echo "  bench           - run performance benchmarks (opt-in, no coverage)"
	@echo "  test-examples   - run Python + JS tests for examples with coverage"
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
	@echo "  install-js      - install JS toolchain via npm ci"
	@echo "  build-js        - compile next.ts to next.min.js via esbuild"
	@echo "  test-js         - run JavaScript unit tests with vitest"
	@echo "  lint-js         - lint TypeScript files with ESLint"
	@echo "  format-js       - format TypeScript files with Prettier"
	@echo "  docs-linkcheck  - check documentation links"

install-js: # install JS toolchain via npm ci
	npm ci

build-js: install-js # compile next/static/next/next.ts to next.min.js via esbuild
	npm run build:next

test-js: # run JavaScript unit tests with vitest
	npm run test:js

lint-js: # lint TypeScript files with ESLint
	npm run lint:js

format-js: # format TypeScript files with Prettier (auto-fix)
	npm run format

format-js-check: # check TypeScript formatting without writing (CI)
	npm run format:check

install: # install the package (editable) using the lockfile
	uv sync --locked --no-dev

test: # run tests with 100% coverage requirement
	uv run pytest tests/ -n auto --cov=next --cov-report=html --cov-report=term-missing --cov-fail-under=100

BENCH_FLAGS ?= \
	-m perf \
	--benchmark-only \
	--benchmark-warmup=on \
	--benchmark-warmup-iterations=1000 \
	--benchmark-min-rounds=10 \
	--benchmark-disable-gc \
	--benchmark-storage=file://./.benchmarks \
	--benchmark-columns=mean,stddev,rounds \
	--benchmark-sort=mean \
	--benchmark-time-unit=auto \
	--no-cov \
	--override-ini=addopts=

# Append extra flags without overriding the base set.
# Example: make bench BENCH_EXTRA="--benchmark-save=before"
BENCH_EXTRA ?=

bench: # run performance benchmarks (opt-in, no coverage, flags match CI)
	uv run pytest tests/benchmarks $(BENCH_FLAGS) $(BENCH_EXTRA)

test-examples: # run Python, JS tests for examples with coverage
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
				cd "$$example_dir" && $(PYTEST) tests/ -n auto --cov=. --cov-config=../.coveragerc --cov-report=term-missing --cov-fail-under=100; \
				cd - > /dev/null; \
			elif [ -f "$$example_dir/tests.py" ]; then \
				cd "$$example_dir" && $(PYTEST) tests.py -n auto --cov=. --cov-config=../.coveragerc --cov-report=term-missing --cov-fail-under=100; \
				cd - > /dev/null; \
			fi; \
		fi; \
	done; \
	for example_dir in examples/*/; do \
		if [ -f "$${example_dir}package.json" ]; then \
			if node -e "const p=require('./$${example_dir}package.json');process.exit(p.scripts&&p.scripts.test?0:1)" 2>/dev/null; then \
				cd "$$example_dir" && npm ci && npm test; \
				cd - > /dev/null; \
			fi; \
		fi; \
	done

lint: # run linting with ruff
	uv run ruff check next/ tests/ examples/ --fix
	uv run ruff format --check next/ tests/ examples/

format: # format code with ruff
	uv run ruff check next/ tests/ examples/ --fix
	uv run ruff format next/ tests/ examples/

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

build: # build the package (hatch custom hook compiles next.ts via npm)
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
	make format-js-check
	make test-js
	make test
	make test-examples

dev-setup: # setup development environment
	uv sync --locked --dev
	make build-js
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

.PHONY: help install install-dev test lint format type-check clean build publish

install: # install the package
	uv pip install -e .

install-dev: # install development dependencies
	uv sync --dev

test: # run tests with 100% coverage requirement
	uv run pytest tests/ -v --cov=next --cov-report=html --cov-report=term-missing --cov-fail-under=100

test-fast: # run tests without coverage
	uv run pytest tests/ -v

test-examples: # run tests for examples with coverage
	@set -e; \
	missing_tests=0; \
	for example_dir in examples/*/; do \
		if [ -d "$$example_dir" ]; then \
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
		if [ -d "$$example_dir" ]; then \
			if [ -d "$$example_dir/tests" ]; then \
				cd "$$example_dir" && uv run pytest tests/ -v --cov=. --cov-config=../.coveragerc --cov-report=term-missing; \
				cd - > /dev/null; \
			elif [ -f "$$example_dir/tests.py" ]; then \
				cd "$$example_dir" && uv run pytest tests.py -v --cov=. --cov-config=../.coveragerc --cov-report=term-missing; \
				cd - > /dev/null; \
			fi; \
		fi; \
	done

test-all: # run all tests including examples
	make test
	make test-examples

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

build: # build the package
	uv run python -m build

publish: # publish to PyPI (dry run)
	uv run python -m twine upload --repository testpypi dist/*

publish-prod: # publish to PyPI
	uv run python -m twine upload dist/*

pre-commit-install: # install pre-commit hooks
	uv run pre-commit install

pre-commit-run: # run pre-commit on all files
	uv run pre-commit run --all-files

ci: # run all CI checks locally with 100% coverage
	make lint
	make type-check
	make test
	make test-examples

dev-setup: # setup development environment
	uv sync --dev
	make pre-commit-install

.PHONY: help install install-dev test lint format type-check clean build publish

install: # install the package
	uv pip install -e .

install-dev: # install development dependencies
	uv sync --dev

test: # run tests
	uv run pytest tests/ -v --cov=next --cov-report=html --cov-report=term-missing

test-fast: # run tests without coverage
	uv run pytest tests/ -v

test-examples: # run tests for examples with 100% coverage
	find examples -name "tests.py" -type f | while read testfile; do \
		dir=$$(dirname "$$testfile"); \
		echo "Running tests with 100% coverage in $$dir"; \
		cd "$$dir" && uv run pytest tests.py -v --cov-report=term-missing --cov-fail-under=100 --cov-config=.coveragerc --cov=.; \
		cd - > /dev/null; \
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

ci: # run all CI checks locally
	make lint
	make type-check
	make test

dev-setup: # setup development environment
	uv sync --dev
	make pre-commit-install

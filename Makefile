.PHONY: help install install-dev test lint format type-check clean build publish

help: # show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: # install the package
	uv pip install -e .

install-dev: # install development dependencies
	uv sync --dev

test: # run tests
	pytest tests/ -v --cov=next --cov-report=html --cov-report=term-missing

test-fast: # run tests without coverage
	pytest tests/ -v

lint: # run linting with ruff
	ruff check next/
	ruff format --check next/

format: # format code with ruff
	ruff check next/ --fix
	ruff format next/

type-check: # run type checking with mypy
	mypy next/

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
	python -m build

publish: # publish to PyPI (dry run)
	python -m twine upload --repository testpypi dist/*

publish-prod: # publish to PyPI
	python -m twine upload dist/*

pre-commit-install: # install pre-commit hooks
	pre-commit install

pre-commit-run: # run pre-commit on all files
	pre-commit run --all-files

ci: # run all CI checks locally
	make lint
	make type-check
	make test

dev-setup: # setup development environment
	uv sync --dev
	make pre-commit-install

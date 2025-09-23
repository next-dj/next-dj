.PHONY: help install install-dev test lint format type-check clean build publish

install: # install the package
	uv pip install -e .

install-dev: # install development dependencies
	uv sync --dev

test: # run tests with 100% coverage requirement
	uv run pytest tests/ -v --cov=next --cov-report=html --cov-report=term-missing --cov-fail-under=100

test-fast: # run tests without coverage
	uv run pytest tests/ -v

test-examples: # run tests for examples with 100% coverage
	@set -e; \
	missing_tests=0; \
	for example_dir in examples/*/; do \
		if [ -d "$$example_dir" ]; then \
			example_name=$$(basename "$$example_dir"); \
			if find "$$example_dir" -name "*.py" -not -name "tests.py" -not -name "__init__.py" | grep -q .; then \
				if [ ! -f "$$example_dir/tests.py" ]; then \
					echo "ERROR: Missing tests.py in examples/$$example_name/ (has Python code)"; \
					missing_tests=1; \
				fi; \
			fi; \
		fi; \
	done; \
	if [ $$missing_tests -eq 1 ]; then \
		echo "Some examples with Python code are missing tests.py files!"; \
		exit 1; \
	fi; \
	find examples -name "tests.py" -type f | while read testfile; do \
		dir=$$(dirname "$$testfile"); \
		if grep -q "^def test_" "$$testfile" || grep -q "^class Test" "$$testfile"; then \
			echo "Running tests with 100% coverage in $$dir"; \
			cd "$$dir" && uv run pytest tests.py -v --cov=. --cov-report=term-missing --cov-fail-under=100 --cov-config=../../examples/.coveragerc; \
			cd - > /dev/null; \
		else \
			echo "ERROR: No test functions found in $$dir but tests.py exists"; \
			echo "Either add tests or remove tests.py file"; \
			exit 1; \
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

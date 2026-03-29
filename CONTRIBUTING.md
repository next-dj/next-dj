# Contributing to next.dj

Guidelines for code, tests, and pull requests. Read this before opening a PR.

## Prerequisites

- Python 3.11+. See `requires-python` in `pyproject.toml`. CI runs 3.11–3.14 against supported Django versions.
- [uv](https://docs.astral.sh/uv/)
- Git

Use project commands via `make` / `uv run` rather than a bare system `python`.

## Commands

### Install and setup

| Command | Purpose |
| --- | --- |
| `make install` | Editable install of the package (`uv pip install -e .`) |
| `make install-dev` | Dev dependencies (`uv sync --dev`) |
| `make dev-setup` | `uv sync --dev` + pre-commit hooks |

### Tests

| Command | Purpose |
| --- | --- |
| `make test` | Runs the main suite under `tests/` with coverage. The run **fails** if coverage for `next/` is below 100%. Uses parallel workers (`pytest -n auto`). Writes an HTML report to `htmlcov/`. |
| `make test-fast` | Runs the same tests without coverage for faster iteration. |
| `make test-examples` | Requires each example to have `tests/` or `tests.py`. Runs pytest with coverage per example (see [Examples](#examples)). |
| `make test-all` | `make test` then `make test-examples` |

There is **no** `make test-coverage`. Use `make test` and open `htmlcov/index.html` if you need a detailed report.

### Lint, types, format

| Command | Purpose |
| --- | --- |
| `make lint` | Ruff check (with fix) and format check on `next/`, `tests/`, `examples/` |
| `make format` | Apply Ruff fixes and format the same paths |
| `make type-check` | Mypy on `next/` |

### Docs and hooks (optional locally)

| Command | Purpose |
| --- | --- |
| `make pre-commit-run` | All pre-commit hooks on all files (typos, `uv.lock`, etc.) |
| `make docs` | Build Sphinx docs |
| `make docs-linkcheck` | Link check only |

### Before a PR

Run **`make ci`**. It runs lint, type-check, `make test`, and `make test-examples`.

### CI vs local `make ci`

GitHub Actions additionally:

- Builds docs with warnings as errors and runs linkcheck (`.github/workflows/ci.yml` jobs `docs`)
- Runs typos and `uv-lock` via pre-commit (`security` job)
- On pull requests: dependency review (`dependency-review` job)
- In CI, lint runs on **`next/` only**, plus a separate import-order pass with `--select I`. Locally, `make lint` also covers `tests/` and `examples/`. Keep those directories clean so you do not surprise reviewers.

Ruff uses `select = ["ALL"]` with ignores and per-file rules in [`pyproject.toml`](pyproject.toml). That includes line length 88, isort with `known-first-party = ["next"]`, and relaxed rules under `examples/` and in test and `conftest` files.

## Code style

### Imports

- Prefer imports at the top of the module. Order groups as stdlib, then third-party, then first-party (`next`), with a blank line between groups. Ruff isort matches `pyproject.toml`.
- Defer imports only for circular imports or optional dependencies.

```python
import os
from pathlib import Path

import pytest
from django.conf import settings

from next.pages import Page
```

### Comments and docstrings

- Write comments in English. Use normal sentence casing unless a proper name requires capitals.
- The module docstring should state what the file is for.
- Function and class docstrings should describe behavior and usage. **Do not** repeat parameter documentation that type hints already express. That is the current project convention and may change later.

### Exceptions

- Keep `try`/`except` scopes small. Catch specific exception types, not bare `Exception` or a bare `except`.
- Do not swallow errors without logging or re-raising when callers need to react.

```python
try:
    return path.read_text(encoding="utf-8")
except OSError as exc:
    logger.warning("read failed: %s", exc)
    raise
```

### Loops and builtins

Prefer builtins, comprehensions, or `itertools` when they read clearly. Avoid manual loops that only duplicate `sum`, `max`, filtering, or dict or set building.

### Type hints

Use annotations throughout `next/`. Mypy is strict (`disallow_untyped_defs`, Django plugin, and related settings). See `[tool.mypy]` in `pyproject.toml`.

## Extensibility

Major pieces (template loaders, router backends, factories) should stay **replaceable**. Use clear protocols or ABCs, registration or settings-driven selection, and dependency injection where practical. When you add extension points, follow patterns in existing `next/` modules.

## Testing

- Pytest discovers `test_*.py`, `*_test.py`, and `tests.py` as configured under `python_files` in `pyproject.toml`. In the main tree, prefer `tests/test_<area>.py`.
- Classes named `Test…` with methods `test_*` are common in this repo but not mandatory for every test.
- Prefer `@pytest.mark.parametrize` for matrix-style cases instead of copy-pasted tests.
- Use **`django.test.Client`** and the `conftest.py` fixtures in examples for HTTP-level checks. Do not use the DRF API client unless the example explicitly adds DRF.

### Coverage

- **`next/`**: CI and `make test` enforce 100% line coverage via `--cov-fail-under=100`.
- **Examples**: Each example must ship tests in `tests/` or `tests.py`. CI runs them with coverage reporting. `make test-examples` does **not** pass `--cov-fail-under=100` today. Aim for full coverage of example application code anyway. Reviewers may ask you to close any gaps.

## Examples

Each example under `examples/` should be self-contained, with a `README.md`, Django app(s) demonstrating the feature, and tests.

Use a typical layout and adjust directory names to match the feature.

```text
examples/
└── feature-name/
    ├── README.md
    ├── config/
    ├── myapp/
    └── tests/
        ├── conftest.py
        └── test_*.py   # or tests/tests.py
```

Use an existing example such as [`examples/file-routing/tests/conftest.py`](examples/file-routing/tests/conftest.py) as the template for Django settings, `INSTALLED_APPS`, and the `client` fixture.

**New user-facing behavior** should normally include an example and tests. Small fixes or internal refactors may omit an example if maintainers agree in the PR.

## Documentation

User-facing docs live under `docs/` and publish to Read the Docs (see README). If you change documentation, run `make docs` or rely on the CI docs job. Fix any warnings and broken links.

## Pull requests

### Checklist

- [ ] `make ci` passes locally
- [ ] New or changed `next/` code covered by tests (100% for package)
- [ ] Example + tests when adding public API or behavior users copy from `examples/`
- [ ] Docs updated when behavior or usage changes
- [ ] Link issues with `Fixes #123` / `Closes #123` when applicable

### Branches

For branch names, use prefixes such as `feat/…`, `fix/…`, `docs/…`, `refactor/…`, and `test/…`.

### Commits

Conventional commits:

```text
type(scope): short description
```

Example messages include `feat(urls): add custom backend hook`, `fix(pages): handle missing template`, and `docs: sync contributing guide`.

Use **draft** PRs for work in progress.

### Review

Maintainers check style with Ruff and mypy, review tests, and assess how the change fits `next/`. They also consider backwards compatibility and deprecation when behavior changes. Response time depends on maintainer availability. This document does not define a fixed SLA.

## Help

- Search [issues](https://github.com/next-dj/next-dj/issues) and prior PRs.
- Mirror patterns in `next/` and `tests/` for similar features.
- For failing coverage after `make test`, inspect terminal output and `htmlcov/index.html`.
- For quick test loops, run `make test-fast`.

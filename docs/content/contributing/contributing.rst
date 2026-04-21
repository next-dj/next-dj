Contributing to next.dj
========================

Guidelines for code, tests, and pull requests. Read this before opening a PR.

Prerequisites
-------------

- Python 3.12+. See ``requires-python`` in ``pyproject.toml``. CI runs Python 3.12–3.14 against Django 4.2, 5.0, 5.1, 5.2, 6.0 (with matrix exclusions — 3.13 drops 4.2/5.0, 3.14 is 6.0-only).
- `uv <https://docs.astral.sh/uv/>`_ for Python dependencies.
- Node.js 24 + npm for the TypeScript bundle in ``next/static/next/``. CI uses Node 24.
- Git.

Use project commands via ``make`` / ``uv run`` / ``npm run`` rather than a bare system ``python`` or globally installed tools.

Project layout
--------------

Skim this map before changing code. Each ``next/<area>/`` module owns one framework concern and typically has a mirror under ``tests/<area>/``.

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Path
     - What lives there
   * - ``next/apps/``
     - Django ``AppConfig``\ s that wire components, templates, static files, and autoreload into Django startup.
   * - ``next/checks/`` + per-module ``checks.py``
     - Django system checks. Registered via ``next/checks/common.py`` and per-area ``next/<area>/checks.py``.
   * - ``next/components/``
     - Reusable template components: registry, loader, scanner, backends, watcher.
   * - ``next/conf/``
     - ``NEXT_FRAMEWORK`` settings object, defaults, signals, and settings checks.
   * - ``next/deps/``
     - Dependency injection: providers, resolver, markers, cache.
   * - ``next/forms/``
     - Forms pipeline: action registry, dispatch, rendering, uid generation.
   * - ``next/pages/``
     - ``Page``, template loaders (``DjxTemplateLoader``, ``PythonTemplateLoader``), layouts, page registry.
   * - ``next/server/``
     - Dev-server autoreload (``NextStatReloader``) and filesystem watcher.
   * - ``next/static/``
     - Static asset discovery, collector, finders, backends, template-tag scripts.
   * - ``next/static/next/``
     - ``next.ts`` + ``next.test.ts`` — the client runtime bundled to ``next.min.js``.
   * - ``next/templatetags/``
     - Django template tag libraries for components, forms, and ``{% next_static %}``.
   * - ``next/urls/``
     - File-router backends, URL-pattern parser, dispatcher, markers (``DUrl``).
   * - ``next/utils.py``
     - Small shared utilities.

Tests mirror this layout:

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Path
     - Purpose
   * - ``tests/conftest.py``
     - Calls ``tests.django_setup.setup()`` at import time and loads ``tests.fixtures`` as a plugin.
   * - ``tests/django_setup.py``
     - Idempotent Django settings configuration for the whole suite.
   * - ``tests/fixtures.py``
     - Shared fixtures (``client``, ``mock_http_request``, ``page_instance``, ``dependency_resolver``, ``reloader_tick_scenario``, …). Registered as a pytest plugin — no need to import.
   * - ``tests/support/``
     - Helpers, dataclass-based parametrize cases, scenarios, and ``unittest.mock`` patch utilities. Prefer reusing these before writing ad-hoc helpers.
   * - ``tests/site_pages/``
     - Sample pages directory registered as ``DIRS`` in the test ``NEXT_FRAMEWORK`` config.
   * - ``tests/benchmarks/``
     - Micro-benchmarks. **Opt-in**; excluded from the default run via ``--ignore=tests/benchmarks``.
   * - ``tests/<area>/``
     - Per-module tests matching ``next/<area>/``. Add new tests here.

Commands
--------

Install and setup
~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Command
     - Purpose
   * - ``make install``
     - Sync runtime deps from ``uv.lock`` (``uv sync --locked --no-dev``). Editable, no dev group.
   * - ``make dev-setup``
     - Full dev environment: ``uv sync --locked --dev``, build the JS bundle (``make build-js``), install pre-commit hooks. Use this for day-to-day work.
   * - ``make install-js``
     - ``npm ci`` only — install the Node toolchain without a rebuild.

Tests
~~~~~

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Command
     - Purpose
   * - ``make test``
     - Runs ``tests/`` with coverage. Fails if ``next/`` coverage is below 100%. Parallel (``pytest -n auto``). HTML report in ``htmlcov/``. Benchmarks skipped by default.
   * - ``make test-examples``
     - Each example must have ``tests/`` or ``tests.py``; runs pytest with coverage per example (see Examples_).
   * - ``make test-js``
     - Vitest unit tests for ``next/static/next/next.ts``.
   * - ``uv run pytest tests/ -n auto``
     - Fast iteration without coverage flags.
   * - ``uv run pytest tests/benchmarks --benchmark-only --no-cov``
     - Opt-in benchmark run. Numbers are only comparable on the same machine.

Lint, types, format
~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Command
     - Purpose
   * - ``make lint``
     - Ruff check (with fix) and format check on ``next/``, ``tests/``, ``examples/``.
   * - ``make format``
     - Apply Ruff fixes and format the same paths.
   * - ``make type-check``
     - Mypy on ``next/``. Strict config (``disallow_untyped_defs``, Django plugin).
   * - ``make lint-js``
     - ESLint on TypeScript.
   * - ``make format-js``
     - Prettier write.
   * - ``make format-js-check``
     - Prettier check (CI).

Build
~~~~~

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Command
     - Purpose
   * - ``make build-js``
     - Bundle ``next/static/next/next.ts`` → ``next.min.js`` via esbuild.
   * - ``make build``
     - ``uv build`` — wheel + sdist. Invokes ``build_hooks.py``, which shells out to ``npm ci && npm run build:next`` so the packaged artifact contains a fresh ``next.min.js``. Set ``NEXT_DJ_SKIP_JS_BUILD=1`` to bypass (only when the bundle is already present — used by CI between the dedicated build job and matrix jobs).

Docs and hooks
~~~~~~~~~~~~~~

.. list-table::
   :widths: 35 65
   :header-rows: 1

   * - Command
     - Purpose
   * - ``make pre-commit-run``
     - All pre-commit hooks on all files: typos, ``uv-lock``, prettier, eslint.
   * - ``make docs``
     - Build Sphinx docs.
   * - ``make docs-linkcheck``
     - Link check only.

Before a PR
~~~~~~~~~~~

Run ``make ci``. It runs, in order: ``lint``, ``type-check``, ``build-js``, ``lint-js``, ``format-js-check``, ``test-js``, ``test``, ``test-examples``.

CI vs local ``make ci``
~~~~~~~~~~~~~~~~~~~~~~~

GitHub Actions (``.github/workflows/ci.yml``) additionally:

- Builds the wheel in a dedicated ``build`` job, then runs the test matrix against the installed wheel (not ``uv run``). See ``.github/workflows/test-matrix.yml``. Python 3.12–3.14 × Django 4.2–6.0 with matrix exclusions.
- Builds docs with warnings as errors (``sphinx-build -W --keep-going``) and runs linkcheck.
- Runs the ``security`` job: typos and ``uv-lock`` via pre-commit.
- On pull requests: dependency review (``dependency-review`` job).
- In CI, lint runs on **`next/` only**, plus a separate import-order pass with ``--select I``. Locally, ``make lint`` also covers ``tests/`` and ``examples/``. Keep those directories clean so you do not surprise reviewers.
- The test matrix installs a specific Django with ``uv pip install "django==…"`` **after** installing the wheel. That override is intentional, not a broken lockfile.

Ruff uses ``select = ["ALL"]`` with ignores and per-file rules in ``pyproject.toml`` (line length 88, isort with ``known-first-party = ["next"]``, relaxed rules for ``examples/``, ``tests/``, and ``conftest.py``).

Code style
----------

Imports
~~~~~~~

- Prefer imports at the top of the module. Order: stdlib, third-party, first-party (``next``), blank line between groups. Ruff isort matches ``pyproject.toml``.
- Defer imports only for circular imports or optional dependencies.

.. code-block:: python

   import os
   from pathlib import Path

   import pytest
   from django.conf import settings

   from next.pages import Page

Comments and docstrings
~~~~~~~~~~~~~~~~~~~~~~~

- Write comments in English, normal sentence casing.
- The module docstring should state what the file is for.
- Function and class docstrings describe behavior and usage. **Do not** repeat parameter documentation that type hints already express.

Exceptions
~~~~~~~~~~

- Keep ``try``/``except`` scopes small. Catch specific types, never bare ``Exception`` or ``except:``.
- Do not swallow errors without logging or re-raising.

.. code-block:: python

   try:
       return path.read_text(encoding="utf-8")
   except OSError as exc:
       logger.warning("read failed: %s", exc)
       raise

Loops and builtins
~~~~~~~~~~~~~~~~~~

Prefer builtins, comprehensions, or ``itertools`` when they read clearly. Avoid manual loops that duplicate ``sum``, ``max``, filtering, or dict/set building.

Type hints
~~~~~~~~~~

Use annotations throughout ``next/``. Mypy is strict (see ``[tool.mypy]`` in ``pyproject.toml``).

Extensibility
-------------

Major pieces (template loaders, router backends, factories) should stay **replaceable**. Use clear protocols or ABCs, registration or settings-driven selection, and dependency injection where practical. Follow patterns in existing ``next/`` modules when adding extension points.

Testing
-------

- Pytest discovers ``test_*.py``, ``*_test.py``, and ``tests.py`` (see ``python_files`` in ``pyproject.toml``). In the main tree, prefer ``tests/<area>/test_<thing>.py``.
- Classes named ``Test…`` with ``test_*`` methods are common but not required.
- Prefer ``@pytest.mark.parametrize`` for matrix-style cases over copy-pasted tests. ``tests/support/cases.py`` holds dataclass-based parametrize rows — reuse and extend them.
- Use **`django.test.Client`** (the ``client`` fixture) for HTTP-level checks. Do not use the DRF API client unless the example explicitly adds DRF.
- For deps/forms/urls internals, reuse ``dependency_resolver``, ``csrf_request``, ``form_engine``, and the helpers in ``tests/support/``.
- The suite expects a pre-configured Django — do not call ``django.setup()`` yourself; ``tests/django_setup.py`` handles it.

Coverage
~~~~~~~~

- **`next/`**: CI and ``make test`` enforce 100% line coverage via ``--cov-fail-under=100``.
- Files under ``next/**/checks.py`` and ``next/checks/`` are **excluded** from coverage (see ``[tool.coverage.run] omit`` in ``pyproject.toml``). Do not chase coverage there.
- ``[tool.coverage.paths]`` collapses ``next/`` and ``*/site-packages/next/`` so coverage works identically for editable and wheel-installed runs.
- **Examples**: Each example must ship tests in ``tests/`` or ``tests.py``. ``make test-examples`` does **not** enforce ``--cov-fail-under=100``. Aim for full coverage anyway; reviewers may ask you to close gaps.

.. _Examples:

Examples
--------

Each example under ``examples/`` should be self-contained, with a ``README.md``, Django app(s) demonstrating the feature, and tests.

.. code-block:: text

   examples/
   └── feature-name/
       ├── README.md
       ├── config/
       ├── myapp/
       └── tests/
           ├── conftest.py
           └── test_*.py   # or tests/tests.py

Use ``examples/file-routing/tests/conftest.py`` as the template for Django settings, ``INSTALLED_APPS``, and the ``client`` fixture.

**New user-facing behavior** should normally include an example and tests. Small fixes or internal refactors may omit an example if maintainers agree in the PR.

Documentation
-------------

User-facing docs live under ``docs/`` and publish to Read the Docs (see ``README.md``). Doc build deps are the ``docs`` group in ``pyproject.toml`` (locked in ``uv.lock``). Read the Docs runs ``uv sync --frozen --no-dev --group docs`` (see ``.readthedocs.yaml``). Locally, run ``make docs`` or rely on the CI docs job. Fix any warnings and broken links — CI builds with ``-W --keep-going``.

See :doc:`documentation-guide` for writing conventions.

Pull requests
-------------

Checklist
~~~~~~~~~

- [ ] ``make ci`` passes locally
- [ ] New or changed ``next/`` code is covered by tests (100% for non-``checks.py`` files)
- [ ] TS changes: ``next.min.js`` rebuilt (``make build-js``) and vitest tests updated
- [ ] Example + tests when adding public API or behavior users copy from ``examples/``
- [ ] Docs updated when behavior or usage changes
- [ ] Link issues with ``Fixes #123`` / ``Closes #123`` when applicable

Branches
~~~~~~~~

Use prefixes such as ``feat/…``, ``fix/…``, ``docs/…``, ``refactor/…``, ``test/…``.

Commits
~~~~~~~

Conventional commits:

.. code-block:: text

   type(scope): short description

Examples: ``feat(urls): add custom backend hook``, ``fix(pages): handle missing template``, ``docs: sync contributing guide``.

Use **draft** PRs for work in progress.

Review
~~~~~~

Maintainers check style with Ruff and mypy, review tests, and assess how the change fits ``next/``. They also consider backwards compatibility and deprecation when behavior changes. Response time depends on maintainer availability; this document does not define a fixed SLA.

Help
----

- Search `issues <https://github.com/next-dj/next-dj/issues>`_ and prior PRs.
- Mirror patterns in ``next/`` and ``tests/`` for similar features — each area is consistent with itself.
- For failing coverage after ``make test``, inspect terminal output and ``htmlcov/index.html``.
- For quick test loops, run ``uv run pytest tests/ -n auto`` without coverage flags.
- If ``uv build`` fails on the JS step with a clear ``npm`` error, ensure Node 24 is on ``PATH`` or set ``NEXT_DJ_SKIP_JS_BUILD=1`` when a valid bundle already exists.

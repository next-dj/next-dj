.. _contributing-quality-gates:

Quality gates
=============

Every pull request passes the same automated gates before a reviewer looks at it.
This page states what each gate measures, where the number comes from, and which command reproduces it locally.

.. contents::
   :local:
   :depth: 2

Where the gates run
-------------------

``make ci`` runs the core gates locally in one command.
It runs ``lint``, ``type-check``, ``build-js``, ``lint-js``, ``format-js-check``, ``type-check-js``, ``test-js-coverage``, ``test``, ``test-examples``, and ``test-compat`` in that order.
GitHub Actions runs the same work split across jobs, and adds the documentation build, the support matrix, the supply-chain checks, and the benchmark comparison, which ``make ci`` leaves out.

.. list-table:: Gates and where they run
   :header-rows: 1
   :widths: 34 33 33

   * - Gate
     - Local command
     - Continuous integration
   * - Python test suite and coverage
     - ``make test``
     - ``test`` job over the support matrix
   * - Example projects
     - ``make test-examples``
     - ``test-examples`` job
   * - Ecosystem compatibility
     - ``make test-compat``
     - ``test-compat`` job
   * - Lint and formatting
     - ``make lint``
     - ``lint`` job
   * - Static types
     - ``make type-check``
     - ``type-check`` job
   * - Client runtime
     - ``make lint-js``, ``make type-check-js``, ``make test-js-coverage``
     - ``test-js`` job
   * - Documentation
     - ``make docs``
     - ``docs`` job
   * - Benchmarks
     - ``make bench``
     - ``bench`` workflow on every pull request
   * - Supply chain
     - ``make pre-commit-run``
     - ``security`` and ``dependency-review`` jobs

Test coverage
-------------

The suite runs with ``--cov=next`` and ``--cov-fail-under=100``, so a single uncovered line in ``next/`` fails the run.
The gate is identical locally and in continuous integration, which runs the suite against the installed wheel rather than the source tree.
A ``[tool.coverage.paths]`` mapping collapses ``next/`` and ``*/site-packages/next/`` so the two runs report the same numbers.

System check modules are excluded from that gate.
The ``omit`` list in ``pyproject.toml`` covers ``next/checks/`` and every per-area ``checks.py`` module, so a contributor writing a new system check does not chase coverage there.
Every other module under ``next/`` is inside the gate.

Each example project carries its own gate.
``make test-examples`` requires a ``tests/`` directory or a ``tests.py`` file in every example that ships a ``manage.py``, and it runs each one with ``--cov-fail-under=100``.
An example that is partly covered fails the build in the same way a partly covered core module does.

The client runtime has a matching gate.
``vitest.config.ts`` sets a threshold of 100 for lines, branches, functions, and statements over ``next/client/*.ts``, with ``next/client/adapters.ts`` excluded as the seam that wraps browser globals the test environment cannot model.

Lint and static types
---------------------

Ruff runs with ``select = ["ALL"]`` and a line length of 88, with the ignores and per-file rules declared in ``pyproject.toml``.
The ``lint`` job runs ``ruff check next/`` and ``ruff format --check next/``, so a formatting drift fails the same way a rule violation does.
``make lint`` covers ``next/``, ``tests/``, and ``examples/``, which is wider than the job, and keeping those directories clean avoids a surprise in review.

Mypy runs over the ``next`` package in strict mode with the Django stubs plugin.
The configuration enables ``disallow_untyped_defs``, ``disallow_incomplete_defs``, ``warn_return_any``, ``warn_unreachable``, ``strict_equality``, and the ``explicit-override``, ``ignore-without-code``, ``redundant-self``, ``possibly-undefined``, and ``truthy-bool`` error codes.
``make type-check`` runs the same command the ``type-check`` job runs.

Benchmarks
----------

Benchmarks live in ``tests/benchmarks/`` and stay out of the default test run through ``--ignore=tests/benchmarks``.
``make bench`` runs them with the flags continuous integration uses, which are the ``perf`` marker, a warmup of 1000 iterations, a minimum of 10 rounds, garbage collection disabled, and result storage under ``.benchmarks/``.
Local numbers compare only against other numbers from the same machine, so a pull request cites the workflow comparison rather than a local delta.

The benchmark workflow runs on every pull request and compares the head commit against the base commit on one runner.
The hard gate is ``--benchmark-compare-fail=median:99%``, which is roughly twice the base median and the strictest value the flag parser accepts, and tripping it fails the job and blocks the merge.
A softer tier warns when a median drifts more than 30 percent above the base without failing the job.
The comparison runs on a single Python and Django combination so matrix noise stays out of the numbers, and ``[skip bench]`` in the pull request title or the commit message bypasses the workflow.

On ``main`` the workflow pushes the head numbers to the ``gh-pages`` branch with an informational alert threshold of 200 percent that never blocks.

Documentation build
-------------------

The ``docs`` job builds the manual with ``sphinx-build -T -W --keep-going -b html`` and then runs the link checker over the same sources.
The ``-W`` flag turns every Sphinx warning into an error, so an unresolved cross reference, a page missing from a toctree, or an unregistered role fails the build.
``make docs`` adds ``-a`` and ``-E`` to force a full rebuild, which is what picks up a change under ``docs/_templates/``.
``uv run doc8 docs/content`` covers the reStructuredText style rules the pre-commit hooks also run.

Support matrix
--------------

A dedicated ``build`` job produces the wheel and the source distribution once, and the matrix jobs install that wheel rather than running from the source tree.
Each matrix job then pins its Django with ``uv pip install "django==<version>"`` after the wheel is installed, which is deliberate and not a broken lockfile.
The matrix covers Python 3.12, 3.13, and 3.14 against Django 5.2 and 6.0, and excludes Python 3.14 against Django 5.2.

A separate ``test-compat`` job runs ``pytest tests/compat`` with the ``compat`` dependency group, which pins django-crispy-forms, crispy-bootstrap5, django-widget-tweaks, django-htmx, and django-allauth.
That job checks the framework against the ecosystem packages a project is likely to have installed already.

Client runtime
--------------

The ``test-js`` job type-checks the TypeScript sources with ``tsc --noEmit``, checks formatting with Prettier, lints with ESLint, and bundles ``next/client/next.ts`` with esbuild.
It then enforces a hard budget of 14 KB gzipped on ``next/static/next/next.min.js``, because the runtime ships on every page, and that budget check runs in continuous integration only.
The job finishes with the vitest run and its coverage thresholds.

Supply chain
------------

The ``security`` job runs the ``typos`` and ``uv-lock`` pre-commit hooks over all files, so a lockfile that drifts from ``pyproject.toml`` fails.
On a pull request, a ``dependency-review`` job inspects dependency changes and fails on a vulnerability of moderate severity or higher.

See also
--------

.. seealso::

   :repo:`CONTRIBUTING.md <blob/main/CONTRIBUTING.md>` for environment setup, the project layout, and the pull request process.
   :doc:`/content/internals/contributing-notes` for the conventions the framework code follows.
   :doc:`/content/misc/project-status` for the supported versions and the public API surface.

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
   * - Documentation prose
     - ``make docs-lint``
     - ``docs`` job, and the ``prose-lint`` pre-commit hook
   * - Documentation code snippets
     - ``make docs-lint``
     - ``docs`` job, "Check documentation code snippets" step
   * - Benchmarks
     - ``make bench``
     - ``bench`` workflow on every pull request
   * - Supply chain
     - ``make pre-commit-run``
     - ``security`` and ``dependency-review`` jobs

Test order
----------

``pytest-randomly`` shuffles the suite on every run, so a test that leans on state another test left behind fails instead of passing by accident.
The seed is printed in the pytest header as ``Using --randomly-seed=<n>``, and passing that value back through ``--randomly-seed=<n>`` replays the exact order.
Benchmarks run with ``-p no:randomly``, because a paired comparison needs the same order on both sides.

Test coverage
-------------

The suite runs with ``--cov=next`` and ``--cov-fail-under=100``, so a single uncovered line in ``next/`` fails the run.
The gate is identical locally and in continuous integration, which runs the suite against the installed wheel rather than the source tree.
A ``[tool.coverage.paths]`` mapping collapses ``next/`` and ``*/site-packages/next/`` so the two runs report the same numbers.

Most system check modules are excluded from that gate.
The ``omit`` list in ``pyproject.toml`` names ``next/checks/``, the ``checks.py`` of ``apps``, ``components``, ``conf``, and ``urls``, and the whole ``checks/`` package of ``forms``, ``pages``, and ``partial``.
``next/static/checks.py`` is not in that list and stays inside the gate, so a static check still carries its own tests.
The generic ``*/settings.py`` entry lifts ``next/conf/settings.py`` out of the gate as well, which is why new configuration logic belongs in another module of the ``conf`` area.

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

Neither of those two sees semantic newlines, so ``docs/prose_lint.py`` covers that rule on its own.
It reports a line that holds the end of one sentence and the start of the next, and a sentence wrapped onto a second physical line, and it skips literal blocks, inline literals, roles, abbreviations, file names, and version numbers so the signal stays usable.
The ``docs`` job runs it before the build, ``make docs-lint`` runs it locally, ``make docs`` runs it before ``sphinx-build``, and the ``prose-lint`` pre-commit hook runs it over the changed files under ``docs/content/``.
The rule it enforces is stated in :doc:`style-guide`.

``docs/snippet_lint.py`` checks the code blocks themselves.
It parses every Python code block with :mod:`ast` to catch a syntax error, flags a ``NEXT_FRAMEWORK`` key that ``next/conf/defaults.py`` does not define, and reports an unbalanced DJX or Django template tag in a template block.
``make docs-lint`` runs it after ``docs/prose_lint.py``, and the ``docs`` job runs it as its own "Check documentation code snippets" step.

Support matrix
--------------

A dedicated ``build`` job produces the wheel and the source distribution once, and the matrix jobs install that wheel rather than running from the source tree.
Each matrix job then pins its Django with ``uv pip install "django==<version>"`` after the wheel is installed, which is deliberate and not a broken lockfile.
The matrix covers every combination the *Requirements* list in :doc:`/content/intro/install` names.

A separate ``test-compat`` job runs ``pytest tests/compat`` with the ``compat`` dependency group, which declares a minimum for django-crispy-forms, crispy-bootstrap5, django-widget-tweaks, django-htmx, and django-allauth.
That job checks the framework against the ecosystem packages a project is likely to have installed already.

Client runtime
--------------

The ``test-js`` job type-checks the TypeScript sources with ``tsc --noEmit``, checks formatting with Prettier, lints with ESLint, and bundles ``next/client/next.ts`` with esbuild.
It then enforces a hard budget of 14 KB gzipped on ``next/static/next/next.min.js``, because the runtime ships on every page, and that budget check runs in continuous integration only.
Growth past the budget calls for a lazily loaded chunk rather than a larger single file.
The job finishes with the vitest run and its coverage thresholds.

``next/client/next.ts`` is the single entry point that mounts ``window.Next`` and pulls in the morph, apply, wire, layer, trigger, asset, and stream modules.
``make build-js`` runs the same esbuild pass locally, minifying the bundle to ``next/static/next/next.min.js`` with a source map beside it and targeting ES2022.
The map is external and the bundle carries no ``sourceMappingURL`` comment, so browser devtools load the map only when a developer points them at it, while manifest storage finds no reference to rewrite at ``collectstatic`` time.
``build_hooks.py`` reads the bundle before packaging and raises on one that carries the comment, and ``NEXT_DJ_SKIP_JS_BUILD=1`` skips the npm run and packages the bundle already on disk.
The compiled file is a build product rather than a tracked source file, and the packaging configuration lists it as a build artefact so a distribution carries it.

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

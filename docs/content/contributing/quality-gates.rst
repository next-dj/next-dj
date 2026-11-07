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
     - ``make bench``, ``make bench-codspeed``
     - CodSpeed workflow on every pull request
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
The ``test`` job starts a Redis service and sets ``REDIS_URL``, so ``tests/realtime/test_integration.py`` runs against a real server there, and a local run reaches it with ``REDIS_URL=redis://localhost:6379/0 make test``.

Most system check modules are excluded from that gate.
The ``omit`` list in ``pyproject.toml`` names ``next/checks/``, the ``checks.py`` of ``apps``, ``components``, ``conf``, ``consent``, ``realtime``, ``scripts``, ``server``, ``site``, and ``urls``, and the whole ``checks/`` package of ``forms``, ``pages``, ``partial``, and ``seo``.
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
``make bench`` runs them locally under pytest-benchmark with the ``perf`` marker, a warmup of 1000 iterations, a minimum of 10 rounds, garbage collection disabled, and result storage under ``.benchmarks/``.
Local numbers compare only against other numbers from the same machine, so a pull request cites the CodSpeed report rather than a local delta.

The CodSpeed workflow runs on every pull request and every push to ``main`` on a single Python version, and CodSpeed posts its report on the pull request.
The CPU simulation instrument runs the whole suite and counts instructions instead of timing the runner, so the numbers do not depend on the hardware, but it leaves system calls out of the measure.
The memory instrument runs the whole suite as well and tracks every heap allocation.
The walltime instrument times only the benchmarks marked ``walltime`` on a CodSpeed bare-metal runner, the ones dominated by filesystem, socket, and Redis calls whose real cost the simulation understates.
``make bench-codspeed`` runs the suite locally under ``pytest-codspeed`` in walltime mode, since the simulation needs Valgrind on Linux.

The realtime benchmarks live in ``tests/benchmarks/realtime/`` and need no running Redis.
The ``realtime.publish``, ``realtime.hello``, ``realtime.presence``, and ``realtime.replay`` groups run on ``MemoryBroker`` and on a ``RedisBroker`` backed by ``fakeredis``, ``realtime.fanout`` runs on the memory broker alone, and the other groups touch no broker.
The ``fakeredis`` rows measure the client and Lua script overhead on an in-process emulator, not the latency of a Redis server.
Each benchmark checks its own result outside the measured body, so a call that degrades into an early exit fails the test instead of looking fast.
Each group measures one step of the publish and delivery path.

.. list-table:: Realtime benchmark groups
   :header-rows: 1
   :widths: 30 70

   * - Group
     - What it measures
   * - ``realtime.publish``
     - Numbering and storing one envelope on the memory and Redis brokers, and the public ``publish`` call through the configured broker with its validation, snapshot, and signal.
   * - ``realtime.fanout``
     - One publish on the memory broker reaching 1, 100, and 1000 synchronous subscribers of the same topic, and the same publish reaching asynchronous subscribers on one event loop.
       The asynchronous cases measure an enqueue that schedules a loop wakeup per subscriber and one that finds the wakeup already queued.
       Decoding one small or medium Redis Pub/Sub payload into a message sits in this group too.
   * - ``realtime.frame``
     - Serialising one small or medium envelope into an SSE event frame, and delivering one message as a WebSocket patches frame.
   * - ``realtime.inbound``
     - Decoding one client text frame with ``frames.parse``, for a heartbeat pong, a subscribe frame with a cursor, and an ``up`` frame.
   * - ``realtime.upstream``
     - Resolving the route of one upstream event in a fresh registry of 1 and of 100 routes, where the target event name holds one route and the rest sit under other names, and a miss on an event name no route serves.
       Dispatching one event to a trivial async handler on the loop and to a trivial sync handler on the handler pool, the ``upstream_received`` signal included.
   * - ``realtime.transform``
     - Applying one subscriber's topic transform to a delivered message with ``atransformed``, an async transform awaited on the loop and a sync transform run on the shared pool.
   * - ``realtime.hello``
     - Reading the latest position of 1 or 64 topics in one batch, and building and serialising the handshake frame from positions read beforehand, without touching a broker while it measures.
   * - ``realtime.presence``
     - Joining one connection to a roster of 1 and of 1000, refreshing the deadline of one connection on a roster of the same sizes, and reading the attendance snapshot of 1 and of 1000 members, on the memory broker and on a Redis broker backed by ``fakeredis``.
       The joining connection leaves again before each round, so every round measures the same roster size.
       The windows are not scheduled while it measures, so no background snapshot enters a timing.
       Cutting the members from a presence message of 1 and of 100 members with ``counted``, and passing a message without a presence event through untouched, need no broker.
   * - ``realtime.replay``
     - Reading the log of one topic after a cursor 1 and 64 messages behind, and after a cursor from a stale epoch that reports the gap, on both brokers.
       ``catch_up`` over 1 and 64 topics that each trail their snapshot by one message, and parsing a valid and a malformed ``Last-Event-ID``, complete the group.
   * - ``realtime.access``
     - Rechecking topic grants for one live connection with the synchronous ``check()``, inside the throttle window where the cached verdict answers and outside it where the authorizer runs again.
       Live connections under ASGI go through ``acheck()``, which this group does not measure.
   * - ``realtime.ticket``
     - Signing and verifying a session-bound WebSocket grant over 1 or 64 topics.
   * - ``realtime.negotiation``
     - Answering a request for a WebSocket ticket on a saved session over 1 or 64 topics, and the SSE fallback for an authorizer without an import-stable path.
   * - ``realtime.tunnel``
     - One ``http`` frame through ``DjangoTunnel``, from the frame checks and the URL routing on the shared pool to a trivial ASGI endpoint and the shaped reply.
   * - ``realtime.transports``
     - Resolving the transports a deployment offers, from the warm cache and cold after a cache clear that re-reads the settings and installed apps.
   * - ``partial.sse``
     - Draining a finite stream through ``PatchStreamWriter`` to its last byte.
       The cases are a stream of 50 heartbeat frames, a stream of 20 topic message frames that each carry the topic, sequence, epoch, and server time, and an empty stream that opens and closes.
       The benchmark lives in ``tests/benchmarks/partial/``.
   * - ``forms.transport``
     - Telling a tunneled form action from a plain one on a WSGI request, an ASGI request, and a tunneled request, and pinning the session of a tunneled action, cold on a fresh session and warm on one already pinned.
       The benchmark lives in ``tests/benchmarks/forms/``.

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
Each matrix job then installs the latest patch of its Django series with ``uv pip install "django~=<series>.0"`` after the wheel is installed, which is deliberate and not a broken lockfile.
The matrix covers every combination the *Requirements* list in :doc:`/content/intro/install` names.
The matrix checkout leaves out ``next/client/`` and ``docs/``, so a ``test-source`` job runs the tests that compare the core with those trees on a full checkout and fails if any of them skips.

A separate ``test-compat`` job runs ``pytest tests/compat`` with the ``compat`` dependency group, which declares a minimum for django-crispy-forms, crispy-bootstrap5, django-widget-tweaks, django-htmx, and django-allauth.
That job checks the framework against the ecosystem packages a project is likely to have installed already.

Client runtime
--------------

The ``test-js`` job type-checks the TypeScript sources with ``tsc --noEmit``, checks formatting with Prettier, lints with ESLint, and builds the seven bundles with esbuild.

.. list-table::
   :header-rows: 1
   :widths: 30 26 44

   * - Bundle
     - Entry point
     - When a page loads it
   * - ``next.min.js``
     - ``next/client/next.ts``
     - On every page, the core runtime behind ``window.Next``.
   * - ``next.scripts.min.js``
     - ``next/client/entries/scripts.ts``
     - Only when the init payload carries ``$scripts`` or ``$consent``, or page code calls ``Next.ready("scripts")``, for consent and third-party scripts.
   * - ``next.stream.min.js``
     - ``next/client/entries/stream.ts``
     - Only once a scan finds a ``data-next-stream`` container.
   * - ``next.ws.min.js``
     - ``next/client/entries/ws.ts``
     - Only while the ``ws`` transport is active, for the WebSocket delivery of a stream.
   * - ``next.csrf.min.js``
     - ``next/client/entries/csrf.ts``
     - Only when a mutation or the first focus in an action form needs a deferred CSRF token, for the fetch that mints it.
   * - ``next.poll.min.js``
     - ``next/client/entries/poll.ts``
     - Only once a scan finds a ``data-next-poll`` zone, for the zone poller.
   * - ``next.dev.min.js``
     - ``next/client/entries/dev.ts``
     - Only under ``DEBUG``, when the payload carries ``$dev``, for the development diagnostics.

The job then enforces a gzip budget on every bundle, 16384 bytes for ``next.min.js``, which ships on every page, and 3584, 4096, 4096, 768, 1024, and 2048 bytes for the scripts, stream, ws, csrf, poll, and dev chunks, and that budget check runs in continuous integration only.
Growth past a budget calls for a lazily loaded chunk rather than a larger core file.
The job finishes with the vitest run and its coverage thresholds.

``next/client/next.ts`` is the entry point that mounts ``window.Next`` and pulls in the morph, apply, wire, layer, trigger, asset, and stream modules, and each chunk entry point hands itself to it when it evaluates.
``make build-js`` runs the same single esbuild call locally, minifying each bundle into ``next/static/next/`` with a source map beside it and targeting ES2022.
The map is external and the bundle carries no ``sourceMappingURL`` comment, so browser devtools load the map only when a developer points them at it, while manifest storage finds no reference to rewrite at ``collectstatic`` time.
``build_hooks.py`` reads all seven bundles before packaging and raises on a missing one or one that carries the comment, and ``NEXT_DJ_SKIP_JS_BUILD=1`` skips the npm run and packages the bundle already on disk.
The compiled files are build products rather than tracked source files, and the packaging configuration lists them as build artefacts so a distribution carries them.

Supply chain
------------

The ``security`` job runs the ``typos`` and ``uv-lock`` pre-commit hooks over all files, so a lockfile that drifts from ``pyproject.toml`` fails.
On a pull request, a ``dependency-review`` job inspects dependency changes and fails on a vulnerability of moderate severity or higher.
Every third-party action is pinned by commit SHA, with its release tag in a comment.

See also
--------

.. seealso::

   :repo:`CONTRIBUTING.md <blob/main/CONTRIBUTING.md>` for environment setup, the project layout, and the pull request process.
   :doc:`/content/internals/contributing-notes` for the conventions the framework code follows.
   :doc:`/content/misc/project-status` for the supported versions and the public API surface.

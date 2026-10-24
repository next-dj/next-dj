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

The realtime benchmarks live in ``tests/benchmarks/realtime/`` and need no running Redis.
Only the ``realtime.publish``, ``realtime.fanout``, and ``realtime.hello`` groups touch a broker.
``realtime.publish`` runs on both the in-process ``MemoryBroker`` and a ``RedisBroker`` backed by ``fakeredis``, and ``realtime.fanout`` runs on the memory broker alone.
In ``realtime.hello`` only the position read goes to a broker, on both of them, while the handshake frame benchmark uses the memory broker in its setup and never calls a broker in the measured body.
The other groups run without a broker.
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
     - One publish on the memory broker reaching 1, 100, and 1000 synchronous subscribers of the same topic.
   * - ``realtime.frame``
     - Serialising one small or medium envelope into an SSE event frame, and delivering one message as a WebSocket patches frame.
   * - ``realtime.hello``
     - Reading the latest position of 1 or 64 topics in one batch, and building and serialising the handshake frame from positions read beforehand, without touching a broker while it measures.
   * - ``realtime.access``
     - Rechecking topic grants for one live connection with the synchronous ``check()``, inside the throttle window where the cached verdict answers and outside it where the authorizer runs again.
       Live connections under ASGI go through ``acheck()``, which this group does not measure.
   * - ``realtime.ticket``
     - Signing and verifying a session-bound WebSocket grant over 1 or 64 topics.
   * - ``realtime.transports``
     - Resolving the transports a deployment offers, from the warm cache and cold after a cache clear that re-reads the settings and installed apps.
   * - ``partial.sse``
     - Draining a finite stream through ``PatchStreamWriter`` to its last byte.
       The cases are a stream of 50 heartbeat frames, a stream of 20 topic message frames that each carry the topic, sequence, epoch, and server time, and an empty stream that opens and closes.
       The benchmark lives in ``tests/benchmarks/partial/``.

Realtime load harness
---------------------

``make load`` runs an end-to-end load scenario that the micro-benchmarks cannot cover.
It spawns a uvicorn server with Channels and configures ``RedisBroker`` against a real Redis.
It starts ``K`` publisher processes that publish through ``next.realtime.publish``, and it opens ``M`` SSE clients and ``N`` WebSocket clients spread over client processes.
The harness then reports whether every subscriber received every message and how long delivery took.

The scenario is opt-in.
``pytest`` ignores ``tests/benchmarks`` and ``tests/load`` by default, and neither ``make test`` nor continuous integration runs it.
It needs the ``load`` dependency group, which ``make load`` pulls in through ``uv run --group load``.
The group adds ``httpx``, ``websockets``, and ``psutil``, and the server comes from ``uvicorn`` in the ``dev`` group.

Profiles
~~~~~~~~

``PROFILE`` selects the shape and defaults to ``small``.
An unknown name fails the run.

.. list-table:: Load profiles
   :header-rows: 1
   :widths: 40 30 30

   * - Setting
     - ``small``
     - ``medium``
   * - Publisher processes
     - 2
     - 4
   * - SSE clients
     - 50
     - 500
   * - WebSocket clients
     - 50
     - 500
   * - Topics
     - 5
     - 20
   * - Publish rate per publisher
     - 20 messages per second
     - 50 messages per second
   * - Duration
     - 20 seconds
     - 30 seconds
   * - Client processes
     - 1
     - 4
   * - Ready timeout
     - 30 seconds
     - 120 seconds
   * - Concurrent connects
     - 64
     - 64
   * - Allowed lost ratio
     - 0
     - 0.001
   * - Allowed lost ratio per client
     - 0
     - 0.01
   * - Allowed error ratio
     - 0
     - 0.01
   * - Allowed resync ratio
     - 0
     - 0.01

The ready timeout bounds how long the clients may take to receive ``hello`` before the run fails without publishing.
A worker process that raises fails the run at once with the worker traceback in the pytest report instead of waiting out a deadline.
The concurrent connects cap how many connections all client processes open at once, which keeps the server's accept queue below the macOS default ``kern.ipc.somaxconn`` of 128.

``LOAD_DURATION`` overrides the duration of the chosen profile with a positive number of seconds, which suits a quick smoke run.

.. code-block:: bash

   make load
   make load PROFILE=medium
   make load LOAD_DURATION=5

Redis source
~~~~~~~~~~~~

With ``REDIS_URL`` set, the target uses that Redis and leaves it running.
Without it, the target starts a throwaway ``redis:7-alpine`` container bound to a free local port and reads the port back from ``docker port``.
It fails with an error when Docker reports no host port.
It waits until ``docker exec`` runs ``redis-cli ping`` inside the container and gets an answer, and gives up after 50 tries 0.2 seconds apart.
When the run ends, it stops that container by its id, and it never stops a container it did not start.
That path needs Docker.
The harness raises the Redis client pool through ``max_connections`` in the URL, because each subscription holds one connection and the library default of 100 is too small.
The local-memory cache that holds the sessions is sized well above the sessions of any profile, because an evicted session fails the WebSocket ticket check.
Every run keys its data under a unique prefix and purges it afterwards.

Metrics and pass criteria
~~~~~~~~~~~~~~~~~~~~~~~~~

The run prints a short summary and writes the full result to ``tests/load/results/<timestamp>-<profile>.json``, a directory that git ignores.
The result carries the machine description, the publish totals, and one section per transport with:

* Clients that opened and clients that received the ``hello`` handshake.
* Messages delivered against messages expected, and the lost, excess, and skipped counts.
* The worst lost ratio, which is the largest share of its expected messages that any single client lost.
* Resyncs, restarts, reconnects, clients that gave up reconnecting, duplicates, gaps, and errors.
* End-to-end latency, server-to-client latency, and handshake latency, each as ``p50``, ``p95``, ``p99``, and ``max`` in milliseconds.

The server section holds the average and peak CPU percentage and the peak resident memory of the server process.
A sampler thread in the harness process watches only the server process through ``psutil``.
It starts once the server reports its port, before the client processes spawn, and stops when publishing and draining finish, so the window also covers client opening.
It takes one sample every 0.5 seconds.
Each CPU sample is the share of one core the server used since the previous sample, so a busy multi-threaded server can exceed 100 percent.
Each memory sample is the resident set size at that moment.
The average is the mean of the CPU samples, and the peak values are the maximum over all samples.
A run too short to take a sample reports no server figures.
The result also records the time spent opening clients, publishing, and draining.

The test fails when published messages plus publish errors differ from the scheduled total of publishers times the ceiling of rate times duration.
It also fails when a publisher hits a broker error, when a client on either transport gets no ``hello``, or when a transport delivers nothing.
Skipped, duplicate, and excess counts must be zero on every transport.
It also fails when the number of clients on a transport differs from the profile.
It also fails when the lost count or the gap count on a transport exceeds the allowed lost ratio times the expected count, or when the worst lost ratio of any single client exceeds the allowed lost ratio per client.
It also fails when the resync count exceeds the allowed resync ratio times the client count.
A resync is a reconnect that resumed past the saved position or a jump in the sequence.
A restart is an epoch that changed inside an open stream, and the test fails on any restart, which is counted apart from resyncs.
It also fails when the error count exceeds the allowed error ratio times the client count, or when any client gave up reconnecting.
The ``small`` profile allows no loss, resyncs, or errors.
The ``medium`` profile allows aggregate loss up to 0.1 percent of the expected messages, up to 1 percent of the messages of any one client, and resyncs and errors up to 1 percent of the client count.
The latency figures are reported and never gated, so compare them only between runs on the same machine.

End-to-end latency is the receive time on the client minus the send time the publisher stamped into the message.
Server-to-client latency is the receive time on the client minus the ``server_time`` the server wrote into the frame.
The server reads its wall clock when it builds the frame and truncates it to whole milliseconds, so the stamp can sit up to 1 ms before the true instant.
The server-to-client figure therefore has a resolution of 1 ms and is an upper bound, and small values near zero mean less than 1 ms.
Both figures compare wall clocks, which is sound because the publishers, server, and clients run on one machine.
Handshake latency runs from the start of the connection attempt to the first ``hello`` frame and uses the monotonic clock.
For a WebSocket client the attempt starts before the HTTP negotiation that mints the ticket, so the figure includes that request and the upgrade.
Each reconnect starts a new attempt and the sample comes from the attempt that received the first ``hello``, so later reconnects add no sample.

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

The ``test-js`` job type-checks the TypeScript sources with ``tsc --noEmit``, checks formatting with Prettier, lints with ESLint, and builds the six bundles with esbuild.

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
     - Only once a scan finds a ``data-next-stream`` or ``data-next-sse`` container.
   * - ``next.csrf.min.js``
     - ``next/client/entries/csrf.ts``
     - Only when a mutation or the first focus in an action form needs a deferred CSRF token, for the fetch that mints it.
   * - ``next.poll.min.js``
     - ``next/client/entries/poll.ts``
     - Only once a scan finds a ``data-next-poll`` zone, for the zone poller.
   * - ``next.dev.min.js``
     - ``next/client/entries/dev.ts``
     - Only under ``DEBUG``, when the payload carries ``$dev``, for the development diagnostics.

The job then enforces a gzip budget on every bundle, 15360 bytes for ``next.min.js``, which ships on every page, and 3584, 3072, 768, 1024, and 2048 bytes for the scripts, stream, csrf, poll, and dev chunks, and that budget check runs in continuous integration only.
Each of the six bundles has its own gzip budget, including the core bundle that every page loads.
Growth past a budget calls for a lazily loaded chunk rather than a larger core file.
The job finishes with the vitest run and its coverage thresholds.

``next/client/next.ts`` is the entry point that mounts ``window.Next`` and pulls in the morph, apply, wire, layer, trigger, asset, and stream modules, and each chunk entry point hands itself to it when it evaluates.
``make build-js`` runs the same single esbuild call locally, minifying each bundle into ``next/static/next/`` with a source map beside it and targeting ES2022.
The map is external and the bundle carries no ``sourceMappingURL`` comment, so browser devtools load the map only when a developer points them at it, while manifest storage finds no reference to rewrite at ``collectstatic`` time.
``build_hooks.py`` reads all six bundles before packaging and raises on a missing one or one that carries the comment, and ``NEXT_DJ_SKIP_JS_BUILD=1`` skips the npm run and packages the bundle already on disk.
The compiled files are build products rather than tracked source files, and the packaging configuration lists them as build artefacts so a distribution carries them.

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

.. _internals-client-runtime:

The client runtime for contributors
===================================

The browser runtime is TypeScript under ``next/client/``, built into minified bundles under ``next/static/next/``.
This page explains the build, how a chunk joins the runtime, how a protocol constant reaches the client, and how the golden fixtures pin both sides of the wire.
:doc:`/content/contributing/quality-gates` states the gates a change to the runtime passes.

.. contents::
   :local:
   :depth: 2

The build
---------

``make build-js`` runs ``next/client/build.mjs``, which does two things in order.

1. It runs ``next/static/protocol.py`` to write ``next/client/protocol.gen.ts`` from the Python constants of the wire protocol.
2. It compiles every entry point with esbuild into a minified bundle with an external source map.

The runtime ``next.ts`` is the first entry and every file of ``next/client/entries/`` is another, so each lazy chunk is a bundle of its own.
Every bundle carries one build id, a digest of the sources of ``next/client/`` and its ``entries/``, which esbuild defines as ``__BUILD__``.
A chunk refuses to install into a runtime of another build and reloads the page once, so a browser cache that kept a chunk of an older release never mixes with a newer runtime.
Build the bundles before a test that reads them, and never edit a file under ``next/static/next/``.

.. mermaid::

   flowchart LR
       Constants["Python constants of the wire protocol"]
       Protocol["next/static/protocol.py"]
       Generated["next/client/protocol.gen.ts"]
       Entries["esbuild entries: next.ts and entries/*.ts"]
       Bundles["minified bundles in next/static/next/"]
       Extend["Next._extend(name, install, __BUILD__)"]
       Installed["chunk installed into the runtime"]
       Reload["page reloads once"]
       Constants --> Protocol --> Generated --> Entries --> Bundles
       Bundles --> Extend
       Extend -->|same build id| Installed
       Extend -->|other build id| Reload

A statement carrying the ``TEST:`` label is a seam for the unit tests, such as a reset of module state or a hook that observes an abort.
The build passes ``dropLabels: ["TEST"]``, so the production bundle never holds one and the unit tests, which run the sources directly, keep it.
The label never marks logic a production path needs.

Adding a chunk
--------------

A chunk is a feature the runtime loads only on a page that needs it.
Adding one touches a short list of places, and the tests of ``tests/static/test_chunk_sync.py`` name each one that is forgotten.

1. Write the module of the chunk in ``next/client/``, exporting a factory.
2. Write its entry in ``next/client/entries/<name>.ts``.
   The entry calls ``window.Next._extend("<name>", install, __BUILD__)``, and ``install`` builds the chunk from the kernel it receives.
3. Declare its type in ``Modules`` of ``next/client/kernel.ts``, which maps each chunk name to what it exports.
4. Add the entry to ``ENTRIES`` of ``next/client/build.mjs`` under the name ``next.<name>.min``.
5. Name the chunk in the ``#modules`` table of ``next/client/next.ts``.
6. Register the bundle in ``CHUNK_STATIC_PATHS`` of ``next/static/runtime.py`` so the server can name its URL.
   A chunk a page names by script tag or in ``$router`` joins ``DECLARED_CHUNK_KEYS``, and any other is listed in ``$chunks``.
7. Add the bundle to the lists of ``build_hooks.py`` and of the wheel and sdist artifacts in ``pyproject.toml``, which the test also compares.
8. Add its gzip budget to the budget step of the ``test-js`` job in ``.github/workflows/ci.yml``.

The contract between a chunk and the runtime is ``Kernel`` in ``kernel.ts``.
A chunk installs through ``Next._extend`` with a function of the kernel, serves the handlers the runtime calls through ``kernel.serve``, and hooks the apply phases through ``kernel.hook``.
No chunk writes a field of the runtime or of another chunk, and a chunk reaches another through ``kernel.load`` and ``kernel.module``.
Hooks run in ascending order, which the ``ORDER_`` constants of ``kernel.ts`` fix, so the result never depends on which bundle evaluated first.
A second copy of a bundle installs nothing.

The budget is the reason a feature lands in a chunk rather than in the core.
The CI step compresses each bundle with gzip and fails when one passes its budget, and raising a budget is a decision for the review, not a fix for a red job.

Adding a protocol constant
--------------------------

A header, an attribute, a payload key, an event name, or a chunk key that both sides spell is declared once in Python and generated for the client.

1. Declare the constant in the Python module that owns it, such as ``next/partial/headers.py``, ``next/partial/keys.py``, or ``next/wire.py``.
2. Add a row to ``EXPORTS`` of ``next/static/protocol.py`` with the TypeScript name, the module, and the constant.
3. Run ``make build-js``, which rewrites ``next/client/protocol.gen.ts``, and commit the result.
4. Import the name from ``./protocol`` in the TypeScript module that uses it.

``protocol.py`` reads the constants with ``ast``, so it needs neither Django nor an installed package.
Two tests guard the arrangement.
``tests/static/test_protocol.py`` fails when the checked-in ``protocol.gen.ts`` differs from what ``render()`` writes, and its message says to run ``make build-js``.
``tests/static/test_literals.py`` scans the Python sources and fails on a protocol string spelled apart from its constant, naming the file, the line, and the constant to use.
Declare a new string once, next to its siblings in ``next/partial/keys.py``, and spell it everywhere else through the constant.

The golden fixtures
-------------------

The golden fixtures pin the bytes the server writes and prove the client applies exactly those bytes.
They live in ``tests/partial/golden/``.

- The envelopes of zone and form answers sit beside a ``.meta.json`` that names the headers and the status.
- ``chunk/`` holds the document chunks of a streamed boundary, ``stream/`` an event stream, and ``navigation/`` the navigation envelopes of a slot, a body, and a visit.

``tests/partial/test_golden.py`` and ``tests/partial/test_golden_formats.py`` build each case with the real serializers and compare it with the committed file.
``next/client/golden.test.ts`` reads the same files and feeds them through the client, the ``Applier``, the reader, the drain and the navigation code, and asserts the resulting DOM.
A wire change that moves either side therefore fails one of the two suites.

A deliberate change to the format regenerates the files with ``GOLDEN_UPDATE=1`` on the Python tests, then ``make test-js`` proves the client still applies them.
Review the diff of the fixtures as the diff of the protocol.

The commands
------------

``make build-js``
   Writes the generated module and the bundles.
``make test-js`` and ``make test-js-coverage``
   Run vitest, the second with the coverage gate over ``next/client/``.
``make lint-js``, ``make format-js`` and ``make type-check-js``
   Run ESLint, Prettier, and ``tsc --noEmit``.

``next/client/adapters.ts`` wraps the browser globals the test environment cannot model and is the one file left out of the coverage gate.
New code that touches a global goes through an adapter, so the rest stays testable.

See also
--------

.. seealso::

   :doc:`router-pipeline` for the router chunk and its server half.
   :doc:`boundary-pipeline` for the boundary chunk and the chunk format of a stream.
   :doc:`/content/ref/client` for the public surface of the runtime.

.. _internals-contributing:

Contributor notes
=================

This page collects the conventions that the framework code itself follows.
It covers module layout, naming rules, and the invariants each subsystem must preserve.
Read it before sending a patch that touches the core packages.

.. note::

   This page targets **framework contributors** editing ``next/``.
   Documentation authoring rules live under :doc:`/content/contributing/index`.
   Contribution workflow (tooling, CI, benchmarks, PR checklist) lives in `CONTRIBUTING.md <https://github.com/next-dj/next-dj/blob/main/CONTRIBUTING.md>`_.

.. contents::
   :local:
   :depth: 2

A patch passes through a fixed set of gates before it merges.

.. mermaid::

   flowchart LR
       lint[Lint] --> types[Types]
       types --> tests[Tests]
       tests --> checks[System checks]
       checks --> pr[Pull request]

Conventions
-----------

Module layout
~~~~~~~~~~~~~

Each subsystem keeps a shallow layout where every submodule is small.
A new submodule joins ``__init__.py`` only when its public surface needs a shorter import path.
A submodule grows into a package of its own only when one concern splits across several bodies, and its façade keeps the import path the callers already use.

Annotations
~~~~~~~~~~~

Modules that participate in dependency resolution never use ``from __future__ import annotations``.
This applies to ``page.py``, ``component.py``, ``providers.py``, every action handler, and every ``get_initial`` callable.
The DI resolver inspects real annotations, not strings, and ``typing.get_origin`` returns ``None`` on stringified generics.

Public callables
~~~~~~~~~~~~~~~~

Names exposed through ``@page.context``, ``@component.context``, ``@action``, and through provider classes never start with an underscore.
``@context`` imported from ``next`` is the documented alias for ``@page.context`` used in page modules.
Names prefixed with ``_`` stay module internal.

System checks
~~~~~~~~~~~~~

Every check lives next to the subsystem it validates.
The codes follow ``next.E<NNN>`` for errors and ``next.W<NNN>`` for warnings.
A new check registers through ``next.checks.register_all``.

Signals
~~~~~~~

Every signal lives in a ``signals`` submodule of its subsystem.
The aggregator ``next.signals`` re-exports each name.
A new signal adds an entry to the aggregator and to the topic catalog in ``docs/content/topics/signals.rst``.

Module docstrings
~~~~~~~~~~~~~~~~~

Test modules carry no module-level docstring.
Every production module opens with a one-line summary at the top.

Imports
~~~~~~~

Every ``import`` lives at the top of the module.
Imports inside functions or methods are not used, including inside tests.

Docstrings
~~~~~~~~~~

A docstring is one summary line, or at most a short paragraph.
Examples, enumerations, and historical notes belong in the guide documentation, not in docstrings.

Prose punctuation
~~~~~~~~~~~~~~~~~

The same punctuation rules that bind the documentation also bind every docstring, comment, and log message in ``next/``.
No semicolon joins two clauses.
No em or en dash separates one statement from the next.
The full rule set lives in :doc:`/content/contributing/style-guide`.

Decorative separators
~~~~~~~~~~~~~~~~~~~~~

CSS, JavaScript, and Jinja files in ``docs/_static`` and ``docs/_templates`` carry no decorative ``/* ---- section ---- */`` banners.
A comment explains a non-obvious choice and nothing else.

Testing the framework
---------------------

The repository ships its own pytest suite plus a per example suite under ``examples/``.

.. code-block:: bash
   :caption: shell

   uv run pytest
   uv run pytest examples/admin
   uv run python examples/admin/manage.py check

The system checks run through an example project because the framework itself ships no ``manage.py``.
Always run the framework suite and the system checks before opening a pull request.

Documentation tests
-------------------

When introducing or removing a public API, update the import-presence coverage in ``tests/test_public_api.py`` or the matching area suite so an accidental removal fails CI.

Code style
----------

The project uses ``ruff`` for linting and ``mypy`` for static type checks.
Run both before submitting.

.. code-block:: bash
   :caption: shell

   uv run ruff check
   uv run mypy

See also
--------

.. seealso::

   `CONTRIBUTING.md <https://github.com/next-dj/next-dj/blob/main/CONTRIBUTING.md>`_ for the full contribution workflow (setup, testing, benchmarks, PRs).
   :doc:`/content/contributing/writing-documentation` for the documentation rules.

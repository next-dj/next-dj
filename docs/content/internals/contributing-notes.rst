.. _internals-contributing:

Contributor Notes
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

Conventions
-----------

Module Layout
~~~~~~~~~~~~~

Each subsystem keeps a flat layout where every submodule is small.
A new submodule joins ``__init__.py`` only when its public surface needs a shorter import path.

Annotations
~~~~~~~~~~~

Modules that participate in dependency resolution never use ``from __future__ import annotations``.
This applies to ``page.py``, ``component.py``, and to ``providers.py``.
The DI resolver inspects real annotations, not strings, and ``typing.get_origin`` returns ``None`` on stringified generics.

Public Callables
~~~~~~~~~~~~~~~~

Names exposed through ``@page.context``, ``@component.context``, ``@action``, and through provider classes never start with an underscore.
Names prefixed with ``_`` stay module internal.

System Checks
~~~~~~~~~~~~~

Every check lives next to the subsystem it validates.
The codes follow ``next.E<NN>`` for errors and ``next.W<NN>`` for warnings.
A new check registers through ``next.checks.register_all``.

Signals
~~~~~~~

Every signal lives in a ``signals`` submodule of its subsystem.
The aggregator ``next.signals`` re-exports each name.
A new signal adds an entry to the aggregator and to the topic catalog in ``docs/content/topics/signals.rst``.

Testing the Framework
---------------------

The repository ships its own pytest suite plus a per example suite under ``examples/``.

.. code-block:: bash
   :caption: shell

   uv run pytest
   uv run pytest examples/admin
   uv run python manage.py check

Always run the framework suite and the system checks before opening a pull request.

Documentation Tests
-------------------

When introducing a new public API or removing one, add or update an import-presence test in the matching ``tests/<area>/`` suite so an accidental removal fails CI.
The test imports the name and confirms its presence in the reference.

Code Style
----------

The project uses ``ruff`` for linting and ``mypy`` for static type checks.
Run both before submitting.

.. code-block:: bash
   :caption: shell

   uv run ruff check
   uv run mypy

See Also
--------

.. seealso::

   `CONTRIBUTING.md <https://github.com/next-dj/next-dj/blob/main/CONTRIBUTING.md>`_ for the full contribution workflow (setup, testing, benchmarks, PRs).
   :doc:`/content/contributing/writing-documentation` for the documentation rules.

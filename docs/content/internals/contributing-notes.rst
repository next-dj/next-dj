.. _internals-contributing:

Contributor Notes
=================

This page collects the conventions that the framework code itself follows.
Read it before sending a patch that touches the core packages.

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
This applies to ``page.py``, ``layout.py``, ``component.py``, and to ``providers.py``.
The DI resolver inspects real annotations, not strings, and ``typing.get_origin`` returns ``None`` on stringified generics.

Public Callables
~~~~~~~~~~~~~~~~

Names exposed through ``@page.context``, ``@component.context``, ``@action``, ``get_initial``, and through provider classes never start with an underscore.
Names prefixed with ``_`` stay module internal.

System Checks
~~~~~~~~~~~~~

Every check lives next to the subsystem it validates.
The codes follow ``next.E<NN>`` for errors and ``next.W<NN>`` for warnings.
A new check registers through ``next.checks.common.register_all``.

Signals
~~~~~~~

Every signal lives in a ``signals`` submodule of its subsystem.
The aggregator ``next.signals`` re-exports each name.
A new signal adds an entry to the aggregator and to the topic catalog in ``docs/content/topics/signals.rst``.

Migration Notes
---------------

Existing applications that upgrade across breaking changes find a migration guide in :doc:`/content/releases/index`.

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

Add a regression test under ``tests/docs/`` when introducing a new public API or removing one.
The test imports the name, confirms its presence in the reference, and prevents accidental removal.

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

   :doc:`/content/contributing/index` for the broader contribution workflow.
   :doc:`/content/contributing/writing-documentation` for the documentation rules.

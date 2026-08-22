.. _internals-adding-an-area:

Adding an area
==============

A new subsystem lands as a package ``next/<area>/`` with a mirror test package ``tests/<area>/``.
This page states the contract such a package follows and separates the mandatory parts from the recurring conventions.

.. contents::
   :local:
   :depth: 1

What every area provides
------------------------

- A package under ``next/`` whose public surface is re-exported from its ``__init__.py``.
- A mirror package ``tests/<area>/`` that holds the area tests.
- A one-line module docstring at the top of every source module.
- Type hints throughout, checked by mypy in strict mode.
- A shallow layout of one-word modules.
  A module grows into a package only when one concern splits across several bodies, as ``next/forms/dispatch/`` does with ``build``, ``permissions``, ``responses``, and ``wizard`` behind its façade.

Recurring modules
-----------------

The recurring module names carry fixed meanings, but every one of them is optional.
An area adds a module when it owns the concern, not to complete a template.

``registry.py``
   An ordered list of registrations plus a dict index over it.
   Present in ``pages``, ``components``, and ``partial``.
   A ``_version`` counter appears only where a derived cache needs invalidation, which today means ``next/components/registry.py`` alone.

``manager.py``
   A façade over the area with lazy backend initialisation.
   Present in ``pages``, ``components``, ``forms``, ``partial``, ``static``, and ``urls``.

``backends.py``
   A Protocol or ABC contract with settings-driven selection.
   Present in ``components``, ``forms``, ``partial``, ``static``, and ``urls``.

``dispatch.py``, ``markers.py``, ``providers.py``, ``signals.py``, ``checks.py``
   Appear when the area dispatches actions, declares frozen dataclass markers, provides dependencies, emits signals, or validates configuration.

The number of submodules varies from a handful in ``conf`` to nearly twenty in ``partial``, so no area serves as a size template for another.

Legitimate deviations
---------------------

Two existing areas depart from the recurring set on purpose.

- ``next/pages/`` ships no ``backends.py`` at all.
  Page loading offers no settings-driven choice between implementations, so a backend contract would guard nothing.
- ``next/partial/`` configures exactly one protocol backend.
  ``PARTIAL_BACKENDS`` activates the first entry, and a second entry is reported by the ``next.W071`` warning instead of joining a pool of interchangeable backends.

Starting point
--------------

Copy the closest existing area in spirit rather than an abstract template.
The module map in :doc:`overview` shows what every area currently ships, and the contract above lists the parts a copy keeps.

See also
--------

.. seealso::

   :doc:`contributing-notes` for the conventions the framework code follows.
   :doc:`overview` for the module map of every existing area.

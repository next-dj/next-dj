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
   A ``_version`` counter joins it wherever a derived cache has to be invalidated.

``manager.py``
   A façade over the area with lazy backend initialisation.

``backends.py``
   A Protocol or ABC contract with settings-driven selection.

``errors.py``
   The area's public exceptions, each re-exported from the area's ``__init__.py`` so callers never import the module directly.

``ports.py``
   The area's implementation of a ``next.ports`` protocol, which ``AppConfig.ready()`` binds into the matching slot.
   An area ships one when another area has to reach it without importing, see :doc:`/content/ref/ports` for the areas that do.

``dispatch.py``, ``markers.py``, ``providers.py``, ``signals.py``, ``checks.py``
   Appear when the area dispatches actions, declares frozen dataclass markers, provides dependencies, emits signals, or validates configuration.
   ``checks.py`` becomes a package of one-word submodules once one file stops holding the area's checks, as it has in ``pages``, ``forms``, ``partial``, and ``seo``, and either form joins the imports and ``CHECK_MODULES`` of ``next/checks/catalog.py``, which ``tests/checks/test_catalog.py`` enforces.

The number of submodules differs widely between areas, so no area serves as a size template for another.

Where an area does ship the full set, the modules form one chain from the settings mapping to the structures a request reads.

.. mermaid::

   flowchart LR
       Settings["NEXT_FRAMEWORK"]
       Backends["backends.py"]
       Manager["manager.py"]
       Registry["registry.py"]
       Checks["checks.py"]
       Signals["signals.py"]

       Settings -- "backend entries" --> Backends
       Backends -- "instantiated backends" --> Manager
       Manager -- "discovered entries" --> Registry
       Manager -- "configuration to validate" --> Checks
       Manager -- "lifecycle emissions" --> Signals

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

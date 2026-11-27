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

``manifest.py``
   The one place the area wires itself at startup, a ``next.manifest.Manifest`` named ``manifest``, see :ref:`internals-adding-an-area-manifest`.

``lifecycle.py``
   The area's participant in the page request, when the area takes part in one, see :ref:`internals-adding-an-area-participant`.

``ports.py``
   The area's implementation of a ``next.ports`` protocol, which its manifest binds into the matching slot.
   An area ships one when another area has to reach it without importing, see :doc:`/content/ref/ports` for the areas that do.

``dispatch.py``, ``markers.py``, ``providers.py``, ``signals.py``, ``checks.py``
   Appear when the area dispatches actions, declares frozen dataclass markers, provides dependencies, emits signals, or validates configuration.
   ``checks.py`` becomes a package of one-word submodules once one file stops holding the area's checks, as it has in ``pages``, ``forms``, ``partial``, and ``seo``, and either form is named by the ``checks`` field of the area manifest, which ``tests/checks/test_catalog.py`` enforces.

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

.. _internals-adding-an-area-manifest:

The area manifest
-----------------

``NextFrameworkConfig.ready()`` names no area.
It walks ``MANIFESTS`` in ``next/apps/areas.py``, an explicit ordered tuple of every area manifest, connects every receiver first, then binds the ports, runs the startup steps, and registers the declarations and participants of each area in turn.
A new area writes its ``manifest.py`` and joins that tuple, and nothing else in the composition changes.

.. code-block:: python

   from next.manifest import Bind, Manifest
   from next.ports import layer_loading_slot

   from . import checks, signals
   from .layers import stash_layers
   from .lifecycle import PartialParticipant
   from .release import ReleaseScripts

   manifest = Manifest(
       "partial",
       ports=(Bind(layer_loading_slot, stash_layers),),
       providers=(ReleaseScripts(),),
       participants=(PartialParticipant(),),
       checks=checks,
       signals=signals,
   )

The fields, all optional but the name.

``setting_changed``, ``settings_reloaded``
   Receivers that act on a settings change, such as rebuilding the routers, and never a receiver that only drops a memo, see :ref:`internals-process-state`.
``connects``
   Pairs of any other signal and its receiver, such as ``user_logged_in``.
``ports``
   ``Bind(slot, impl)`` pairs, each binding a ``next.ports`` slot.
``startup``
   Calls run once the ports are bound and before any user module is imported.
``declarations``
   The ``page.*`` declarations the area describes, see :doc:`/content/howto/add-a-page-declaration`.
``participants``
   The area's lifecycle participants.
``providers``
   What the area adds to every rendered page through the static manager.
``checks``, ``signals``
   The area's checks module, which joins ``NextFrameworkConfig.check_modules``, and its signals module, whose every signal ``next.signals`` re-exports.

The order of ``MANIFESTS`` is significant.
A signal reaches the receivers of an earlier area first, so ``conf`` comes first, the flat root modules second with the failure-log reset at their head, and the app's own reinstalls last.
Participants register in the same order, so the order of the protocol and feature areas is the order their blocks open in.
``next.signals`` stays a static list of re-exports, and ``tests/apps/test_areas.py`` fails once it and the manifests disagree.

.. _internals-adding-an-area-participant:

Taking part in the page request
-------------------------------

A feature that acts on a page request ships a participant in ``lifecycle.py``, an object with a ``name`` and three methods ``next.lifecycle.Participant`` describes.

``claim()``
   Returns a callable that tells a partial request of a kind the feature serves, or ``None``.
``gate(kind)``
   Returns a ``Gate`` for a request of ``kind`` before its page is read, or ``None``.
``share(facts)``
   Returns the ``Share`` of points the feature takes in a request ``facts`` describes, or ``None``.

``gate`` runs once per kind and ``share`` once per page, kind, and method, so neither costs a request anything.
``share`` reads the ``PageFacts`` the plan analysed, whether the render may defer, may meet an error boundary, renders straight, marks its links, and spells a layer link, and returns only the points the page needs.
A share that returns ``None`` for a plain page keeps that page on the bare render, so a feature has no reason to check its own applicability on the request.
:ref:`internals-request-lifecycle-participants` lists the steps and the participants of the framework.

Legitimate deviations
---------------------

Two existing areas depart from the recurring set on purpose.

- ``next/pages/`` ships no ``backends.py`` at all.
  Page loading offers no settings-driven choice between implementations, so a backend contract would guard nothing.
- ``next/partial/`` configures exactly one protocol backend.
  ``PARTIAL_BACKENDS`` activates the first entry, and a second entry is reported by the ``next.W071`` warning instead of joining a pool of interchangeable backends.

Choosing a layer
----------------

Every area sits in one layer of the stack :ref:`internals-subsystem-dependencies` describes, and ``tests/test_layers.py`` fails until the new area is placed in its ``LAYERS`` table.

- A flat module every other area reads, such as a vocabulary or a cache helper, joins the foundation.
- An area the page render needs to work at all, with no feature switched on, joins the kernel at the tier above the kernel areas it imports.
- An area that adds a capability on top of the page render joins the features and imports neither another feature nor the composition layer.
  A dependency on another feature is a design decision, so the pair enters ``FEATURE_PAIRS`` in the same test.
- The kernel learns about a feature only through a ``next.ports`` slot the feature's manifest binds, never through an import.
- The page view in ``next.views.pages`` imports no feature and no protocol module beyond the partial headers and the stream ownership block, and ``tests/test_layers.py`` keeps it so.
  A feature reaches the view through its participant.

Starting point
--------------

Copy the closest existing area in spirit rather than an abstract template.
The module map in :doc:`overview` shows what every area currently ships, and the contract above lists the parts a copy keeps.

See also
--------

.. seealso::

   :doc:`contributing-notes` for the conventions the framework code follows.
   :doc:`overview` for the module map of every existing area.

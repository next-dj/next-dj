.. _ref:

API reference
=============

Module by module reference for the next.dj public API.
Each page lists the public surface plus configuration and signal entries that belong to the subsystem.

.. rubric:: Top-level API

The ``next`` package itself exports five curated names, the ones that page modules, component modules, and form handlers use most often.
Import them from the package root with ``from next import Depends, action, component, context, page``.

``next.page``
   The page manager that owns template registration and rendering, re-exported from :doc:`pages`.

``next.context``
   The page context decorator, re-exported from :doc:`pages` and documented in :doc:`decorators`.

``next.component``
   The component context manager behind ``@component.context``, re-exported from :doc:`components`.

``next.action``
   The form action decorator, re-exported from :doc:`forms`.

``next.Depends``
   The dependency marker for injected values, re-exported from :doc:`deps`.

``next.VERSION``
   The framework version string, the sixth and only eager name in ``next.__all__``.

Each of the five resolves from its owning subpackage on first attribute access, so ``import next`` on its own pulls in no Django module.
Anything outside the five stays a deep import, such as ``from next.urls import DUrl`` or ``from next.forms import Form``.
The subsystem pages below are the reference for that wider surface.

.. warning::

   ``next.page`` and ``next.pages`` differ by one letter and name different things.
   ``next.page`` is the manager object re-exported at the package root, while ``next.pages`` is the subpackage that owns it together with the rest of the pages API.

.. note::

   The curated ``context`` is the decorator, not the ``Context`` and ``ContextResult`` classes that share the word.
   Those classes belong to the same subpackage and stay behind ``from next.pages import Context, ContextResult``.

The laziness covers the package root, not the subsystems it fronts.
Reading ``Depends`` imports ``next.deps``, which pulls in a small set of modules from ``django.dispatch`` and ``django.utils``.
Reading ``page``, ``context``, ``component``, or ``action`` loads a much larger part of Django.

.. rubric:: Subsystems

:doc:`pages`
   ``next.pages`` for page modules, layouts, context, and template loaders.

:doc:`components`
   ``next.components`` for component discovery and rendering.

:doc:`urls`
   ``next.urls`` for the file router, URL reverse helpers, and dispatcher.

:doc:`forms`
   ``next.forms`` for form actions, dispatch, formsets, and frozen specs.

:doc:`static`
   ``next.static`` for the static collector, asset kinds, and backends.

:doc:`partial`
   ``next.partial`` for the patch builder, zones, SSE streams, and the protocol backend.

:doc:`deps`
   ``next.deps`` for the dependency resolver and providers.

:doc:`conf`
   ``next.conf`` for settings loading and the ``extend_default_backend`` helper.

:doc:`server`
   ``next.server`` for the autoreload watcher.

:doc:`signals`
   ``next.signals`` aggregator that re-exports every framework signal.

:doc:`testing`
   ``next.testing`` for the test client, signal recorder, and isolation helpers.

:doc:`apps`
   ``next.apps`` for the Django application configuration.

:doc:`utils`
   ``next.utils`` for small helpers that the framework uses internally.

:doc:`backends`
   ``next.backends`` for the shared loading of settings-driven backend families.

.. rubric:: Configuration

:doc:`settings`
   Every ``NEXT_FRAMEWORK`` key with defaults.

:doc:`system-checks`
   Django system checks that the framework contributes.

.. rubric:: Templates and decorators

:doc:`template-tags`
   Every template tag registered by ``next.dj``.

:doc:`decorators`
   Every public decorator and dependency marker.

.. toctree::
   :hidden:
   :maxdepth: 1

   pages
   components
   urls
   forms
   static
   partial
   deps
   conf
   server
   signals
   testing
   apps
   utils
   backends
   settings
   system-checks
   template-tags
   decorators

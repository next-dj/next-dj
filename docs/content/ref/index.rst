.. _ref:

API Reference
=============

Module by module reference for the next.dj public API.
Each page lists the public surface plus configuration and signal entries that belong to the subsystem.

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

.. rubric:: Configuration

:doc:`settings`
   Every ``NEXT_FRAMEWORK`` key with defaults.

:doc:`system-checks`
   Django system checks that the framework contributes.

.. rubric:: Templates and Decorators

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
   settings
   system-checks
   template-tags
   decorators

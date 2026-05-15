.. _internals-autoreload:

Autoreload
==========

This page covers how the development server watches the filesystem and rebuilds the route set without a restart.

.. contents::
   :local:
   :depth: 2

Overview
--------

The autoreload pipeline runs only when ``runserver`` boots with autoreload enabled.
It collects watch specs from every subsystem, registers them with Django, and reacts to filesystem events.

Pipeline
--------

.. mermaid::

   flowchart TB
       Boot[runserver boots] --> Collect[iter_all_autoreload_watch_specs]
       Collect --> Specs[Watch specs]
       Specs --> WatchReady[(watch_specs_ready)]
       Specs --> Watcher[Django autoreload]
       Watcher -- file change --> Diff[Diff route set]
       Diff -- changed --> Reload[router_manager.reload]
       Reload --> RouterReloaded[(router_reloaded)]
       Diff -- code only --> InProcess[In process reset]
       Watcher -- python module change --> ProcessRestart[Django reloader restarts process]

Modules
-------

``next.server.autoreload``.
   ``NextStatReloader`` runs the Django autoreloader and decides whether to fully restart or to reset in process state.

``next.server.watcher``.
   ``iter_all_autoreload_watch_specs`` collects watch specs from every registered subsystem.

``next.server.roots``.
   Helper that resolves the page roots, components roots, and stem patterns into concrete glob specs.

``next.server.signals``.
   ``watch_specs_ready`` fires once after the collector completes.

Watch Specs
-----------

A watch spec is a tuple of root path and one or more glob patterns.
Each subsystem contributes specs.

- Pages contribute the page roots and the template loader patterns.
- Components contribute the component roots and the stem patterns.
- Static contribute the page and component roots restricted to asset extensions.
- Routes are tracked indirectly because directory changes trigger ``router_reloaded`` through the page watch specs.

The aggregator deduplicates the specs and emits ``watch_specs_ready`` with the final list.

Reload Decisions
----------------

Two kinds of changes happen.

Python module change.
   Django's reloader restarts the process.
   The framework re-imports every page and component at boot.

Filesystem only change.
   The framework calls ``router_manager.reload`` to rebuild the route set in process.
   This avoids restarting the worker on every saved HTML file.

The decision is taken by ``NextStatReloader`` and depends on the file extension and the configured page roots.

Signals
-------

The autoreload pipeline fires two signals.

- ``watch_specs_ready`` after the aggregator completes.
- ``router_reloaded`` after each in process reload.

Long lived processes subscribe to ``router_reloaded`` to refresh cached URL references.
Custom routers subscribe to ``watch_specs_ready`` to register additional patterns.

Extension Points
----------------

- Implement a custom backend that contributes its own watch specs through the subsystem watch module.
- Subscribe to ``router_reloaded`` for in process cache refresh.
- Subscribe to ``watch_specs_ready`` for diagnostic logging during development.

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the hot reload semantics.
   :doc:`/content/howto/reload-routes-from-code` for the public API.

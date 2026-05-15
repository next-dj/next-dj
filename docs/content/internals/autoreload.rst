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
       Watcher -- python module change --> ProcessRestart[Django reloader restarts process]
       Watcher -- tick --> Diff[NextStatReloader diffs route set]
       Diff -- changed --> Notify[notify_file_changed]
       Notify --> ProcessRestart

Modules
-------

``next.server.autoreload``.
   ``NextStatReloader`` extends the Django stat reloader and also restarts the process when the discovered route set changes.

``next.server.watcher``.
   ``iter_all_autoreload_watch_specs`` collects watch specs from every registered subsystem.

``next.server.roots``.
   ``get_framework_filesystem_roots_for_linking`` returns the canonical page and component directory roots for build tooling.

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

Two kinds of changes trigger a reload.

Python module change.
   Django's reloader restarts the process.
   The framework re-imports every page and component at boot.

Route set change.
   ``NextStatReloader`` diffs the discovered route set on every tick.
   A new or removed page directory calls ``notify_file_changed`` so Django restarts the process even when no watched file mtime changed.

The route set diff is taken by ``NextStatReloader`` from the configured page roots.
A custom router that builds routes from another source rebuilds them through ``router_manager.reload``, which is the public API covered in :doc:`/content/howto/reload-routes-from-code`.

Signals
-------

The autoreload pipeline fires ``watch_specs_ready`` after the aggregator completes.
A call to ``router_manager.reload`` fires ``router_reloaded`` after each in process route rebuild.

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

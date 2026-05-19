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

Startup Integration
-------------------

The pipeline is wired by ``next.apps.autoreload.install()``, which ``NextFrameworkConfig.ready`` calls at application startup.
``install`` performs two actions:

1. Replaces ``django.utils.autoreload.StatReloader`` with ``NextStatReloader``.
   The swap is idempotent: subsequent calls are no-ops if ``StatReloader`` is already a ``NextStatReloader`` subclass.
2. Connects ``_watch_next_filesystem`` to Django's ``autoreload_started`` signal so the watch specs are registered the moment the dev server starts.

.. note::

   If another library has already replaced ``autoreload.StatReloader`` with a class that is not a ``StatReloader`` subclass, the swap is skipped and a warning is logged.
   In that case the route-set diff is inactive.
   To restore the original reloader in tests, call ``next.apps.autoreload.uninstall()``.

Modules
-------

``next.apps.autoreload``.
   ``install()`` swaps ``StatReloader`` and connects the watch signal.
   ``uninstall()`` restores the previous reloader. Test suites that call ``AppConfig.ready`` multiple times use it to avoid double-patching.

``next.server.autoreload`` (distinct from ``next.apps.autoreload``, the installer above).
   ``NextStatReloader`` extends the Django stat reloader and also restarts the process when the discovered route set changes.

``next.server.watcher``.
   ``iter_all_autoreload_watch_specs`` collects watch specs from every registered subsystem.
   ``FilesystemWatchContributor`` is the Protocol a subsystem implements to contribute ``(root, glob)`` watch specs through an ``iter_watch_specs`` method.

``next.server.roots``.
   ``get_framework_filesystem_roots_for_linking`` returns the canonical page and component directory roots for build tooling.

``next.server.signals``.
   ``watch_specs_ready`` fires once after ``iter_all_autoreload_watch_specs`` finishes building the spec list.

Watch Specs
-----------

A watch spec is a tuple of a root path and one glob pattern.
``iter_default_autoreload_watch_specs`` is an internal helper of ``next.server.watcher`` that builds the built-in set.
User code calls ``iter_all_autoreload_watch_specs`` instead, which wraps the built-in set with the registered extra specs.

- Each page root contributes a ``**/page.py`` spec.
- Each page root paired with its components folder name contributes a ``**/<components>/**/component.py`` spec.
- Each extra component root from ``DEFAULT_COMPONENT_BACKENDS`` contributes a ``**/component.py`` spec.

Only Python entrypoints are watched.
``.djx`` templates and co-located assets are deliberately omitted from the specs.

``iter_all_autoreload_watch_specs`` appends the specs registered through ``register_autoreload_watch_spec``.
It deduplicates the combined list by resolved path and glob, then emits ``watch_specs_ready`` with the final list.

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

A ``.djx`` edit triggers neither path.
Templates are re-read on render and their cached compilation is invalidated by source mtime inside the page and component layers.
A saved edit shows up on the next request without a process restart.

Signals
-------

The autoreload pipeline fires ``watch_specs_ready`` after the watch-spec aggregation completes.
The sender is the ``iter_all_autoreload_watch_specs`` function itself, so a receiver connected with ``sender=iter_all_autoreload_watch_specs`` fires only for that aggregation.

Custom routers subscribe to ``watch_specs_ready`` to register additional patterns.

Extension Points
----------------

- Implement a custom backend that contributes its own watch specs through the subsystem watch module.
- Subscribe to ``router_reloaded`` for in process cache refresh.
- Subscribe to ``watch_specs_ready`` for diagnostic logging during development.

Registering extra watch directories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Packages that generate templates or routes outside the usual page trees can register additional ``(path, glob)`` pairs without forking the framework.

Call ``register_autoreload_watch_spec`` from ``next.server`` inside ``AppConfig.ready``.

.. code-block:: python
   :caption: myapp/apps.py

   from pathlib import Path
   from django.apps import AppConfig
   from next.server import register_autoreload_watch_spec

   class MyAppConfig(AppConfig):
       name = "myapp"

       def ready(self) -> None:
           register_autoreload_watch_spec(Path("/var/cache/myapp/templates"), "**/*.djx")

Pairs merge into the list returned by ``iter_all_autoreload_watch_specs`` and receive the same deduplication pass as built-in specs.
Subscribe to ``watch_specs_ready`` if you need to assert on the effective list during development.

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the hot reload semantics.
   :doc:`/content/howto/reload-routes-from-code` for the public API.
   :doc:`/content/ref/apps` for ``next.apps.autoreload.install`` and startup wiring.

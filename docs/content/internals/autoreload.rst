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

Startup integration
-------------------

The pipeline is wired by ``next.apps.autoreload.install()``, which ``NextFrameworkConfig.ready`` calls at application startup.
``install`` performs two actions:

#. Replaces ``django.utils.autoreload.StatReloader`` with ``NextStatReloader``.
   The swap is idempotent.
   Subsequent calls are no-ops when ``StatReloader`` is already a ``NextStatReloader`` subclass.
#. Connects ``_watch_next_filesystem`` to Django's ``autoreload_started`` signal so the watch specs are registered the moment the dev server starts.

.. note::

   If another library has already replaced ``autoreload.StatReloader`` with a class that is not a ``StatReloader`` subclass, the swap is skipped and a warning is logged.
   In that case the route-set diff is inactive.
   To restore the original reloader in tests, call ``next.apps.autoreload.uninstall()``.

Modules
-------

Installer
~~~~~~~~~

``next.apps.autoreload``.
   ``install()`` swaps ``StatReloader`` and connects the watch signal.
   ``uninstall()`` restores the previous reloader.
   Test suites that patched the reloader through ``AppConfig.ready`` call it to put the original class back.
   Repeated ``ready`` calls need no such cleanup because ``install()`` itself is idempotent.

Runtime
~~~~~~~

``next.server.autoreload``.
   ``NextStatReloader`` extends the Django stat reloader and also restarts the process when the discovered route set changes.

``next.server.watcher``.
   ``iter_all_autoreload_watch_specs`` returns the deduplicated list of built-in specs plus pairs registered through ``register_autoreload_watch_spec``.
   ``FilesystemWatchContributor`` is a runtime-checkable protocol exported for type annotations only and is not iterated at runtime.

``next.server.roots``.
   ``get_framework_filesystem_roots_for_linking`` returns the canonical page and component directory roots for build tooling.

``next.server.signals``.
   ``watch_specs_ready`` fires on every call to ``iter_all_autoreload_watch_specs`` after the spec list is built.

Watch specs
-----------

A watch spec is a tuple of a root path and one glob pattern.
``_iter_default_autoreload_watch_specs`` is an internal helper of ``next.server.watcher`` that builds the built-in set.
User code calls ``iter_all_autoreload_watch_specs`` instead, which wraps the built-in set with the registered extra specs.

The page roots are the trees every configured router reports through ``page_roots``, the trees it serves URLs from.
A tree no router routes is not watched, because no edit under it can change a response.
A backend that does not implement ``page_roots`` reports no tree and is therefore not watched at all.
The system checks walk exactly that set and no tree beyond it.
A ``pages`` directory beside the working directory that no router routes is neither watched nor walked, and ``next.W002`` names it instead.

Every read of a router here is guarded, and the guard drops a value of the wrong type instead of passing it on.
``runserver`` boots, ``collectstatic`` runs, and the staticfiles finder answers even when a backend raises from ``page_roots`` or ``components_folder_name``, and equally when it answers the wrong shape.
The failure costs that backend its trees and nothing else.
It is logged once per backend and subject rather than per tick, and again after a settings reload.

- Each page root contributes a ``**/page.py`` spec.
- Each page root paired with the name its router returns from ``components_folder_name`` contributes a ``**/<components-folder>/**/component.py`` spec, ``_components`` by default.
  A router that returns ``None`` there contributes no component spec.
- Each extra component root from ``COMPONENT_BACKENDS`` contributes a ``**/component.py`` spec.

Only Python entrypoints are watched.
``.djx`` templates and co-located assets are deliberately omitted from the specs.

``iter_all_autoreload_watch_specs`` appends the specs registered through ``register_autoreload_watch_spec``.
It deduplicates the combined list by resolved path and glob, then emits ``watch_specs_ready`` with the final list.

Reload decisions
----------------

Two kinds of changes trigger a reload.

Python module change.
   Django's reloader restarts the process.
   The framework re-imports every page and component at boot.

Route set change.
   ``NextStatReloader`` diffs the discovered route set on every tick.
   A per-tree signature of directory mtimes and directory count gates the rescan, so an unchanged tree reuses the cached route set.
   A new or removed page directory calls ``notify_file_changed`` so Django restarts the process even when no watched file mtime changed.

The route set diff is taken by ``NextStatReloader`` from the configured page roots.
A custom router that builds routes from another source rebuilds them through ``router_manager.reload``, which is the public API covered in :doc:`/content/howto/reload-routes-from-code`.

A ``.djx`` edit triggers neither path.
Templates are re-read on render and their cached compilation is invalidated by source mtime inside the page and component layers.
A saved edit shows up on the next request without a process restart.

Signals
-------

The autoreload pipeline fires ``watch_specs_ready`` on every call to ``iter_all_autoreload_watch_specs`` after the watch-spec aggregation completes.
The sender is the ``iter_all_autoreload_watch_specs`` function itself, so a receiver connected with ``sender=iter_all_autoreload_watch_specs`` fires only for that aggregation.

Receivers subscribe to ``watch_specs_ready`` to log or assert on the resolved spec set.

Extension points
----------------

- Register extra ``(path, glob)`` pairs from ``AppConfig.ready`` through ``register_autoreload_watch_spec``.
- Subscribe to ``router_reloaded`` for in process cache refresh.
- Subscribe to ``watch_specs_ready`` for diagnostic logging during development.

Registering extra watch directories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Packages that generate templates or routes outside the usual page trees register additional ``(path, glob)`` pairs by calling ``register_autoreload_watch_spec`` inside ``AppConfig.ready``, as the worked example in :ref:`topics-extending` shows.
Registered pairs merge into the list returned by ``iter_all_autoreload_watch_specs`` and receive the same deduplication pass as built-in specs.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the hot reload semantics.
   :doc:`/content/howto/reload-routes-from-code` for the public API.
   :doc:`/content/ref/apps` for ``next.apps.autoreload.install`` and startup wiring.

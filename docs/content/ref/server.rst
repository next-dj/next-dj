.. _ref-server:

Server Reference
================

Module Summary
--------------

``next.server`` exposes the autoreload watcher, the watch-spec helpers, and the filesystem roots used by the development server.
The Django wiring that activates them lives in ``next.apps`` (see :doc:`apps`).

Public API
----------

Autoreload
~~~~~~~~~~

``NextStatReloader`` subclasses Django's ``StatReloader``. In addition to watching ``.py`` mtimes, it recomputes the discovered route set on every tick and triggers a reload when pages appear or disappear from the routing tree, even when no file mtime changed. ``.djx`` templates are not watched.
They are re-read on render with mtime-based invalidation.

.. automodule:: next.server.autoreload
   :members:

Watcher
~~~~~~~

``FilesystemWatchContributor`` is a runtime-checkable protocol for objects that yield ``(root, glob)`` pairs through ``iter_watch_specs``. Each pair is a filesystem root and a glob pattern relative to that root, both consumed by ``StatReloader.watch_dir``. The watcher collects contributor specs through ``iter_all_autoreload_watch_specs`` and feeds the deduplicated list to the reloader.

``register_autoreload_watch_spec(path, glob)`` registers one extra directory and glob pair with the watcher. Call it from your own ``AppConfig.ready`` to have additional trees watched without changing the ``next`` package. The built-in specs for pages and filesystem components are derived from ``NEXT_FRAMEWORK`` and need no registration.

``iter_all_autoreload_watch_specs`` resolves the complete spec set, the built-in roots plus every registered extra spec, and emits the ``watch_specs_ready`` signal so subscribers can inspect or augment the result.

.. automodule:: next.server.watcher
   :members:

Roots
~~~~~

``get_framework_filesystem_roots_for_linking`` returns the sorted unique roots derived from page trees and component ``DIRS``. Each root is resolved to an absolute path. Tooling that needs to symlink or scan those directories reads them from here instead of recomputing paths.

.. automodule:: next.server.roots
   :members:

Signals
-------

``watch_specs_ready`` fires after the reloader resolves the full watch-spec list.
The sender is the ``iter_all_autoreload_watch_specs`` function.
The single payload argument is ``specs``, the deduplicated ``(path, glob)`` list passed to the watcher.

See :doc:`signals` for the signal index.

See Also
--------

.. seealso::

   :doc:`/content/internals/autoreload` for the reloader internals.

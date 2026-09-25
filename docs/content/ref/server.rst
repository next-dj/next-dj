.. _ref-server:

Server reference
================

Module summary
--------------

``next.server`` exposes the autoreload watcher, the watch-spec helpers, and the filesystem roots used by the development server.
The Django wiring that activates them lives in ``next.apps`` (see :doc:`apps`).

Public API
----------

Autoreload
~~~~~~~~~~

``NextStatReloader`` subclasses Django's ``StatReloader``.
In addition to watching ``.py`` mtimes, it diffs the discovered route set on every tick.
A per-tree signature of directory mtimes and directory count gates the rescan, so an unchanged tree reuses the cached route set.
A reload triggers when pages appear or disappear from the routing tree, even when no file mtime changed.
``.djx`` templates are not watched.
They are re-read on render with mtime-based invalidation under ``DEBUG``.

.. automodule:: next.server.autoreload
   :members:

Watcher
~~~~~~~

``register_autoreload_watch_spec(path, glob)`` registers one extra directory and glob pair with the watcher.
Call it from your own ``AppConfig.ready`` to have additional trees watched without changing the ``next`` package.
The built-in specs for pages and filesystem components are derived from ``NEXT_FRAMEWORK`` and need no registration.
Every page root also contributes one spec per name in ``SEO_SOURCE_NAMES``, the ``sitemap.py``, ``robots.py``, and ``robots.txt`` at the top of the tree that switch the sitemap and robots routes on, see :doc:`/content/topics/seo/sitemaps`.

``iter_all_autoreload_watch_specs`` returns the deduplicated list of built-in watch specs together with every pair registered through ``register_autoreload_watch_spec``.
Each entry is a ``(path, glob)`` tuple consumed by ``StatReloader.watch_dir``.
The function emits the ``watch_specs_ready`` signal on every call so subscribers can inspect the resolved set.

.. automodule:: next.server.watcher
   :members:

Roots
~~~~~

``get_framework_filesystem_roots_for_linking`` returns the sorted unique roots of the configured page trees together with every tree the components backends report through ``watch_roots``, which for the shipped file backend are its ``DIRS`` entries.
Each root is resolved to an absolute path.
Tooling that needs to symlink or scan those directories reads them from here instead of recomputing paths.

.. automodule:: next.server.roots
   :members:

Signals
-------

``watch_specs_ready`` fires after the reloader resolves the full watch-spec list.
The sender is the ``iter_all_autoreload_watch_specs`` function.
The single payload argument is ``specs``, the deduplicated ``(path, glob)`` list passed to the watcher.

.. automodule:: next.server.signals
   :members:
   :no-index:

See :doc:`signals` for the signal index.

See also
--------

.. seealso::

   :doc:`/content/internals/autoreload` for the reloader internals.

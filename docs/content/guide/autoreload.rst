Development server and autoreload
=================================

next.dj extends Django's development server so that changes to Python entrypoints for pages and components trigger an automatic reload.
``.djx`` template files are **not** watched: they load on demand and refresh via mtime when you save (see :doc:`pages-and-templates`).
This section describes what restarts the process and what does not.

Overview
--------

When you run ``manage.py runserver``, Django uses a reloader (by default ``StatReloader``) that watches files and restarts the process when they change.
Django does not provide a reloader registry, so next.dj replaces ``django.utils.autoreload.StatReloader`` with its own subclass
in :meth:`NextFrameworkConfig.ready() <next.apps.NextFrameworkConfig.ready>`.


What is watched
---------------

* **Existing files (mtime)**
  Django's reloader watches Python modules and paths registered via ``watch_dir``.
  next.dj registers watch specs from :mod:`next.server` (see :func:`~next.server.iter_all_autoreload_watch_specs`):
  every ``**/page.py`` under configured **pages** roots,
  ``**/component.py`` under each pages root's ``COMPONENTS_DIR`` (and under ``DEFAULT_COMPONENT_BACKENDS`` ``DIRS`` for standalone component trees).
  **No** ``*.djx`` glob is registered, so saving a layout, page template, or component ``.djx`` does **not** restart the process.

* **Set of routes**
  The set of page routes (each ``page.py`` and each virtual page from ``template.djx``) is recomputed every tick.
  If the set **grows or shrinks** (new or deleted ``page.py`` or virtual page), the reloader calls ``notify_file_changed`` so the server restarts and URL patterns are rebuilt.

Implementation
--------------

* **Patch in ``ready()``**
  In :mod:`next.apps.autoreload`, ``install()`` swaps ``autoreload.StatReloader`` with :class:`next.server.NextStatReloader`.
  The swap is **idempotent** — calling ``install()`` again when the current class is already a ``NextStatReloader`` subclass is a no-op.
  The previous class is stashed in a module-level variable so tests and other packages can call ``uninstall()`` to restore it (and disconnect the watch-spec signal handler).
  If another package has already replaced ``StatReloader`` with something that is not a ``StatReloader`` subclass,
  ``install()`` logs a warning and leaves the foreign class in place rather than silently overwriting it.

* **Registration of watch dirs**
  The signal :py:data:`django.utils.autoreload.autoreload_started` is connected to a handler that calls :func:`next.server.iter_all_autoreload_watch_specs`
  and, for each ``(path, glob)`` pair, ``sender.watch_dir(path, glob)``.
  The connection is also idempotent: ``install()`` only connects the receiver once, and ``uninstall()`` disconnects it.
  Built-in specs cover pages and filesystem components.
  Third-party code can add pairs with :func:`next.server.register_autoreload_watch_spec`.

* **NextStatReloader**
  In :file:`next/server.py`, :class:`NextStatReloader` subclasses :class:`django.utils.autoreload.StatReloader`.
  Its :meth:`~NextStatReloader.tick` generator recomputes the route set and compares it to the previous tick
  (calling ``notify_file_changed`` when routes are added or removed), then delegates to the parent's tick (mtime loop and sleep).
  That route set includes virtual pages backed by ``template.djx``.
  **Creating or deleting** a file that adds or removes such a route still restarts the process so URLconf can be rebuilt.
  Pure **edits** to an existing ``.djx`` (or new ``layout.djx`` / component ``.djx`` that do not change discovered routes) do not go through this comparison.
  They are picked up via mtime when templates render.

* **Linking / tooling**
  :func:`next.server.get_framework_filesystem_roots_for_linking` returns sorted pages roots plus component ``DIRS`` roots for editors or other tools that need a single list of canonical paths.

Limitations
-----------

* Only applies when using Django's development server (e.g. ``runserver``).
  Production servers (gunicorn, uWSGI, etc.) do not use this reloader.
* Reload is process restart.
  There is no in-process hot reload of URLconf.
* Saving **changes inside** an existing ``.djx`` does not use the file watcher and does not restart the process.
  The next request reloads that template when its mtime changes (see :doc:`pages-and-templates`).
* Adding or removing a **virtual route** (for example a new or deleted standalone ``template.djx`` that the file router exposes as a URL) **does** restart the server,
  because the route set changes and URL patterns must be rebuilt
  — not because ``.djx`` is glob-watched, but because of the route comparison above.

Extension points
----------------

The server subsystem exposes two pluggable surfaces for contributing watch specs.

* ``next.server.watcher.FilesystemWatchContributor`` is the protocol used internally to collect watch specs.
  Implement it for tools that want to ship additional directories to the reloader.
* ``next.server.watcher.register_autoreload_watch_spec`` registers a single ``(path, glob)`` pair without subclassing anything.

Register a spec once at startup.

.. code-block:: python

   from pathlib import Path

   from next.server import register_autoreload_watch_spec

   register_autoreload_watch_spec(Path("/opt/myapp/content"), "**/*.md")

The signal emitted by :mod:`next.server.signals` lets external code inspect the resolved watch set.

* ``watch_specs_ready`` fires after the reloader resolves the full watch-spec list.

See :doc:`extending` for the overall extension model.

Next
----

:doc:`API Reference </content/api/reference>` — all classes and methods.

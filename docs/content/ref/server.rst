.. _ref-server:

Server reference
================

Module summary
--------------

``next.server`` exposes the autoreload watcher, the watch-spec helpers, the filesystem roots, and the ASGI development server used by ``manage.py runserver``.
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
The ``sitemap.py``, ``robots.py``, ``robots.txt``, and ``scripts.py`` at the top of a page tree carry no spec, since under ``DEBUG`` the SEO and scripts managers read a source again in-process once its mtime moves, so an edit needs no restart.

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

ASGI server
~~~~~~~~~~~

``serve_development(server, *, host, port, static, announce=None, options=None)`` builds the app that ``ASGI_APPLICATION`` names, or ``next.realtime.application`` over Django's handler, calls ``announce`` once the app is mounted, and runs it on ``server``.
``server`` is any object with the ``run(app, **options)`` method of ``ASGIServer``, such as ``UvicornServer()``, and it receives the server-neutral socket options alongside ``host`` and ``port``.
``asgi_server()`` builds the class ``NEXT_FRAMEWORK['DEV_SERVER']`` names, ``UvicornServer`` by default, and answers ``None`` when that default finds no uvicorn.
``server_class(path)`` returns the class a path names and raises :exc:`~django.core.exceptions.ImproperlyConfigured` for a path that does not import or names no ``ASGIServer`` class.
``server_options()`` returns the server-neutral ``max_frame_bytes`` and ``ping_interval`` options the realtime socket needs, and the ``options`` argument overrides them.
``UvicornServer`` maps them onto uvicorn's ``ws_max_size`` and ``ws_ping_interval``, and another ``ASGIServer`` maps them onto its own server or ignores them.
A CLI of a project uses these to serve the same app as ``manage.py runserver`` without a Django command.

.. automodule:: next.server.asgi
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

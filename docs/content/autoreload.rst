Development server and autoreload
=================================

next.dj extends Django's development server so that changes to pages, layouts, and templates trigger an automatic reload. This section describes how it works and what is watched.

Overview
--------

When you run ``manage.py runserver``, Django uses a reloader (by default ``StatReloader``) that watches files and restarts the process when they change. Django does not provide a reloader registry, so next.dj replaces ``django.utils.autoreload.StatReloader`` with its own subclass in :meth:`NextFrameworkConfig.ready() <next.apps.NextFrameworkConfig.ready>`.

What is watched
---------------

* **Existing files (mtime)**  
  Django's reloader already watches Python modules and any paths registered via ``watch_dir``. next.dj ensures that all **pages** directories from ``NEXT_PAGES`` are registered so that every ``**/page.py`` under them is watched. Changes to those files are detected by the standard mtime loop and trigger a reload.

* **Set of routes**  
  The set of page routes (each ``page.py`` and each virtual page from ``template.djx``) is recomputed every tick. If the set **grows or shrinks** (new or deleted ``page.py`` or virtual page), the reloader calls ``notify_file_changed`` so the server restarts and URL patterns are rebuilt.

* **Set of layout.djx**  
  The set of ``layout.djx`` paths under NEXT_PAGES dirs is compared each tick. A reload is triggered only when this set changes **and** at least one changed layout directory contains (or is a parent of) a current route. So adding a layout in a directory that no page uses does not restart the server.

* **Set of template.djx**  
  The set of ``template.djx`` paths is compared each tick. If it changes (new or removed template), the reloader notifies and the server restarts.

Implementation
--------------

* **Patch in ``ready()``**  
  In :file:`next/apps.py`, ``autoreload.StatReloader`` is replaced with :class:`next.utils.NextStatReloader`. This happens at app load, before the reloader instance is created, so ``runserver`` uses the next.dj reloader.

* **Registration of pages dirs**  
  The signal :py:data:`django.utils.autoreload.autoreload_started` is connected to a handler that calls :func:`next.urls.get_pages_directories_for_watch` and, for each path, ``sender.watch_dir(path, "**/page.py")``. So Django's watcher sees every ``page.py`` under your NEXT_PAGES directories.

* **NextStatReloader**  
  In :file:`next/utils.py`, :class:`NextStatReloader` subclasses :class:`django.utils.autoreload.StatReloader`. Its :meth:`~NextStatReloader.tick` generator runs one full "next" check per tick (recompute route/layout/template sets and compare to previous; call ``notify_file_changed`` on any change), then delegates to the parent's tick (mtime loop and sleep). The parent's tick uses the same watched files as before; next.dj only adds logic for **set** changes (add/remove of files), not for mtime of individual files.

Limitations
-----------

* Only applies when using Django's development server (e.g. ``runserver``). Production servers (gunicorn, uWSGI, etc.) do not use this reloader.
* Reload is process restart; there is no in-process hot reload of URLconf or templates.
* Layout/template **content** changes (without adding/removing files) are not handled by the reloader: they are picked up at render time if you use lazy loading and mtime-based invalidation for templates.

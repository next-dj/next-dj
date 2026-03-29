.. _api-reference:

API Reference
=============

This page documents all public classes, functions, and configuration for next.dj. In the guide we sometimes link to specific items (e.g. :class:`next.deps.Deps`, :meth:`next.pages.Page.get_context`) for details.

Pages (next.pages)
------------------

Page rendering, template loaders, context and layout management.

.. automodule:: next.pages
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: resolver

Components (next.components)
----------------------------

Scoped reusable template fragments, backends, and rendering helpers. See :doc:`/content/guide/components` for usage, settings, and examples.

.. automodule:: next.components
   :members:
   :undoc-members:
   :show-inheritance:

URLs and routing (next.urls)
----------------------------

File-based URL pattern generation and router backends.

.. automodule:: next.urls
   :members:
   :undoc-members:
   :show-inheritance:

Utils (next.utils)
------------------

Development server reloader that watches route/layout/template set changes. See :doc:`/content/guide/autoreload` for how autoreload works.

.. automodule:: next.utils
   :members:
   :undoc-members:
   :show-inheritance:

Checks (next.checks)
--------------------

Django system checks for next.dj configuration.

.. automodule:: next.checks
   :members:
   :undoc-members:
   :show-inheritance:

.. _dependency-injection-api:

Dependency injection (next.deps)
--------------------------------

Dependency resolution and built-in providers for request, URL kwargs, and forms. Used in page context and form handlers (see :doc:`/content/guide/dependency-injection`).

.. automodule:: next.deps
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: resolver

Configuration
-------------

NEXT_FRAMEWORK
~~~~~~~~~~~~~~

Single dictionary in Django settings. Top-level keys (each optional beyond defaults):

* ``DEFAULT_PAGE_ROUTERS`` — list of file-router backend dicts (``BACKEND``, ``PAGES_DIR``, ``APP_DIRS``, ``OPTIONS``, …). See :doc:`/content/guide/file-router`.
* ``URL_NAME_TEMPLATE`` — format string for URL pattern names (default ``page_{name}``).
* ``DEFAULT_COMPONENT_BACKENDS`` — list of component backend dicts (``BACKEND``, ``APP_DIRS``, ``OPTIONS``, …). See :doc:`/content/guide/components`.

.. code-block:: python

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_ROUTERS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": True,
               "OPTIONS": {
                   "context_processors": [
                       "myapp.context_processors.global_context",
                   ],
               },
           },
       ],
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "APP_DIRS": True,
               "OPTIONS": {
                   "COMPONENTS_DIR": "_components",
                   "PAGES_DIR": "pages",
                   "COMPONENTS_DIRS": [str(BASE_DIR / "root_components")],
               },
           },
       ],
   }

Implementation: :mod:`next.conf`.

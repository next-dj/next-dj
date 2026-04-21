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

Static assets (next.static)
---------------------------

Co-located CSS/JS discovery, staticfiles integration via
``next.static.NextStaticFilesFinder``, and the ``{% collect_styles %}`` /
``{% collect_scripts %}`` / ``{% use_style %}`` / ``{% use_script %}``
template tags. See :doc:`/content/guide/static-assets`
for usage, settings, and examples. ``next.static`` re-exports the public
surface of its submodules; :doc:`/content/api/static` documents each
submodule individually.

.. toctree::

   static

URLs and routing (next.urls)
----------------------------

File-based URL pattern generation and router backends.

.. automodule:: next.urls
   :members:
   :undoc-members:
   :show-inheritance:

Server (next.server)
--------------------

``runserver`` integration: :class:`~next.server.NextStatReloader` watches the route set and Python entrypoints. ``.djx`` files are not watched. See :doc:`/content/guide/autoreload`.

.. automodule:: next.server
   :members:
   :undoc-members:
   :show-inheritance:

Utils (next.utils)
------------------

Small reusable helpers not tied to the HTTP server or routing. The module is intentionally minimal. Add shared utilities here as needed.

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

* ``DEFAULT_PAGE_BACKENDS`` — list of file-router backend dicts (``BACKEND``, ``PAGES_DIR``, ``APP_DIRS``, ``DIRS``, ``OPTIONS``, …). The file router’s skip-folder name always comes from ``DEFAULT_COMPONENT_BACKENDS`` (``COMPONENTS_DIR`` on the first entry). See :doc:`/content/guide/file-router`.
* ``URL_NAME_TEMPLATE`` — format string for URL pattern names (default ``page_{name}``).
* ``DEFAULT_COMPONENT_BACKENDS`` — list of component backend dicts (``BACKEND``, ``DIRS``, ``COMPONENTS_DIR``, …). See :doc:`/content/guide/components`.
* ``DEFAULT_STATIC_BACKENDS`` — list of static backend dicts (``BACKEND``, ``OPTIONS``). The default backend resolves co-located CSS/JS through Django ``staticfiles_storage``. See :doc:`/content/guide/static-assets`.

.. code-block:: python

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": True,
               "DIRS": [],
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
               "DIRS": [str(BASE_DIR / "root_components")],
               "COMPONENTS_DIR": "_components",
           },
       ],
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {},
           },
       ],
   }

Implementation: :mod:`next.conf`.

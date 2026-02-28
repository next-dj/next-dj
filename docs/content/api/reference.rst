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

NEXT_PAGES
~~~~~~~~~~

Configure backends and options in Django settings:

.. code-block:: python

   NEXT_PAGES = [
       {
           'BACKEND': 'next.urls.FileRouterBackend',
           'APP_DIRS': True,
           'OPTIONS': {
               'context_processors': [
                   'myapp.context_processors.global_context',
               ],
           },
       },
   ]

FileRouterBackend options: ``APP_DIRS`` (bool), ``context_processors`` (list of dotted paths).

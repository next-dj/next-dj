.. _deployment-settings:

Production Settings
===================

This page lists recommended values for ``NEXT_FRAMEWORK`` in production.
The values differ from the development defaults to prefer correctness, predictability, and observability over development speed.

.. contents::
   :local:
   :depth: 2

Strict Context
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "STRICT_CONTEXT": True,
   }

Production renders fail fast on undefined context keys.
A typo or a missing context function surfaces immediately instead of silently rendering an empty string.

Eager Component Loading
-----------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "LAZY_COMPONENT_MODULES": False,
   }

Every ``component.py`` imports at startup.
Action registrations, context registrations, and component context functions are visible from the first request.

Static Backend
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {"BACKEND": "notes.backends.CdnBackend", "OPTIONS": {}},
       ]
   }

Point at a CDN aware backend in production.
The default ``StaticFilesBackend`` is appropriate for single host deployments where the same process serves both HTML and static files.

JS Context Serializer
---------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "next.static.PydanticJsContextSerializer",
   }

Set the serializer when context values include types beyond the standard JSON set.
``PydanticJsContextSerializer`` handles Pydantic models and falls back to the Django JSON encoder for plain values.

Page Backends With Context Processors
-------------------------------------

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": extend_default_backend(
           "DEFAULT_PAGE_BACKENDS",
           OPTIONS={"context_processors": [
               "notes.context_processors.csp_nonce",
               "notes.context_processors.tenant",
           ]},
       )
   }

Use ``extend_default_backend`` to patch the default page backend entry with production context processors.
The ``OPTIONS`` dict is merged, so the other default keys survive.

Form Action Backend
-------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.RateLimitedFormActionBackend"},
       ]
   }

Register a custom backend that subclasses ``RegistryFormActionBackend`` and rate limits dispatch for endpoints exposed to anonymous users.
See :doc:`/content/howto/write-a-form-action-backend`.

Template Loaders
----------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "TEMPLATE_LOADERS": [
           "next.pages.loaders.DjxTemplateLoader",
       ]
   }

Keep the loader list short.
Each loader runs in order for every page resolution, and a long list adds startup cost.

Full Example
------------

.. code-block:: python
   :caption: config/settings.py

   from pathlib import Path


   BASE_DIR = Path(__file__).resolve().parent.parent


   NEXT_FRAMEWORK = {
       "STRICT_CONTEXT": True,
       "LAZY_COMPONENT_MODULES": False,
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "DIRS": [str(BASE_DIR / "chrome")],
               "PAGES_DIR": "routes",
               "OPTIONS": {"context_processors": [
                   "notes.context_processors.tenant",
               ]},
           }
       ],
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "COMPONENTS_DIR": "_components",
           }
       ],
       "DEFAULT_STATIC_BACKENDS": [
           {"BACKEND": "notes.backends.CdnBackend", "OPTIONS": {}},
       ],
       "DEFAULT_FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.RateLimitedFormActionBackend"},
       ],
       "JS_CONTEXT_SERIALIZER": "next.static.PydanticJsContextSerializer",
   }

See Also
--------

.. seealso::

   :doc:`checklist` for the full pre-flight list.
   :doc:`/content/ref/settings` for every available key.

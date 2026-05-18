.. _topics-static-backends:

Static Backends
===============

A static backend resolves an asset file to a public URL and renders the link, script, and module tags.
The framework ships ``StaticFilesBackend``.
A custom backend rewrites URLs, adds attributes, or points at a CDN.

.. contents::
   :local:
   :depth: 2

Backend Contract
----------------

A backend subclasses ``next.static.StaticBackend``, an abstract base class.
The constructor receives the full backend entry from ``DEFAULT_STATIC_BACKENDS``, a dict of the shape ``{"BACKEND": "...", "OPTIONS": {...}}``.

The only abstract method is ``register_file``.

.. code-block:: python
   :caption: register_file contract

   def register_file(
       self,
       source_path: Path,
       logical_name: str,
       kind: str,
   ) -> str:
       """Return the public URL for a co-located asset file."""

``source_path`` is the absolute path to the file.
``logical_name`` is the path without an extension, such as ``components/card``.
``kind`` is a registered asset kind.
The method raises ``RuntimeError`` when the asset cannot be resolved.

Renderer methods are not abstract.
A backend adds the renderer methods that its registered kinds reference, see :doc:`asset-kinds`.

The Default Backend
-------------------

``StaticFilesBackend`` resolves assets through Django staticfiles.
Assets live in the ``next/`` staticfiles namespace, so manifest storage, S3 storage, and CDN settings apply automatically.

The backend ships three renderer methods.

- ``render_link_tag`` for the ``css`` kind.
- ``render_script_tag`` for the ``js`` kind.
- ``render_module_tag`` for the ``module`` kind.

Each method takes the URL and an optional ``request`` keyword.
The default backend ignores ``request``.

Configuring the Default Backend
--------------------------------

``StaticFilesBackend`` reads three option keys for the rendered tag markup.

``css_tag``.
   Format string for ``<link>`` tags.
   Must contain the ``{url}`` placeholder.

``js_tag``.
   Format string for ``<script>`` tags.

``module_tag``.
   Format string for ``<script type="module">`` tags.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "css_tag": '<link rel="stylesheet" href="{url}" crossorigin>',
                   "js_tag": '<script src="{url}" defer></script>',
                   "module_tag": '<script type="module" src="{url}" crossorigin></script>',
               },
           }
       ]
   }

Bake attributes such as ``crossorigin``, ``defer``, or ``integrity`` directly into the format string.
This covers most customisation without a subclass.

Dedup and JS Context Options
----------------------------

The first backend ``OPTIONS`` also carries two pipeline level keys.

``DEDUP_STRATEGY``.
   Dotted path to a dedup strategy, see :doc:`deduplication`.

``JS_CONTEXT_POLICY``.
   Dotted path to a JS context conflict policy, see :doc:`js-context`.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "DEDUP_STRATEGY": "next.static.collector.HashContentDedup",
                   "JS_CONTEXT_POLICY": "next.static.collector.DeepMergePolicy",
               },
           }
       ]
   }

Writing a Custom Backend
------------------------

Subclass ``StaticFilesBackend`` to keep the staticfiles resolution and change only the rendered markup.

.. code-block:: python
   :caption: notes/backends.py

   import base64
   import hashlib
   from pathlib import Path

   from next.static import StaticFilesBackend


   class SriBackend(StaticFilesBackend):
       def render_link_tag(self, url, *, request=None) -> str:
           return f'<link rel="stylesheet" href="{url}" crossorigin>'

       def render_script_tag(self, url, *, request=None) -> str:
           return f'<script src="{url}" crossorigin></script>'

Subclass the abstract ``StaticBackend`` directly only when the project resolves assets from a source other than Django staticfiles, such as a build manifest.

Registering a Backend
---------------------

List the dotted path of the backend in ``DEFAULT_STATIC_BACKENDS``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "notes.backends.SriBackend",
               "OPTIONS": {},
           }
       ]
   }

The ``StaticsFactory`` builds the backend instance from the config dict and emits the ``backend_loaded`` signal.

Request Aware Output
--------------------

Every renderer method accepts a ``request`` keyword.
A custom backend can vary its output per request, for example to pick a CDN host based on the tenant.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend


   class TenantPrefixBackend(StaticFilesBackend):
       def render_link_tag(self, url, *, request=None) -> str:
           prefix = getattr(getattr(request, "tenant", None), "cdn", "")
           return f'<link rel="stylesheet" href="{prefix}{url}">'

The manager passes the current request to every renderer call.
See ``examples/multi-tenant`` for a worked tenant prefix backend.

Signals
-------

The ``backend_loaded`` signal fires once per backend when the factory builds it.
The payload carries ``sender`` as the backend class, ``config`` as the config dict, and ``instance`` as the backend instance.

System Checks
-------------

The static checks validate the backend configuration at startup.
They use the codes ``next.E036``, ``next.E037``, ``next.E038``, ``next.W030``, ``next.W031``, and ``next.W042``.
Run ``uv run python manage.py check`` after editing the backend list.

The ``next.W031`` check validates the ``css_tag`` and ``js_tag`` templates.
The ``module_tag`` template is not checked, so verify it contains ``{url}`` yourself.

Common Patterns
---------------

Cache Busting
~~~~~~~~~~~~~

Use the default backend with ``ManifestStaticFilesStorage``.
The manifest filename changes when the content changes, which invalidates browser caches.

Subresource Integrity
~~~~~~~~~~~~~~~~~~~~~

Subclass ``StaticFilesBackend`` and override ``render_link_tag`` and ``render_script_tag`` to add an ``integrity`` attribute.

Per Tenant CDN
~~~~~~~~~~~~~~

Use a request aware backend that reads the tenant from the request and chooses a CDN host.

See Also
--------

.. seealso::

   :doc:`asset-kinds` for renderer method selection.
   :doc:`deduplication` for the dedup strategy.
   :doc:`js-context` for the JS context policy.
   :doc:`/content/howto/write-a-static-backend` for a recipe.

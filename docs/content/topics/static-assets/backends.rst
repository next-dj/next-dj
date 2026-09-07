.. _topics-static-backends:

Static backends
===============

A static backend resolves an asset file to a public URL and renders the link, script, and module tags.
The framework ships ``StaticFilesBackend``.
A custom backend rewrites URLs, adds attributes, or points at a CDN.

.. contents::
   :local:
   :depth: 2

Backend contract
----------------

A backend subclasses ``next.static.StaticBackend``, an abstract base class.
The constructor receives the full backend entry from ``STATIC_BACKENDS``, a dict of the shape ``{"BACKEND": "...", "OPTIONS": {...}}``.

The only abstract method is ``register_file``.

.. code-block:: python
   :caption: next/static/backends.py

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

Discovery catches ``OSError`` and ``ValueError`` from ``register_file`` and logs a warning, dropping that one asset.
Any other exception, including ``RuntimeError``, propagates and aborts the render.
The bundled ``StaticFilesBackend`` raises ``RuntimeError`` when an asset is missing from the Django staticfiles manifest, so a stale manifest aborts the render rather than dropping the asset.
A custom backend that wants a soft fail for an unresolvable asset should raise ``ValueError``.

Renderer methods are not abstract.
A backend adds the renderer methods that its registered kinds reference, see :doc:`asset-kinds`.

``asset_url`` is concrete on the base class and returns the URL unchanged.

.. code-block:: python
   :caption: next/static/backends.py

   def asset_url(
       self,
       url: str,
       *,
       request: HttpRequest | None = None,
   ) -> str:
       """Return the public URL of an already-resolved asset for this render."""

Every URL the pipeline renders passes through it, including the ``next.min.js`` runtime bundle and its preload hint, which the framework builds rather than a renderer method.
Override ``asset_url`` when the URL must change, override the renderer methods when the markup must change.

The default backend
-------------------

``StaticFilesBackend`` resolves assets through Django staticfiles.
Assets live in the ``next/`` staticfiles namespace, so manifest storage, S3 storage, and CDN settings apply automatically.

.. note::

   ``StaticFilesBackend`` caches resolved URLs per ``(logical_name, suffix)`` pair for the lifetime of the backend instance.
   Tests that use ``override_settings`` to swap storage backends should be aware that the cache is reset when the framework rebuilds the backend.

The backend ships three renderer methods.

- ``render_link_tag`` for the ``css`` kind.
- ``render_script_tag`` for the ``js`` kind.
- ``render_module_tag`` for the ``module`` kind.

Each method takes the URL and an optional ``request`` keyword.
The default backend ignores ``request`` in every renderer and in ``asset_url``.

Configuring the default backend
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
       "STATIC_BACKENDS": [
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

Dedup and JS context options
----------------------------

The first entry of ``STATIC_BACKENDS`` owns the pipeline-level options.
``DEDUP_STRATEGY`` and ``JS_CONTEXT_POLICY`` are read from the first backend ``OPTIONS`` only.
The same keys on a second or third backend are ignored, so place the configured values on the leading entry.

.. note::

   Tag format keys (``css_tag``, ``js_tag``, ``module_tag``) are lowercase.
   Pipeline strategy keys (``DEDUP_STRATEGY``, ``JS_CONTEXT_POLICY``) are uppercase.

``DEDUP_STRATEGY``.
   Dotted path to a dedup strategy, see :doc:`deduplication`.

``JS_CONTEXT_POLICY``.
   Dotted path to a JS context conflict policy, see :doc:`js-context`.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "DEDUP_STRATEGY": "next.static.collector.HashContentDedup",
                   "JS_CONTEXT_POLICY": "next.static.collector.DeepMergePolicy",
               },
           }
       ]
   }

Writing a custom backend
------------------------

Subclass ``StaticFilesBackend`` to keep the staticfiles resolution and change only the rendered markup.
Override ``render_link_tag``, ``render_script_tag``, and ``render_module_tag`` so ``.css``, ``.js``, and ``.mjs`` assets all carry the new attribute or host.
A renderer that is not overridden falls back to the parent output, which is why dropping ``render_module_tag`` makes ``.mjs`` assets skip the customisation.

To move the URL rather than the markup, override ``asset_url`` instead.
One override then covers all three kinds and the runtime bundle, and the tag templates configured through ``css_tag``, ``js_tag``, ``module_tag``, and ``NEXT_JS_OPTIONS`` keep applying on top of the new URL.

.. warning::

   Do not rewrite the URL inside a renderer method when the runtime bundle must move with it.
   The framework builds the ``next.min.js`` script tag and its preload hint from ``NEXT_JS_OPTIONS``, not from ``render_script_tag``, so a rewrite that lives only in a renderer leaves the bundle on the unrewritten URL.

:doc:`/content/howto/write-a-static-backend` walks through the attribute and CDN recipes.
For a complete Subresource Integrity implementation that also computes the ``integrity`` hash, see :doc:`/content/security/static-assets`.

Subclass the abstract ``StaticBackend`` directly only when the project resolves assets from a source other than Django staticfiles, such as a build manifest.

Registering a backend
---------------------

List the dotted path of the backend in ``STATIC_BACKENDS``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "STATIC_BACKENDS": [
           {
               "BACKEND": "notes.backends.SriBackend",
               "OPTIONS": {},
           }
       ]
   }

The manager builds the backend instance from the config dict through ``load_backends`` and emits the ``backend_loaded`` signal.
An entry that names a class outside the ``StaticBackend`` family, or a path that cannot be imported, is logged and skipped, and the remaining entries still load.
A backend that raises ``ImproperlyConfigured`` from its own ``__init__`` is skipped the same way.
Any other exception a constructor raises is a bug in that backend and reaches the caller.
When no entry survives, the manager seeds the built-in staticfiles backend so rendering always has one, and that seed announces itself through the same signal.

Request aware output
--------------------

``asset_url`` and every renderer method accept a ``request`` keyword.
A custom backend can vary its output per request, for example to pick a CDN host based on the tenant.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend

   class TenantPrefixBackend(StaticFilesBackend):
       def asset_url(self, url, *, request=None) -> str:
           prefix = getattr(getattr(request, "tenant", None), "cdn", "")
           return f"{prefix}{url}"

The manager passes the current request to ``asset_url`` and to every renderer call.
See the `multi-tenant example <https://github.com/next-dj/next-dj/tree/main/examples/multi-tenant>`__ for a worked tenant prefix backend.

Signals
-------

The ``backend_loaded`` signal fires once per configured backend when the manager builds it.
The payload carries ``sender`` as the backend class, ``config`` as the config dict, and ``instance`` as the backend instance.

System checks
-------------

The static checks validate the backend configuration at startup.
Run ``uv run python manage.py check`` after editing the backend list.
The full list of static check codes lives in :doc:`/content/ref/system-checks`.

The ``next.W031`` check validates the ``css_tag``, ``js_tag``, and ``module_tag`` templates.
A template that carries no ``{url}`` placeholder raises the warning, because the rendered tag would carry no asset URL.

Common patterns
---------------

Cache busting
~~~~~~~~~~~~~

Use the default backend with ``ManifestStaticFilesStorage``.
The manifest filename changes when the content changes, which invalidates browser caches.

Subresource Integrity
~~~~~~~~~~~~~~~~~~~~~

Subclass ``StaticFilesBackend`` and override ``render_link_tag`` and ``render_script_tag`` to add an ``integrity`` attribute.

Per-tenant CDN
~~~~~~~~~~~~~~

Use a request aware ``asset_url`` that reads the tenant from the request and chooses a CDN host.

See also
--------

.. seealso::

   :doc:`asset-kinds` for renderer method selection.
   :doc:`deduplication` for the dedup strategy.
   :doc:`js-context` for the JS context policy.
   :doc:`/content/howto/write-a-static-backend` for a recipe.

.. _topics-static-backends:

Static backends
===============

A static backend resolves an asset file or an authored reference to a public URL and renders the link, script, and module tags.
The framework ships ``StaticFilesBackend``.
A custom backend rewrites URLs, adds attributes, or points at a CDN.

.. contents::
   :local:
   :depth: 2

Backend contract
----------------

A backend subclasses ``next.static.StaticBackend``, an abstract base class.
The constructor receives the full backend entry from ``STATIC_BACKENDS``, a dict of the shape ``{"BACKEND": "...", "OPTIONS": {...}}``.

The read-only ``config`` property hands that entry back, and it is the supported way to read ``OPTIONS`` from inside a backend.

.. code-block:: python
   :caption: notes/backends.py

   class CdnBackend(StaticFilesBackend):
       def __init__(self, config=None) -> None:
           super().__init__(config)
           opts = dict(self.config.get("OPTIONS") or {})
           self._cdn_host = opts.get("CDN_HOST", "")

A subclass that defines its own ``__init__`` calls ``super().__init__(config)`` first, because the base stores the mapping and primes the URL memo that ``forget_urls`` clears.
:doc:`/content/howto/build-a-custom-asset-backend` reads a Vite manifest path through the same property.

The only abstract method is ``register_file``, which covers co-located files.
``resolve_url``, ``asset_url``, and ``forget_urls`` are concrete on the base class, and a backend overrides the ones whose behaviour it changes.

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
The bundled ``StaticFilesBackend`` raises ``StaticAssetNotFoundError``, a ``RuntimeError`` exported from ``next.static``, when an asset is missing from the Django staticfiles manifest, so a stale manifest aborts the render rather than dropping the asset.

The soft fail belongs to the door rather than to the backend.
A ``resolve_url`` call for a module-level ``styles`` or ``scripts`` entry is wrapped the way ``register_file`` is, so an ``OSError`` or a ``ValueError`` raised there drops that one asset with a logged warning.
A ``resolve_url`` call for a reference a template tag supplied is wrapped nowhere, so whatever it raises leaves the render, ``ValueError`` included.

Renderer methods are not abstract, and they are concrete on ``StaticFilesBackend`` rather than on the base.
A backend subclassing ``StaticFilesBackend`` inherits all three and overrides the ones whose markup it changes.
A backend subclassing ``StaticBackend`` directly supplies ``render_link_tag``, ``render_script_tag``, and ``render_module_tag`` itself, along with any further method its registered kinds name, see :doc:`asset-kinds`.

Resolving a name
~~~~~~~~~~~~~~~~

``resolve_url`` turns an authored reference into a public URL.
Every ``{% use_style %}``, ``{% use_script %}``, ``{% use_module %}``, and ``{% asset %}`` value goes through it, as does every entry of a module-level ``styles`` or ``scripts`` list.

.. code-block:: python
   :caption: next/static/backends.py

   def resolve_url(self, reference: str) -> str:
       """Turn an authored asset reference into a public URL."""

It is concrete on the base class and returns the reference unchanged, so a backend subclassing ``StaticBackend`` directly renders literal references until it overrides the method.
``StaticFilesBackend`` overrides it, resolving a staticfiles name through storage and leaving every other shape alone, see :doc:`name-resolution`.
The shape rule lives inside the method, so one override replaces both the rule and the lookup, and ``next.static.static_name`` is exported for a backend that wants to keep core's reading of a reference.
Call it and handle the ``None`` it answers for a reference that is already a URL, see :ref:`ref-static` for the full return contract.
A missing name raises ``StaticAssetNotFoundError``, the same error a co-located file missing from the manifest raises.

Rewriting a URL per request
~~~~~~~~~~~~~~~~~~~~~~~~~~~

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
A partial patch envelope carries bare URLs for the assets a zone body introduces, and those pass through the hook as well, so a zone morph reaches the client with the URLs a full-page render would have written.
Override ``resolve_url`` when a reference must be looked up elsewhere, override ``asset_url`` when a resolved URL must move for this request, and override the renderer methods when the markup must change.

Invalidating a memo
~~~~~~~~~~~~~~~~~~~

``forget_urls`` is concrete on the base class and clears the memo the base fills.
The manager calls it on every configured backend whenever ``STATIC_ROOT``, ``STATIC_URL``, or ``STORAGES`` changes, because those settings rebuild the storage the URLs were resolved against.
A backend that remembers what it resolved somewhere other than the base memo, such as a parsed build manifest, overrides this hook and drops its own state there.

The default backend
-------------------

``StaticFilesBackend`` resolves assets through Django staticfiles.
Co-located assets live in the ``next/`` staticfiles namespace and a named asset lives wherever the project put it, and manifest storage, S3 storage, and CDN settings apply to both automatically.

.. note::

   ``StaticFilesBackend`` caches what it resolved, a co-located file per ``(logical_name, suffix)`` pair and a reference per authored string, in one memo.
   A ``STATIC_ROOT``, ``STATIC_URL``, or ``STORAGES`` change drops that cache through ``forget_urls`` without rebuilding the backend, so a test that swaps storage through ``override_settings`` sees fresh URLs on the next render.

The backend ships three renderer methods.

- ``render_link_tag`` for the ``css`` kind.
- ``render_script_tag`` for the ``js`` kind.
- ``render_module_tag`` for the ``module`` kind.

Each method takes the URL and an optional ``request`` keyword.
The default backend ignores ``request`` in every renderer and in ``asset_url``.

All three escape ``str(url)`` through the standard library :func:`html.escape` before formatting it into their tag template.
A finished ``<link>`` or ``<script>`` is spliced into the document past the template engine, so the engine never gets the chance to escape what the tag carries and the escaping has to live in the renderer.
An override takes that obligation on with the method, so a renderer building its own markup runs every interpolated value through :func:`django.utils.html.escape` itself.

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
To look a reference up somewhere other than staticfiles, override ``resolve_url``, which runs once per reference rather than once per render.
A single constant host in front of the static origin is neither, and belongs in ``STATIC_URL``, see :doc:`/content/deployment/static-files`.

.. warning::

   Do not rewrite the URL inside a renderer method when the runtime bundle must move with it.
   The framework builds the ``next.min.js`` script tag and its preload hint from ``NEXT_JS_OPTIONS``, not from ``render_script_tag``, so a rewrite that lives only in a renderer leaves the bundle on the unrewritten URL.

:doc:`/content/howto/write-a-static-backend` walks through the attribute and CDN recipes.
For a complete Subresource Integrity implementation that also computes the ``integrity`` hash, see :doc:`/content/security/static-assets`.
An ``integrity`` or ``nonce`` override builds the whole tag rather than filling a template, so it carries the escaping obligation above along with the new attribute.

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

The manager builds the backend instance from the config dict through ``load_backends`` and emits the ``static_backend_loaded`` signal.
An entry that names a class outside the ``StaticBackend`` family, or a path that cannot be imported, is logged and skipped, and the remaining entries still load.
A backend that raises ``ImproperlyConfigured`` from its own ``__init__`` is skipped the same way.
Any other exception a constructor raises is a bug in that backend and reaches the caller.
When no entry survives, the manager seeds the built-in staticfiles backend so rendering always has one, and that seed announces itself through the same signal.

``StaticManager.default_backend`` is the first entry, and it is the only one the render path uses.
A later entry is built and receives ``static_backend_loaded`` and ``forget_urls``, and renders nothing.

Request-aware output
--------------------

``asset_url`` and every renderer method accept a ``request`` keyword.
The manager passes the current request to ``asset_url`` and to every renderer call, so a custom backend can vary its output per request, for example to pick a CDN host based on the tenant.
See :ref:`Tenant URL prefix <howto-static-backend-tenant-prefix>` for a worked backend, and the `multi-tenant example <https://github.com/next-dj/next-dj/tree/main/examples/multi-tenant>`__ for the same pattern in a running project.

Signals
-------

The ``static_backend_loaded`` signal fires once per configured backend when the manager builds it.
The payload carries ``sender`` as the backend class, ``config`` as the config dict, and ``instance`` as the backend instance.

System checks
-------------

The static checks validate the backend configuration at startup.
Run ``uv run python manage.py check`` after editing the backend list.
The full list of static check codes lives in :doc:`/content/ref/system-checks`.

The ``next.W031`` check validates the ``css_tag``, ``js_tag``, and ``module_tag`` templates.
A template that carries no ``{url}`` placeholder raises the warning, because the rendered tag would carry no asset URL.

See also
--------

.. seealso::

   :doc:`asset-kinds` for renderer method selection.
   :doc:`name-resolution` for the reference shapes ``resolve_url`` receives.
   :doc:`deduplication` for the dedup strategy.
   :doc:`js-context` for the JS context policy.
   :doc:`/content/howto/write-a-static-backend` for a recipe.

.. _topics-static-backends:

Static Backends
===============

A backend converts an in memory ``Asset`` into the URL and HTML that the browser receives.
The framework ships a default backend that hashes content, points at ``STATIC_URL``, and emits minimal tags.
A custom backend can rewrite URLs, add integrity attributes, inject a CDN host, or override the rendered HTML entirely.

.. contents::
   :local:
   :depth: 2

Backend Contract
----------------

A backend subclasses ``next.static.backends.StaticBackend`` and implements at least one method.

.. code-block:: python
   :caption: notes/backends.py

   from next.static.backends import StaticBackend
   from next.static.assets import Asset


   class HashedBackend(StaticBackend):
       def url(self, asset: Asset) -> str:
           return f"{self.base_url}{asset.relative_path}?h={asset.content_hash[:8]}"

The base class implements ``url``, ``render_link_tag``, ``render_script_tag``, and ``render_module_tag``.
Override only the methods you need to change.

Override Points
---------------

``url``.
   Computes the final URL for an asset.
   The default reads from ``STATIC_URL`` and appends a content hash.

``render_link_tag``.
   Renders the ``<link rel="stylesheet">`` element.

``render_script_tag``.
   Renders the ``<script src="...">`` element.

``render_module_tag``.
   Renders the ``<script type="module" src="...">`` element.

Each render method receives the asset plus any keyword arguments passed on the template tag.

Configuring a Backend
---------------------

Set ``NEXT_FRAMEWORK["DEFAULT_STATIC_BACKENDS"]`` to a list of dotted paths.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           "notes.backends.HashedBackend",
       ]
   }

A list of two or more entries lets several backends contribute to one render.
The first backend that returns a non-empty result wins.

Request Aware Backends
----------------------

A backend can read the request to vary its output per visitor.
Override the constructor to accept a request keyword.

.. code-block:: python
   :caption: per request backend

   from django.http import HttpRequest

   from next.static.backends import StaticBackend
   from next.static.assets import Asset


   class CdnBackend(StaticBackend):
       def __init__(self, *, request: HttpRequest | None = None) -> None:
           super().__init__()
           self.request = request

       def url(self, asset: Asset) -> str:
           cdn = "https://cdn.example.com"
           if self.request and self.request.user.is_staff:
               cdn = "https://staff-cdn.example.com"
           return f"{cdn}{asset.relative_path}?h={asset.content_hash[:8]}"

The framework instantiates the backend with the request when it is available.
Backends that do not take a request keyword continue to work, the framework calls them with positional arguments.

Subresource Integrity
---------------------

Add integrity attributes by overriding ``render_link_tag``.

.. code-block:: python
   :caption: SRI backend

   import hashlib

   from next.static.backends import StaticBackend
   from next.static.assets import Asset


   class SriBackend(StaticBackend):
       def integrity(self, asset: Asset) -> str:
           digest = hashlib.sha384(asset.content).digest()
           return "sha384-" + base64.b64encode(digest).decode()

       def render_link_tag(self, asset: Asset, **attrs: str) -> str:
           url = self.url(asset)
           return (
               f'<link rel="stylesheet" href="{url}" '
               f'integrity="{self.integrity(asset)}" crossorigin>'
           )

Apply the same pattern to script tags and module tags.

JS Context Serializer
---------------------

The static pipeline ships the page context to the browser through a serializer.
Override ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` to control the wire format.

See :doc:`js-context` for the contract and patterns.

Tag Templates
-------------

A backend can render through Django template strings instead of inline f-strings.

.. code-block:: python
   :caption: template based backend

   from django.template import Template, Context

   from next.static.backends import StaticBackend


   class TemplateBackend(StaticBackend):
       link_template = Template(
           '<link rel="stylesheet" href="{{ url }}" data-asset="{{ asset.name }}">'
       )

       def render_link_tag(self, asset, **attrs):
           ctx = Context({"url": self.url(asset), "asset": asset})
           return self.link_template.render(ctx)

This is useful when the rendered HTML must follow a specific design system that pure string concatenation cannot express cleanly.

Collector Strategy
------------------

The collector calls the backend at emission time.
Two strategies live on the collector itself.

``CollectStrategy``.
   Decides how the collector merges assets.
   The default is ordered insertion.

``DedupStrategy``.
   Decides how the collector deduplicates entries.
   See :doc:`deduplication`.

A backend usually does not override either, but ``NEXT_FRAMEWORK`` can replace them through ``STATIC_COLLECT`` and ``STATIC_DEDUP``.

extend_default_backend
----------------------

When you want to add a backend without replacing the framework defaults, use the helper.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": extend_default_backend(
           "DEFAULT_STATIC_BACKENDS",
           "notes.backends.HashedBackend",
           position="last",
       ),
   }

The helper supports ``before``, ``after``, ``first``, and ``last`` positions, plus an optional ``target`` for relative placement.

Signals
-------

A backend that registers itself fires ``backend_loaded`` once at startup.
Use the signal to log which backend is active in production.

System Checks
-------------

The framework validates backend registration at startup.

- ``next.E080`` reports an unknown backend dotted path.
- ``next.E081`` reports a backend class that does not inherit from ``StaticBackend``.

Run ``uv run python manage.py check`` after every backend change.

Common Patterns
---------------

Cache Busting
~~~~~~~~~~~~~

Use the default backend in production.
The content hash in the URL invalidates browser caches when the file changes.

Multi CDN
~~~~~~~~~

Two backends, one for CSS and one for JS.
Each backend points at the CDN host that serves the appropriate kind.

Per Tenant CDN
~~~~~~~~~~~~~~

Use a request aware backend that reads the tenant from the host header and chooses a CDN host accordingly.

See Also
--------

.. seealso::

   :doc:`js-context` for the JS context serializer.
   :doc:`deduplication` for the dedup strategy.
   :doc:`/content/howto/write-a-static-backend` for a recipe.
   :doc:`/content/internals/static-pipeline` for the dispatcher view.

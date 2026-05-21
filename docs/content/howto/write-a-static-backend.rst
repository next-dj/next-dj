.. _howto-static-backend:

Customise Rendered Static Tags
==============================

Pick this page when the tag markup must change, for example to add ``crossorigin`` or load from a CDN host.
To resolve asset URLs through an external manifest, see :doc:`/content/howto/build-a-custom-asset-backend`.

Problem
-------

You want collected assets to render with extra attributes such as ``crossorigin`` or to load from a CDN host.

Solution
--------

For attribute only changes, set the ``css_tag``, ``js_tag``, and ``module_tag`` options on the default backend.
For URL rewriting, subclass ``StaticFilesBackend`` and override the renderer methods.

Walkthrough
-----------

Attributes Through Options
~~~~~~~~~~~~~~~~~~~~~~~~~~

The simplest customisation needs no Python.
Bake the attributes into the tag format strings.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "css_tag": '<link rel="stylesheet" href="{url}" crossorigin>',
                   "js_tag": '<script src="{url}" defer crossorigin></script>',
                   "module_tag": '<script type="module" src="{url}" crossorigin></script>',
               },
           }
       ]
   }

The format string must contain the ``{url}`` placeholder.

Subclass for URL Rewriting
~~~~~~~~~~~~~~~~~~~~~~~~~~

When the URL itself must change, subclass ``StaticFilesBackend`` and override the renderer methods.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend

   CDN = "https://cdn.example.com"

   class CdnBackend(StaticFilesBackend):
       def render_link_tag(self, url, *, request=None) -> str:
           return f'<link rel="stylesheet" href="{CDN}{url}">'

       def render_script_tag(self, url, *, request=None) -> str:
           return f'<script src="{CDN}{url}" defer></script>'

       def render_module_tag(self, url, *, request=None) -> str:
           return f'<script type="module" src="{CDN}{url}"></script>'

Each renderer method receives the URL and an optional ``request`` keyword.

Register the backend.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {"BACKEND": "notes.backends.CdnBackend", "OPTIONS": {}}
       ]
   }

Request Aware Output
~~~~~~~~~~~~~~~~~~~~

A renderer can read the request to vary its output per visitor.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend

   class TenantBackend(StaticFilesBackend):
       def render_link_tag(self, url, *, request=None) -> str:
           prefix = getattr(getattr(request, "tenant", None), "cdn", "")
           return f'<link rel="stylesheet" href="{prefix}{url}">'

The static manager passes the current request to every renderer call.

Tenant URL Prefix
~~~~~~~~~~~~~~~~~

A common multi-tenant pattern is to prefix every collected URL with a tenant slug so static files are scoped per tenant.
Override all three renderer methods and delegate to the parent after rewriting the URL.
Leave absolute URLs untouched.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend
   from notes.access import get_active_tenant

   PREFIX_FORMAT = "/_t/{slug}"

   class TenantPrefixStaticBackend(StaticFilesBackend):
       def render_link_tag(self, url, *, request=None) -> str:
           return super().render_link_tag(_prefixed(url, request))

       def render_script_tag(self, url, *, request=None) -> str:
           return super().render_script_tag(_prefixed(url, request))

       def render_module_tag(self, url, *, request=None) -> str:
           return super().render_module_tag(_prefixed(url, request))

   def _prefixed(url, request):
       tenant = get_active_tenant(request) if request is not None else None
       if tenant is None or not url.startswith("/"):
           return url
       return PREFIX_FORMAT.format(slug=tenant.slug) + url

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {"BACKEND": "notes.backends.TenantPrefixStaticBackend"},
       ],
   }

``get_active_tenant`` is a project-level helper that reads ``request.tenant`` set by middleware.
See :doc:`/content/howto/scope-requests-per-tenant` for the full middleware and provider setup.

Verification
------------

Reload a page and inspect the HTML.
Every ``<link>`` and ``<script>`` tag carries the new attributes or the CDN host.

Run ``uv run python manage.py check`` and confirm the backend is registered.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/backends` for the backend contract.
   :doc:`/content/topics/static-assets/asset-kinds` for the renderer methods.
   :doc:`/content/howto/build-a-custom-asset-backend` for resolving URLs through an external manifest.

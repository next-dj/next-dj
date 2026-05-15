.. _howto-static-backend:

Write a Static Backend
======================

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

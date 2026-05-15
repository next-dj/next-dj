.. _howto-static-backend:

Write a Static Backend
======================

Problem
-------

You want collected assets to load from a CDN host with subresource integrity attributes and a cache-busting query string.

Solution
--------

Subclass ``next.static.backends.StaticBackend``, override ``url``, ``render_link_tag``, and ``render_script_tag``, and register the dotted path in ``DEFAULT_STATIC_BACKENDS``.

Walkthrough
-----------

Write the backend.

.. code-block:: python
   :caption: notes/backends.py

   import base64
   import hashlib

   from next.static.assets import Asset
   from next.static.backends import StaticBackend


   CDN = "https://cdn.example.com"


   class CdnIntegrityBackend(StaticBackend):
       def url(self, asset: Asset) -> str:
           return f"{CDN}{asset.relative_path}?h={asset.content_hash[:8]}"

       def integrity(self, asset: Asset) -> str:
           digest = hashlib.sha384(asset.content).digest()
           return "sha384-" + base64.b64encode(digest).decode()

       def render_link_tag(self, asset: Asset, **attrs: str) -> str:
           return (
               f'<link rel="stylesheet" href="{self.url(asset)}" '
               f'integrity="{self.integrity(asset)}" crossorigin>'
           )

       def render_script_tag(self, asset: Asset, **attrs: str) -> str:
           return (
               f'<script src="{self.url(asset)}" '
               f'integrity="{self.integrity(asset)}" crossorigin></script>'
           )

Register the backend.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           "notes.backends.CdnIntegrityBackend",
       ]
   }

The configured backend replaces the default ``StaticBackend``.
Every emitted link and script now points at the CDN host with an integrity attribute.

Verification
------------

Reload a page and inspect the HTML.
Every ``<link>`` and ``<script>`` tag contains ``integrity="sha384-..."`` and ``crossorigin``.

Run ``uv run python manage.py check`` and confirm the backend is registered.

Extending Default Backends
--------------------------

Keep the default behaviour and add the backend as a second option using ``extend_default_backend``.

.. code-block:: python
   :caption: alternative wiring

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": extend_default_backend(
           "DEFAULT_STATIC_BACKENDS",
           "notes.backends.CdnIntegrityBackend",
           position="last",
       )
   }

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/backends` for the contract.
   :doc:`extend-a-default-backend` for the helper details.
   :doc:`/content/internals/static-pipeline` for the dispatcher view.

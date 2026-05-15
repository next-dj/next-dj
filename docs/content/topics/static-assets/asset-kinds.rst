.. _topics-static-asset-kinds:

Asset Kinds
===========

An asset kind binds an extension to a backend method and to a bucket inside the collector.
The framework ships three kinds out of the box.
You can register more kinds for ``.ts``, ``.jsx``, ``.vue``, or any other format that the project ships.

.. contents::
   :local:
   :depth: 2

Built In Kinds
--------------

Three kinds are registered at startup.

``css``.
   Source extension ``.css``.
   Rendered through ``StaticBackend.render_link_tag`` into ``<link rel="stylesheet">``.

``js``.
   Source extension ``.js``.
   Rendered through ``StaticBackend.render_script_tag`` into ``<script src="...">``.

``module``.
   Source extension ``.mjs``.
   Rendered through ``StaticBackend.render_module_tag`` into ``<script type="module" src="...">``.

The collector keeps each kind in its own bucket.
``{% collect_styles %}`` emits the ``css`` bucket.
``{% collect_scripts %}`` emits the ``js`` and ``module`` buckets in that order.

Registering a Custom Kind
-------------------------

Add a kind through ``NEXT_FRAMEWORK["DEFAULT_ASSET_KINDS"]``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_ASSET_KINDS": {
           "jsx": {
               "extension": ".jsx",
               "renderer": "notes.static.render_jsx",
               "bucket": "scripts",
           }
       }
   }

The settings merge with the framework defaults.
Only the kinds you declare appear in the project, the built ins remain unchanged.

Renderer Contract
-----------------

A renderer is a callable that receives the backend, the asset, and any attributes from the template tag.

.. code-block:: python
   :caption: notes/static.py

   from next.static.assets import Asset
   from next.static.backends import StaticBackend


   def render_jsx(backend: StaticBackend, asset: Asset, **attrs: str) -> str:
       url = backend.url(asset)
       return f'<script type="text/babel" src="{url}"></script>'

The renderer returns the HTML string.
The framework escapes attribute values, but the rendered HTML itself is treated as safe.

Bucket Selection
----------------

The ``bucket`` field decides which template tag emits the asset.

- ``styles`` becomes part of ``{% collect_styles %}``.
- ``scripts`` becomes part of ``{% collect_scripts %}``.
- Any other name becomes a new bucket.
  Emit it through ``{% collect_bucket "name" %}`` from the template tag library.

Custom buckets are useful for preload hints, font links, and other tags that share asset semantics but render in a different place.

Registering Through the API
---------------------------

The Python API also accepts kind registration.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig

   from next.static import kind_registry

   from notes.static import render_jsx


   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           kind_registry.register(
               name="jsx",
               extension=".jsx",
               renderer=render_jsx,
               bucket="scripts",
           )

Use this when the kind depends on conditional configuration that does not fit in settings.

Module Kind Details
-------------------

The ``module`` kind renders ``<script type="module" src="...">`` through ``StaticBackend.render_module_tag``.
A custom backend can override the method to add ``crossorigin`` or ``integrity`` attributes.

.. code-block:: python
   :caption: custom backend that adds crossorigin

   from next.static.backends import StaticBackend


   class CrossOriginBackend(StaticBackend):
       def render_module_tag(self, asset, **attrs) -> str:
           url = self.url(asset)
           return f'<script type="module" src="{url}" crossorigin></script>'

See :doc:`backends` for the full contract.

System Checks
-------------

The framework validates kind registration at startup.

- ``next.E070`` reports a kind whose extension is empty or duplicated.
- ``next.E071`` reports an unknown renderer dotted path.

Run ``uv run python manage.py check`` after every kind registration change.

Common Patterns
---------------

TypeScript
~~~~~~~~~~

Register a ``ts`` kind that points at a build hook.
The renderer can either reference a pre built JS file or run a compiler in development.

Vue Single File Components
~~~~~~~~~~~~~~~~~~~~~~~~~~

Register a ``vue`` kind that maps to a custom stem.
See ``examples/live-polls`` for a worked setup.

Font Preloads
~~~~~~~~~~~~~

Register a ``font`` kind in a ``preload`` bucket.
The template renders preload links separately from styles and scripts.

See Also
--------

.. seealso::

   :doc:`custom-stems` for filename conventions.
   :doc:`backends` for the renderer methods.
   :doc:`/content/howto/add-a-new-asset-kind` for a recipe.
   :doc:`/content/ref/static` for the public API.

.. _howto-asset-kind:

Add a New Asset Kind
====================

Problem
-------

You want the static collector to recognise files with a new extension such as ``.jsx`` and emit a matching script tag.

Solution
--------

Register the kind through ``NEXT_FRAMEWORK["DEFAULT_ASSET_KINDS"]`` and provide a renderer callable that converts an asset into the correct HTML element.

Walkthrough
-----------

Write the renderer.

.. code-block:: python
   :caption: notes/static.py

   from next.static.assets import Asset
   from next.static.backends import StaticBackend


   def render_jsx(backend: StaticBackend, asset: Asset, **attrs: str) -> str:
       url = backend.url(asset)
       return f'<script type="text/babel" src="{url}"></script>'

Register the kind.

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

Ship the file.

.. code-block:: text
   :caption: notes/_components/note_card/component.jsx

   const NoteCard = ({ title }) => <article>{title}</article>;

Confirm that the component template still loads.
The collector picks up ``component.jsx`` because the new kind matches the extension.

Emit the Asset
~~~~~~~~~~~~~~

The kind sits in the ``scripts`` bucket, so ``{% collect_scripts %}`` in the layout emits the new tag.
No template change is required.

Verification
------------

Reload the page and inspect the HTML source.
A ``<script type="text/babel">`` element points at the JSX file.

Run ``uv run python manage.py check`` and confirm no warnings.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/asset-kinds` for the registration mechanics.
   :doc:`/content/topics/static-assets/backends` for the backend contract.
   :doc:`/content/topics/extending` for the registry pattern.

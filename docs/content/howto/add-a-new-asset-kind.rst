.. _howto-asset-kind:

Add a New Asset Kind
====================

Problem
-------

You want discovery to recognise files with a new extension such as ``.jsx`` and emit a matching script tag.

Solution
--------

Register the kind through ``next.static.default_kinds`` in ``AppConfig.ready`` and point it at a backend renderer method.

Walkthrough
-----------

Register the kind.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig

   from next.static import default_kinds


   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_kinds.register(
               "jsx",
               extension=".jsx",
               slot="scripts",
               renderer="render_module_tag",
           )

See :doc:`/content/topics/static-assets/asset-kinds` for the ``register`` signature.
The ``module`` style ``render_module_tag`` is reused here because pre compiled JSX ships as ES modules.

Ship the file.

.. code-block:: text
   :caption: notes/_components/note_card/component.jsx

   export const NoteCard = ({ title }) => title;

Discovery picks up ``component.jsx`` because ``component`` is a registered stem and ``.jsx`` is now a registered extension.

Emit the Asset
~~~~~~~~~~~~~~

The kind sits in the ``scripts`` slot, so ``{% collect_scripts %}`` in the layout emits the tag.
No template change is needed.

Custom Renderer
~~~~~~~~~~~~~~~

When the new kind needs a tag shape that the bundled methods do not produce, add a renderer method on a custom backend.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend


   class BabelBackend(StaticFilesBackend):
       def render_babel_tag(self, url: str, *, request=None) -> str:
           return f'<script type="text/babel" src="{url}"></script>'

Register the kind against the new method and register the backend.

.. code-block:: python
   :caption: notes/apps.py

   default_kinds.register(
       "jsx",
       extension=".jsx",
       slot="scripts",
       renderer="render_babel_tag",
   )

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {"BACKEND": "notes.backends.BabelBackend", "OPTIONS": {}}
       ]
   }

Verification
------------

Reload the page and inspect the HTML source.
A script tag points at the JSX file.

Run ``uv run python manage.py check`` and confirm no warnings.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/asset-kinds` for the registration mechanics.
   :doc:`/content/topics/static-assets/backends` for the backend renderer methods.

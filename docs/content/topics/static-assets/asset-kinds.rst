.. _topics-static-asset-kinds:

Asset Kinds
===========

An asset kind binds a file extension to a placeholder slot and to a backend renderer method.
The framework ships three kinds and lets projects register more.

.. contents::
   :local:
   :depth: 2

Built In Kinds
--------------

``register_defaults`` registers three kinds at startup through ``next.static.default_kinds``.

.. list-table::
   :header-rows: 1
   :widths: 20 25 25 30

   * - Kind
     - Extension
     - Slot
     - Renderer method
   * - ``css``
     - ``.css``
     - ``styles``
     - ``render_link_tag``
   * - ``js``
     - ``.js``
     - ``scripts``
     - ``render_script_tag``
   * - ``module``
     - ``.mjs``
     - ``scripts``
     - ``render_module_tag``

The static subsystem does not privilege CSS or JS in core code.
The three built in kinds register through the same public API that a project uses for a new kind.

The Registry
------------

The kind registry is ``next.static.default_kinds``, an instance of ``KindRegistry``.
A kind registration is keyed by the ``kind`` identifier and carries three pieces of metadata.

``kind``.
   The registry key, a non-empty Python identifier such as ``css`` or ``jsx``.

``extension``.
   The file suffix, starting with a dot, such as ``.jsx``.
   Discovery looks for files matching ``{stem}{extension}``.

``slot``.
   The name of the placeholder slot that buckets the asset at render time.
   The bundled slots are ``styles`` and ``scripts``.

``renderer``.
   The method name that the active static backend exposes for rendering this kind.
   The manager looks the method up with ``getattr`` on the backend per asset.

Registering a Kind
------------------

Register kinds in ``AppConfig.ready`` so the kind exists before the first request.

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

The ``jsx`` kind now lands in the ``scripts`` slot and renders through ``render_module_tag``.
A repeated call with identical parameters is idempotent.
A repeated call with different parameters raises ``ValueError``.

Renderer Methods
----------------

The ``renderer`` value is a method name on the static backend.
The bundled ``StaticFilesBackend`` exposes three.

- ``render_link_tag`` for stylesheets.
- ``render_script_tag`` for scripts.
- ``render_module_tag`` for module scripts.

A custom kind reuses one of these methods, or a custom backend can add a new method.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend

   class BabelBackend(StaticFilesBackend):
       def render_babel_tag(self, url: str, *, request=None) -> str:
           return f'<script type="text/babel" src="{url}"></script>'

.. code-block:: python
   :caption: notes/apps.py

   default_kinds.register(
       "jsx",
       extension=".jsx",
       slot="scripts",
       renderer="render_babel_tag",
   )

The backend is registered through ``DEFAULT_STATIC_BACKENDS``, see :doc:`backends`.

Placeholder Slots
-----------------

A slot is the location where the static manager injects rendered tags.
The slot registry is ``next.static.default_placeholders``.
The framework registers two slots, ``styles`` and ``scripts``, each with an HTML comment token.

The Placeholder Registry
~~~~~~~~~~~~~~~~~~~~~~~~

``default_placeholders`` is an instance of ``PlaceholderRegistry``, exported from ``next.static`` alongside the ``PlaceholderSlot`` record.
A ``PlaceholderSlot`` binds a slot ``name`` to the HTML comment ``token`` that the matching ``{% collect_* %}`` tag emits.
The ``register`` method has the signature ``register(name, *, token)``.
A repeated call with the same token is idempotent, and a repeated call with a different token raises ``ValueError``.

Register a new slot when a kind should inject somewhere other than the standard two slots.

.. code-block:: python
   :caption: notes/apps.py

   from next.static import default_kinds, default_placeholders

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_placeholders.register("preload", token="<!-- next:preload -->")
           default_kinds.register(
               "font",
               extension=".woff2",
               slot="preload",
               renderer="render_link_tag",
           )

The layout must contain the slot token, or a template tag that emits it, for the manager to find a place to inject.

Module Kind
-----------

The ``module`` kind renders ``<script type="module" src="...">`` through ``render_module_tag``.
Customise the rendered output through the ``module_tag`` key in the backend ``OPTIONS`` mapping, see :doc:`backends`.

System Checks
-------------

The static system checks validate the backend configuration only.
They do not validate kind registration.
A bad call to ``default_kinds.register`` raises ``ValueError`` during ``AppConfig.ready``, which aborts ``manage.py check`` before any check runs.

Common Patterns
---------------

Custom asset kinds drive worked projects, including the ``kanban`` example with a ``.jsx`` kind and the ``live-polls`` example with a Vue single file component kind.
See :doc:`/content/misc/examples` for the runnable projects and their walkthroughs.

See Also
--------

.. seealso::

   :doc:`custom-stems` for recognising new filenames.
   :doc:`backends` for the renderer methods.
   :doc:`/content/howto/add-a-new-asset-kind` for a recipe.
   :doc:`/content/ref/static` for the public API.
